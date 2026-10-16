#!/usr/bin/env python3
"""Falsification campaign for scripts/k3d-gpu-witness (docs/witness-prereg.md).

The simulator models an agent-style decode loop as piecewise-constant activity
segments (GPU engine busy rate per device, CPU cores per process). It then emits
ONLY the raw text a sidecar would read: DRM fdinfo files, /proc/<pid>/stat lines
quantized to 10 ms ticks, and nvidia-smi pmon rows refreshed once a second. The
witness receives that text plus the claim and rule, never the run's class or
parameters, and is evaluated through the same code path as on a real host.

Usage: tests/witness_sim.py [--runs N] [--seed S] [--json OUT]
"""
import json
import math
import os
import random
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
W = SourceFileLoader("witness", os.path.join(HERE, "..", "scripts", "k3d-gpu-witness")).load_module()
RULE = json.load(open(os.path.join(HERE, "..", "share", "witness", "rule.json")))

ENGINE, TOOL, SIDECAR = 4242, 5151, 7
CLAIM = {"i915": "0000:00:02.0", "xe": "0000:03:00.0", "nvidia": "nvidia:0"}
OTHER = {"i915": "0000:03:00.0", "xe": "0000:00:02.0", "nvidia": "nvidia:1"}
XE_HZ = 19.2e6  # GPU timestamp rate for drm-total-cycles; only the ratio matters


class Trace:
    """Piecewise-constant rates; cumulative(t) integrates them."""

    def __init__(self):
        self.seg = {}

    def add(self, key, t0, t1, rate):
        if t1 > t0 and rate > 0:
            self.seg.setdefault(key, []).append((t0, t1, rate))

    def cumulative(self, key, times):
        """times must be sorted; segments of one key never overlap."""
        segs = sorted(self.seg.get(key, []))
        out, done, j = [], 0.0, 0
        for t in times:
            while j < len(segs) and segs[j][1] <= t:
                a, b, r = segs[j]
                done += r * (b - a)
                j += 1
            part = 0.0
            if j < len(segs) and segs[j][0] < t:
                a, _, r = segs[j]
                part = r * (t - a)
            out.append(done + part)
        return out


def gen_run(rng, cls, driver, p):
    """Return (trace, duration) for one run. p carries the sweep overrides."""
    tr = Trace()
    dev = OTHER[driver] if cls == "WRONG" else CLAIM[driver]
    u = rng.uniform(*p["u"])                  # engine busy fraction while in a GPU phase
    k_base = rng.uniform(0.008, 0.040)        # per-token GPU kernel seconds
    spin = rng.uniform(0, p["spin_max"])      # runtime poll/spin thread
    rho = rng.uniform(0, 0.7)                 # agent tool-wait fraction of wall
    has_tool = rng.random() < 0.5
    f = {"CLEAN": 0.0, "WRONG": 0.0, "FULL": 1.0}.get(cls) if cls != "PARTIAL" else rng.uniform(*p["f"])
    threads = rng.randint(*p["threads"])
    slow = rng.uniform(3, 12)                 # CPU seconds per GPU second of the same work
    T = rng.uniform(10, 30)
    t = 0.0

    def compute(t, k, uu):
        if f > 0:
            eff = rng.uniform(0.85, 1.0)
            dur = f * k * slow / (threads * eff)
            tr.add(("cpu", ENGINE), t, t + dur, threads * eff)
            t += dur
        if f < 1:
            dur = (1 - f) * k / uu
            tr.add(("gpu", dev), t, t + dur, uu)
            tr.add(("cpu", ENGINE), t, t + dur, spin)
            t += dur
        return t

    while t < T:
        t_turn = t
        t = compute(t, rng.uniform(0.2, 1.0) * 0.9, 0.9)       # prefill, compute-bound
        for _ in range(rng.randint(30, 150)):
            t = compute(t, k_base * rng.uniform(0.9, 1.1), u)
            h = rng.uniform(0.001, 0.008)                       # sampling + detokenize
            tr.add(("cpu", ENGINE), t, t + h, 1.0)
            t += h
        wait = (t - t_turn) * rho / (1 - rho) * rng.uniform(0.5, 1.5)
        if not (has_tool and p["tool_inproc"]):
            tr.add(("cpu", ENGINE), t, t + wait, 0.02)
        if has_tool:
            who = ENGINE if p["tool_inproc"] else TOOL
            tr.add(("cpu", who), t, t + wait * rng.uniform(0.3, 1.0), rng.uniform(2, 6))
        t += wait
    tr.add(("cpu", SIDECAR), 0, t, 0.01)
    return tr, t


def stat_line(pid, ticks):
    f = [str(pid), "(p%d)" % pid, "S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 37
    return " ".join(f) + "\n"


def i915_text(cid, pdev, ns):
    return ("pos:\t0\nflags:\t02100002\nmnt_id:\t25\nino:\t570\ndrm-driver:\ti915\n"
            "drm-client-id:\t%d\ndrm-pdev:\t%s\ndrm-engine-render:\t%d ns\n"
            "drm-engine-copy:\t0 ns\ndrm-engine-video:\t0 ns\ndrm-engine-capacity-video:\t2\n"
            "drm-engine-video-enhance:\t0 ns\n" % (cid, pdev, ns))


def xe_text(cid, pdev, cyc, total):
    s = "pos:\t0\nflags:\t0100002\ndrm-driver:\txe\ndrm-client-id:\t%d\ndrm-pdev:\t%s\n" % (cid, pdev)
    for c in ("rcs", "bcs", "vcs", "vecs", "ccs"):
        s += "drm-cycles-%s:\t%d\ndrm-total-cycles-%s:\t%d\n" % (c, cyc if c == "ccs" else 0, c, total)
    return s + "drm-engine-capacity-vcs:\t2\ndrm-engine-capacity-ccs:\t4\n"


def snapshots(rng, tr, T, driver, period):
    times, t = [], 0.0
    while t <= T:
        times.append(max(0.0, t + rng.gauss(0, 0.003)) if t else 0.0)
        t += period
    times.append(T)
    times = sorted(times)
    devs = [CLAIM[driver], OTHER[driver]]
    gpu = {d: tr.cumulative(("gpu", d), times) for d in devs}
    cpu = {p: tr.cumulative(("cpu", p), times) for p in (ENGINE, TOOL, SIDECAR)}
    # pmon refreshes once a second with the SM% of the previous second.
    if driver == "nvidia":
        secs = list(range(0, int(T) + 2))
        gsec = {d: tr.cumulative(("gpu", d), secs) for d in devs}
    snaps = []
    for i, t in enumerate(times):
        s = {"t_ns": int(t * 1e9), "fdinfo": {}, "stat": {}, "nvfd": {}, "pmon": None}
        for p in (ENGINE, TOOL, SIDECAR):
            s["stat"][str(p)] = stat_line(p, int(math.floor(cpu[p][i] * 100 + 1e-9)))
        if driver == "nvidia":
            s["nvfd"][str(ENGINE)] = [0, 1]
            rows = []
            sec = int(t)
            for idx, d in enumerate(devs):
                if sec >= 1:
                    sm = round(100 * (gsec[d][sec] - gsec[d][sec - 1]))
                    rows.append("    %d     %d     C    %s      -      -      -      -      -    llama-server"
                                % (idx, 900000 + ENGINE, sm if sm > 0 else "-"))
            s["pmon"] = "\n".join(rows) or None
        else:
            for fd, (d, cid) in enumerate([(devs[0], 11), (devs[0], 11), (devs[1], 12)]):
                if driver == "i915":
                    txt = i915_text(cid, d, int(gpu[d][i] * 1e9))
                else:
                    txt = xe_text(cid, d, int(gpu[d][i] * XE_HZ), int(t * XE_HZ) + 1000)
                s["fdinfo"]["%d/%d" % (ENGINE, 5 + fd)] = txt
        snaps.append(s)
    return snaps


RULES = {
    "witness": RULE,
    "A1_aggregate": dict(RULE, interval_resolved=False),
    "A2_pod_cpu": dict(RULE, cpu_scope="pod"),
    "B1_any_busy": {"kind": "any_busy"},
    "B2_fixed_ratio": {"kind": "fixed_ratio", "min_busy_over_wall": 0.7, "max_cpu_cores": 1.5},
}
BASE = {"u": (0.25, 0.95), "f": (0.10, 0.50), "threads": (2, 8), "spin_max": 1.0, "period": 0.1,
        "tool_inproc": False}


def campaign(runs, seed, p=None, rules=RULES, classes=("CLEAN", "PARTIAL", "FULL", "WRONG")):
    p = dict(BASE, **(p or {}))
    rng = random.Random(seed)
    flags = {r: {c: 0 for c in classes} for r in rules}
    by_driver = {r: {} for r in rules}
    n = {c: 0 for c in classes}
    for c in classes:
        for i in range(runs):
            driver = ("i915", "xe", "nvidia")[i % 3]
            tr, T = gen_run(rng, c, driver, p)
            snaps = snapshots(rng, tr, T, driver, p["period"])
            pidmap = {str(900000 + ENGINE): str(ENGINE)} if driver == "nvidia" else None
            n[c] += 1
            reduced = W.intervals(snaps, CLAIM[driver], RULE, pidmap)
            for name, rule in rules.items():
                v = W.evaluate(snaps, CLAIM[driver], rule, pidmap, reduced)["verdict"]
                hit = v != "PASS"
                flags[name][c] += hit
                d = by_driver[name].setdefault(driver, {}).setdefault(c, [0, 0])
                d[0] += hit
                d[1] += 1
    rate = {r: {c: flags[r][c] / n[c] for c in classes} for r in rules}
    return rate, by_driver


def fmt(rate):
    cols = list(next(iter(rate.values())).keys())
    out = "%-16s" % "rule" + "".join("%9s" % c for c in cols) + "\n"
    for r, row in rate.items():
        out += "%-16s" % r + "".join("%8.1f%%" % (100 * row[c]) for c in cols) + "\n"
    return out


def main(argv):
    runs = int(argv[argv.index("--runs") + 1]) if "--runs" in argv else 200
    seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 1
    res = {}
    print("== main campaign: %d runs/class, seed %d (flag rate; CLEAN = false flags)" % (runs, seed))
    rate, by_driver = campaign(runs, seed)
    print(fmt(rate))
    print("witness by driver:", json.dumps({d: {c: "%d/%d" % tuple(v) for c, v in cs.items()}
                                            for d, cs in by_driver["witness"].items()}))
    res["main"] = rate
    res["main_by_driver"] = by_driver

    sweeps = [
        ("seed 2", {}, 2), ("seed 3", {}, 3),
        ("period 50 ms", {"period": 0.05}, seed), ("period 250 ms", {"period": 0.25}, seed),
        ("period 1 s", {"period": 1.0}, seed), ("period 2 s", {"period": 2.0}, seed),
        ("fallback 1 thread", {"threads": (1, 1)}, seed),
        ("fallback f 0.02-0.10", {"f": (0.02, 0.10)}, seed),
        ("spin up to 2 cores", {"spin_max": 2.0}, seed),
        ("decode u 0.05-0.25", {"u": (0.05, 0.25)}, seed),
        ("tool runs inside the engine process", {"tool_inproc": True}, seed),
    ]
    sub = {k: RULES[k] for k in ("witness", "A1_aggregate", "A2_pod_cpu", "B2_fixed_ratio")}
    res["sweeps"] = {}
    for name, p, s in sweeps:
        n = max(30, runs // 4)
        r, _ = campaign(n, s, p, sub, ("CLEAN", "PARTIAL"))
        res["sweeps"][name] = r
        print("== sweep: %s (%d runs/class)" % (name, n))
        print(fmt(r))
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

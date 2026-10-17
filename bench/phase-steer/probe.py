#!/usr/bin/env python3
"""H20 phase-steer oracle probe (EXPERIMENTS.md, "H20 pre-registration").

Runs N closed-loop agents, each against its own llama-server container, under
four CPU-mask arms (SP, PS, ALL, NEG) in the pre-registered Latin-square order,
and writes one JSON line per cell (energy, latency, placement, confounds).
`--analyze FILE` turns those lines into the pre-registered verdict.

Stdlib only. Needs root for RAPL (`sudo`). Caps its own wall time (default
19.5 min) and removes every `sci-h20-*` container it started, on exit or abort.
Run it under the live lock:

  flock -w 10800 "$LOCK" sudo python3 bench/phase-steer/probe.py \
      --image "$IMAGE" --model "$MODEL" --out "$OUT"
  python3 bench/phase-steer/probe.py --analyze "$OUT"
"""
import argparse
import glob
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

P, E, ALLC = "0-15", "16-31", "0-31"
# Amendment A1 (2026-10-17, before any run): ggml strict mode pins thread j of
# every pool to the j-th set bit of its mask, counting from bit 0 in each
# process (ggml-cpu.c ggml_thread_cpumask_next). Shared masks therefore stack
# every agent on the same first CPUs. Each agent now gets a disjoint slice, one
# thread per physical core: P = cores {4i, 4i+2} (no SMT sibling), E = the
# 4-core Gracemont L2 cluster 16+4i..16+4i+3. ALL is non-strict with no mask,
# so the kernel (ITMT/HFI) places the threads.
PHASES = {  # arm: (decode slice, prefill slice); None = no mask, non-strict
    "SP": ("P", "P"), "PS": ("E", "P"), "ALL": (None, None), "NEG": ("P", "E"),
}
ORDER = ["SP", "PS", "ALL", "PS", "ALL", "SP", "ALL", "SP", "PS", "NEG"]
REQUIRED_FLAGS = ["--cpu-mask", "--cpu-mask-batch", "--cpu-strict", "--cpu-strict-batch", "--parallel", "--mlock",
                  "--poll", "--poll-batch"]
RAPL = "/sys/class/powercap/intel-rapl:0"
PREFIX_TOK, TURN_TOK, OUT_TOK = 1024, 396, 96
PRIMARY = ("SP", "PS", "ALL")


def expand(cpulist):
    out = []
    for part in cpulist.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def hexmask(cpulist):
    return hex(sum(1 << c for c in expand(cpulist)))


def sh(*cmd, check=True, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r.stdout


def rd(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


# ---- host readings -------------------------------------------------------------
def energy_uj():
    return int(rd(f"{RAPL}/energy_uj", "0")), int(rd(f"{RAPL}/max_energy_range_uj", "0"))


def energy_delta_j(a, b):
    (e0, rng), (e1, _) = a, b
    d = e1 - e0
    if d < 0 and rng:
        d += rng
    return d / 1e6


def cpu_busy():
    """{cpu: (busy, total)} jiffies from /proc/stat."""
    out = {}
    for line in rd("/proc/stat").splitlines():
        m = re.match(r"cpu(\d+) (.*)", line)
        if m:
            v = [int(x) for x in m.group(2).split()]
            idle = v[3] + v[4]
            out[int(m.group(1))] = (sum(v[:8]) - idle, sum(v[:8]))
    return out


def busy_frac(a, b, cpus):
    busy = sum(b[c][0] - a[c][0] for c in cpus if c in a and c in b)
    total = sum(b[c][1] - a[c][1] for c in cpus if c in a and c in b)
    return round(busy / total, 4) if total else None


def throttle_total():
    return sum(int(rd(p, "0") or 0) for p in glob.glob("/sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count"))


def pswpin():
    for line in rd("/proc/vmstat").splitlines():
        if line.startswith("pswpin "):
            return int(line.split()[1])
    return 0


def mean_mhz(cpus):
    v = [int(rd(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", "0")) for c in cpus]
    v = [x for x in v if x]
    return round(statistics.mean(v) / 1000) if v else None


def watts_over(seconds):
    a, t0 = energy_uj(), time.monotonic()
    time.sleep(seconds)
    return energy_delta_j(a, energy_uj()) / (time.monotonic() - t0)


def slice_cpus(kind, i):
    """Agent i's disjoint CPUs: kind 'P' or 'E' (i9-13900T layout, asserted in prechecks)."""
    return [4 * i, 4 * i + 2] if kind == "P" else [16 + 4 * i + k for k in range(4)]


def server_args(arm, i):
    """llama-server placement flags for agent i in arm: one thread per physical core."""
    dec, pre = PHASES[arm]
    if dec is None:
        return ["-t", "2", "-tb", "2", "--cpu-strict", "0", "--cpu-strict-batch", "0"]
    d, p = slice_cpus(dec, i), slice_cpus(pre, i)
    tolist = lambda v: ",".join(map(str, v))
    return ["-t", str(len(d)), "-tb", str(len(p)),
            "--cpu-mask", hexmask(tolist(d)), "--cpu-mask-batch", hexmask(tolist(p)),
            "--cpu-strict", "1", "--cpu-strict-batch", "1"]


def expected_cpus(arm, i):
    dec, pre = PHASES[arm]
    return set() if dec is None else set(slice_cpus(dec, i)) | set(slice_cpus(pre, i))


def pinned(lists):
    """CPUs that some thread is pinned to alone (Cpus_allowed_list of one CPU)."""
    return {int(x) for x in lists if x.isdigit()}


def overlap(affinity):
    """CPUs pinned by threads of more than one server."""
    seen, shared = {}, set()
    for name, lists in affinity.items():
        for c in pinned(lists):
            if c in seen and seen[c] != name:
                shared.add(c)
            seen[c] = name
    return sorted(shared)


def placement_ok(arm, affinity):
    """Strict arms: each server pins threads only inside its own slice; no CPU
    shared. A subset is allowed: the compute main thread moves between pools."""
    if PHASES[arm][0] is None:
        return all(not pinned(v) for v in affinity.values())
    if overlap(affinity):
        return False
    return all(pinned(v) and pinned(v) <= expected_cpus(arm, i) for i, v in enumerate(affinity.values()))


# ---- servers and agents --------------------------------------------------------
def start_servers(args, arm, cell, n):
    names = []
    for i in range(n):
        name = f"sci-h20-c{cell}-a{i}"
        sh("docker", "run", "-d", "--rm", "--name", name, "--cpuset-cpus", ALLC,
           "--ulimit", "memlock=-1:-1", "-p", f"127.0.0.1:{args.port0 + i}:8080",
           "-v", f"{os.path.abspath(args.model)}:/m/model.gguf:ro", args.image,
           "-m", "/m/model.gguf", "--host", "0.0.0.0", "--port", "8080", "-c", "2048",
           "--parallel", "1", "--poll", str(args.poll), "--poll-batch", str(int(args.poll > 0)),
           *server_args(arm, i), "--mlock")
        names.append(name)
    return names


def remove(names):
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)


def wait_healthy(n, port0, deadline):
    for i in range(n):
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port0 + i}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"server {i} not healthy")
            time.sleep(0.5)


def thread_affinity(name):
    pid = sh("docker", "inspect", "-f", "{{.State.Pid}}", name).strip()
    lists = {rd(f"{t}/status").split("Cpus_allowed_list:")[-1].split()[0]
             for t in glob.glob(f"/proc/{pid}/task/*") if "Cpus_allowed_list" in rd(f"{t}/status")}
    return sorted(lists)


def turn(port, tokens):
    body = json.dumps({"prompt": tokens, "n_predict": OUT_TOK, "ignore_eos": True,
                       "cache_prompt": True, "stream": True, "temperature": 0}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", body, {"Content-Type": "application/json"})
    t0 = time.monotonic()
    stamps = []
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                if ev.get("content") or ev.get("tokens"):
                    stamps.append(time.monotonic())
                if ev.get("stop"):
                    break
    return t0, stamps


def agent(i, port, prefix, stop, rows, errors):
    rng = random.Random(1000 + i)
    while not stop.is_set():
        tokens = prefix + [rng.randrange(1000, 100000) for _ in range(TURN_TOK)]
        try:
            t0, s = turn(port, tokens)
            rows.append({"agent": i, "start": t0, "first": s[0] if s else None,
                         "end": s[-1] if s else time.monotonic(), "tokens": len(s)})
        except Exception as ex:  # a failed request is a guardrail breach, recorded
            errors.append(f"agent {i}: {ex}")
            time.sleep(0.5)


def run_cell(args, arm, rep, cell, idle_w, deadline):
    pcpus, ecpus = expand(P), expand(E)
    s0, pre_t = cpu_busy(), time.monotonic()
    time.sleep(5)
    s1 = cpu_busy()
    pre_busy = {"P": busy_frac(s0, s1, pcpus), "E": busy_frac(s0, s1, ecpus)}
    names = start_servers(args, arm, cell, args.agents)
    try:
        wait_healthy(args.agents, args.port0, min(deadline, time.monotonic() + 120))
        stop, rows, errors = threading.Event(), [], []
        prefixes = [[random.Random(i).randrange(1000, 100000) for _ in range(PREFIX_TOK)] for i in range(args.agents)]
        th = [threading.Thread(target=agent, args=(i, args.port0 + i, prefixes[i], stop, rows, errors), daemon=True)
              for i in range(args.agents)]
        for t in th:
            t.start()
        time.sleep(args.warm)
        while len({r["agent"] for r in rows if r["tokens"]}) < args.agents and time.monotonic() < deadline:
            time.sleep(0.5)
        affinity = {n: thread_affinity(n) for n in names}
        b0, e0, w0, thr0, sw0 = cpu_busy(), energy_uj(), time.monotonic(), throttle_total(), pswpin()
        mhz = {"P": mean_mhz(pcpus), "E": mean_mhz(ecpus)}
        time.sleep(min(args.window, max(0, deadline - time.monotonic())))
        b1, e1, w1, thr1, sw1 = cpu_busy(), energy_uj(), time.monotonic(), throttle_total(), pswpin()
        stop.set()
        for t in th:
            t.join(timeout=130)
    finally:
        remove(names)
    secs = w1 - w0
    turns = [r for r in rows if r["start"] >= w0 and r["end"] <= w1 and r["tokens"]]
    toks = sum(r["tokens"] for r in turns)
    joules = energy_delta_j(e0, e1)
    return {
        "kind": "cell", "arm": arm, "rep": rep, "cell": cell, "window_s": round(secs, 2),
        "turns": [{"latency": r["end"] - r["start"], "ttft": r["first"] - r["start"],
                   "tpot": (r["end"] - r["first"]) / max(1, r["tokens"] - 1), "tokens": r["tokens"]} for r in turns],
        "tokens": toks, "joules": round(joules, 2), "idle_w": round(idle_w, 2),
        "j_per_tok": round(joules / toks, 4) if toks else None,
        "j_per_tok_net": round((joules - idle_w * secs) / toks, 4) if toks else None,
        "errors": errors, "busy": {"P": busy_frac(b0, b1, pcpus), "E": busy_frac(b0, b1, ecpus)},
        "pre_busy": pre_busy, "affinity": affinity, "overlap": overlap(affinity),
        "placement_ok": placement_ok(arm, affinity), "mhz": mhz,
        "throttle_delta": thr1 - thr0, "pswpin_delta": sw1 - sw0, "load1": rd("/proc/loadavg").split()[0],
        "pre_t": pre_t,
    }


def drain(idle_w, limit=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if watts_over(2) <= idle_w + 3:
            return


def prechecks(args):
    clusters = sh("k3d", "cluster", "list", "-o", "json", check=False) or "[]"
    busy = [c["name"] for c in json.loads(clusters) if c["name"].startswith("k3dgpu-f-")]
    if busy:
        raise SystemExit(f"refusing: k3d clusters {busy} exist (one live experiment at a time)")
    helptext = sh("docker", "run", "--rm", args.image, "--help", timeout=180)
    missing = [f for f in REQUIRED_FLAGS if f not in helptext]
    if missing:
        raise SystemExit(f"refusing: {args.image} --help lacks {missing}")
    layout = (rd("/sys/devices/cpu_core/cpus"), rd("/sys/devices/cpu_atom/cpus"))
    if layout != (P, E) or args.agents > 4:
        raise SystemExit(f"refusing: slices assume P={P} E={E} and <= 4 agents, host has {layout}")
    if not os.access(f"{RAPL}/energy_uj", os.R_OK):
        raise SystemExit(f"refusing: {RAPL}/energy_uj not readable (run with sudo)")
    steer = sh(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "k3d-gpu"), "phase-steer", "--json", check=False)
    return {"kind": "header", "image": args.image, "model": args.model, "agents": args.agents,
            "poll": args.poll, "slices": {a: [server_args(a, i) for i in range(args.agents)] for a in PHASES}, "order": ORDER[: args.cells], "cpu_core": rd("/sys/devices/cpu_core/cpus"),
            "cpu_atom": rd("/sys/devices/cpu_atom/cpus"),
            "hfi": " hfi " in f" {rd('/proc/cpuinfo')} ", "itd": " itd " in f" {rd('/proc/cpuinfo')} ",
            "phase_steer": steer.strip(), "kernel": rd("/proc/sys/kernel/osrelease")}


def run(args):
    start = time.monotonic()
    deadline = start + args.budget
    with open(args.out, "a") as out:
        def emit(d):
            out.write(json.dumps(d) + "\n")
            out.flush()
        emit(dict(prechecks(args), t=time.time()))
        reps = {}
        for cell, arm in enumerate(ORDER[: args.cells]):
            if time.monotonic() + args.warm + args.window + 45 > deadline:
                emit({"kind": "abort", "reason": "wall budget", "cell": cell, "t": time.time()})
                break
            idle_w = watts_over(5)
            reps[arm] = reps.get(arm, 0) + 1
            emit(run_cell(args, arm, reps[arm], cell, idle_w, deadline))
            drain(idle_w)
        emit({"kind": "end", "wall_s": round(time.monotonic() - start, 1), "t": time.time()})


# ---- analysis ------------------------------------------------------------------
def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))] if v else None


def summarize(cells):
    arms = {}
    for c in cells:
        a = arms.setdefault(c["arm"], {"cells": [], "turns": []})
        a["cells"].append(c)
        a["turns"].extend(c["turns"])
    out = {}
    for arm, a in arms.items():
        jt = [c["j_per_tok_net"] for c in a["cells"] if c["j_per_tok_net"] is not None]
        p95s = [pct([t["latency"] for t in c["turns"]], 0.95) for c in a["cells"] if c["turns"]]
        out[arm] = {
            "n": len(a["cells"]), "j_per_tok_net": statistics.median(jt) if jt else None,
            "j_spread": (max(jt) - min(jt)) if jt else None,
            "p95_latency": pct([t["latency"] for t in a["turns"]], 0.95),
            "p95_spread": (max(p95s) - min(p95s)) if p95s else None,
            "p95_ttft": pct([t["ttft"] for t in a["turns"]], 0.95),
            "median_ttft": statistics.median([t["ttft"] for t in a["turns"]]) if a["turns"] else None,
            "median_tpot": statistics.median([t["tpot"] for t in a["turns"]]) if a["turns"] else None,
            "tok_s": statistics.median([c["tokens"] / c["window_s"] for c in a["cells"]]),
            "errors": sum(len(c["errors"]) for c in a["cells"]),
            "throttled": sum(1 for c in a["cells"] if c["throttle_delta"] > 0),
            "swapped": sum(1 for c in a["cells"] if c["pswpin_delta"] > 0),
            "contaminated": sum(1 for c in a["cells"] if contaminated(c)),
        }
    return out


def contaminated(c):
    """Placement proof: every server on its own pinned slice (A1); PS must load
    the E-cores above background, SP must not."""
    if c.get("placement_ok") is False or c.get("overlap"):
        return True
    e, pe = c["busy"]["E"] or 0, c["pre_busy"]["E"] or 0
    if c["arm"] == "PS":
        return e <= pe + 0.05
    if c["arm"] == "SP":
        return e > pe + 0.05
    return False


def verdict(s):
    """Pre-registered outcome table -> (verdict, reason)."""
    if any(s.get(a, {}).get("n", 0) < 3 for a in PRIMARY):
        return "INCONCLUSIVE", "a primary arm has fewer than 3 reps"
    sp, ps, al = s["SP"], s["PS"], s["ALL"]
    neg = s.get("NEG")
    if not neg or neg["median_ttft"] is None or neg["median_ttft"] <= max(x["median_ttft"] for x in (sp, ps, al)):
        return "INCONCLUSIVE", "NEG does not have the worst median TTFT: the probe cannot see placement"
    if sp["j_per_tok_net"] and sp["j_spread"] > 0.2 * sp["j_per_tok_net"]:
        return "INCONCLUSIVE", "SP J/token spread above 20% of its median"
    for k in ("throttled", "swapped", "contaminated"):
        hit = [a for a in PRIMARY if s[a][k]]
        if len(hit) == 1 or any(s[a][k] > 1 for a in PRIMARY):
            return "INCONCLUSIVE", f"confound '{k}' in {hit}"
    if ps["j_per_tok_net"] > 0.85 * sp["j_per_tok_net"]:
        return "REFUTED", "PS J/token above 0.85x SP"
    guard = (ps["p95_latency"] <= sp["p95_latency"] + sp["p95_spread"] and ps["p95_ttft"] <= 1.2 * sp["p95_ttft"]
             and ps["median_tpot"] <= 1.5 * sp["median_tpot"] and ps["errors"] == 0 and ps["tok_s"] >= 0.9 * sp["tok_s"])
    if not guard:
        return "REFUTED", "energy bought with latency: p95 or a guardrail breached"
    if al["j_per_tok_net"] <= ps["j_per_tok_net"]:
        return "REFUTED", "ALL (kernel placement) equal or better on J/token: no invention"
    return "SUPPORTED", "PS <= 0.85x SP J/token, p95 within SP spread, guardrails hold, beats ALL"


def analyze(path):
    cells = [json.loads(l) for l in open(path) if l.strip()]
    cells = [c for c in cells if c.get("kind") == "cell"]
    s = summarize(cells)
    v, why = verdict(s)
    return {"arms": s, "verdict": v, "reason": why}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--analyze", metavar="JSONL")
    ap.add_argument("--image", default=os.environ.get("IMAGE"))
    ap.add_argument("--model")
    ap.add_argument("--out", default="h20.jsonl")
    ap.add_argument("--agents", type=int, default=4)
    ap.add_argument("--poll", type=int, default=0, help="ggml --poll/--poll-batch for every arm (0 = no spin)")
    ap.add_argument("--warm", type=float, default=15)
    ap.add_argument("--window", type=float, default=60)
    ap.add_argument("--cells", type=int, default=len(ORDER))
    ap.add_argument("--budget", type=float, default=1170, help="wall seconds, capped at 1170 (19.5 min)")
    ap.add_argument("--port0", type=int, default=18600)
    a = ap.parse_args(argv)
    if a.analyze:
        print(json.dumps(analyze(a.analyze), indent=1, sort_keys=True))
        return 0
    if not a.image or not a.model:
        ap.error("--image (or $IMAGE) and --model are required for a run")
    a.budget = min(a.budget, 1170)
    try:
        run(a)
    finally:
        names = sh("docker", "ps", "-aq", "--filter", "name=^sci-h20-", check=False).split()
        if names:
            subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

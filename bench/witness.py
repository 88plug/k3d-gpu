#!/usr/bin/env python3
"""Interference witness for a balloon-pinned pod on an Intel hybrid CPU.

Per sample window it reports, for one pod cgroup pinned to CPU set S:

  p_residency       share of the pod's CPU time on cpu_core (P-core) CPUs
  foreign_share     non-pod busy time on S / window time on S
  sibling_share     busy time on the SMT siblings of S outside S / their window time
  coverage          pod CPU rate on S plus siblings / pod cgroup cpu.stat rate

Inputs are raw text in the formats the collector reads on a live host:
  - `perf stat -a -A -j -C <S+siblings> -e cpu-clock,cpu-clock -G <cgroup>, -- sleep <w>`
    lines: per-CPU on-CPU msec of the cgroup, and per-CPU window msec
  - /proc/stat before and after the window (per-CPU busy ticks)
  - the cgroup's cpu.stat before and after the window (usage_usec)

A sample is accepted or rejected by a rule set fixed before the run. The bundle
stores the rules, their SHA-256 and the raw text, so `verify` can recompute every
metric and verdict.

Usage:
  witness.py rules-hash RULES.json
  witness.py plan   --cgroup CG --cpus LIST [--window-s 1]
  witness.py record --cgroup CG --cpus LIST --expect performance|efficient
                    --windows N [--window-s 1] [--rules F] -o BUNDLE.json
  witness.py verify BUNDLE.json [--rules-sha256 HEX]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

SCHEMA = "k3d-gpu.witness/v1"
BUSY_FIELDS = (0, 1, 2, 5, 6, 7)  # user nice system irq softirq steal
IDLE_FIELDS = (3, 4)              # idle iowait
RULE_KEYS = ("max_foreign_share", "max_sibling_foreign_share",
             "min_core_type_residency", "min_coverage")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def rules_sha256(rules):
    return hashlib.sha256(canonical(rules).encode()).hexdigest()


def parse_cpulist(text):
    cpus = set()
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-")
            cpus.update(range(int(lo), int(hi) + 1))
        else:
            cpus.add(int(part))
    return sorted(cpus)


def format_cpulist(cpus):
    cpus = sorted(set(cpus))
    out, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(out)


def _read(path):
    with open(path) as f:
        return f.read()


def read_topology(sysroot="/"):
    """P/E CPU lists from the hybrid PMUs and SMT siblings from sysfs."""
    dev = os.path.join(sysroot, "sys/devices")
    p = parse_cpulist(_read(os.path.join(dev, "cpu_core/cpus")))
    e = parse_cpulist(_read(os.path.join(dev, "cpu_atom/cpus")))
    siblings = {}
    for cpu in p + e:
        f = os.path.join(dev, f"system/cpu/cpu{cpu}/topology/thread_siblings_list")
        siblings[str(cpu)] = format_cpulist(parse_cpulist(_read(f)))
    return {"p": format_cpulist(p), "e": format_cpulist(e), "siblings": siblings}


def sibling_cpus(cpus, topology):
    """SMT siblings of `cpus` that are not themselves in `cpus`."""
    sib = set()
    for c in cpus:
        sib.update(parse_cpulist(topology["siblings"].get(str(c), str(c))))
    return sorted(sib - set(cpus))


def parse_proc_stat(text):
    """{cpu: (busy_ticks, total_ticks)} from /proc/stat per-CPU lines."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if not f or not f[0].startswith("cpu") or f[0] == "cpu":
            continue
        v = [int(x) for x in f[1:]]
        v += [0] * (8 - len(v))
        busy = sum(v[i] for i in BUSY_FIELDS)
        out[int(f[0][3:])] = (busy, busy + sum(v[i] for i in IDLE_FIELDS))
    return out


def parse_cpu_stat(text):
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        if k == "usage_usec":
            return int(v)
    raise ValueError("cpu.stat has no usage_usec")


def parse_perf(lines):
    """({cpu: cgroup msec}, {cpu: window msec}) from `perf stat -A -j` lines.

    The cgroup-scoped cpu-clock counts while the cgroup runs on that CPU; the
    system-wide cpu-clock (empty cgroup) counts the whole window on that CPU.
    """
    own, win = {}, {}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        rec = json.loads(line)
        if rec.get("event") != "cpu-clock" or "cpu" not in rec:
            continue
        val = rec.get("counter-value", "")
        try:
            ms = float(val)
        except ValueError:
            if not val.startswith("<not counted>"):
                raise ValueError(f"perf counter unusable: {val!r}")
            ms = 0.0
        d = own if rec.get("cgroup") else win
        cpu = int(rec["cpu"])
        d[cpu] = d.get(cpu, 0.0) + ms
    return own, win


def compute_metrics(raw, topology, cpus):
    """Metrics for one window from its raw text. Pure function of its inputs.

    Shares are per-CPU fractions, each taken over its own clock: busy ticks over
    total ticks from /proc/stat, cgroup msec over window msec from perf. So the
    few ms by which the /proc/stat window is longer than the perf window cancel.
    """
    s0, s1 = parse_proc_stat(raw["proc_stat_0"]), parse_proc_stat(raw["proc_stat_1"])
    own, win = parse_perf(raw["perf_json"])
    sib = sibling_cpus(cpus, topology)
    pset = set(parse_cpulist(topology["p"]))

    def busy_frac(c):
        db, dt = s1[c][0] - s0[c][0], s1[c][1] - s0[c][1]
        return db / dt if dt else 0.0

    def own_frac(c):
        return own.get(c, 0.0) / win[c] if win.get(c) else 0.0

    def foreign(cs):
        if not cs:
            return 0.0
        return sum(max(0.0, busy_frac(c) - own_frac(c)) for c in cs) / len(cs)

    mon = list(cpus) + sib
    own_ms = sum(own.get(c, 0.0) for c in mon)
    own_p = sum(own.get(c, 0.0) for c in mon if c in pset)
    # CPUs' worth of pod time on the monitored CPUs, and in the whole cgroup.
    own_rate = sum(own_frac(c) for c in mon)
    stat_ms = sum(s1[c][1] - s0[c][1] for c in mon) * 1000.0 / raw["user_hz"] / len(mon)
    usage_ms = (parse_cpu_stat(raw["cpu_stat_1"]) - parse_cpu_stat(raw["cpu_stat_0"])) / 1000.0
    usage_rate = usage_ms / stat_ms if stat_ms else 0.0
    return {
        "foreign_share": round(foreign(list(cpus)), 6),
        "sibling_share": round(foreign(sib), 6),
        "p_residency": round(own_p / own_ms, 6) if own_ms else 0.0,
        "coverage": round(own_rate / usage_rate, 6) if usage_rate else 0.0,
    }


def judge(metrics, rules, expect):
    reasons = []
    if metrics["foreign_share"] > rules["max_foreign_share"]:
        reasons.append("foreign_share")
    if metrics["sibling_share"] > rules["max_sibling_foreign_share"]:
        reasons.append("sibling_share")
    residency = metrics["p_residency"] if expect == "performance" else 1.0 - metrics["p_residency"]
    if residency < rules["min_core_type_residency"]:
        reasons.append("core_type_residency")
    if metrics["coverage"] < rules["min_coverage"]:
        reasons.append("coverage")
    return {"accept": not reasons, "reasons": reasons}


def check_rules(rules):
    missing = [k for k in RULE_KEYS if k not in rules]
    if missing:
        raise ValueError(f"rules missing {missing}")


def build_bundle(raws, topology, cpus, cgroup, expect, rules):
    check_rules(rules)
    if expect not in ("performance", "efficient"):
        raise ValueError("expect must be performance or efficient")
    samples = []
    for raw in raws:
        m = compute_metrics(raw, topology, cpus)
        samples.append({"raw": raw, "metrics": m, "verdict": judge(m, rules, expect)})
    return {"schema": SCHEMA, "topology": topology, "cpus": format_cpulist(cpus),
            "cgroup": cgroup, "expect": expect, "rules": rules,
            "rules_sha256": rules_sha256(rules), "samples": samples}


def verify_bundle(bundle, expect_rules_sha256=None):
    """List of problems; empty when every hash, metric and verdict recomputes."""
    problems = []
    if bundle.get("schema") != SCHEMA:
        problems.append("schema")
    got = rules_sha256(bundle["rules"])
    if got != bundle["rules_sha256"]:
        problems.append("rules_sha256 does not match rules")
    if expect_rules_sha256 and got != expect_rules_sha256:
        problems.append("rules differ from the pre-committed hash")
    cpus = parse_cpulist(bundle["cpus"])
    for i, s in enumerate(bundle["samples"]):
        m = compute_metrics(s["raw"], bundle["topology"], cpus)
        if canonical(m) != canonical(s["metrics"]):
            problems.append(f"sample {i}: metrics do not recompute")
        if canonical(judge(m, bundle["rules"], bundle["expect"])) != canonical(s["verdict"]):
            problems.append(f"sample {i}: verdict does not recompute")
    return problems


def perf_argv(cgroup, mon_cpus, window_s, perf="perf"):
    # Two cpu-clock events: the first scoped to the cgroup, the second (empty
    # cgroup after the comma) system-wide, which gives each CPU's window length.
    return [perf, "stat", "-a", "-A", "-j", "-C", format_cpulist(mon_cpus),
            "-e", "cpu-clock,cpu-clock", "-G", f"{cgroup},", "--", "sleep", str(window_s)]


def record(cgroup, cpus, windows, window_s, sysroot="/", perf=None):
    """Raw samples from a live host. Needs perf_event_paranoid <= 0 or CAP_PERFMON."""
    perf = perf or os.environ.get("K3D_GPU_PERF", "perf")
    topology = read_topology(sysroot)
    mon = list(cpus) + sibling_cpus(cpus, topology)
    cg_stat = os.path.join(sysroot, "sys/fs/cgroup", cgroup, "cpu.stat")
    proc_stat = os.path.join(sysroot, "proc/stat")
    hz = os.sysconf("SC_CLK_TCK")
    raws = []
    for _ in range(windows):
        c0, p0 = _read(cg_stat), _read(proc_stat)
        res = subprocess.run(perf_argv(cgroup, mon, window_s, perf),
                             capture_output=True, text=True, check=True)
        p1, c1 = _read(proc_stat), _read(cg_stat)
        # perf -j writes its counters to stderr.
        raws.append({"user_hz": hz, "perf_json": res.stderr.splitlines(),
                     "proc_stat_0": p0, "proc_stat_1": p1,
                     "cpu_stat_0": c0, "cpu_stat_1": c1})
    return topology, raws


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("rules-hash")
    h.add_argument("rules")
    for name in ("plan", "record"):
        p = sub.add_parser(name)
        p.add_argument("--cgroup", required=True, help="path below /sys/fs/cgroup")
        p.add_argument("--cpus", required=True)
        p.add_argument("--window-s", type=float, default=1.0)
        p.add_argument("--sysroot", default="/")
        if name == "record":
            p.add_argument("--expect", required=True, choices=("performance", "efficient"))
            p.add_argument("--windows", type=int, required=True)
            p.add_argument("--rules", default=os.path.join(os.path.dirname(__file__), "witness-rules.json"))
            p.add_argument("-o", "--output", required=True)
    v = sub.add_parser("verify")
    v.add_argument("bundle")
    v.add_argument("--rules-sha256")
    a = ap.parse_args(argv)

    if a.cmd == "rules-hash":
        print(rules_sha256(json.loads(_read(a.rules))))
        return 0
    if a.cmd == "verify":
        problems = verify_bundle(json.loads(_read(a.bundle)), a.rules_sha256)
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print("ok: rules hash, metrics and verdicts recompute")
        return 1 if problems else 0
    cpus = parse_cpulist(a.cpus)
    if a.cmd == "plan":
        topo = read_topology(a.sysroot)
        print(" ".join(perf_argv(a.cgroup, cpus + sibling_cpus(cpus, topo), a.window_s)))
        return 0
    rules = json.loads(_read(a.rules))
    print(f"rules sha256 {rules_sha256(rules)}", file=sys.stderr)
    topo, raws = record(a.cgroup, cpus, a.windows, a.window_s, a.sysroot)
    bundle = build_bundle(raws, topo, cpus, a.cgroup, a.expect, rules)
    with open(a.output, "w") as f:
        json.dump(bundle, f, indent=1, sort_keys=True)
    rej = sum(not s["verdict"]["accept"] for s in bundle["samples"])
    print(f"{len(raws)} windows, {rej} rejected -> {a.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

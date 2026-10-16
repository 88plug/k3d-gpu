#!/usr/bin/env python3
"""Simulation that can falsify the interference witness (bench/witness.py).

A hidden scheduler places the benchmark pod's threads and co-tenant threads on
CPUs of an i9-13900T-shaped topology (P-cores 0-15 as SMT pairs, E-cores 16-31).
Ground-truth throughput follows from that placement. The witness and the
baselines never see the placement. They get only rendered text:

  witness   perf -j lines for its cgroup and window, /proc/stat, its cpu.stat
  B1        cpu.stat usage and cpuset.cpus.effective of every cgroup
            (cpuset-overlap scan, the pre-registered fair baseline)
  B2        per-CPU /proc/stat plus the pod's total cpu.stat usage, no perf
            (a stronger baseline added after pre-registration)

Physics assumed by the simulator, not tested by it:
  - CFS share: a co-tenant with weight ratio w takes at most w/(1+w) of a CPU the
    pod also wants;
  - an SMT sibling busy for fraction b slows the pod's thread by beta*b;
  - an E-core runs the pod's thread at 0.55 of P-core speed.
The live probes in docs/interference-witness.md measure these on the host.

Run: python3 bench/witness_sim.py   (prints the A1-A7 table as JSON)
"""
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import witness as wt  # noqa: E402

N_CPU = 32
P_CPUS = list(range(16))
E_CPUS = list(range(16, 32))
RESERVED = [31]
OWN_CG = "kubepods/pod-bench"
SIB = {c: (c ^ 1 if c < 16 else c) for c in range(N_CPU)}
TOPOLOGY = {"p": "0-15", "e": "16-31",
            "siblings": {str(c): wt.format_cpulist({c, SIB[c]}) for c in range(N_CPU)}}
E_SPEED = 0.55
S_MAIN = [0, 2, 4, 6]          # one thread per core: siblings 1,3,5,7 are outside S
MIXED_CPUSET = [0, 2, 4, 6, 16, 17]


class Params:
    def __init__(self, beta=0.30, weight=1.0, jitter=0.01, placement="skewed",
                 hz=100, exact=False, thr_noise=0.003, ring=0.0):
        self.beta, self.weight, self.jitter = beta, weight, jitter
        self.placement, self.hz, self.exact = placement, hz, exact
        # thr_noise: run-to-run throughput noise; ring: slowdown of P-core threads
        # at full E-core load through the shared ring/LLC, which no CPU-time
        # counter on S sees (negative control).
        self.thr_noise, self.ring = thr_noise, ring


def scenario(kind, rng, prm):
    """Hidden truth for one window: own cpuset/threads and co-tenant threads."""
    own_cpuset = list(S_MAIN)
    own_place = list(S_MAIN)
    cotenants = []  # (cgroup, cpuset, [(cpu, demand)])
    everything = [c for c in range(N_CPU)]
    m = rng.randint(0, 4)
    demands = [rng.uniform(0.3, 1.0) for _ in range(m)]
    p_hit = rng.random()
    if kind == "smt":
        cs = [c for c in everything if c not in S_MAIN and c not in RESERVED]
        sibs = [SIB[c] for c in S_MAIN]
        place = [(rng.choice(sibs) if prm.placement == "skewed" and rng.random() < p_hit
                  else rng.choice(cs), d) for d in demands]
        cotenants.append(("kubepods/pod-shared", cs, place))
    elif kind == "host":
        place = [(rng.choice(S_MAIN) if prm.placement == "skewed" and rng.random() < p_hit
                  else rng.choice(everything), d) for d in demands]
        cotenants.append(("system.slice/stress.service", everything, place))
    elif kind == "same":
        place = [(rng.choice(S_MAIN), d) for d in demands]
        cotenants.append(("kubepods/pod-neighbour", list(S_MAIN), place))
    elif kind == "ecore":
        cs = [c for c in E_CPUS if c not in RESERVED]
        place = [(rng.choice(cs), d) for d in demands]
        cotenants.append(("kubepods/pod-efficient", cs, place))
    elif kind == "mixed":
        own_cpuset = list(MIXED_CPUSET)
        own_place = rng.sample(MIXED_CPUSET, 4)
    elif kind != "none":
        raise ValueError(kind)
    # Background kernel and daemon noise, everywhere, tiny.
    cotenants.append(("init.scope", everything,
                      [(c, rng.uniform(0.0, 0.004)) for c in everything]))
    return own_cpuset, own_place, cotenants


def resolve(own_place, cotenants, prm):
    """Per-CPU realised time for the pod and each co-tenant cgroup."""
    cap = prm.weight / (1.0 + prm.weight)
    own_t = [0.0] * N_CPU
    cg_t = {cg: [0.0] * N_CPU for cg, _, _ in cotenants}
    demand = [{} for _ in range(N_CPU)]
    for cg, _, place in cotenants:
        for c, d in place:
            demand[c][cg] = demand[c].get(cg, 0.0) + d
    for c in range(N_CPU):
        fd = min(1.0, sum(demand[c].values()))
        fr = min(fd, cap) if c in own_place else fd
        if c in own_place:
            own_t[c] = 1.0 - fr
        tot = sum(demand[c].values())
        for cg, d in demand[c].items():
            cg_t[cg][c] = fr * d / tot if tot else 0.0
    busy = [own_t[c] + sum(cg_t[cg][c] for cg in cg_t) for c in range(N_CPU)]
    return own_t, cg_t, busy


def throughput(own_place, own_t, busy, prm, rng):
    e_load = sum(busy[c] for c in E_CPUS) / len(E_CPUS)
    thr = 0.0
    for c in own_place:
        speed = 1.0 if c in P_CPUS else E_SPEED
        smt = 1.0 - prm.beta * busy[SIB[c]] if SIB[c] != c else 1.0
        ring = 1.0 - prm.ring * e_load if c in P_CPUS else 1.0
        thr += own_t[c] * speed * smt * ring
    return thr * (1.0 + rng.gauss(0.0, prm.thr_noise))


def _stat_line(c, busy_ticks, total):
    return f"cpu{c} {busy_ticks} 0 0 {total - busy_ticks} 0 0 0 0 0 0"


def render(own_cpuset, own_place, own_t, cg_t, busy, cotenants, prm, rng):
    """Raw witness sample plus baseline inputs, as text only."""
    total = prm.hz  # 1 s window
    base = [rng.randint(10**6, 10**7) for _ in range(N_CPU)]
    s0, s1 = ["cpu 0 0 0 0 0 0 0 0 0 0"], ["cpu 0 0 0 0 0 0 0 0 0 0"]
    for c in range(N_CPU):
        p = min(1.0, max(0.0, busy[c]))
        if prm.exact:
            b = round(p * total)
        else:  # tick accounting samples who is on the CPU at each tick
            b = sum(rng.random() < p for _ in range(total))
        s0.append(_stat_line(c, base[c], base[c] * 3))
        s1.append(_stat_line(c, base[c] + b, base[c] * 3 + total))
    sib_mon = sorted({SIB[c] for c in own_cpuset} - set(own_cpuset))
    perf = []
    for c in sorted(set(own_cpuset) | set(sib_mon)):
        win_ms = 1000.0 + (0.0 if prm.exact else rng.uniform(0, 2))
        own_ms = own_t[c] * win_ms
        if not prm.exact and own_ms:
            own_ms = min(win_ms, max(0.0, own_ms * (1.0 + rng.gauss(0.0, prm.jitter))))
        perf.append(json.dumps({"cpu": str(c), "counter-value": f"{own_ms:f}", "unit": "msec",
                                "cgroup": OWN_CG, "event": "cpu-clock"}))
        perf.append(json.dumps({"cpu": str(c), "counter-value": f"{win_ms:f}", "unit": "msec",
                                "cgroup": "", "event": "cpu-clock"}))
    u0 = rng.randint(10**9, 10**10)
    own_usec = round(sum(own_t) * 1e6)
    raw = {"user_hz": prm.hz, "perf_json": perf,
           "proc_stat_0": "\n".join(s0) + "\n", "proc_stat_1": "\n".join(s1) + "\n",
           "cpu_stat_0": f"usage_usec {u0}\nuser_usec 0\n",
           "cpu_stat_1": f"usage_usec {u0 + own_usec}\nuser_usec 0\n"}
    cgroups = {OWN_CG: {"usage_usec": own_usec, "cpuset": wt.format_cpulist(own_cpuset)}}
    for cg, cs, _ in cotenants:
        cgroups[cg] = {"usage_usec": round(sum(cg_t[cg]) * 1e6), "cpuset": wt.format_cpulist(cs)}
    return raw, cgroups


def baseline_b1(cgroups, own_cpuset):
    """Cpuset-overlap scan: prorate each other cgroup's usage over its cpuset."""
    sib = sorted({SIB[c] for c in own_cpuset} - set(own_cpuset))
    fs = fsib = 0.0
    for cg, v in cgroups.items():
        if cg == OWN_CG:
            continue
        cs = set(wt.parse_cpulist(v["cpuset"]))
        rate = v["usage_usec"] / 1e6 / len(cs)  # CPUs' worth per cpuset CPU
        fs += rate * len(cs & set(own_cpuset))
        fsib += rate * len(cs & set(sib))
    p = len(set(own_cpuset) & set(P_CPUS)) / len(own_cpuset)
    return {"foreign_share": fs / len(own_cpuset),
            "sibling_share": fsib / len(sib) if sib else 0.0, "p_residency": p}


def baseline_b2(raw, cgroups, own_cpuset):
    """Per-CPU /proc/stat busy minus the pod's total usage, no perf."""
    s0, s1 = wt.parse_proc_stat(raw["proc_stat_0"]), wt.parse_proc_stat(raw["proc_stat_1"])

    def frac(c):
        return (s1[c][0] - s0[c][0]) / (s1[c][1] - s0[c][1])
    sib = sorted({SIB[c] for c in own_cpuset} - set(own_cpuset))
    own_rate = cgroups[OWN_CG]["usage_usec"] / 1e6
    fs = max(0.0, sum(frac(c) for c in own_cpuset) - own_rate) / len(own_cpuset)
    fsib = sum(frac(c) for c in sib) / len(sib) if sib else 0.0
    p = len(set(own_cpuset) & set(P_CPUS)) / len(own_cpuset)
    return {"foreign_share": fs, "sibling_share": fsib, "p_residency": p}


def permute_proc_stat(raw, rng):
    """Ablation: relabel the per-CPU /proc/stat lines across all CPUs."""
    perm = list(range(N_CPU))
    rng.shuffle(perm)
    out = dict(raw)
    for key in ("proc_stat_0", "proc_stat_1"):
        lines = []
        for line in raw[key].splitlines():
            f = line.split()
            if f[0] != "cpu":
                f[0] = f"cpu{perm[int(f[0][3:])]}"
            lines.append(" ".join(f))
        out[key] = "\n".join(lines) + "\n"
    return out


def generate(kinds, n, prm, seed):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        kind = kinds[i % len(kinds)]
        own_cpuset, own_place, cot = scenario(kind, rng, prm)
        own_t, cg_t, busy = resolve(own_place, cot, prm)
        thr = throughput(own_place, own_t, busy, prm, rng)
        raw, cgroups = render(own_cpuset, own_place, own_t, cg_t, busy, cot, prm, rng)
        rows.append({"kind": kind, "cpus": own_cpuset, "thr": thr, "deficit": 1.0 - thr / 4.0,
                     "raw": raw, "cgroups": cgroups,
                     "truth": {"own_t": own_t, "busy": busy, "place": own_place}})
    return rows


def ols_r2(xs, ys):
    """Fit y ~ 1 + x on even rows, return R^2 on odd rows."""
    def design(x):
        return [1.0] + list(x)
    tr = [(design(x), y) for i, (x, y) in enumerate(zip(xs, ys)) if i % 2 == 0]
    te = [(design(x), y) for i, (x, y) in enumerate(zip(xs, ys)) if i % 2 == 1]
    k = len(tr[0][0])
    a = [[sum(r[0][i] * r[0][j] for r in tr) + (1e-9 if i == j else 0.0) for j in range(k)]
         for i in range(k)]
    b = [sum(r[0][i] * r[1] for r in tr) for i in range(k)]
    for col in range(k):  # Gaussian elimination with partial pivoting
        piv = max(range(col, k), key=lambda r: abs(a[r][col]))
        a[col], a[piv], b[col], b[piv] = a[piv], a[col], b[piv], b[col]
        for r in range(k):
            if r != col and a[col][col]:
                f = a[r][col] / a[col][col]
                a[r] = [a[r][j] - f * a[col][j] for j in range(k)]
                b[r] -= f * b[col]
    beta = [b[i] / a[i][i] if a[i][i] else 0.0 for i in range(k)]
    pred = [sum(bi * xi for bi, xi in zip(beta, x)) for x, _ in te]
    yt = [y for _, y in te]
    mu = statistics.fmean(yt)
    ss_tot = sum((y - mu) ** 2 for y in yt)
    ss_res = sum((y - p) ** 2 for y, p in zip(yt, pred))
    return 1.0 - ss_res / ss_tot if ss_tot else 0.0


FEATURES = ("foreign_share", "sibling_share", "p_residency")


def feats(m, drop=()):
    return [m[f] for f in FEATURES if f not in drop]


def witness_metrics(rows, ablate=None, seed=0):
    rng = random.Random(seed)
    out = []
    for r in rows:
        raw = permute_proc_stat(r["raw"], rng) if ablate == "permute" else r["raw"]
        out.append(wt.compute_metrics(raw, TOPOLOGY, r["cpus"]))
    return out


def r2_table(rows, seed=0):
    y = [r["deficit"] for r in rows]
    w = witness_metrics(rows)
    b1 = [baseline_b1(r["cgroups"], r["cpus"]) for r in rows]
    b2 = [baseline_b2(r["raw"], r["cgroups"], r["cpus"]) for r in rows]
    wp = witness_metrics(rows, ablate="permute", seed=seed)
    return {"witness": ols_r2([feats(m) for m in w], y),
            "b1_cpuset_overlap": ols_r2([feats(m) for m in b1], y),
            "b2_procstat_minus_usage": ols_r2([feats(m) for m in b2], y),
            "witness_permuted": ols_r2([feats(m) for m in wp], y),
            "witness_no_siblings": ols_r2([feats(m, ("sibling_share",)) for m in w], y)}


def cov(xs):
    return statistics.pstdev(xs) / statistics.fmean(xs)


def gate(rows, metrics, rules, expect="performance"):
    acc = [wt.judge(m, rules, expect)["accept"] for m in metrics]
    thr_all = [r["thr"] for r in rows]
    thr_acc = [r["thr"] for r, a in zip(rows, acc) if a]
    bad = [r["deficit"] >= 0.03 for r in rows]
    tp = sum(b and not a for b, a in zip(bad, acc))
    fp = sum((not b) and not a for b, a in zip(bad, acc))
    return {"accepted": len(thr_acc), "n": len(rows),
            "cov_all": cov(thr_all), "cov_accepted": cov(thr_acc) if len(thr_acc) > 1 else None,
            "tpr": tp / max(1, sum(bad)), "fpr": fp / max(1, len(bad) - sum(bad))}


MIX = ("none", "smt", "host", "same")


def run_all(n=2000, seed=1):
    rules = json.loads(wt._read(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "witness-rules.json")))
    res = {}
    # A1: noiseless arithmetic, compare to hidden truth.
    exact = Params(jitter=0.0, exact=True, hz=10**6)
    err = 0.0
    for r in generate(MIX + ("mixed",), 200, exact, seed):
        m = wt.compute_metrics(r["raw"], TOPOLOGY, r["cpus"])
        t = r["truth"]
        sib = sorted({SIB[c] for c in r["cpus"]} - set(r["cpus"]))
        f_s = statistics.fmean(t["busy"][c] - t["own_t"][c] for c in r["cpus"])
        f_sib = statistics.fmean(t["busy"][c] - t["own_t"][c] for c in sib) if sib else 0.0
        p = sum(t["own_t"][c] for c in r["cpus"] if c in P_CPUS) / sum(t["own_t"])
        err = max(err, abs(m["foreign_share"] - f_s), abs(m["sibling_share"] - f_sib),
                  abs(m["p_residency"] - p), abs(m["coverage"] - 1.0))
    res["A1_max_abs_error"] = err
    res["A1_pass"] = err <= 1e-6

    nominal = Params()
    rows = generate(MIX + ("mixed",), n, nominal, seed)
    t = r2_table(rows, seed)
    res["A2_skewed_r2"] = t
    res["A2_pass"] = t["witness"] >= 0.80 and t["witness"] >= t["b1_cpuset_overlap"] + 0.20
    res["A3_pass"] = (t["witness"] - t["witness_permuted"] >= 0.20
                      and t["witness_permuted"] <= t["b1_cpuset_overlap"] + 0.10)
    smt = [r for r in rows if r["kind"] == "smt"]
    ts = r2_table(smt, seed)
    res["A3b_smt_r2"] = {"witness": ts["witness"], "witness_no_siblings": ts["witness_no_siblings"]}
    res["A3b_pass"] = ts["witness"] - ts["witness_no_siblings"] >= 0.20
    same = generate(("same",), n // 2, nominal, seed + 1)
    tsame = r2_table(same, seed)
    res["A4_same_balloon_r2"] = tsame
    res["A4_pass"] = abs(tsame["witness"] - tsame["b1_cpuset_overlap"]) <= 0.10
    uni = generate(MIX + ("mixed",), n, Params(placement="uniform"), seed + 2)
    res["extra_uniform_placement_r2"] = r2_table(uni, seed)
    # Where the witness's lead comes from: R^2 within each scenario kind.
    res["extra_per_kind_r2"] = {
        k: {n: round(v, 3) for n, v in r2_table([r for r in rows if r["kind"] == k], seed).items()
            if n in ("witness", "b1_cpuset_overlap", "b2_procstat_minus_usage")}
        for k in ("smt", "host", "same", "mixed")}
    res["extra_noisy_throughput_r2"] = r2_table(
        generate(MIX + ("mixed",), n, Params(thr_noise=0.02), seed + 4), seed)
    # Negative control: E-core co-tenant slows P-cores through the ring only.
    ec = generate(("ecore",), n // 2, Params(ring=0.20), seed + 5)
    res["extra_ecore_ring_negative_control"] = {
        "r2": r2_table(ec, seed),
        "mean_deficit": statistics.fmean(r["deficit"] for r in ec),
        "max_deficit": max(r["deficit"] for r in ec),
        "witness_accept_rate": statistics.fmean(
            wt.judge(m, rules, "performance")["accept"] for m in witness_metrics(ec))}

    grid, passed = [], 0
    for beta in (0.15, 0.30, 0.45):
        for weight in (0.5, 1.0, 2.0):
            for jitter in (0.0, 0.01, 0.03):
                g = r2_table(generate(MIX + ("mixed",), 800, Params(beta, weight, jitter),
                                      seed + 3), seed)
                ok = g["witness"] >= 0.80 and g["witness"] >= g["b1_cpuset_overlap"] + 0.20
                passed += ok
                grid.append({"beta": beta, "weight": weight, "jitter": jitter,
                             "witness": round(g["witness"], 3),
                             "b1": round(g["b1_cpuset_overlap"], 3),
                             "b2": round(g["b2_procstat_minus_usage"], 3), "pass": ok})
    res["A5_grid"] = grid
    res["A5_pass_fraction"] = passed / len(grid)
    res["A5_pass"] = passed / len(grid) >= 0.90

    wm = witness_metrics(rows)
    gw = gate(rows, wm, rules)
    b1m = [dict(baseline_b1(r["cgroups"], r["cpus"]), coverage=1.0) for r in rows]
    b2m = [dict(baseline_b2(r["raw"], r["cgroups"], r["cpus"]), coverage=1.0) for r in rows]
    res["A6_gate"] = {"witness": gw, "b1": gate(rows, b1m, rules), "b2": gate(rows, b2m, rules)}
    res["A6_pass"] = (gw["cov_accepted"] is not None and gw["cov_accepted"] <= 0.5 * gw["cov_all"]
                      and gw["tpr"] >= 0.90 and gw["fpr"] <= 0.05)
    return res


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=1, default=lambda o: round(o, 4)))

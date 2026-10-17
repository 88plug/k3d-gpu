#!/usr/bin/env python3
"""joule-pack (H21): RAPL-attributed per-pod energy and P/E placement for agent pods.

Prototype for the pre-registered hypothesis H21 in EXPERIMENTS.md (PENDING until
bench/joule-pack/probe.sh has run on real hardware). Standard library only.

Subcommands:
  detect  [--sysroot DIR]            RAPL zones by name and hybrid CPU pools -> JSON plan
  attribute --core-j J --pods FILE [--model cputime|typed] [--weights P=..,E=..]
                                      split core-domain joules over pods -> JSON
  place   --arms FILE [--slo S] [--min-gain G] [--incumbent E]
                                      pick the placement with most agents per watt
                                      under the p95 SLO -> JSON decision
  verdict OUT_DIR [--json]            analyse a probe.sh run with the H21 rules

Auto-adapt: zones are found by their `name` file, never by index. Client parts
expose package-N, core (PP0), uncore (PP1: mostly the iGPU) and psys; servers
usually package-N and dram. With no core zone the attribution falls back to the
package domain. Pools come from /sys/devices/cpu_core, cpu_atom and cpu_lowpower
(kernel hybrid PMU devices); a host without them is homogeneous, and placement
has nothing to choose.
"""
import argparse
import glob
import json
import math
import os
import statistics as st
import sys

SLO_S = 1.0          # p95 turn latency SLO, seconds (pre-registered)
MIN_GAIN = 1.15      # agents-per-watt gain needed to move off the incumbent rule
N_AGENTS = 12
ARM_LABEL = {"U": "", "P": "performance", "E": "efficient", "X": "ecluster"}


# ---------------------------------------------------------------- detection
def cpulist(text):
    """'0-3,8,10-11' -> sorted list of ints; '' -> []."""
    out = []
    for part in (text or "").strip().split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return sorted(set(out))


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def discover(sysroot=""):
    """RAPL zones (by name) and CPU pools under sysroot. Never raises on a missing tree."""
    sysroot = sysroot or "/"
    zones = {}
    for z in sorted(glob.glob(os.path.join(sysroot, "sys/class/powercap/intel-rapl:*"))):
        name = _read(os.path.join(z, "name"))
        if not name:
            continue
        kind = "package" if name.startswith("package-") else name
        rng = _read(os.path.join(z, "max_energy_range_uj"))
        zones.setdefault(kind, []).append(
            {"zone": os.path.basename(z), "name": name, "max_energy_range_uj": int(rng) if rng.isdigit() else None,
             "readable": os.access(os.path.join(z, "energy_uj"), os.R_OK)})
    pools = {}
    for pmu, pool in (("core", "performance"), ("atom", "efficient"), ("lowpower", "lowpower")):
        cpus = cpulist(_read(os.path.join(sysroot, "sys/devices/cpu_%s/cpus" % pmu)))
        if cpus:
            pools[pool] = cpus
    hybrid = "performance" in pools and ("efficient" in pools or "lowpower" in pools)
    if "core" in zones and "package" in zones:
        domain = "core"
    elif "package" in zones:
        domain = "package"
    else:
        domain = None
    notes = []
    if "uncore" in zones:
        notes.append("uncore is PP1 (mostly the iGPU) on client parts: used as a negative control, "
                     "not as CPU uncore; non-core residual = package - core")
    if domain == "package":
        notes.append("no core zone: per-pod energy is split from the package domain")
    if domain is None:
        notes.append("no RAPL zone: joule-pack cannot meter this host; placement stays on the static label")
    if not hybrid:
        notes.append("homogeneous CPU: no P/E pools to choose between")
    return {
        "rapl": zones,
        "pools": pools,
        "hybrid": hybrid,
        "attribution_domain": domain,
        "enabled": bool(hybrid and domain),
        "default_agent_label": "efficient" if hybrid else "",
        "notes": notes,
    }


# -------------------------------------------------------------- attribution
def attribute(domain_j, pods, model="cputime", weights=None):
    """Split domain_j joules over pods.

    pods: [{"name", "cpu_s", "type"}], type in performance|efficient|lowpower.
    cputime: share = cpu_s / sum(cpu_s)  (Kepler's rule; H21b tests its bias).
    typed:   share = w[type]*cpu_s / sum(w*cpu_s), w = measured J per CPU-second
             of each core type (from single-type arms).
    """
    if model not in ("cputime", "typed"):
        raise ValueError("model must be cputime or typed")
    if model == "typed" and not weights:
        raise ValueError("typed model needs weights")
    def w(p):
        return 1.0 if model == "cputime" else float(weights[p["type"]])
    tot = sum(w(p) * max(p["cpu_s"], 0.0) for p in pods)
    out = {}
    for p in pods:
        out[p["name"]] = domain_j * w(p) * max(p["cpu_s"], 0.0) / tot if tot > 0 else 0.0
    return out


def j_per_cpu_s(domain_w, window_s, cpu_s):
    """Measured joules per CPU-second for a single-type arm."""
    return domain_w * window_s / cpu_s if cpu_s > 0 else math.nan


# ---------------------------------------------------------------- placement
def place(arms, slo=SLO_S, min_gain=MIN_GAIN, incumbent="E"):
    """Choose a placement. arms: {arm: {"n", "pkg_w", "p95"}}.

    Admission: an arm is feasible when p95 <= slo and pkg_w > 0. No feasible arm
    -> decision "reject" (admit fewer agents). The controller leaves the
    incumbent (static) rule only when the best arm's agents-per-watt beats it by
    min_gain, so meter noise cannot flap the placement.
    """
    feas = {a: v for a, v in arms.items() if v.get("p95", math.inf) <= slo and v.get("pkg_w", 0) > 0}
    if not feas:
        return {"decision": "reject", "arm": None, "reason": "no placement meets the p95 SLO"}
    apw = {a: v["n"] / v["pkg_w"] for a, v in feas.items()}
    best = max(sorted(apw), key=lambda a: apw[a])
    inc = apw.get(incumbent)
    gain = apw[best] / inc if inc else math.inf
    if inc is not None and gain < min_gain:
        return {"decision": "keep", "arm": incumbent, "label": ARM_LABEL.get(incumbent, ""),
                "agents_per_w": apw, "gain": gain, "reason": "best %s gains %.3fx < %.2fx" % (best, gain, min_gain)}
    return {"decision": "move", "arm": best, "label": ARM_LABEL.get(best, ""), "agents_per_w": apw, "gain": gain,
            "reason": "incumbent fails the SLO" if inc is None else "gain %.3fx >= %.2fx" % (gain, min_gain)}


# ------------------------------------------------------------------ verdict
def _wrapdelta(a, b, rng):
    d = b - a
    return d + rng if d < 0 and rng else d


def load_run(out):
    """Parse a probe.sh results dir."""
    zones = {}  # kind -> (column index, max range)
    with open(os.path.join(out, "zones.txt")) as f:
        for col, line in enumerate(f):
            zone, name, rng = line.split()
            kind = "package" if name.startswith("package-") else name
            zones[kind] = (col, int(rng) if rng.isdigit() else 0)
    samples = []
    with open(os.path.join(out, "rapl.txt")) as f:
        for line in f:
            p = line.split()
            if len(p) == 2 + len(zones) and p[0] == "E":
                samples.append((float(p[1]), [int(x) for x in p[2:]]))
    pools = {}
    pf = os.path.join(out, "pools.txt")
    if os.path.exists(pf):
        with open(pf) as f:
            for line in f:
                k, _, v = line.partition(" ")
                pools[k] = set(cpulist(v))
    with open(os.path.join(out, "windows.txt")) as f:
        wins = [l.split() for l in f if l.strip()]
    conf = []
    cf = os.path.join(out, "confounds.txt")
    if os.path.exists(cf):
        with open(cf) as f:
            for line in f:
                p = line.split()
                if p and p[0] == "C":
                    conf.append((int(p[1]), dict(kv.split("=", 1) for kv in p[2:] if "=" in kv)))
    return zones, samples, pools, wins, conf


def window_watts(zones, samples, t0, t1):
    pts = [s for s in samples if t0 <= s[0] <= t1]
    if len(pts) < 3:
        return None
    dt = pts[-1][0] - pts[0][0]
    out = {}
    for kind, (col, rng) in zones.items():
        tot = sum(_wrapdelta(a[1][col], b[1][col], rng) for a, b in zip(pts, pts[1:]))
        out[kind] = tot / 1e6 / dt
    return out


def p95(xs):
    """Nearest-rank (lower) p95; nan on empty."""
    xs = sorted(xs)
    return xs[int(0.95 * (len(xs) - 1))] if xs else math.nan


def _conf_delta(conf, t0, t1, key):
    v = [float(c[key]) for t, c in conf if t0 <= t <= t1 and key in c]
    return (max(v) - min(v)) if len(v) >= 2 else 0.0


def analyse(out, n=N_AGENTS, slo=SLO_S):
    zones, samples, pools, wins, conf = load_run(out)
    rows, idle_pkg = [], []
    for i, w in enumerate(wins):
        if w[1] == "idle":
            iw = window_watts(zones, samples, int(w[2]), int(w[3]))
            if iw:
                idle_pkg.append(iw["package"])
            continue
        arm, t0, t1, rnd = w[1], int(w[2]), int(w[3]), w[4].split("=")[1]
        before = next((x for x in reversed(wins[:i]) if x[1] == "idle"), None)
        after = next((x for x in wins[i + 1:] if x[1] == "idle"), None)
        idles = [window_watts(zones, samples, int(x[2]), int(x[3])) for x in (before, after) if x]
        idles = [x for x in idles if x]
        aw = window_watts(zones, samples, t0, t1)
        if not aw or not idles:
            continue
        marg = {k: aw[k] - st.mean(x[k] for x in idles) for k in aw}
        idle_thr = [_conf_delta(conf, int(x[2]), int(x[3]), "throttle") for x in (before, after) if x]
        lat, cpu, bad_cpuset = [], {"performance": 0.0, "efficient": 0.0}, []
        for f in sorted(glob.glob(os.path.join(out, "r%s-%s-*.log" % (rnd, arm)))):
            u0 = u1 = lab = None
            with open(f) as fh:
                for line in fh:
                    p = line.split()
                    if not p:
                        continue
                    if p[0] == "T" and len(p) == 4 and t0 <= int(p[1]) < t1:
                        lat.append(float(p[3]) - float(p[2]))
                    elif p[0] == "U0":
                        u0 = int(p[2])
                    elif p[0] == "U1":
                        u1 = int(p[2])
                    elif p[0] == "CPUS" and len(p) >= 4:
                        lab = p[3]
                        want = {"performance": pools.get("performance"), "efficient": pools.get("efficient"),
                                "ecluster": pools.get("efficient")}.get(lab)
                        if want is not None and not set(cpulist(p[1])) <= want:
                            bad_cpuset.append(os.path.basename(f))
            if u0 is not None and u1 is not None and lab in cpu:
                cpu[lab] += (u1 - u0) / 1e6
        thr = _conf_delta(conf, t0, t1, "throttle")
        swap = _conf_delta(conf, t0, t1, "swap_mib")
        rows.append({
            "round": rnd, "arm": arm, "marginal_w": marg, "turns": len(lat), "p95_s": p95(lat),
            "agents_per_w": n / marg["package"] if marg["package"] > 0 else math.inf,
            "cpu_s": cpu, "bad_cpuset": bad_cpuset,
            "flag_throttle": thr > 3 * max(idle_thr or [0.0]) and thr > 0,
            "flag_swap": swap > 512,
        })
    return rows, idle_pkg


def verdict(out, n=N_AGENTS, slo=SLO_S, min_gain=MIN_GAIN):
    rows, idle_pkg = analyse(out, n, slo)
    rounds = sorted({r["round"] for r in rows})
    by = {rd: {r["arm"]: r for r in rows if r["round"] == rd} for rd in rounds}
    res = {"n_rounds": len(rounds), "rows": rows, "invalid": [], "a": {}, "b": {}}
    if any(r["bad_cpuset"] for r in rows):
        res["invalid"].append("wrong cpuset: " + ", ".join(sorted({f for r in rows for f in r["bad_cpuset"]})))
    complete = [rd for rd in rounds if set("UPEXM") <= set(by[rd])]
    if len(complete) < 2:
        res["invalid"].append("fewer than 2 complete rounds (%d)" % len(complete))
    spread = max(idle_pkg) - min(idle_pkg) if len(idle_pkg) >= 2 else math.nan
    res["idle_pkg_w"] = {"median": st.median(idle_pkg) if idle_pkg else math.nan, "spread": spread, "n": len(idle_pkg)}
    inconc = []
    if any(abs(r["marginal_w"].get("uncore", 0.0)) > 0.5 for r in rows):
        inconc.append("uncore marginal |dW| > 0.5")
    p_m = [r["marginal_w"]["package"] for r in rows if r["arm"] == "P"]
    if p_m and not math.isnan(spread) and min(p_m) < spread:
        inconc.append("P marginal < idle spread")
    e_m = [r["marginal_w"]["package"] for r in rows if r["arm"] == "E"]
    if e_m and not math.isnan(spread) and spread > 0.5 * st.median(e_m):
        inconc.append("idle spread > 50% of median E marginal")
    res["inconclusive_flags"] = inconc
    res["validity_flags"] = sorted({"throttle r%s %s" % (r["round"], r["arm"]) for r in rows if r["flag_throttle"]}
                                   | {"swap r%s %s" % (r["round"], r["arm"]) for r in rows if r["flag_swap"]})
    # H21a: oracle best vs E and vs U, paired per round.
    ge, ge_u, per = 0, 0, []
    for rd in complete:
        arms = {a: {"n": n, "pkg_w": by[rd][a]["marginal_w"]["package"], "p95": by[rd][a]["p95_s"]} for a in "UPEX"}
        d = place(arms, slo, min_gain=1.0, incumbent="E")
        apw = d.get("agents_per_w", {})
        best = d["arm"]
        re_ = apw[best] / apw["E"] if best and apw.get("E") else (math.inf if best else math.nan)
        ru = apw[best] / apw["U"] if best and apw.get("U") else (math.inf if best else math.nan)
        ge += re_ >= min_gain
        ge_u += ru >= 1.3
        per.append({"round": rd, "best": best, "best_over_E": re_, "best_over_U": ru,
                    "E_meets_slo": by[rd]["E"]["p95_s"] <= slo, "P_meets_slo": by[rd]["P"]["p95_s"] <= slo})
    k = len(complete)
    if res["invalid"]:
        va = "VOID"
    elif inconc:
        va = "INCONCLUSIVE"
    elif k == 3 and ge == 3:
        va = "SUPPORTED"
    elif k - ge >= 2:
        va = "REFUTED"
    else:
        va = "INCONCLUSIVE"
    agree = ge if va == "SUPPORTED" else k - ge
    res["a"] = {"per_round": per, "rounds_ge_gain": ge, "rounds_best_over_U_ge_1.3": ge_u, "verdict": va,
                "confidence": 1 - 0.5 ** agree if va in ("SUPPORTED", "REFUTED") else None}
    # H21b: CPU-time share of P pods in M vs core-domain energy share from P and E arms.
    dom = "core" if rows and "core" in rows[0]["marginal_w"] else "package"
    perb, ok, low, gap = [], 0, 0, False
    for rd in complete:
        m, P, E = by[rd]["M"], by[rd]["P"], by[rd]["E"]
        cp, ce = m["cpu_s"]["performance"], m["cpu_s"]["efficient"]
        s_cpu = cp / (cp + ce) if cp + ce else math.nan
        pe = P["marginal_w"][dom] + E["marginal_w"][dom]
        s_true = P["marginal_w"][dom] / pe if pe else math.nan
        err = s_cpu - s_true
        half = pe / 2
        add_gap = abs(m["marginal_w"][dom] - half) / half if half else math.inf
        gap |= add_gap > 0.25
        ok += abs(err) <= 0.10
        low += err <= -0.15
        perb.append({"round": rd, "cpu_share_P": s_cpu, "energy_share_P": s_true, "err": err, "additivity_gap": add_gap})
    if res["invalid"]:
        vb = "VOID"
    elif gap or inconc or not k:
        vb = "INCONCLUSIVE"
    elif k == 3 and ok == 3:
        vb = "SUPPORTED"
    elif k == 3 and low == 3:
        vb = "REFUTED"
    else:
        vb = "INCONCLUSIVE"
    res["b"] = {"domain": dom, "per_round": perb, "verdict": vb,
                "confidence": 1 - 0.5 ** k if vb in ("SUPPORTED", "REFUTED") else None}
    return res


# ---------------------------------------------------------------------- cli
def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _dump(o):
    print(json.dumps(_clean(o), indent=2, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="joulepack.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--sysroot", default=os.environ.get("K3D_GPU_SYSROOT", ""))
    a = sub.add_parser("attribute")
    a.add_argument("--core-j", type=float, required=True)
    a.add_argument("--pods", required=True, help="JSON file: [{name, cpu_s, type}]")
    a.add_argument("--model", default="cputime", choices=["cputime", "typed"])
    a.add_argument("--weights", default="", help="performance=J,efficient=J (J per CPU-second)")
    p = sub.add_parser("place")
    p.add_argument("--arms", required=True, help="JSON file: {arm: {n, pkg_w, p95}}")
    p.add_argument("--slo", type=float, default=SLO_S)
    p.add_argument("--min-gain", type=float, default=MIN_GAIN)
    p.add_argument("--incumbent", default="E")
    v = sub.add_parser("verdict")
    v.add_argument("out")
    v.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "detect":
        _dump(discover(args.sysroot))
    elif args.cmd == "attribute":
        with open(args.pods) as f:
            pods = json.load(f)
        w = {k: float(x) for k, x in (kv.split("=") for kv in args.weights.split(",") if kv)} or None
        _dump(attribute(args.core_j, pods, args.model, w))
    elif args.cmd == "place":
        with open(args.arms) as f:
            _dump(place(json.load(f), args.slo, args.min_gain, args.incumbent))
    elif args.cmd == "verdict":
        r = verdict(args.out)
        if args.json:
            _dump(r)
        else:
            print("rnd arm   pkgW  coreW  uncW  turns  p95s  agents/W  flags")
            for x in r["rows"]:
                m = x["marginal_w"]
                fl = ",".join(f for f, on in (("throttle", x["flag_throttle"]), ("swap", x["flag_swap"]),
                                               ("cpuset", bool(x["bad_cpuset"]))) if on)
                print("%3s %3s %6.2f %6.2f %5.2f %6d %5.2f %9.3f  %s" % (
                    x["round"], x["arm"], m["package"], m.get("core", math.nan), m.get("uncore", math.nan),
                    x["turns"], x["p95_s"], x["agents_per_w"], fl))
            print("idle pkg W: median %.2f spread %.2f n=%d" % (r["idle_pkg_w"]["median"], r["idle_pkg_w"]["spread"],
                                                               r["idle_pkg_w"]["n"]))
            for k in ("invalid", "inconclusive_flags", "validity_flags"):
                if r[k]:
                    print("%s: %s" % (k, "; ".join(r[k])))
            for pr in r["a"]["per_round"]:
                print("H21a round %s: best=%s best/E=%.3f best/U=%.3f" % (pr["round"], pr["best"], pr["best_over_E"],
                                                                          pr["best_over_U"]))
            print("H21a verdict: %s confidence=%s" % (r["a"]["verdict"], r["a"]["confidence"]))
            for pr in r["b"]["per_round"]:
                print("H21b round %s: cpu-share P=%.3f energy-share P=%.3f err=%+.3f additivity gap=%.2f" % (
                    pr["round"], pr["cpu_share_P"], pr["energy_share_P"], pr["err"], pr["additivity_gap"]))
            print("H21b verdict: %s confidence=%s (domain %s)" % (r["b"]["verdict"], r["b"]["confidence"],
                                                                  r["b"]["domain"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

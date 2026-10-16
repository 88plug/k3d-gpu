#!/usr/bin/env python3
"""Falsification sim for numerics contracts (docs/numerics-contracts.md).

Every number this prints is a SIMULATION OUTPUT driven by assumed priors. The
resolver under test is the real one in scripts/k3d-gpu-numerics; the sim only
builds its inputs (realisation lists + evidence ConfigMap data). Ground truth is
held here and never passed to the resolver.

Usage: python3 tests/numerics_sim.py [--seeds N] [--json]
"""
import argparse
import importlib.machinery
import importlib.util
import json
import math
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader(
    "k3d_gpu_numerics", os.path.join(_HERE, "..", "scripts", "k3d-gpu-numerics"))
_spec = importlib.util.spec_from_loader("k3d_gpu_numerics", _loader)
nx = importlib.util.module_from_spec(_spec)
_loader.exec_module(nx)

# Prior mean per-token KL vs a BF16/FP16 reference. ASSUMPTIONS, except q4_k_m whose
# 0.031 / 91.9% same-top is the llama.cpp-published Q4_K_M figure. Top-1 maps from KL via
# 1 - 0.46*sqrt(kl), the constant fitted to that one published point.
FORMAT_PRIOR_KL = {"fp16": 0.0005, "bf16": 0.0008, "fp8": 0.004, "int8": 0.003,
                   "q8_0": 0.002, "q4_k_m": 0.031, "int4": 0.05}
TOP1_C = 0.46

# Six host profiles, realisations in a speed-first preference order. Names are unique per host.
HOSTS = {
    "ai-pc-npu": [("npu-ov-int4", "int4"), ("igpu-ov-int4", "int4"), ("igpu-ov-fp16", "fp16"),
                  ("cpu-llamacpp-q4km", "q4_k_m"), ("cpu-ov-int8", "int8")],
    "xeon-amx": [("amx-ov-int8", "int8"), ("amx-ov-bf16", "bf16"),
                 ("cpu-llamacpp-q4km", "q4_k_m"), ("cpu-llamacpp-q8", "q8_0")],
    "gaudi3": [("gaudi-vllm-fp8", "fp8"), ("gaudi-vllm-bf16", "bf16"),
               ("amx-ov-bf16", "bf16"), ("cpu-ov-int8", "int8")],
    "nvidia-ws": [("cuda-vllm-fp8", "fp8"), ("cuda-vllm-awq-int4", "int4"),
                  ("cuda-vllm-fp16", "fp16"), ("cuda-llamacpp-q4km", "q4_k_m")],
    "i9-13900t-2nv-uhd770": [("cuda-llamacpp-q4km", "q4_k_m"), ("cuda-vllm-fp16", "fp16"),
                             ("igpu-ov-int4", "int4"), ("igpu-ov-fp16", "fp16"),
                             ("cpu-llamacpp-q4km", "q4_k_m")],
    "arc-dgpu": [("arc-ov-int4", "int4"), ("arc-ov-int8", "int8"), ("arc-ov-fp16", "fp16"),
                 ("arc-llamacpp-q4km", "q4_k_m")],
}
CLASSES = ["exact", "bf16-equivalent", "draft-ok"]
MODELS = ["model-a", "model-b"]
CELLS = [(h, c, m) for h in HOSTS for c in CLASSES for m in MODELS]  # 6 x 3 x 2 = 36

DEFAULTS = dict(sigma=0.5, p_bug=0.10, q=0.85, noise=0.15, prev_cov=0.9, n_tokens=8192)


def top1_of(kl):
    return 1.0 - TOP1_C * math.sqrt(kl)


def violates(kl, top1, contract):
    return kl > contract["max_kl"] or top1 < contract["min_top1"]


def draw_world(rng, p):
    """Hidden truth per (host, realisation, model) and the realisation lists."""
    truth, reals = {}, {}
    for host, lst in HOSTS.items():
        reals[host] = []
        for name, fmt in lst:
            cur = "sha256:" + format(rng.getrandbits(64), "016x")
            prev = "sha256:" + format(rng.getrandbits(64), "016x")
            reals[host].append({"name": name, "format": fmt, "engine_digest": cur})
            for model in MODELS:
                base = FORMAT_PRIOR_KL[fmt] * math.exp(rng.gauss(0, p["sigma"]))
                kl_cur = base * (rng.uniform(5, 20) if rng.random() < p["p_bug"] else 1.0)
                t_cur = min(1.0, top1_of(kl_cur) + rng.gauss(0, 0.004))
                t_prev = min(1.0, top1_of(base) + rng.gauss(0, 0.004))
                truth[(host, name, model)] = (kl_cur, t_cur, base, t_prev, cur, prev, fmt)
    return truth, reals


def measure(rng, p, truth, source=None):
    """Evidence records per host. source maps a key to the truth it is measured from
    (identity normally; a permutation for the decorrelation control)."""
    ev = {h: {} for h in HOSTS}
    for key, (kl_cur, t_cur, base, t_prev, cur, prev, fmt) in truth.items():
        host, name, model = key
        src = truth[source[key]] if source else truth[key]
        u = rng.random()
        if u < p["q"]:
            kl, t1, dig = src[0], src[1], cur
        elif u < p["q"] + (1 - p["q"]) * p["prev_cov"]:
            kl, t1, dig = src[2], src[3], prev
        else:
            continue
        n = p["n_tokens"]
        rec = {"model": model, "realisation": name, "format": fmt, "engine_digest": dig,
               "reference": "ref-bf16", "golden_set": "golden-v1", "n_tokens": n,
               "kl_method": "full", "teacher_forced": True,
               "kl_mean": kl * math.exp(rng.gauss(0, p["noise"])),
               "top1": min(1.0, t1 + rng.gauss(0, math.sqrt(max(t1 * (1 - t1), 1e-9) / n)))}
        ev[host][nx.evidence_key(model, name, dig)] = rec
    return ev


def declared_evidence(reals):
    """Pseudo-evidence from the format prior table: what a declared-only resolver knows."""
    ev = {h: {} for h in HOSTS}
    for host, lst in reals.items():
        for r in lst:
            for model in MODELS:
                kl = FORMAT_PRIOR_KL[r["format"]]
                ev[host][nx.evidence_key(model, r["name"], r["engine_digest"])] = {
                    "model": model, "realisation": r["name"], "format": r["format"],
                    "engine_digest": r["engine_digest"], "n_tokens": 10**9, "kl_method": "full",
                    "teacher_forced": True, "kl_mean": kl, "top1": top1_of(kl)}
    return ev


def nodigest(ev, reals):
    """Ablation: relabel previous-digest records as current (the digest key is ignored)."""
    out = {}
    for host, recs in ev.items():
        cur = {r["name"]: r["engine_digest"] for r in reals[host]}
        out[host] = {}
        for rec in recs.values():
            r2 = dict(rec, engine_digest=cur[rec["realisation"]])
            out[host].setdefault(nx.evidence_key(r2["model"], r2["realisation"], r2["engine_digest"]), r2)
        # a current-digest record wins over a relabelled previous one
        for k, rec in recs.items():
            if rec["engine_digest"] == cur[rec["realisation"]]:
                out[host][k] = rec
    return out


def b0(cands, contract):
    return cands[0]["name"] if cands else None


def b_table(allowed):
    def pick(cands, contract, cls):
        for c in cands:
            if (cls, c["format"]) in allowed:
                return c["name"]
        return None
    return pick


def b1_allowed():
    return {(cls, f) for cls in CLASSES for f, kl in FORMAT_PRIOR_KL.items()
            if not violates(kl, top1_of(kl), nx.CLASSES[cls])}


def run(p, seeds, seed0=0, policies=None, shuffle=False, b2_tables=None):
    allowed1 = b1_allowed()
    names = ["B0", "B1", "R", "R-nodigest", "R-declared"] + [f"B2@{t}" for t in (b2_tables or {})]
    tally = {n: {"viol": 0, "reject": 0, "needless_reject": 0} for n in names}
    agree_declared_b1 = 0
    cells = 0
    for s in range(seed0, seed0 + seeds):
        rng = random.Random(s)
        truth, reals = draw_world(rng, p)
        source = None
        if shuffle:
            keys = list(truth)
            perm = keys[:]
            rng.shuffle(perm)
            source = dict(zip(keys, perm))
        ev = measure(rng, p, truth, source)
        ev_nd = nodigest(ev, reals)
        ev_decl = declared_evidence(reals)
        for host, cls, model in CELLS:
            cells += 1
            contract = nx.CLASSES[cls]
            cands = reals[host][1:]  # top preference removed
            ok = {c["name"]: not violates(truth[(host, c["name"], model)][0],
                                          truth[(host, c["name"], model)][1], contract)
                  for c in cands}
            pick = {
                "B0": b0(cands, contract),
                "B1": b_table(allowed1)(cands, contract, cls),
                "R": nx.resolve(cls, model, cands, ev[host])["placed"],
                "R-nodigest": nx.resolve(cls, model, cands, ev_nd[host])["placed"],
                "R-declared": nx.resolve(cls, model, cands, ev_decl[host])["placed"],
            }
            for t, tab in (b2_tables or {}).items():
                pick[f"B2@{t}"] = b_table(tab)(cands, contract, cls)
            agree_declared_b1 += pick["R-declared"] == pick["B1"]
            for n, choice in pick.items():
                if choice is None:
                    tally[n]["reject"] += 1
                    tally[n]["needless_reject"] += any(ok.values())
                elif not ok[choice]:
                    tally[n]["viol"] += 1
    res = {n: {k: v / cells for k, v in t.items()} for n, t in tally.items()}
    res["_cells"] = cells
    res["_declared_eq_b1"] = agree_declared_b1 / cells
    return res


def train_b2(p, seeds, seed0, taus):
    """Best static (class, format) table: allow iff empirical violation prob <= tau."""
    cnt, bad = {}, {}
    for s in range(seed0, seed0 + seeds):
        rng = random.Random(s)
        truth, _ = draw_world(rng, p)
        for (host, name, model), (kl, t1, *_rest) in truth.items():
            fmt = _rest[-1]
            for cls in CLASSES:
                k = (cls, fmt)
                cnt[k] = cnt.get(k, 0) + 1
                bad[k] = bad.get(k, 0) + violates(kl, t1, nx.CLASSES[cls])
    return {t: {k for k in cnt if bad[k] / cnt[k] <= t} for t in taus}


def pct(x):
    return f"{100 * x:5.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    N = a.seeds
    taus = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
    out = {}

    p = dict(DEFAULTS)
    b2 = train_b2(p, N, 10_000, taus)
    d = run(p, N, b2_tables=b2)
    out["defaults"] = d
    out["control_sigma0_pbug0"] = run(dict(p, sigma=0.0, p_bug=0.0), N)
    out["control_shuffled_truth"] = run(p, N, shuffle=True)
    out["ablate_pbug0"] = run(dict(p, p_bug=0.0), N)
    out["sweep_q"] = {q: run(dict(p, q=q), N)["R"] for q in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0)}
    out["sweep_pbug"] = {pb: {k: run(dict(p, p_bug=pb), N)[k] for k in ("B1", "R", "R-nodigest")}
                         for pb in (0.0, 0.02, 0.05, 0.1, 0.2)}
    out["sweep_sigma"] = {sg: {k: v for k, v in run(dict(p, sigma=sg), N).items() if k in ("B1", "R")}
                          for sg in (0.0, 0.25, 0.5, 1.0)}
    out["sweep_noise"] = {nz: run(dict(p, noise=nz), N)["R"] for nz in (0.05, 0.15, 0.5, 1.0)}

    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return

    print(f"SIMULATION ONLY ({N} seeds x 36 cells, top preference removed; priors are assumptions)\n")
    print(f"{'policy':14s} {'violate':>8s} {'reject':>8s} {'needless':>9s}")
    for n in ["B0", "B1", "R", "R-nodigest", "R-declared"] + [f"B2@{t}" for t in taus]:
        r = d[n]
        print(f"{n:14s} {pct(r['viol']):>8s} {pct(r['reject']):>8s} {pct(r['needless_reject']):>9s}")
    R, B1 = d["R"], d["B1"]
    c0, sh, pb0 = out["control_sigma0_pbug0"], out["control_shuffled_truth"], out["ablate_pbug0"]
    v = []
    v.append(("AC1 R-declared == B1 in every cell", d["_declared_eq_b1"] == 1.0, f"{pct(d['_declared_eq_b1'])} agree"))
    v.append(("AC2 [declared-only, table-defined, NOT evidence] B0 viol / R-declared viol / reject", None,
              f"{pct(d['B0']['viol'])} / {pct(d['R-declared']['viol'])} / {pct(d['R-declared']['reject'])}"
              " (truth here is the sim generator, sigma/p_bug > 0)"))
    gap = B1["viol"] - R["viol"]
    v.append(("AC3 R viol<=2%, B1-R>=5pp, R reject<=30%",
              R["viol"] <= 0.02 and gap >= 0.05 and R["reject"] <= 0.30,
              f"R viol {pct(R['viol'])}, gap {100*gap:.1f}pp, R reject {pct(R['reject'])}"))
    g0 = c0["B1"]["viol"] - c0["R"]["viol"]
    v.append(("AC4 sigma=0,p_bug=0: |B1-R| viol <= 1pp", abs(g0) <= 0.01,
              f"B1 {pct(c0['B1']['viol'])}, R {pct(c0['R']['viol'])}, R reject {pct(c0['R']['reject'])}"))
    gs = sh["B1"]["viol"] - sh["R"]["viol"]
    v.append(("AC5 shuffled evidence: advantage collapses (<2pp)", gs < 0.02,
              f"B1 {pct(sh['B1']['viol'])}, R {pct(sh['R']['viol'])} (gap {100*gs:.1f}pp)"))
    gd = d["R-nodigest"]["viol"] - R["viol"]
    gd0 = pb0["R-nodigest"]["viol"] - pb0["R"]["viol"]
    v.append(("AC6 digest ablation: +>=3pp at p_bug>0, <=1pp at p_bug=0", gd >= 0.03 and abs(gd0) <= 0.01,
              f"nodigest-R {100*gd:.1f}pp @p_bug=.1, {100*gd0:.1f}pp @p_bug=0; "
              f"nodigest reject {pct(d['R-nodigest']['reject'])} vs R {pct(R['reject'])}"))
    dom = [t for t in taus if d[f"B2@{t}"]["viol"] < R["viol"] and d[f"B2@{t}"]["reject"] < R["reject"]]
    v.append(("AC7 R not dominated by B2 frontier", not dom, f"dominating taus: {dom or 'none'}"))
    cross = [q for q, r in out["sweep_q"].items() if r["reject"] > 0.30]
    v.append(("AC8 R reject vs q", None,
              ", ".join(f"q={q}:{pct(r['reject']).strip()}" for q, r in out["sweep_q"].items())
              + f"; reject>30% at q in {cross or 'none'}"))
    print()
    for name, ok, detail in v:
        tag = "INFO" if ok is None else ("PASS" if ok else "FAIL")
        print(f"[{tag}] {name}: {detail}")
    print("\nsweep p_bug (viol B1 / R / R-nodigest):")
    for pb, r in out["sweep_pbug"].items():
        print(f"  p_bug={pb:<5} {pct(r['B1']['viol'])} / {pct(r['R']['viol'])} / {pct(r['R-nodigest']['viol'])}"
              f"   R reject {pct(r['R']['reject'])}")
    print("sweep sigma (viol B1 / R, reject B1 / R):")
    for sg, r in out["sweep_sigma"].items():
        print(f"  sigma={sg:<5} {pct(r['B1']['viol'])} / {pct(r['R']['viol'])}   "
              f"{pct(r['B1']['reject'])} / {pct(r['R']['reject'])}")
    print("sweep KL measurement noise (R viol / reject):")
    for nz, r in out["sweep_noise"].items():
        print(f"  noise={nz:<5} {pct(r['viol'])} / {pct(r['reject'])}")


if __name__ == "__main__":
    main()

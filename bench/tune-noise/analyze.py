#!/usr/bin/env python3
"""tune-noise/analyze.py: H19 under matched background load (EXPERIMENTS.md, H19 amendment A1).

  analyze.py candidate PROFILE.json         # the config the replication measures, as JSON
  analyze.py verdict DIR [--resamples N]    # DIR from run.sh: candidate.json, rep-*/, load.jsonl

candidate: the tune's winner when its decision is `tuned`, else its pending
challenger, else the screened neighbour with the best screening score; prints
{"candidate": cfg|null, "crowned": bool}.

verdict: replays the replication trials (A = default, B = candidate, order
A B B A A B ...) through console/tune.py's own confirmation and gates
(_confirm), adds per-arm medians of aggregate tok/s with 95% bootstrap CIs, the
B/A ratio with its CI, the load per arm with `bench/load/load.py matched`, the
registered outcome table and a derived confidence. Python 3 stdlib only.
"""
import argparse
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "console"))
sys.path.insert(0, os.path.join(HERE, "..", "load"))
import load  # noqa: E402
import tune  # noqa: E402

MIN_PER_SIDE = 3
MARGIN = 0.05


def candidate(prof):
    if prof.get("decision") == "tuned" and prof.get("winner"):
        return {"candidate": tune.validate_config(prof["winner"]), "crowned": True}
    last = prof.get("last_step") or {}
    pend = prof.get("pending") or last.get("pending")
    if pend and pend.get("config"):
        return {"candidate": tune.validate_config(pend["config"]), "crowned": False}
    ok = [e for e in last.get("screened") or [] if e.get("ok") and e.get("score") is not None]
    if not ok:
        return {"candidate": None, "crowned": False}
    _, sign = tune.OBJECTIVES[prof.get("objective") or "throughput"]
    best = max(ok, key=lambda e: sign * e["score"])
    return {"candidate": tune.validate_config(best["config"]), "crowned": False}


class Replay:
    """Evaluator that returns the recorded replication trials in run order."""

    def __init__(self, by_key):
        self.by_key = {k: list(v) for k, v in by_key.items()}

    def __call__(self, cfg, budget):
        return self.by_key[tune.config_key(cfg)].pop(0)


def boot_ci(xs, rng, n):
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [meds[int(0.025 * n)], meds[int(0.975 * n) - 1]]


def outcome(crowned, passes):
    """The registered outcome table (row H19)."""
    if crowned:
        return "SUPPORTED" if passes else "REFUTED"
    return "INCONCLUSIVE" if passes else "SUPPORTED"


def read_trials(d):
    trials = {"A": [], "B": []}
    i = 1
    while os.path.exists(os.path.join(d, f"rep-{i:02d}", "arm")):
        with open(os.path.join(d, f"rep-{i:02d}", "arm")) as f:
            arm = f.read().strip()
        try:
            with open(os.path.join(d, f"rep-{i:02d}", "result.json")) as f:
                trials[arm].append(json.load(f))
        except (OSError, ValueError) as e:
            trials[arm].append({"ok": False, "error": f"no result: {e}", "runs": []})
        i += 1
    return trials


def verdict(d, resamples=10000, seed=1):
    with open(os.path.join(d, "candidate.json")) as f:
        cand = json.load(f)
    out = {"crowned": cand["crowned"], "candidate": cand["candidate"]}
    if not cand["candidate"]:
        out.update(verdict="INCONCLUSIVE", reason="no screened candidate to replicate")
        return out
    base = {"baseline": True}
    trials = read_trials(d)
    n = min(len(trials["A"]), len(trials["B"]))
    out["n"] = {"default": len(trials["A"]), "candidate": len(trials["B"]), "paired": n}
    windows = []
    lp = os.path.join(d, "load.jsonl")
    if os.path.exists(lp):
        with open(lp) as f:
            windows = [json.loads(line) for line in f if line.strip()]
    if windows:
        m = load.matched(windows)
        out.update(load_checks=m["checks"], load_matched=m["matched"], load_verdict=m["verdict"])
    if n < MIN_PER_SIDE:
        out.update(verdict="INCONCLUSIVE", reason=f"{n} trials per side < {MIN_PER_SIDE}")
        return out
    ev = Replay({tune.config_key(base): trials["A"][:n], tune.config_key(cand["candidate"]): trials["B"][:n]})
    try:
        res = tune._confirm(ev, base, [cand["candidate"]], "throughput", n_confirm=n, margin=MARGIN, k=1.0,
                            alpha=0.05, guard_tol=0.25, ttft_slo=10.0, conc=[1, 4, 8], say=lambda *_: None)
    except RuntimeError as e:
        out.update(verdict="INCONCLUSIVE", reason=f"default trial failed: {e}")
        return out
    v = res["verdicts"][0] if res["verdicts"] else {"accepted": False, "reasons": ["candidate failed"]}
    out.update(gates_pass=bool(v.get("accepted")), gate_reasons=v.get("reasons"), gain=v.get("gain"),
               separation=v.get("separation"), p_value=v.get("p_value"),
               ttft_s_median={"default": res["baseline"]["ttft_s"]["median"],
                              "candidate": (v.get("summary") or {}).get("ttft_s", {}).get("median")})
    rng = random.Random(seed)
    xs = {}
    for arm, key in (("A", "default"), ("B", "candidate")):
        xs[key] = [x for t in trials[arm][:n] if t.get("ok") for r in t["runs"]
                   for x in [tune.run_metrics(r, 10.0)["aggregate_tokens_per_s"]] if x is not None]
    if xs["default"] and xs["candidate"]:
        a, b = xs["default"], xs["candidate"]
        boot = sorted(statistics.median(rng.choices(b, k=len(b))) / statistics.median(rng.choices(a, k=len(a)))
                      for _ in range(resamples))
        gain_side = out["gates_pass"]
        p_boot = sum((x >= 1 + MARGIN) == gain_side for x in boot) / resamples
        conf = min(p_boot, 1 - out["p_value"]) if gain_side else p_boot
        out.update(samples=xs, median={k: statistics.median(x) for k, x in xs.items()},
                   ci95={k: boot_ci(x, rng, resamples) for k, x in xs.items()},
                   ratio=statistics.median(b) / statistics.median(a),
                   ratio_ci95=[boot[int(0.025 * resamples)], boot[int(0.975 * resamples) - 1]],
                   p_boot=p_boot, confidence=conf)
    out["verdict_if_matched"] = outcome(cand["crowned"], out["gates_pass"])
    out["verdict"] = out["verdict_if_matched"] if out.get("load_matched") else "INCONCLUSIVE"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidate")
    c.add_argument("profile")
    v = sub.add_parser("verdict")
    v.add_argument("dir")
    v.add_argument("--resamples", type=int, default=10000)
    o = ap.parse_args(argv)
    if o.cmd == "candidate":
        with open(o.profile) as f:
            print(json.dumps(candidate(json.load(f))))
    else:
        print(json.dumps(verdict(o.dir, o.resamples), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

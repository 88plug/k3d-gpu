"""Run both simulations against the pre-registered criteria (PREREG.md).

    python3 -m presence.report [frames|energy|all]

Single-threaded, a couple of minutes for `all`.
"""

import random
import sys
from dataclasses import replace

from . import sim_energy as E
from . import sim_frames as F


def frames():
    print("== claim (2): iGPU prefill chunk vs compositor (8k prompt) ==")
    ph = F.Physics()
    print("-- nominal physics", ph)
    for reg in ("step", "kernel", "timeslice"):
        r = F.evaluate(ph, reg, seconds=60)
        print(f"{reg:9s} present_chunk={r['present_chunk']:5d} away_chunk={r['away_chunk']:5d} "
              f"p99/idle gated={r['gated_present_p99']:.2f} smooth_static={r['smooth_static_p99']:.2f} "
              f"tp_static={r['tp_static_p99']:.2f} | away/tp={r['away_vs_tp']:.3f} "
              f"away-vs-smooth={r['away_vs_smooth']:+.3f} switch_back={r['switch_back_s']:.2f}s "
              f"| kill={r['kill']} holds={r['holds']}")
    print("-- ablations (kernel regime, 120 Hz panel)")
    ph120 = replace(ph, refresh_hz=120.0)
    good = F.evaluate(ph120, "kernel", seconds=60)
    wrong = F.evaluate(ph120, "kernel", cal=F.calib_from(ph120, refresh_hz=60.0), seconds=60)
    reload = F.evaluate(ph120, "kernel", seconds=60, reload_s=10.0)
    print(f"budget from true 120 Hz : chunk={good['present_chunk']} p99/idle={good['gated_present_p99']:.2f} smooth_ok={good['smooth_ok']}")
    print(f"budget from wrong 60 Hz : chunk={wrong['present_chunk']} p99/idle={wrong['gated_present_p99']:.2f} smooth_ok={wrong['smooth_ok']}")
    print(f"server reload (10 s)    : switch_back={reload['switch_back_s']:.1f}s switch_ok={reload['switch_ok']}")
    print("-- grid, kernel regime: t_tok_ms x w_ms x refresh -> holds | which leg fails")
    hold = 0
    total = 0
    for hz in (60.0, 120.0):
        for w in (5.0, 15.0, 50.0):
            row = []
            for tt in (0.25, 0.5, 1.0, 2.0, 3.75, 7.5):
                r = F.evaluate(replace(ph, t_tok_ms=tt, w_ms=w, refresh_hz=hz), "kernel", seconds=40)
                total += 1
                hold += r["holds"]
                why = ("OK" if r["holds"] else "kill" if r["kill"] else "jank" if not r["smooth_ok"]
                       else "switch" if not r["switch_ok"] else "away<97%" if r["away_vs_tp"] < 0.97
                       else f"{100 * r['away_vs_smooth']:+.0f}%")
                row.append(f"t{tt}:{why}")
            print(f"{int(hz)}Hz w={w:4.0f}ms  " + "  ".join(row))
    print(f"grid: claim holds in {hold}/{total} cells")
    for name, base in (("nominal", ph), ("small-model", replace(ph, t_tok_ms=0.5, w_ms=50.0))):
        rng = random.Random(7)
        verdicts = {"holds": 0, "kill": 0, "jank": 0, "other": 0}
        for i in range(30):
            p = F.perturb(base, rng)
            r = F.evaluate(p, "kernel", cal=F.calib_from(p, err=0.15, rng=random.Random(i)), seconds=30, seed=i)
            k = "holds" if r["holds"] else "kill" if r["kill"] else "jank" if not r["smooth_ok"] else "other"
            verdicts[k] += 1
        print(f"perturb ±30% + calib ±15% around {name}: {verdicts}")


def energy():
    print("== claim (1): node J/task, gated vs best static (50 tasks) ==")
    rows = []
    for alpha in (0.1, 0.3, 0.6):
        for agents in (1, 4):
            for away in (0.3, 0.7):
                node = replace(E.Node(), alpha_decode=alpha)
                work = replace(E.Work(), agents=agents)
                r = E.evaluate(node, work, E.Human(away_frac=away, mean_cycle_s=1800, seed=agents * 10 + int(away * 10)),
                               dt=0.5, cal_seed=int(alpha * 10))
                ok = (r["saving_vs_static"] >= 0.25 and r["wall_vs_ref"] <= 1.15 and r["ttft_vs_ref"] <= 1.05)
                verdict = "HOLDS" if ok else "REFUTED" if r["saving_vs_static"] < 0.10 else "between"
                rows.append(verdict)
                print(f"alpha_dec={alpha:.1f} agents={agents} away={away:.1f}: "
                      f"save_vs_static={100 * r['saving_vs_static']:+5.1f}% save_vs_ref={100 * r['saving_vs_ref']:+5.1f}% "
                      f"wall={r['wall_vs_ref']:.2f}x ttft={r['ttft_vs_ref']:.2f}x "
                      f"static={r['best_static']['op'].cap:.2f}/{r['best_static']['op'].epp} "
                      f"away={r['away_op'].cap:.2f}/{r['away_op'].epp}/hold{r['away_op'].hold} -> {verdict}")
    print("summary:", {v: rows.count(v) for v in set(rows)})
    print("-- present p95 TTFT / latency-tuned over 8 humans (alpha_dec 0.3, 4 agents, away 0.5)")
    for shuffle in (False, True):
        v = [E.evaluate(E.Node(), E.Work(), E.Human(away_frac=0.5, mean_cycle_s=900, seed=s), dt=0.5,
                        shuffle=shuffle)["ttft_vs_ref"] for s in range(1, 9)]
        print(f"{'shuffled' if shuffle else 'presence'}: {[round(x, 2) for x in v]} "
              f"-> {sum(x > 1.05 for x in v)}/8 violate 1.05x")
    node = replace(E.Node(), alpha_decode=0.0, gpu_idle_w=(2.0, 2.0), cpu_idle_w=2.0, decode_dem=1.0)
    work = replace(E.Work(), agents=2, prompt_tokens=300, out_tokens=1500, tool_s=1.0)
    r = E.evaluate(node, work, E.Human(away_frac=0.8, mean_cycle_s=1800, seed=3), dt=0.25)
    print(f"positive control (decode power-insensitive, low idle, away 0.8): save_vs_static="
          f"{100 * r['saving_vs_static']:+.1f}% save_vs_ref={100 * r['saving_vs_ref']:+.1f}% "
          f"ttft={r['ttft_vs_ref']:.2f}x -> the sim can grant large savings when physics allows")
    print("-- ablations (alpha_dec 0.3, 4 agents, away 0.5)")
    node, work, hum = E.Node(), E.Work(), lambda: E.Human(away_frac=0.5, mean_cycle_s=1800, seed=1)
    for label, kw in (("gated", {}), ("shuffled signal", {"shuffle": True}), ("no gateway hold", {"away_hold": 1})):
        r = E.evaluate(node, work, hum(), dt=0.5, **kw)
        print(f"{label:16s} save_vs_static={100 * r['saving_vs_static']:+5.1f}% ttft={r['ttft_vs_ref']:.2f}x "
              f"controller_away={r['controller_away_frac']:.2f} true_away={r['true_away_frac']:.2f}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("frames", "all"):
        frames()
    if what in ("energy", "all"):
        energy()

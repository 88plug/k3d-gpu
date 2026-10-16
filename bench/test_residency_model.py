#!/usr/bin/env python3
"""Tests for bench/residency_model.py. Run: python3 -m unittest bench/test_residency_model.py"""
import json
import math
import os
import random
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import residency_model as rm  # noqa: E402


def random_busy(rng, n, tail_s):
    t, busy = rng.uniform(0, 2), []
    for _ in range(n):
        w = rng.uniform(0.05, 3.0)
        busy.append((t, t + w))
        # gaps straddle the tail length: some calls land inside the previous tail
        t += w + rng.choice([0.0, rng.uniform(0, tail_s), rng.uniform(tail_s, 4 * tail_s + 40)])
    return busy


class LedgerAccounting(unittest.TestCase):
    """A1: causal per-call charges telescope to the integrated power timeline."""

    def test_ledger_equals_timeline(self):
        rng = random.Random(0)
        for _ in range(300):
            dev = rm.Device("d", "dgpu", p_floor=rng.uniform(0, 20), p_active=rng.uniform(0, 60),
                            tail_w=rng.uniform(0, 40), tail_s=rng.uniform(0, 10),
                            decode_tps=100, prefill_tps=1000, wake_j=rng.uniform(0, 3),
                            deep_after_s=rng.choice([math.inf, rng.uniform(1, 30)]))
            busy = random_busy(rng, rng.randint(1, 40), dev.tail_s)
            t_end = busy[-1][1] + rng.uniform(0, 2 * dev.tail_s + 1)
            led = rm.TailLedger(dev)
            for s, e in busy:
                led.charge(s, e)
            truth = rm.integrate_timeline(dev, busy, t_end)
            self.assertAlmostEqual(led.total(t_end), truth, delta=1e-6 * max(1.0, truth))

    def test_naive_full_tail_overcounts_when_gaps_shorter_than_tail(self):
        dev = rm.Device("d", "dgpu", 7.0, 35.0, tail_w=10.0, tail_s=5.0, decode_tps=100,
                        prefill_tps=1000)
        busy = [(i * 3.0, i * 3.0 + 1.0) for i in range(10)]   # 2 s gaps < 5 s tail
        t_end = busy[-1][1] + 5.0
        truth = rm.integrate_timeline(dev, busy, t_end)
        naive = rm.naive_tail_total(dev, busy, t_end)
        self.assertAlmostEqual(naive - truth, 9 * 10.0 * (5.0 - 2.0), places=6)
        led = rm.TailLedger(dev)
        for s, e in busy:
            led.charge(s, e)
        self.assertAlmostEqual(led.total(t_end), truth, places=6)

    def test_naive_matches_when_gaps_exceed_tail(self):
        dev = rm.Device("d", "dgpu", 7.0, 35.0, tail_w=10.0, tail_s=2.0, decode_tps=100,
                        prefill_tps=1000)
        busy = [(i * 10.0, i * 10.0 + 1.0) for i in range(5)]
        t_end = busy[-1][1] + 2.0
        self.assertAlmostEqual(rm.naive_tail_total(dev, busy, t_end),
                               rm.integrate_timeline(dev, busy, t_end), places=6)


def _steps(seed, gap, n=60, agents=2):
    rng = random.Random(seed)
    return [rm.gen_agent_steps(rng, n, gap) for _ in range(agents)]


class Router(unittest.TestCase):

    def test_decisions_are_causal(self):
        """Changing calls after step k must not change any routing decision up to step k."""
        dgpu, igpu = rm.nominal_devices(tail_w=40, tail_s=10, igpu_tps=30, igpu_w=6)
        steps = _steps(1, 5.0, agents=1)
        k = 30
        altered = [steps[0][:k] + [(300, 250, 0.1)] * (len(steps[0]) - k)]
        picks = {}
        for label, st in (("orig", steps), ("alt", altered)):
            seen = []
            pol = rm.make_ledger_policy(floor_time=False, slack=math.inf)

            def rec(state, a, p, o, f, pol=pol, seen=seen):
                i = pol(state, a, p, o, f)
                seen.append(i)
                return i
            rm.route_sim([dgpu, igpu], [dgpu, igpu], st, rec, rm.RTX4000_FLOOR_W, seed=3)
            picks[label] = seen[:k]
        self.assertEqual(picks["orig"], picks["alt"])

    def test_zero_tail_makes_tail_term_inert(self):
        dgpu, igpu = rm.nominal_devices(tail_w=0.0, tail_s=0.0)
        steps = _steps(2, 3.0)
        a = rm.route_sim([dgpu, igpu], [dgpu, igpu], steps, rm.make_ledger_policy(),
                         rm.RTX4000_FLOOR_W)
        b = rm.route_sim([dgpu, igpu], [dgpu, igpu], steps, rm.make_ledger_policy(tail=False),
                         rm.RTX4000_FLOOR_W)
        self.assertEqual(a, b)

    def test_tail_term_moves_calls_only_when_tail_is_large(self):
        """Mechanism check: the tail term must be what moves calls to the iGPU."""
        steps = _steps(4, 10.0)
        big = rm.nominal_devices(tail_w=40, tail_s=10, igpu_tps=30, igpu_w=6)
        none = rm.nominal_devices(tail_w=0.0, tail_s=0.0, igpu_tps=30, igpu_w=6)
        led = rm.make_ledger_policy(floor_time=False, slack=math.inf)
        nt = rm.make_ledger_policy(tail=False, floor_time=False, slack=math.inf)
        r_big = rm.route_sim(list(big), list(big), steps, led, rm.RTX4000_FLOOR_W)
        r_none = rm.route_sim(list(none), list(none), steps, led, rm.RTX4000_FLOOR_W)
        r_big_nt = rm.route_sim(list(big), list(big), steps, nt, rm.RTX4000_FLOOR_W)
        self.assertGreater(r_big["share"][1], 0.8)
        self.assertLess(r_none["share"][1], r_big["share"][1])
        self.assertLess(r_big["incremental_j_per_task"], r_big_nt["incremental_j_per_task"])

    def test_energy_comes_from_truth_not_calibration(self):
        """A router given wrong calibration still gets charged the true energy."""
        dgpu, igpu = rm.nominal_devices()
        steps = _steps(5, 3.0)
        wrong = [replace(dgpu, p_active=1.0, tail_w=0.0), igpu]
        r_true = rm.route_sim([dgpu, igpu], [dgpu, igpu], steps, rm.policy_static(0),
                              rm.RTX4000_FLOOR_W)
        r_wrong = rm.route_sim([dgpu, igpu], wrong, steps, rm.policy_static(0),
                               rm.RTX4000_FLOOR_W)
        self.assertEqual(r_true["platform_j_per_task"], r_wrong["platform_j_per_task"])

    def test_perturbed_calibration_runs(self):
        rng = random.Random(9)
        dgpu, igpu = rm.nominal_devices()
        cal = [rm.perturb(d, rng, 0.3) for d in (dgpu, igpu)]
        r = rm.route_sim([dgpu, igpu], cal, _steps(6, 3.0), rm.make_ledger_policy(),
                         rm.RTX4000_FLOOR_W)
        self.assertGreater(r["platform_j_per_task"], 0)


class Residency(unittest.TestCase):

    def test_lognormal_mean_min_closed_form(self):
        rng = random.Random(1)
        for tau, med, sig in ((2.0, 5.0, 1.0), (10.0, 3.0, 0.5), (0.5, 1.0, 1.5)):
            mc = sum(min(tau, rm.lognormal_median(rng, med, sig)) for _ in range(200000)) / 200000
            self.assertAlmostEqual(rm.lognormal_mean_min(tau, med, sig), mc, delta=0.01 * tau)

    def test_desktop_floor_kills_claim2(self):
        """A4: with the dGPU floor paid regardless of residency, iGPU residency never saves 30 %."""
        for row in rm.claim2_report():
            if not row["rtd3"]:
                for g in (5, 10, 30):
                    self.assertLess(row["saving_gap_%g" % g], 0.30)

    def test_crossover_prediction_matches_sim(self):
        for row in rm.claim2_report():
            if row["t_star_pred"] and row["t_star_sim"]:
                self.assertLess(abs(row["t_star_pred"] / row["t_star_sim"] - 1), 0.25)

    def test_floor_difference_is_the_only_gap_lever(self):
        """Ablation: on a desktop card the absolute J/task gap between placements is fixed once
        the tail saturates; only a residency-dependent floor (RTD3) makes it shrink with gap."""
        dgpu, igpu = rm.nominal_devices(10, 3, 18, 10)
        rtd3 = replace(dgpu, p_floor_empty=0.5)

        def penalty(d, g):
            r = rm.residency_energy(d, igpu, rm.RTX4000_FLOOR_W, g)
            return r["igpu"] - r["dgpu"]
        desk = penalty(dgpu, 30) - penalty(dgpu, 5)
        lap = penalty(rtd3, 30) - penalty(rtd3, 5)
        self.assertLess(abs(desk), 0.15 * dgpu.tail_w * dgpu.tail_s)
        mean_gap_delta = (30 - 5) * math.exp(0.5)
        self.assertLess(lap, -0.8 * (dgpu.p_floor - 0.5) * mean_gap_delta)


class Advisor(unittest.TestCase):

    def setUp(self):
        self.pools = rm.nominal_pools(3.0)
        self.trace = rm.gen_trace(1, rm.MIX_TRAIN, n_agents=2, n_steps=40)

    def test_footprint_exceeds_memory(self):
        fp = sum(m.size_gb for m in rm.MODELS) / sum(p.mem_gb for p in self.pools)
        self.assertGreaterEqual(fp, 1.5)

    def test_pinned_model_never_stalls(self):
        trace = [[("router-1.5b", 1.0)] * 20]
        r = rm.replay(trace, self.pools, rm.MODELS, {"router-1.5b": [1]}, rm.ReactivePolicy())
        self.assertEqual(r["stall_s"], 0.0)
        r0 = rm.replay(trace, self.pools, rm.MODELS, {}, rm.ReactivePolicy())
        self.assertAlmostEqual(r0["stall_s"], 1.2 / 3.0)

    def test_overcommitted_pins_rejected(self):
        with self.assertRaises(ValueError):
            rm.replay(self.trace, self.pools, rm.MODELS,
                      {"planner-32b": [1]}, rm.ReactivePolicy())

    def test_reactive_policies_stall_under_pressure(self):
        r = rm.replay(self.trace, self.pools, rm.MODELS, {}, rm.ReactivePolicy())
        self.assertGreater(r["stall_s"], 0.0)

    def test_advisor_no_worse_than_baseline_in_sample(self):
        traces = [self.trace]
        pins, pol, adv = rm.advise(traces, self.pools, rm.MODELS, rounds=1)
        _, base = rm.tune_reactive(traces, self.pools, rm.MODELS)
        self.assertTrue(rm._feasible(pins, self.pools, rm.MODELS))
        self.assertLessEqual(rm.objective(adv, base), rm.objective(base, base) + 1e-9)


class Meter(unittest.TestCase):

    def _err(self, meter, est, tail_w, tail_s, n=30, spike=0.0):
        rng = random.Random(2)
        e = sum(rm.tail_estimate(rng, meter, 6.94, 35.0, 3.0, tail_w, tail_s, estimator=est,
                                 spike=spike) for _ in range(n)) / n
        return abs(e - tail_w * tail_s) / (tail_w * tail_s)

    def test_offset_cancels(self):
        rng = random.Random(3)
        a = rm.tail_estimate(rng, "instant", 6.94, 35, 3.0, 10, 2, noise_w=0.0, gain_err=0.0,
                             offset_w=0.0)
        rng = random.Random(3)
        b = rm.tail_estimate(rng, "instant", 6.94, 35, 3.0, 10, 2, noise_w=0.0, gain_err=0.0,
                             offset_w=5.0)
        self.assertAlmostEqual(a, b, delta=0.3)

    def test_averaged_meter_smears_active_into_tail(self):
        self.assertGreater(self._err("avg1s", "naive", 5, 0.1), 5.0)

    def test_corrected_instant_beats_naive_on_short_tail(self):
        self.assertLess(self._err("instant", "corrected", 5, 0.1),
                        self._err("instant", "naive", 5, 0.1))

    def test_prefill_burst_breaks_averaged_plateau_correction(self):
        self.assertLess(self._err("avg1s", "corrected", 10, 0.5), 0.2)
        self.assertGreater(self._err("avg1s", "corrected", 10, 0.5, spike=0.5), 0.5)
        self.assertLess(self._err("instant", "corrected", 10, 0.5, spike=0.5), 0.2)


class Deploy(unittest.TestCase):

    def test_plan_to_deployments(self):
        plan = {"pools": {"rtx2000e": "dgpu", "uhd770": "igpu"},
                "pins": {"router-1.5b": ["rtx2000e", "uhd770"], "coder-14b": ["rtx2000e"]}}
        out = json.loads(json.dumps(rm.deployments(plan, "registry.example/server:1")))
        self.assertEqual(out["kind"], "List")
        self.assertEqual(len(out["items"]), 3)
        by = {d["metadata"]["name"]: d for d in out["items"]}
        dg = by["router-1-5b-rtx2000e"]["spec"]["template"]["spec"]
        ig = by["router-1-5b-uhd770"]["spec"]["template"]["spec"]
        self.assertEqual(dg["containers"][0]["resources"]["limits"], {"nvidia.com/gpu": 1})
        self.assertEqual(dg["runtimeClassName"], "nvidia")
        self.assertEqual(ig["containers"][0]["resources"]["limits"], {"gpu.intel.com/i915": 1})
        self.assertNotIn("runtimeClassName", ig)
        for d in out["items"]:
            self.assertLessEqual(len(d["metadata"]["name"]), 63)
            sel = d["spec"]["selector"]["matchLabels"]
            self.assertTrue(set(sel.items()) <= set(d["spec"]["template"]["metadata"]
                                                    ["labels"].items()))


if __name__ == "__main__":
    unittest.main()

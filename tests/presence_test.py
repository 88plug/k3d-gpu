"""Unit tests for presence-gated operating points (presence/). No hardware, no cluster.

    python3 -m unittest tests/presence_test.py
"""

import os
import random
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from presence import oppoint, sim_energy as E, sim_frames as F  # noqa: E402
from presence.attention import AWAY, PRESENT, Attention, display_gpu, parse_loginctl, replay  # noqa: E402
from presence.chunk import budget_chunk, frame_budget_ms, prefix_plan, requests  # noqa: E402


class AttentionTest(unittest.TestCase):
    def test_away_only_after_t_away(self):
        a = Attention(t_away=120, now=0)
        self.assertFalse(a.tick(119.9))
        self.assertEqual(a.state, PRESENT)
        self.assertTrue(a.tick(120.0))
        self.assertEqual(a.state, AWAY)

    def test_input_returns_on_the_event(self):
        a = Attention(t_away=120, now=0)
        a.tick(200)
        self.assertTrue(a.on_input(200.01))
        self.assertEqual((a.state, a.since), (PRESENT, 200.01))

    def test_lock_is_immediate_and_input_while_locked_does_not_wake(self):
        a = Attention(now=0)
        self.assertTrue(a.on_session(5, locked=True))
        self.assertFalse(a.on_input(6))
        self.assertEqual(a.state, AWAY)
        self.assertTrue(a.on_session(7, locked=False))
        self.assertEqual(a.state, PRESENT)

    def test_idle_hint_goes_away(self):
        a = Attention(now=0)
        self.assertTrue(a.on_session(3, idle_hint=True))
        self.assertEqual(a.state, AWAY)

    def test_replay_sees_only_timestamps(self):
        tr = replay([0, 10, 50, 300], horizon=500, t_away=120)
        self.assertEqual([s for _, s in tr], [PRESENT, AWAY, PRESENT, AWAY])
        self.assertAlmostEqual(tr[1][0], 170.0)
        self.assertEqual(tr[2][0], 300)

    def test_parse_loginctl(self):
        s = parse_loginctl("IdleHint=no\nLockedHint=yes\nActive=yes\nType=wayland\n")
        self.assertEqual(s, {"idle_hint": False, "locked": True, "active": True, "type": "wayland"})

    def test_display_gpu_reads_connected_connectors(self):
        with tempfile.TemporaryDirectory() as root:
            for card, vendor, conns in (("card0", "0x8086", {"DP-1": "connected", "DP-2": "disconnected"}),
                                        ("card1", "0x10de", {"DP-6": "disconnected"})):
                os.makedirs(f"{root}/sys/class/drm/{card}/device")
                with open(f"{root}/sys/class/drm/{card}/device/vendor", "w") as f:
                    f.write(vendor + "\n")
                for c, st in conns.items():
                    os.makedirs(f"{root}/sys/class/drm/{card}-{c}")
                    with open(f"{root}/sys/class/drm/{card}-{c}/status", "w") as f:
                        f.write(st + "\n")
            self.assertEqual(display_gpu(root), {"card0": {"vendor": "0x8086", "connectors": ["DP-1"]}})


class TieringTest(unittest.TestCase):
    gpus = [(30.0, 70.0), (30.0, 50.0)]

    def test_default_points_flip_only_live_levers_within_2s(self):
        pts = oppoint.default_points(self.gpus)
        oppoint.validate(pts)
        for a, b in (("present", "away"), ("away", "present")):
            changes = oppoint.plan(pts[a], pts[b])
            self.assertTrue(changes)
            self.assertTrue(all(oppoint.LEVERS[n][0] == oppoint.LIVE for n, _ in changes))
            self.assertLessEqual(oppoint.critical_latency(changes), 2.0)

    def test_restart_bound_lever_cannot_differ(self):
        pts = oppoint.default_points(self.gpus)
        pts["away"]["ovms_max_num_batched_tokens"] = 8192
        with self.assertRaises(oppoint.TieringError):
            oppoint.validate(pts)
        with self.assertRaises(oppoint.TieringError):
            oppoint.plan(pts["present"], pts["away"])

    def test_commands_clamp_power_and_label(self):
        cmds = oppoint.commands([("gpu_power_w", [10, 99]), ("label", "away")],
                                p_cpus=[0], e_cpus=[16], gpus=self.gpus, node="n0", state="away")
        self.assertIn(("exec", ["nvidia-smi", "-i", "0", "-pl", "30.0"]), cmds)
        self.assertIn(("exec", ["nvidia-smi", "-i", "1", "-pl", "50.0"]), cmds)
        self.assertIn(("exec", ["kubectl", "label", "node", "n0", "k3d-gpu.io/attention=away", "--overwrite"]), cmds)


class ChunkTest(unittest.TestCase):
    def test_budget_hand_numbers(self):
        self.assertAlmostEqual(frame_budget_ms(60, 6.0, 1.0), 1000 / 60 - 7.0)
        # 9.667 ms / (3.75 ms/tok * 0.02) = 128.9 -> 128
        self.assertEqual(budget_chunk(1 / 3.75, 60, 6.0, slice_frac=0.02), 128)
        # 120 Hz: 1.333 ms / 0.075 = 17.8 -> 16
        self.assertEqual(budget_chunk(1 / 3.75, 120, 6.0, slice_frac=0.02), 16)
        # no budget left -> one block
        self.assertEqual(budget_chunk(1 / 3.75, 240, 6.0), 16)

    def test_prefix_plan_and_requests(self):
        self.assertEqual(prefix_plan(100, 32), [32, 64, 96, 100])
        with self.assertRaises(ValueError):
            prefix_plan(100, 30)
        text = "abcdefghij" * 10
        ends = list(range(1, 101))  # one char per token
        bodies = requests(text, ends, 32)
        self.assertEqual([len(b["prompt"]) for b in bodies], [32, 64, 96, 100])
        self.assertTrue(all(b["max_tokens"] == 1 for b in bodies[:-1]))
        self.assertTrue(bodies[-1]["stream"])


class FrameSimTest(unittest.TestCase):
    """Claim (2). These assert what the simulation found, so a model change that
    flips a verdict fails loudly."""

    def test_timeslice_regime_kills_the_claim(self):
        r = F.evaluate(F.Physics(), "timeslice", seconds=30)
        self.assertTrue(r["kill"])
        self.assertFalse(r["holds"])

    def test_kernel_regime_nominal_smooth_but_no_throughput_gain(self):
        r = F.evaluate(F.Physics(), "kernel", seconds=30)
        self.assertTrue(r["smooth_ok"])
        self.assertFalse(r["kill"])
        self.assertLess(r["away_vs_smooth"], 0.15)  # refuted leg at nominal UHD 770 physics

    def test_claim_can_hold_where_step_overhead_dominates(self):
        r = F.evaluate(replace(F.Physics(), t_tok_ms=0.5, w_ms=15.0, refresh_hz=120.0), "kernel", seconds=30)
        self.assertTrue(r["holds"], r)

    def test_wrong_refresh_budget_janks(self):
        ph = replace(F.Physics(), refresh_hz=120.0)
        bad = F.evaluate(ph, "kernel", cal=F.calib_from(ph, refresh_hz=60.0), seconds=30)
        self.assertFalse(bad["smooth_ok"])

    def test_server_reload_breaks_switch_back(self):
        self.assertFalse(F.evaluate(F.Physics(), "kernel", seconds=30, reload_s=10.0)["switch_ok"])

    def test_controller_reads_calibration_not_physics(self):
        ph = F.Physics()
        cal = F.calib_from(ph)
        self.assertEqual(F.present_chunk(cal, "kernel"), 128)
        self.assertEqual(F.present_chunk(replace(cal, t_tok_ms=7.5), "kernel"), 64)


class EnergySimTest(unittest.TestCase):
    """Claim (1)."""

    work = replace(E.Work(), tasks=16)

    def test_shuffled_signal_hurts_present_ttft_more_than_presence(self):
        work = E.Work()
        gated, shuffled = [], []
        for seed in (1, 2, 3, 4):
            h = lambda: E.Human(away_frac=0.5, mean_cycle_s=900, seed=seed)  # noqa: E731
            gated.append(E.evaluate(E.Node(), work, h(), dt=0.5)["ttft_vs_ref"])
            shuffled.append(E.evaluate(E.Node(), work, h(), dt=0.5, shuffle=True)["ttft_vs_ref"])
        self.assertGreater(sum(shuffled) / 4, 1.5 * sum(gated) / 4)

    def test_nominal_saving_vs_static_is_below_the_refutation_line(self):
        r = E.evaluate(E.Node(), self.work, E.Human(away_frac=0.5, mean_cycle_s=900, seed=1), dt=0.5)
        self.assertLess(r["saving_vs_static"], 0.10)

    def test_positive_control_sim_can_grant_large_savings(self):
        node = replace(E.Node(), alpha_decode=0.0, gpu_idle_w=(2.0, 2.0), cpu_idle_w=2.0, decode_dem=1.0)
        work = replace(E.Work(), agents=2, tasks=16, prompt_tokens=300, out_tokens=1500, tool_s=1.0)
        r = E.evaluate(node, work, E.Human(away_frac=0.8, mean_cycle_s=900, seed=3), dt=0.5)
        self.assertGreater(r["saving_vs_ref"], 0.25)

    def test_choose_away_uses_calibration_only(self):
        node = E.Node()
        lat = E.OpPoint()
        a = E.choose_away(replace(node, alpha_decode=0.0, alpha_prefill=0.0), self.work, lat)
        b = E.choose_away(replace(node, alpha_decode=2.0, alpha_prefill=2.0), self.work, lat)
        self.assertLess(a.cap, b.cap)


if __name__ == "__main__":
    random.seed(0)
    unittest.main()

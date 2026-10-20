#!/usr/bin/env python3
"""Unit tests for the crescent hold tools: serving.py's slot covariate, lockwait.py
(H56 stage 0) and h56.py (the H56 scorer). No docker, no host state."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SERVING_SLOT", None)
import serving  # noqa: E402


class ServingSlotTest(unittest.TestCase):
    def test_sibling_counted_foreign(self):
        mine = serving.mine_for(0)
        for name in ("k3d-k3dgpu-a-s0-a1-server-0", "k3d-k3dgpu-a-s0-a1-serverlb", "k3dgpu-a-s0-litellm",
                     "k3dgpu-a-s0-rapl", "k3d-gpu-bench-load-s0-cpu"):
            self.assertTrue(serving.is_mine(name, mine), name)
        for name in ("k3d-k3dgpu-a-s1-a1-server-0", "k3dgpu-a-s1-litellm", "k3d-gpu-bench-load-s1-stream",
                     "qwen38-a-hr", "k3d-k3dgpu-x-h34-server-0", "k3d-gpu-bench-load-cpu"):
            self.assertFalse(serving.is_mine(name, mine), name)

    def test_legacy_mine_unchanged(self):
        self.assertEqual(serving.MINE, ("k3dgpu", "k3d-gpu-bench-load"))

    def test_no_slot_is_legacy(self):
        self.assertEqual(serving.mine_for(None), serving.MINE)
        self.assertTrue(serving.is_mine("k3d-k3dgpu-a-s1-a1-server-0"))
        self.assertFalse(serving.is_mine("qwen38-a-hr"))

    def test_slot_fields(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(f"{tmp}/devices/system/cpu")
            with open(f"{tmp}/devices/system/cpu/online", "w") as f:
                f.write("0-3")
            for c, khz in ((0, 2000000), (1, 3000000)):
                os.makedirs(f"{tmp}/devices/system/cpu/cpu{c}/cpufreq")
                with open(f"{tmp}/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", "w") as f:
                    f.write(str(khz))
            s0 = "".join(f"cpu{c} 0 0 0 100 0 0 0 0\n" for c in range(4))
            s1 = ("cpu0 0 0 0 200 0 0 0 0\ncpu1 0 0 0 200 0 0 0 0\n"
                  "cpu2 100 0 0 100 0 0 0 0\ncpu3 100 0 0 100 0 0 0 0\n")
            r = serving.slot_fields(s0, s1, [0, 1], tmp)
            self.assertEqual(r, {"own_busy_pct": 0.0, "other_busy_pct": 100.0, "own_mhz": 2500.0})
        finally:
            shutil.rmtree(tmp)


class LockwaitTest(unittest.TestCase):
    def run_log(self, text, waits=()):
        import lockwait
        tmp = tempfile.mkdtemp()
        try:
            log = os.path.join(tmp, "campaign.log")
            with open(log, "w") as f:
                f.write(text)
            for name, w in waits:
                os.makedirs(os.path.join(tmp, "runs", name))
                with open(os.path.join(tmp, "runs", name, "wait_s"), "w") as f:
                    f.write(f"{w}\n")
            return lockwait.analyse(log, os.path.join(tmp, "runs"), last=20)
        finally:
            shutil.rmtree(tmp)

    def test_per_hold_waits_and_share(self):
        r = self.run_log(
            "== H50-hold1 waiting for the lock 2026-10-18T10:00:00Z\n"
            "== H50-hold1 lock taken 2026-10-18T10:00:00Z\n"
            "== H50-hold1 start: env X=1 bash live.sh 2026-10-18T10:02:00Z\n"
            "== H50-hold2 waiting for the lock 2026-10-18T10:10:00Z\n"
            "== H50-hold1 rc=0; clusters deleted: none; now:  2026-10-18T10:40:00Z\n"
            "== H50-hold2 lock taken 2026-10-18T10:40:00Z\n"
            "== H50-hold2 busy (load1 13, aiperf 0): waiting 120 s 2026-10-18T10:40:00Z\n"
            "== H50-hold2 rc=3; clusters deleted: k3dgpu-e-h50; now:  2026-10-18T11:20:00Z\n"
            "== H50-hold3 waiting for the lock 2026-10-18T12:00:00Z\n"
            "== H50-hold3 lock not taken in 7200 s 2026-10-18T14:00:00Z\n"
            "noise line\n", waits=(("H50-hold1", 0), ("H50-hold2", 120)))
        h = {x["name"]: x for x in r["holds"]}
        self.assertEqual((h["H50-hold1"]["lock_wait_s"], h["H50-hold1"]["hold_s"], h["H50-hold1"]["gate_wait_s"]),
                         (0, 2400, 0))
        self.assertEqual((h["H50-hold2"]["lock_wait_s"], h["H50-hold2"]["hold_s"], h["H50-hold2"]["gate_wait_s"]),
                         (1800, 2400, 120))
        self.assertEqual((h["H50-hold3"]["lock_wait_s"], h["H50-hold3"]["hold_s"]), (7200, 0))
        self.assertEqual(h["H50-hold2"]["rc"], "3")
        self.assertEqual(r["sum_lock_wait_s"], 9000)
        self.assertEqual(r["sum_gate_wait_s"], 120)
        self.assertEqual(r["sum_hold_s"], 4800)
        self.assertEqual(r["share"], 1.875)
        self.assertTrue(r["stage1"])
        self.assertTrue(r["adoption_share_met"])

    def test_serial_holds_stop_stage1(self):
        r = self.run_log("== a waiting for the lock 2026-10-18T10:00:00Z\n== a lock taken 2026-10-18T10:00:10Z\n"
                         "== a rc=0; x 2026-10-18T10:40:10Z\n")
        self.assertIsNone(r["holds"][0]["gate_wait_s"])
        self.assertLess(r["share"], 0.10)
        self.assertFalse(r["stage1"])
        self.assertFalse(r["adoption_share_met"])

    def test_last_n_window(self):
        text = ""
        for i in range(25):
            w = 600 if i < 5 else 0
            day, hr = 18 + i // 24, i % 24
            text += (f"== h{i} waiting for the lock 2026-10-{day}T{hr:02d}:00:00Z\n"
                     f"== h{i} lock taken 2026-10-{day}T{hr:02d}:{w // 60:02d}:00Z\n"
                     f"== h{i} rc=0; x 2026-10-{day}T{hr:02d}:50:00Z\n")
        r = self.run_log(text)
        self.assertGreater(r["share"], 0)
        self.assertEqual(r["share_last"], 0.0)
        self.assertEqual(r["last"], 20)


def rows(times, passed=True, t0=1000.0, gap=1.0):
    out, t = [], t0
    for i, d in enumerate(times):
        out.append({"task": f"B-T{i}", "passed": passed, "censored": False, "duration_s": d,
                    "t_start": t, "t_end": t + d, "cycle": 0})
        t += d + gap
    return out


class H56ScoreTest(unittest.TestCase):
    SLICE0, LOAD0, SLICE1, LOAD1 = "0-23,48-71", "12-23,60-71", "24-47,72-95", "36-47,84-95"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def jsonl(self, path, items):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.writelines(json.dumps(x) + "\n" for x in items)

    def text(self, path, s):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(s)

    def slice_dir(self, d, slot, results, watts=200.0, cpuset_ok=True, flagged=False, rc=0):
        node, ld = (self.SLICE0, self.LOAD0) if slot == 0 else (self.SLICE1, self.LOAD1)
        self.jsonl(f"{d}/run/results.jsonl", results)
        secs = 700.0
        self.jsonl(f"{d}/run/blocks.jsonl", [{"seconds": secs, "energy": {"energy_j": watts * secs}}])
        self.text(f"{d}/live.log", "== run budget 720\n")
        pod = node if cpuset_ok else "0-95"
        self.text(f"{d}/cpuset.txt",
                  f"expect_node {node}\nexpect_load {ld}\nnode k3d-n-server-0 {node}\n"
                  f"node_effective k3d-n-server-0 {node}\nlitellm l {node}\nrapl r {node}\n"
                  f"load c1 {ld}\nload c2 {ld}\nload c3 {ld}\npod_allowed ovms {pod}\npod_nproc ovms 48\n"
                  "load_end - 3\n")
        self.text(f"{d}/hold/rc", f"{rc}\n")
        self.text(f"{d}/hold/wait_s", "0\n")
        if flagged:
            self.text(f"{d}/hold/flagged", "own_busy 40\n")

    def build(self, w_scale=1.0, n_tasks=24, noise=None, pl1_uw=350000000, w_watts=200.0,
              uncovered=(), bad_cpuset=(), flagged=()):
        import h56
        for n, cond in enumerate(h56.ORDER, start=1):
            h = f"{self.tmp}/hold{n}"
            self.text(f"{h}/condition.txt", f"cond={cond} n={n}\n")
            self.text(f"{h}/pl1.txt", f"{pl1_uw}\n" if pl1_uw else "unreadable\n")
            base = [20.0 + (i % 5) for i in range(n_tasks)]
            if cond == "A":
                t = base
            else:
                t = [x * w_scale * (noise[i % len(noise)] if noise else 1.0) for i, x in enumerate(base)]
            self.slice_dir(f"{h}/s0", 0, rows(t, t0=2000.0), watts=w_watts if cond == "W" else 120.0,
                           cpuset_ok=n not in bad_cpuset, flagged=n in flagged)
            if cond == "W":
                start = 9000.0 if n in uncovered else 1000.0
                self.slice_dir(f"{h}/s1", 1, rows([3000.0], t0=start), watts=w_watts)
        return h56.score(self.tmp)

    def test_equal_supported(self):
        r = self.build()
        self.assertEqual(r["verdict"], "SUPPORTED", r)
        self.assertEqual(r["pairs"], 96)
        self.assertAlmostEqual(r["time_ratio"], 1.0)
        self.assertAlmostEqual(r["pkg_w_over_pl1"], 200 / 350, places=3)

    def test_slower_refuted(self):
        self.assertEqual(self.build(w_scale=1.3)["verdict"], "REFUTED")

    def test_noisy_inconclusive(self):
        r = self.build(noise=[0.5, 2.0, 0.6, 1.8, 1.0])
        self.assertEqual(r["verdict"], "INCONCLUSIVE", r)

    def test_uncovered_w_void(self):
        r = self.build(uncovered=(2,))
        self.assertIn("hold2", r["void"])
        self.assertTrue(any("not inside" in x for x in r["void"]["hold2"]))
        self.assertEqual(r["pairs"], 72)

    def test_power_near_limit_inconclusive(self):
        r = self.build(w_watts=340.0)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("power", r["reason"])

    def test_cpuset_mismatch_void(self):
        r = self.build(bad_cpuset=(1, 4, 5))
        self.assertEqual(sorted(r["void"]), ["hold1", "hold4", "hold5"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("VOID", r["reason"])

    def test_few_pairs_inconclusive(self):
        r = self.build(n_tasks=12)
        self.assertEqual(r["pairs"], 48)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_flagged_hold_replaced(self):
        import h56
        self.build(flagged=(3,))
        shutil.copytree(f"{self.tmp}/hold2", f"{self.tmp}/hold3-r1")
        self.text(f"{self.tmp}/hold3-r1/condition.txt", "cond=W n=3\n")
        r = h56.score(self.tmp)
        self.assertEqual(sorted(r["void"]), ["hold3"])
        self.assertEqual(r["used"]["3"], "hold3-r1")
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_unreadable_pl1_inconclusive(self):
        self.assertEqual(self.build(pl1_uw=0)["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()

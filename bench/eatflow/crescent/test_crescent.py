#!/usr/bin/env python3
"""Unit tests for the crescent hold tools: serving.py's slot covariate, lockwait.py
(H56 stage 0), h56.py (the H56 scorer) and h52b.sh with h52b.py (H52b's steps, run against
stubs). No docker, no host state."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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

    def test_unterminated_holds_counted(self):
        # a: lock taken, then killed (no rc= line); b: re-run under the same name after an
        # exit before any lock line; both count, up to the last line seen.
        r = self.run_log(
            "== a waiting for the lock 2026-10-18T10:00:00Z\n"
            "== a lock taken 2026-10-18T10:30:00Z\n"
            "== a start: x 2026-10-18T10:31:00Z\n"
            "== b waiting for the lock 2026-10-18T10:05:00Z\n"
            "== b waiting for the lock 2026-10-18T10:20:00Z\n"
            "== b lock taken 2026-10-18T10:40:00Z\n"
            "== b rc=0; x 2026-10-18T11:00:00Z\n")
        h = [(x["name"], x["lock_wait_s"], x["hold_s"], x["ended"]) for x in r["holds"]]
        self.assertEqual(h, [("a", 1800, 60, "unterminated"), ("b", 0, 0, "unterminated"),
                             ("b", 1200, 1200, "rc")])
        self.assertEqual(r["unterminated"], 2)
        self.assertEqual(r["sum_lock_wait_s"], 3000)

    def test_h56_holds_excluded(self):
        r = self.run_log(
            "== H56-hold1 waiting for slot-0, slot-1, main 2026-10-18T10:00:00Z\n"
            "== H56-hold1-s0 waiting for the lock 2026-10-18T10:01:00Z\n"
            "== H56-hold1-s0 lock taken 2026-10-18T10:01:00Z\n"
            "== H56-hold1-s0 rc=0; x 2026-10-18T10:40:00Z\n"
            "== H50-hold9 waiting for the lock 2026-10-18T10:00:00Z\n"
            "== H50-hold9 lock taken 2026-10-18T10:50:00Z\n"
            "== H50-hold9 rc=0; x 2026-10-18T11:30:00Z\n")
        self.assertEqual([x["name"] for x in r["holds"]], ["H50-hold9"])
        self.assertEqual(r["by_row"]["H50"]["holds"], 1)

    def test_wait_behind_x_campaign_not_addressable(self):
        import lockwait
        tmp = tempfile.mkdtemp()
        try:
            log, xlog = os.path.join(tmp, "campaign.log"), os.path.join(tmp, "x.log")
            with open(log, "w") as f:
                f.write("== H50-hold1 waiting for the lock 2026-10-18T10:00:00Z\n"
                        "== H50-hold1 lock taken 2026-10-18T10:40:00Z\n"
                        "== H50-hold1 rc=0; x 2026-10-18T11:20:00Z\n")
            with open(xlog, "w") as f:
                f.write("== H27x waiting for the lock 2026-10-18T09:50:00Z\n"
                        "== H27x lock taken 2026-10-18T10:05:00Z\n"
                        "== H27x rc=0; x 2026-10-18T10:35:00Z\n")
            r = lockwait.analyse(log, tmp, xlog=xlog)
        finally:
            shutil.rmtree(tmp)
        h = r["holds"][0]
        self.assertEqual((h["lock_wait_s"], h["wait_behind_x_s"]), (2400, 1800))
        self.assertEqual(r["share_all"], 1.0)
        self.assertEqual(r["share"], 0.25)
        self.assertTrue(r["xlog_found"])

    def test_amended_pool_counts_every_campaign_log(self):
        # Amendment S0: the pooled share is lock wait over hold time across every campaign
        # log, nothing netted out; stage1 follows it, the original statistic is kept beside it.
        import lockwait
        tmp = tempfile.mkdtemp()
        try:
            logs = [os.path.join(tmp, n) for n in ("bench.log", "x.log", "gemma.log")]
            texts = ["== H50-hold1 waiting for the lock 2026-10-18T10:00:00Z\n"
                     "== H50-hold1 lock taken 2026-10-18T10:40:00Z\n"
                     "== H50-hold1 rc=0; x 2026-10-18T11:20:00Z\n",
                     "== H27x waiting for the lock 2026-10-18T09:50:00Z\n"
                     "== H27x lock taken 2026-10-18T10:00:00Z\n"
                     "== H27x rc=0; x 2026-10-18T10:38:00Z\n",
                     "== H54smoke1-hold1 waiting for the lock (arms: B1g Lg) 2026-10-18T11:00:00Z\n"
                     "== H54smoke1-hold1 lock taken 2026-10-18T11:20:00Z\n"
                     "== H54smoke1-hold1 rc=0; x 2026-10-18T11:50:00Z\n"
                     "== H56-hold1 waiting for the lock 2026-10-18T12:00:00Z\n"
                     "== H56-hold1 lock taken 2026-10-18T13:00:00Z\n"
                     "== H56-hold1 rc=0 2026-10-18T13:10:00Z\n"]
            for path, text in zip(logs, texts):
                with open(path, "w") as f:
                    f.write(text)
            r = lockwait.analyse(logs[0], tmp, xlog=logs[1], pool=logs)
        finally:
            shutil.rmtree(tmp)
        # original: 2400 s wait, 2280 s of it behind x, over 2400 s held: 0.05, below the bar.
        # pooled: waits 2400 + 600 + 1200 s, holds 2400 + 2280 + 1800 s; H56-* not counted.
        self.assertEqual(r["original"]["share"], 0.05)
        self.assertFalse(r["original"]["stage1"])
        a = r["amended_s0"]
        self.assertEqual((a["holds"], a["sum_lock_wait_s"], a["sum_hold_s"]), (3, 4200, 6480))
        self.assertEqual(a["share"], 0.6481)
        self.assertTrue(r["stage1"])

    def test_without_pool_stage1_is_the_original(self):
        r = self.run_log("== H50-hold1 waiting for the lock 2026-10-18T10:00:00Z\n"
                         "== H50-hold1 lock taken 2026-10-18T10:00:05Z\n"
                         "== H50-hold1 rc=0; x 2026-10-18T11:00:00Z\n")
        self.assertFalse(r["stage1"])
        self.assertNotIn("amended_s0", r)


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

    def slice_dir(self, d, slot, results, watts=200.0, cpuset_ok=True, flagged=False, rc=0, mhz=3000.0, nproc=48):
        node, ld = (self.SLICE0, self.LOAD0) if slot == 0 else (self.SLICE1, self.LOAD1)
        self.jsonl(f"{d}/run/results.jsonl", results)
        t0, t1 = min(r["t_start"] for r in results), max(r["t_end"] for r in results)
        self.jsonl(f"{d}/hold/serving.jsonl", [{"ts": t, "own_mhz": mhz} for t in (t0 - 50, (t0 + t1) / 2, t1 + 50)])
        self.text(f"{d}/t0", f"{t0 - 300}\n")
        secs = 700.0
        self.jsonl(f"{d}/run/blocks.jsonl", [{"seconds": secs, "energy": {"energy_j": watts * secs}}])
        self.text(f"{d}/live.log", "== run budget 720\n")
        pod = node if cpuset_ok else "0-95"
        self.text(f"{d}/cpuset.txt",
                  f"expect_node {node}\nexpect_load {ld}\nnode k3d-n-server-0 {node}\n"
                  f"node_effective k3d-n-server-0 {node}\nlitellm l {node}\nrapl r {node}\n"
                  f"load c1 {ld}\nload c2 {ld}\nload c3 {ld}\npod_allowed ovms {pod}\npod_nproc ovms {nproc}\n"
                  "load_end - 3\n")
        self.text(f"{d}/hold/rc", f"{rc}\n")
        self.text(f"{d}/hold/wait_s", "0\n")
        if flagged:
            self.text(f"{d}/hold/flagged", "own_busy 40\n")

    def build(self, w_scale=1.0, n_tasks=24, noise=None, pl1_uw=350000000, w_watts=200.0,
              uncovered=(), bad_cpuset=(), flagged=(), scale_at=None, w_done=None, w_mhz=3000.0,
              bad_nproc=(), skip=()):
        import h56
        for n, cond in enumerate(h56.ORDER, start=1):
            if n in skip:
                continue
            h = f"{self.tmp}/hold{n}"
            self.text(f"{h}/condition.txt", f"cond={cond} n={n}\n")
            self.text(f"{h}/pl1.txt", f"{pl1_uw}\n" if pl1_uw else "unreadable\n")
            base = [20.0 + (i % 5) for i in range(n_tasks)]
            if cond == "A":
                t = base
            else:
                sc = (scale_at or {}).get(n, w_scale)
                t = [x * sc * (noise[i % len(noise)] if noise else 1.0) for i, x in enumerate(base)]
                t = t[:w_done] if w_done is not None else t
            self.slice_dir(f"{h}/s0", 0, rows(t, t0=2000.0), watts=w_watts if cond == "W" else 120.0,
                           cpuset_ok=n not in bad_cpuset, flagged=n in flagged,
                           mhz=w_mhz if cond == "W" else 3000.0, nproc=96 if n in bad_nproc else 48)
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
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("position 2 missing", r["reason"])

    def test_missing_position_inconclusive_not_shifted(self):
        # hold3 never written: no shifted A/W pairing, no verdict from 3 of 4 hold pairs.
        r = self.build(skip=(3,))
        self.assertEqual(r["verdict"], "INCONCLUSIVE", r)
        self.assertIn("position 3 missing", r["reason"])
        self.assertNotIn("3", r["used"])

    def test_pod_nproc_not_slice_void(self):
        r = self.build(bad_nproc=(1,))
        self.assertIn("hold1", r["void"])
        self.assertTrue(any("nproc" in x for x in r["void"]["hold1"]), r["void"])

    def test_uniform_7pct_slowdown_refuted(self):
        r = self.build(w_scale=1.07)
        self.assertEqual(r["verdict"], "REFUTED", r)

    def test_one_slow_hold_pair_widens_ci(self):
        # Interference in one of four hold pairs: a task-level bootstrap sees every task id
        # with the same four pairs and a CI of zero width; the hold-pair resample does not.
        r = self.build(scale_at={2: 1.2})
        self.assertEqual(r["verdict"], "INCONCLUSIVE", r)
        self.assertGreater(r["time_ci"][1], 1.09)
        self.assertLess(r["time_ci"][0], 1.01)

    def test_fewer_completions_refuted(self):
        r = self.build(w_done=18)
        self.assertEqual(r["completion_ratio"], 0.75)
        self.assertEqual(r["verdict"], "REFUTED", r)
        self.assertIn("completion", r["reason"])

    def test_mhz_drop_inconclusive(self):
        r = self.build(w_mhz=2700.0)
        self.assertEqual(r["verdict"], "INCONCLUSIVE", r)
        self.assertIn("MHz", r["reason"])
        self.assertAlmostEqual(r["mhz_w_over_a"], 0.9)

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


class H56GateTest(unittest.TestCase):
    """h56.py gate: stage 0 and the smoke's feasibility, read by h56.sh before any lock."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def lockwait(self, stage1):
        with open(f"{self.tmp}/lockwait.json", "w") as f:
            json.dump({"stage1": stage1, "share": 0.2 if stage1 else 0.01}, f)

    def smoke(self, done, setup_s):
        d = f"{self.tmp}/smoke-W/s0"
        os.makedirs(f"{d}/run")
        with open(f"{d}/t0", "w") as f:
            f.write("1000\n")
        with open(f"{d}/run/results.jsonl", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows([20.0] * done, t0=1000.0 + setup_s))

    def gate(self, smoke=False):
        import h56
        return h56.gate(self.tmp, smoke=smoke)

    def test_no_stage0_refused(self):
        self.assertFalse(self.gate(smoke=True)["ok"])
        self.assertFalse(self.gate()["ok"])

    def test_stage0_below_share_refused(self):
        self.lockwait(False)
        self.assertFalse(self.gate(smoke=True)["ok"])

    def test_smoke_allowed_after_stage0(self):
        self.lockwait(True)
        self.assertTrue(self.gate(smoke=True)["ok"])
        self.assertFalse(self.gate()["ok"])

    def test_feasible_smoke_opens_stage1(self):
        self.lockwait(True)
        self.smoke(done=3, setup_s=300)
        self.assertTrue(self.gate()["ok"])

    def test_slow_setup_refused(self):
        self.lockwait(True)
        self.smoke(done=3, setup_s=1000)
        r = self.gate()
        self.assertFalse(r["ok"])
        self.assertIn("setup", " ".join(r["reasons"]))

    def test_low_yield_refused(self):
        self.lockwait(True)
        self.smoke(done=2, setup_s=300)
        self.assertFalse(self.gate()["ok"])


ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
PLAN = os.path.join(ROOT, "bench/eatflow/results/2026-10-29/H52b-plan-precheck/plan.txt")
H52B_REVERTS = [
    "sudo accel-config disable-wq dsa0/wq0.0 && sudo accel-config disable-device dsa0",
    "sudo accel-config disable-wq dsa2/wq2.0 && sudo accel-config disable-device dsa2",
    "sudo accel-config disable-wq dsa4/wq4.0 && sudo accel-config disable-device dsa4",
    "echo 0 | sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs",
    "echo 0 | sudo tee /sys/bus/pci/devices/0000:06:00.0/sriov_numvfs",
    "echo 0 | sudo tee /sys/bus/pci/devices/0000:0b:00.0/sriov_numvfs",
    "sudo modprobe -r vfio-pci   # skip if VMs or other devices use vfio-pci",
]
# What the stub sudo logs for those reverts, in order.
H52B_SUDO = [
    "sudo accel-config disable-wq dsa0/wq0.0", "sudo accel-config disable-device dsa0",
    "sudo accel-config disable-wq dsa2/wq2.0", "sudo accel-config disable-device dsa2",
    "sudo accel-config disable-wq dsa4/wq4.0", "sudo accel-config disable-device dsa4",
    "sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs",
    "sudo tee /sys/bus/pci/devices/0000:06:00.0/sriov_numvfs",
    "sudo tee /sys/bus/pci/devices/0000:0b:00.0/sriov_numvfs",
    "sudo modprobe -r vfio-pci",
]


class H52bHelperTest(unittest.TestCase):
    """h52b.py on the plan crescent printed on 2026-10-29 (amendment D2 item 2)."""

    @classmethod
    def setUpClass(cls):
        import h52b
        cls.h = h52b
        cls.lines = h52b.read(PLAN)

    def test_crescent_plan_passes(self):
        ok, report = self.h.gate(self.lines)
        self.assertTrue(ok, report)
        self.assertEqual(report[-1], "gate: pass (8 commands)")
        self.assertEqual(sum(r.startswith("ok: ") for r in report), 8)

    def test_reverts_in_order(self):
        self.assertEqual(self.h.reverts(self.lines), H52B_REVERTS)

    def test_reverts_first_block_of_an_apply(self):
        apply = self.lines + ["Run these 8 commands now? [y/N] ==> + sudo modprobe vfio-pci",
                              "  DSA/IAA/QAT changes last until reboot. To undo them sooner:",
                              "    revert: sudo something-else"]
        self.assertEqual(self.h.reverts(apply), H52B_REVERTS)

    def test_ansi_colour_ignored(self):
        lines = ["\x1b[1;34m==>\x1b[0m Install plan (1 commands):", "    sudo modprobe vfio-pci", "  end"]
        self.assertTrue(self.h.gate(self.h.clean(lines))[0])

    def swap(self, old, new):
        return [new if line.strip() == old else line for line in self.lines]

    def test_other_commands_stop(self):
        shrink = ("sudo accel-config disable-device iax1 && sudo accel-config load-config -c "
                  "/var/lib/k3d-gpu/idxd-iax1.conf && sudo accel-config config-wq iax1/wq1.0 --wq-size=240")
        for bad in ("    sudo apt-get update && sudo apt-get install -y accel-config intel-gpu-tools",
                    "    sudo modprobe vfio-pci && sudo rm -rf /var/lib/x",
                    "    " + shrink,
                    "    sudo accel-config config-wq dsa0/wq0.0 --type=user; reboot",
                    "    echo 16 | sudo tee /sys/bus/pci/devices/0000:01:00.0/remove"):
            ok, report = self.h.gate(self.swap("sudo modprobe vfio-pci", bad))
            self.assertFalse(ok, bad)
            self.assertEqual(report[-1], "gate: STOP (8 commands)", bad)
            self.assertIn("NOT ALLOWED: " + bad.strip(), report)

    def test_count_mismatch_stops(self):
        lines = [line.replace("(8 commands)", "(9 commands)") for line in self.lines]
        ok, report = self.h.gate(lines)
        self.assertFalse(ok)
        self.assertIn("the header names 9 commands, 8 were read", report)

    def test_no_plan_stops(self):
        ok, report = self.h.gate(["==> Every check passed: nothing to install."])
        self.assertFalse(ok)
        self.assertEqual(report, ["no install plan in the output: nothing to apply"])


STUB_K3DGPU = """#!/usr/bin/env bash
echo "k3d-gpu $*" >> "${STUBLOG}"
cat "${STUBPLAN}"
case " $* " in *" --apply "*)
    printf 'Run these 8 commands now? [y/N] '
    read -r ans; echo "answer=${ans}" >> "${STUBLOG}" ;;
esac
"""
STUB_H52 = """#!/usr/bin/env bash
echo "h52 $*" >> "${STUBLOG}"
sleep "${STUBSLEEP:-0}"
"""
STUB_INV = """#!/usr/bin/env bash
echo "inventory" >> "${STUBLOG}"
echo "# date $(date +%s%N)"; echo "dsa0 state=disabled"
"""
STUB_SUDO = """#!/usr/bin/env bash
echo "sudo $*" >> "${STUBLOG}"
[ "$1" != tee ] || cat > /dev/null
"""
STUB_K3D = """#!/usr/bin/env bash
echo "k3d $*" >> "${STUBLOG}"
[ "$1 $2" != "cluster list" ] || cat "${STUBCLUSTERS}"
"""


class H52bRunnerTest(unittest.TestCase):
    """h52b.sh's order, its stop at the plan and its reverts on a signal, against stubs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.bin = os.path.join(self.d, "k3dgpu-bench", "bin")
        os.makedirs(self.bin)
        stubs = {"sudo": STUB_SUDO, "k3d": STUB_K3D, "dpkg-query": "#!/bin/sh\necho stub\n",
                 "lsmod": "#!/bin/sh\necho stub\n", "k3d-gpu": STUB_K3DGPU, "h52.sh": STUB_H52,
                 "inventory.sh": STUB_INV}
        for name, text in stubs.items():
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, 0o755)
        self.log = os.path.join(self.d, "log")
        self.out = os.path.join(self.d, "out")
        self.plan = os.path.join(self.d, "plan.txt")
        shutil.copy(PLAN, self.plan)
        self.clusters = os.path.join(self.d, "clusters.json")
        with open(self.clusters, "w") as f:
            f.write("[]")
        self.env = dict(os.environ, HOME=self.d, STUBLOG=self.log, STUBPLAN=self.plan,
                        STUBCLUSTERS=self.clusters, H52B_K3DGPU=os.path.join(self.bin, "k3d-gpu"),
                        H52B_H52=os.path.join(self.bin, "h52.sh"),
                        H52B_INVENTORY=os.path.join(self.bin, "inventory.sh"))

    def tearDown(self):
        shutil.rmtree(self.d)

    def start(self, **env):
        return subprocess.Popen(["bash", os.path.join(HERE, "h52b.sh"), self.out], env=dict(self.env, **env),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    NEEDRESTART = "sudo -n timeout 120 needrestart -b -r l"

    def logged(self):
        """The stub log without the needrestart reads (test_needrestart_read_twice counts them)."""
        return [x for x in self.raw() if x != self.NEEDRESTART]

    def raw(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    @staticmethod
    def sudo_after_list(log):
        return [x for x in log[log.index("k3d cluster list -o json"):] if x.startswith("sudo ")]

    def test_order(self):
        self.assertEqual(self.start().wait(60), 0)
        log = self.logged()
        self.assertEqual(log[:6], ["inventory", "k3d-gpu host-setup --plan --with-kernel-wqs",
                                   "k3d-gpu host-setup --apply --with-kernel-wqs", "answer=y",
                                   f"h52 {self.out}/h52b k3dgpu-h52b", "k3d cluster list -o json"])
        self.assertEqual(self.sudo_after_list(log), H52B_SUDO)
        self.assertEqual(log[-1], "inventory")
        self.assertNotIn("k3d cluster delete k3dgpu-h52b", log)
        with open(os.path.join(self.out, "inventory-diff.txt")) as f:
            self.assertEqual(f.read(), "")
        with open(os.path.join(self.out, "reverts.txt")) as f:
            self.assertEqual(f.read().splitlines(), H52B_REVERTS)
        with open(os.path.join(self.out, "plan-gate.txt")) as f:
            self.assertEqual(f.read().splitlines()[-1], "gate: pass (8 commands)")

    def test_needrestart_read_twice(self):
        self.assertEqual(self.start().wait(60), 0)
        raw = self.raw()
        self.assertEqual(raw.count(self.NEEDRESTART), 2)
        self.assertLess(raw.index(self.NEEDRESTART), raw.index("k3d-gpu host-setup --plan --with-kernel-wqs"))
        self.assertEqual(raw[-1], self.NEEDRESTART)

    def test_plan_gate_stops_before_apply(self):
        with open(self.plan) as f:
            text = f.read().replace("    sudo modprobe vfio-pci\n", "    sudo modprobe vfio-pci && sudo reboot\n")
        with open(self.plan, "w") as f:
            f.write(text)
        self.assertEqual(self.start().wait(60), 3)
        self.assertEqual(self.logged(), ["inventory", "k3d-gpu host-setup --plan --with-kernel-wqs"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "reverts.txt")))

    def test_signal_deletes_cluster_then_reverts(self):
        with open(self.clusters, "w") as f:
            f.write('[{"name": "k3dgpu-h52b"}]')
        p = self.start(STUBSLEEP="30")
        deadline = time.time() + 30
        while time.time() < deadline and not any(x.startswith("h52 ") for x in self.logged()):
            time.sleep(0.1)
        time.sleep(0.3)
        os.killpg(p.pid, signal.SIGTERM)
        self.assertEqual(p.wait(60), 143)
        log = self.logged()
        self.assertLess(log.index("k3d cluster delete k3dgpu-h52b"), log.index(H52B_SUDO[0]))
        self.assertEqual(self.sudo_after_list(log), H52B_SUDO)
        self.assertEqual(log[-1], "inventory")
        with open(os.path.join(self.out, "steps.txt")) as f:
            self.assertIn("signal 15", f.read())


if __name__ == "__main__":
    unittest.main()

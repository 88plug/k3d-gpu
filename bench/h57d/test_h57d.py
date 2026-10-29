#!/usr/bin/env python3
"""Tests for bench/h57d (EXPERIMENTS.md "H57d amendment R1", "H57dp amendment R1" and "R2"): the
arm table and renders, Od's read-only transform, the P-core sampler's arithmetic, the load gate,
every H57da, H57dm, H57dp and H57de verdict branch on synthetic hold records, the hold.sh guards
and the Reproduce header. No cluster, no network."""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h57d  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


class Arms(unittest.TestCase):
    def test_llamacpp_flags(self):
        a = h57d.llamacpp_args(False)
        self.assertEqual(a[a.index("-t") + 1], "8")
        self.assertEqual(a[a.index("-C") + 1], "0x5555")
        self.assertEqual(a[a.index("--cpu-strict") + 1], "1")
        self.assertIn("--jinja", a)
        self.assertNotIn("--spec-type", a)
        m = h57d.llamacpp_args(True)
        self.assertEqual(m[m.index("--spec-type") + 1], "draft-mtp")
        self.assertTrue(m[m.index("-md") + 1].endswith("qat-assistant-q4_0.gguf"))
        self.assertNotIn("--spec-draft-n-max", m)   # the default draft length

    def test_mask_is_one_thread_per_physical_pcore(self):
        cpus = [i for i in range(32) if int(h57d.MASK, 16) >> i & 1]
        self.assertEqual(cpus, list(range(0, 16, 2)))

    def test_orders_rotate(self):
        o = h57d.ORDERS
        for k in (1, 2, 3, 4):
            self.assertEqual(sorted(o[k]), sorted(h57d.H57D_ARMS))
        self.assertEqual({o[k][0] for k in (1, 2, 3)}, set(h57d.H57D_ARMS))   # each arm first once
        self.assertEqual({o[k][2] for k in (1, 2, 3)}, set(h57d.H57D_ARMS))   # and last once

    @unittest.skipUnless(HAVE_YAML, "PyYAML")
    def test_render_pcores(self):
        for arm in ("Ld", "Ld-mtp"):
            ns, dep, svc = h57d.render(arm)
            t = dep["spec"]["template"]
            self.assertEqual(t["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
            c = t["spec"]["containers"][0]
            self.assertEqual(c["resources"]["requests"]["cpu"], "16")
            self.assertTrue(c["volumeMounts"][0]["readOnly"])
            self.assertEqual(t["spec"]["volumes"][0]["hostPath"]["path"], "/var/apb-models")
            self.assertEqual(c["image"], h57d.LLAMACPP)
            self.assertEqual(svc["metadata"]["name"], h57d.ARMS[arm]["svc"])
        with self.assertRaises(SystemExit):
            h57d.render("Od")

    def test_od_readonly(self):
        docs = [{"kind": "Namespace", "metadata": {"name": "k3d-gpu-agent"}},
                {"kind": "Deployment", "spec": {"template": {"metadata": {"labels": {"app": "ovms", "k3d-gpu.io/cores": "performance"}},
                 "spec": {"initContainers": [{"name": "pull"}],
                          "containers": [{"name": "ovms", "image": "img", "args": ["--rest_port=8000", "--model_path=/models/x", "--cache_dir=/models/.ov_cache"],
                                          "resources": {"requests": {"cpu": "16"}},
                                          "volumeMounts": [{"name": "models", "mountPath": "/models"}]}],
                          "volumes": [{"name": "models", "hostPath": {"path": "/var/apb-models"}}]}}}}]
        out = h57d.od_readonly(docs)
        ps = out[1]["spec"]["template"]["spec"]
        self.assertNotIn("initContainers", ps)
        c = ps["containers"][0]
        self.assertEqual(c["args"], ["--rest_port=8000", "--model_path=/models/x", "--cache_dir=/ov-cache"])
        self.assertTrue([m for m in c["volumeMounts"] if m["name"] == "models"][0]["readOnly"])
        self.assertIn({"name": "ov-cache", "emptyDir": {}}, ps["volumes"])
        self.assertEqual(c["resources"], {"requests": {"cpu": "16"}})
        self.assertEqual(out[1]["spec"]["template"]["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
        self.assertIn("initContainers", docs[1]["spec"]["template"]["spec"])   # input untouched

    def test_cli_arms(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "field", "Od", "model"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), h57d.OVB)


class Sampler(unittest.TestCase):
    def test_cpu_ticks(self):
        stat = "cpu  9 9 9 9 9 9 9 9 0 0\ncpu0 10 0 5 80 5 0 0 0 0 0\ncpu1 20 0 0 70 10 0 0 0 0 0\ncpu16 50 0 0 50 0 0 0 0 0 0\n"
        busy, total = h57d.cpu_ticks(stat, [0, 1])
        self.assertEqual((busy, total), (35, 200))

    def test_foreign(self):
        s = [{"ts": t, "seconds": 5, "pcore_busy": 10.0, "node_cpu": 7.0} for t in range(5, 60, 5)]
        self.assertAlmostEqual(h57d.foreign(s, 10, 30), 3.0)
        self.assertIsNone(h57d.foreign(s, 100, 120))
        s2 = [{"ts": 5, "seconds": 5, "pcore_busy": 2.0, "node_cpu": 7.0}]
        self.assertEqual(h57d.foreign(s2, 0, 5), 0.0)   # never below 0

    def test_load_gate(self):
        self.assertTrue(h57d.load_gate({"a": 3.0, "b": 4.5, "c": 4.9})["matched"])    # spread 1.9 <= 2.0
        self.assertFalse(h57d.load_gate({"a": 3.0, "b": 5.5})["matched"])             # 2.5 > 2.0
        self.assertTrue(h57d.load_gate({"a": 10.0, "b": 12.5})["matched"])            # 2.5 <= 25% of 11.25
        self.assertFalse(h57d.load_gate({"a": 3.0})["matched"])


def write_holds(root, decode, quality, foreign=None, missing=()):
    """decode: {arm: tok/s centre}; quality: {arm: correct of 50}; foreign: {arm: CPU-s/s}."""
    rng = random.Random(7)
    foreign = foreign or {a: 3.0 for a in decode}
    for k in (1, 2, 3, 4):
        hd = os.path.join(root, f"hold{k}")
        os.makedirs(hd)
        t = 1000.0 * k
        samples = []
        for arm in h57d.ORDERS[k]:
            ad = os.path.join(hd, arm)
            os.makedirs(ad)
            if (k, arm) in missing:
                json.dump({"status": "not_deliverable"}, open(os.path.join(ad, "status.json"), "w"))
                continue
            json.dump({"status": "measured"}, open(os.path.join(ad, "status.json"), "w"))
            if k == 4:
                json.dump({"correct": quality[arm], "n": 50, "score": quality[arm] / 50},
                          open(os.path.join(ad, "quality.json"), "w"))
                continue
            s = [{"ok": True, "decode_tokens_per_s": decode[arm] * (1 + rng.uniform(-0.03, 0.03))} for _ in range(8)]
            json.dump({"phases": [{"samples": s}]}, open(os.path.join(ad, "load-n1.json"), "w"))
            with open(os.path.join(ad, "phases.jsonl"), "w") as f:
                f.write(json.dumps({"phase": "n1", "t0": t, "t1": t + 100}) + "\n")
            for ts in range(int(t) + 5, int(t) + 101, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 8.0 + foreign[arm], "node_cpu": 8.0})
            t += 200
        with open(os.path.join(hd, "load.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


class Score(unittest.TestCase):
    Q = {"Od": 44, "Ld": 45, "Ld-mtp": 45}

    def run_score(self, decode, quality=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, decode, quality or self.Q, **kw)
            return h57d.score(d)

    def test_refuted_llamacpp_faster(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0})
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")
        self.assertEqual(r["H57da"]["x"], "Ld")
        self.assertGreater(r["H57da"]["ci"][0], 1.05)
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")   # 14/15, CI high < 1.20

    def test_supported_od_fastest(self):
        r = self.run_score({"Od": 15.0, "Ld": 12.0, "Ld-mtp": 11.0})
        self.assertEqual(r["H57da"]["verdict"], "SUPPORTED")

    def test_inconclusive_near_threshold(self):
        r = self.run_score({"Od": 10.0, "Ld": 10.5, "Ld-mtp": 9.0})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")

    def test_od_fails_gate(self):
        r = self.run_score({"Od": 20.0, "Ld": 10.0, "Ld-mtp": 10.0}, quality={"Od": 30, "Ld": 45, "Ld-mtp": 45})
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")
        self.assertIn("gate", r["H57da"]["reason"])

    def test_gate_is_best_minus_five_items(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, quality={"Od": 40, "Ld": 45, "Ld-mtp": 39})
        self.assertTrue(r["gate"]["Od"])          # 40 >= 45 - 5
        self.assertFalse(r["gate"]["Ld-mtp"])     # 39 < 40
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")
        self.assertIn("gate", r["H57dm"]["reason"])

    def test_mtp_supported(self):
        r = self.run_score({"Od": 10.0, "Ld": 12.0, "Ld-mtp": 16.0})
        self.assertEqual(r["H57dm"]["verdict"], "SUPPORTED")
        self.assertEqual(r["H57da"]["x"], "Ld-mtp")

    def test_mtp_inconclusive(self):
        # 1.33x in holds 1 and 2, slower in hold 3: pooled median >= 1.20, not faster in every hold.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 12.0, "Ld-mtp": 16.0}, self.Q)
            p = os.path.join(d, "hold3", "Ld-mtp", "load-n1.json")
            j = json.load(open(p))
            for s in j["phases"][0]["samples"]:
                s["decode_tokens_per_s"] = 11.0
            json.dump(j, open(p, "w"))
            r = h57d.score(d)
        self.assertGreaterEqual(r["H57dm"]["ci"][1], 1.20)
        self.assertFalse(r["H57dm"]["all_holds_faster"])
        self.assertEqual(r["H57dm"]["verdict"], "INCONCLUSIVE")

    def test_od_not_delivered(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, missing={(2, "Od")})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")

    def test_load_unmatched(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, foreign={"Od": 1.0, "Ld": 5.0, "Ld-mtp": 1.0})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", r["H57da"]["reason"])
        self.assertEqual(r["H57dm"]["verdict"], "INCONCLUSIVE")

    def test_spare_hold_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, self.Q, missing={(2, "Od")})
            spare = os.path.join(d, "hold5")
            src = os.path.join(d, "hold1")
            subprocess.run(["cp", "-r", src, spare], check=True)
            open(os.path.join(spare, "repeats"), "w").write("2\n")
            r = h57d.score(d)
        self.assertEqual(r["arms"]["Od"]["holds_measured"], 3)
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")

    def test_not_faster_in_every_hold(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, self.Q)
            # hold 3: Ld slower than Od
            p = os.path.join(d, "hold3", "Ld", "load-n1.json")
            j = json.load(open(p))
            for s in j["phases"][0]["samples"]:
                s["decode_tokens_per_s"] = 8.0
            json.dump(j, open(p, "w"))
            r = h57d.score(d)
        self.assertFalse(r["H57da"]["all_holds_faster"])
        self.assertNotEqual(r["H57da"]["verdict"], "REFUTED")

    def test_committed_h57d_verdict_unchanged(self):
        # The scorer's H57d path must still give the committed H57d run's verdict.json, as
        # `h57d.py score` writes it (JSON, so the per-hold keys are strings).
        res = os.path.join(HERE, "results", "2026-10-21")
        self.assertEqual(json.loads(json.dumps(h57d.score(res))), json.load(open(os.path.join(res, "verdict.json"))))


# ---- H57dp amendment R1: the unpinned arm, the row's orders, the two-hold scorer -------------

PIN = ["-C", "0x5555", "--cpu-strict", "1"]


class Unpinned(unittest.TestCase):
    def test_unpinned_drops_only_the_pinning_flags(self):
        pinned = h57d.llamacpp_args(True)
        u = h57d.llamacpp_args(True, pinned=False)
        for f in ("-C", "--cpu-strict", "--cpu-range", "-Cr"):
            self.assertNotIn(f, u)
        i = pinned.index("-C")
        self.assertEqual(pinned[i:i + 4], PIN)
        self.assertEqual(u, pinned[:i] + pinned[i + 4:])   # every other flag identical, same order
        self.assertEqual(u[u.index("-t") + 1], "8")
        self.assertEqual(u[u.index("--spec-type") + 1], "draft-mtp")
        self.assertEqual(h57d.llamacpp_args(True, pinned=True), pinned)

    def test_arm_table(self):
        a = h57d.ARMS["Ld-mtp-u"]
        self.assertEqual(a["svc"], "ld-mtp-u")
        self.assertEqual({k: v for k, v in a.items() if k != "svc"},
                         {k: v for k, v in h57d.ARMS["Ld-mtp"].items() if k != "svc"})
        self.assertNotIn("Ld-mtp-u", h57d.H57D_ARMS)   # H57d's rows never see it
        r = subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "field", "Ld-mtp-u", "svc"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "ld-mtp-u")

    @unittest.skipUnless(HAVE_YAML, "PyYAML")
    def test_render_matches_ld_mtp_but_affinity(self):
        u, m = h57d.render("Ld-mtp-u"), h57d.render("Ld-mtp")
        cu = u[1]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(cu["args"], h57d.llamacpp_args(True, pinned=False))
        # Ld-mtp's render with its name and the four pinning flags swapped is Ld-mtp-u's render.
        s = json.dumps(m).replace('"ld-mtp"', '"ld-mtp-u"').replace(', "-C", "0x5555", "--cpu-strict", "1"', "")
        self.assertEqual(json.loads(s), u)

    def test_cli_arms_lists_unpinned_flags(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "arms"], capture_output=True, text=True)
        j = json.loads(r.stdout)
        self.assertEqual(j["args"]["Ld-mtp-u"], h57d.llamacpp_args(True, pinned=False))
        self.assertEqual(j["orders"], {str(k): v for k, v in h57d.ORDERS.items()})


class RowOrders(unittest.TestCase):
    def test_h57dp_orders_as_registered(self):
        self.assertEqual(h57d.P_ORDERS, {1: ["Ld-mtp-u", "Od", "Ld-mtp"], 2: ["Ld-mtp", "Ld-mtp-u", "Od"]})
        self.assertEqual(sorted(h57d.P_ARMS), ["Ld-mtp", "Ld-mtp-u", "Od"])
        for k in (1, 2):
            self.assertEqual(sorted(h57d.P_ORDERS[k]), sorted(h57d.P_ARMS))

    def test_orders_by_row(self):
        self.assertIs(h57d.orders("H57dp"), h57d.P_ORDERS)
        self.assertIs(h57d.orders("H57d"), h57d.ORDERS)
        self.assertIs(h57d.orders("H57w"), h57d.ORDERS)   # wildct's replication keeps H57d's

    def run_order(self, row, hold):
        return subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "order", row, hold],
                              capture_output=True, text=True)

    def test_cli_order(self):
        self.assertEqual(self.run_order("H57dp", "1").stdout.strip(), "Ld-mtp-u Od Ld-mtp")
        self.assertEqual(self.run_order("H57dp", "2").stdout.strip(), "Ld-mtp Ld-mtp-u Od")
        self.assertEqual(self.run_order("H57d", "3").stdout.strip(), "Ld-mtp Od Ld")
        self.assertEqual(self.run_order("H57w", "4").stdout.strip(), "Ld Od Ld-mtp")
        for row, hold in (("H57dp", "3"), ("H57d", "smoke1")):
            r = self.run_order(row, hold)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")


class HoldGuards(unittest.TestCase):
    """hold.sh refuses an unregistered hold before it takes the lock or writes anything. LOCK
    points into a missing directory, so a hold.sh that got past the guards fails fast (exit 4)
    instead of waiting on a real lock."""

    def run_hold(self, row, hold, mode):
        with tempfile.TemporaryDirectory() as g:
            env = dict(os.environ, G=g, LOCK=os.path.join(g, "missing", "live.lock"), H57D_ROW=row)
            r = subprocess.run(["bash", os.path.join(HERE, "hold.sh"), hold, mode], env=env,
                               capture_output=True, text=True, timeout=60)
            return r, os.listdir(g)

    def test_h57dp_refuses_quality_and_third_hold(self):
        for hold, mode in (("1", "quality"), ("3", "perf")):
            r, made = self.run_hold("H57dp", hold, mode)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual(made, [])

    def test_h57d_unregistered_hold(self):
        r, made = self.run_hold("H57d", "9", "perf")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(made, [])


PINNED_CPUS = """cpuset 0-15
Cpus_allowed_list:\t0-15
avx512f 0 amx 0 nproc 16
server threads by affinity:
      2 0
     34 0-15
      1 10
      1 12
      1 14
      1 2
      1 4
      1 6
      1 8
"""
FLOATING_CPUS = """cpuset 0-15
Cpus_allowed_list:\t0-15
avx512f 0 amx 0 nproc 16
server threads by affinity:
     42 0-15
"""


def write_p_holds(root, decode, foreign=None, missing=(), cpus=None):
    """The two H57dp holds. decode: {arm: tok/s centre, or (hold 1, hold 2)}; foreign: {arm: CPU-s/s};
    cpus: {(hold, arm): cpus.txt text, or None for no file} over the default (Ld-mtp pinned as
    recorded in H57d, the others floating)."""
    rng = random.Random(11)
    cpus = cpus or {}
    foreign = foreign or {a: 3.0 for a in h57d.P_ARMS}
    for k in (1, 2):
        hd = os.path.join(root, f"hold{k}")
        os.makedirs(hd)
        t = 1000.0 * k
        samples = []
        for arm in h57d.P_ORDERS[k]:
            ad = os.path.join(hd, arm)
            os.makedirs(ad)
            if (k, arm) in missing:
                json.dump({"status": "not_deliverable"}, open(os.path.join(ad, "status.json"), "w"))
                continue
            json.dump({"status": "measured"}, open(os.path.join(ad, "status.json"), "w"))
            ct = cpus.get((k, arm), PINNED_CPUS if arm == "Ld-mtp" else FLOATING_CPUS)
            if ct is not None:
                open(os.path.join(ad, "cpus.txt"), "w").write(ct)
            c = decode[arm][k - 1] if isinstance(decode[arm], tuple) else decode[arm]
            s = [{"ok": True, "decode_tokens_per_s": c * (1 + rng.uniform(-0.03, 0.03))} for _ in range(8)]
            json.dump({"phases": [{"samples": s}]}, open(os.path.join(ad, "load-n1.json"), "w"))
            with open(os.path.join(ad, "phases.jsonl"), "w") as f:
                f.write(json.dumps({"phase": "n1", "t0": t, "t1": t + 100}) + "\n")
            for ts in range(int(t) + 5, int(t) + 101, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 8.0 + foreign[arm], "node_cpu": 8.0})
            t += 200
        with open(os.path.join(hd, "load.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


class ScoreP(unittest.TestCase):
    def run_score(self, decode, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_p_holds(d, decode, **kw)
            return h57d.score_p(d)

    def test_unpinned_faster_than_both(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0})
        self.assertEqual(r["H57dp"]["verdict"], "SUPPORTED")
        self.assertAlmostEqual(r["H57dp"]["ratio"], 1.6, delta=0.1)
        self.assertEqual(sorted(r["H57dp"]["per_hold"]), [1, 2])
        self.assertEqual(r["H57de"]["verdict"], "REFUTED")
        self.assertEqual(r["H57de"]["x"], "Ld-mtp-u")
        self.assertTrue(r["load_gate"]["matched"])

    def test_pinning_does_not_matter_od_fastest(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 5.0, "Od": 7.0})
        self.assertEqual(r["H57dp"]["verdict"], "REFUTED")     # CI upper bound < 1.10
        self.assertLess(r["H57dp"]["ci"][1], 1.10)
        self.assertEqual(r["H57de"]["verdict"], "SUPPORTED")   # CI upper bound < 1.05
        self.assertLess(r["H57de"]["ci"][1], 1.05)

    def test_near_thresholds_inconclusive(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 5.4, "Od": 5.2})
        dp, de = r["H57dp"], r["H57de"]
        self.assertLess(dp["ratio"], 1.10)          # not SUPPORTED
        self.assertGreaterEqual(dp["ci"][1], 1.10)  # not REFUTED
        self.assertEqual(dp["verdict"], "INCONCLUSIVE")
        self.assertLess(de["ci"][0], 1.05)          # not REFUTED
        self.assertGreaterEqual(de["ci"][1], 1.05)  # not SUPPORTED
        self.assertEqual(de["verdict"], "INCONCLUSIVE")

    def test_not_faster_in_both_holds(self):
        # Ld-mtp-u 2x faster in hold 1, 3% slower in hold 2: the pooled ratio clears 1.10, the
        # per-hold rule does not.
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": (10.0, 4.85), "Od": (4.0, 4.0)})
        self.assertFalse(r["H57dp"]["all_holds_faster"])
        self.assertNotEqual(r["H57dp"]["verdict"], "SUPPORTED")
        self.assertTrue(r["H57de"]["all_holds_faster"])

    def test_od_slower_in_one_hold_blocks_refuted(self):
        r = self.run_score({"Ld-mtp": 4.0, "Ld-mtp-u": (9.0, 6.0), "Od": (6.0, 6.5)})
        self.assertFalse(r["H57de"]["all_holds_faster"])
        self.assertEqual(r["H57de"]["verdict"], "INCONCLUSIVE")

    def test_load_unmatched(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, foreign={"Ld-mtp": 1.0, "Ld-mtp-u": 5.0, "Od": 1.0})
        self.assertFalse(r["load_gate"]["matched"])
        for row in ("H57dp", "H57de"):
            self.assertEqual(r[row]["verdict"], "INCONCLUSIVE")
            self.assertIn("load unmatched", r[row]["reason"])

    def test_arm_missing_a_hold(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, missing={(2, "Od")})
        self.assertFalse(r["delivered"]["Od"])
        self.assertEqual(r["H57de"]["verdict"], "INCONCLUSIVE")
        self.assertIn("not delivered", r["H57de"]["reason"])
        self.assertEqual(r["H57dp"]["verdict"], "SUPPORTED")   # its own two arms were delivered
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, missing={(1, "Ld-mtp-u")})
        for row in ("H57dp", "H57de"):
            self.assertEqual(r[row]["verdict"], "INCONCLUSIVE")

    def test_no_quality_gate_and_no_third_hold(self):
        with tempfile.TemporaryDirectory() as d:
            write_p_holds(d, {"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0})
            # A stray hold3 (a third hold is not registered) is never read.
            write_holds(os.path.join(d, "stray"), {"Od": 1.0, "Ld": 1.0, "Ld-mtp": 1.0}, Score.Q)
            os.rename(os.path.join(d, "stray", "hold3"), os.path.join(d, "hold3"))
            r = h57d.score_p(d)
        self.assertEqual({a["holds_measured"] for a in r["arms"].values()}, {2})
        self.assertNotIn("gate", r)
        self.assertIn("inherited", r["quality"])

    def test_affinity_read_from_cpus_txt(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0})
        self.assertTrue(r["affinity"]["Ld-mtp"]["as_registered"])
        self.assertTrue(r["affinity"]["Ld-mtp-u"]["as_registered"])
        self.assertEqual(r["affinity"]["Ld-mtp"]["single_cpu_threads"][1], {c: 2 if c == 0 else 1 for c in range(0, 16, 2)})
        self.assertEqual(r["affinity"]["Ld-mtp-u"]["single_cpu_threads"][2], {})

    def test_unpinned_arm_found_pinned(self):
        # Ld-mtp-u's server shows threads bound to one CPU in hold 2: the treatment did not apply.
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, cpus={(2, "Ld-mtp-u"): PINNED_CPUS})
        self.assertFalse(r["affinity"]["Ld-mtp-u"]["as_registered"])
        for row in ("H57dp", "H57de"):
            self.assertEqual(r[row]["verdict"], "INCONCLUSIVE")
            self.assertIn("affinity", r[row]["reason"])

    def test_pinned_arm_found_floating(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, cpus={(1, "Ld-mtp"): FLOATING_CPUS})
        self.assertEqual(r["H57dp"]["verdict"], "INCONCLUSIVE")
        self.assertIn("affinity", r["H57dp"]["reason"])
        self.assertEqual(r["H57de"]["verdict"], "REFUTED")   # H57de reads only Ld-mtp-u's affinity

    def test_affinity_not_recorded(self):
        r = self.run_score({"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}, cpus={(1, "Ld-mtp-u"): None})
        self.assertIsNone(r["affinity"]["Ld-mtp-u"]["single_cpu_threads"][1])
        for row in ("H57dp", "H57de"):
            self.assertEqual(r[row]["verdict"], "INCONCLUSIVE")

    def test_single_cpu_threads_on_the_h57d_records(self):
        res = os.path.join(HERE, "results", "2026-10-21")
        for k in (1, 2, 3, 4):
            for arm in ("Ld", "Ld-mtp"):
                got = h57d.single_cpu_threads(os.path.join(res, f"hold{k}", arm, "cpus.txt"))
                self.assertEqual(sorted(got), h57d.MASK_CPUS)
            self.assertEqual(h57d.single_cpu_threads(os.path.join(res, f"hold{k}", "Od", "cpus.txt")), {})
        self.assertIsNone(h57d.single_cpu_threads(os.path.join(res, "missing.txt")))

    def test_cli_score_p(self):
        with tempfile.TemporaryDirectory() as d:
            write_p_holds(d, {"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0})
            out = subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "score-p", d],
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["H57dp"]["verdict"], "SUPPORTED")


# ---- H57dp amendment R2: the inherited quality records, the spare-hold guard, the Reproduce
# header's engine flags -------------------------------------------------------------------------

H57D_HOLD4 = os.path.join(HERE, "results", "2026-10-21", "hold4")


def write_quality(root, correct):
    """A stand-in for H57d's quality hold: root/hold4/<arm>/quality.json, correct of 50."""
    for arm, c in correct.items():
        ad = os.path.join(root, "hold4", arm)
        os.makedirs(ad)
        json.dump({"status": "measured"}, open(os.path.join(ad, "status.json"), "w"))
        json.dump({"correct": c, "n": 50, "score": c / 50}, open(os.path.join(ad, "quality.json"), "w"))


class InheritedQuality(unittest.TestCase):
    def test_read_from_the_committed_h57d_quality_hold(self):
        q = h57d.inherited_quality()
        self.assertEqual(sorted(q), sorted(h57d.P_ARMS))
        for arm, src in (("Ld-mtp-u", "Ld-mtp"), ("Ld-mtp", "Ld-mtp"), ("Od", "Od")):
            rec = json.load(open(os.path.join(H57D_HOLD4, src, "quality.json")))
            self.assertEqual(q[arm], {"from": f"H57d hold 4, {src}", "correct": rec["correct"],
                                      "n": rec["n"], "score": rec["score"]})
        self.assertEqual((q["Ld-mtp-u"]["correct"], q["Od"]["correct"]), (50, 50))

    def test_missing_record_reads_none(self):
        with tempfile.TemporaryDirectory() as d:
            write_quality(d, {"Ld-mtp": 49})
            q = h57d.inherited_quality(d)
        self.assertEqual(q["Ld-mtp-u"]["correct"], 49)   # Ld-mtp's record stands for Ld-mtp-u
        self.assertIsNone(q["Od"])

    def test_score_p_reports_the_records_with_no_gate(self):
        decode = {"Ld-mtp": 5.0, "Ld-mtp-u": 8.0, "Od": 6.0}
        with tempfile.TemporaryDirectory() as d:
            write_p_holds(d, decode)
            r = h57d.score_p(d)
            self.assertEqual(r["quality_records"], h57d.inherited_quality())
            # A low inherited score changes no verdict: neither row has a quality gate.
            qd = os.path.join(d, "q")
            write_quality(qd, {"Ld-mtp": 20, "Od": 20})
            low = h57d.score_p(d, quality_from=qd)
        self.assertEqual(low["quality_records"]["Ld-mtp-u"]["correct"], 20)
        for row in ("H57dp", "H57de"):
            self.assertEqual(low[row]["verdict"], r[row]["verdict"])
        self.assertEqual((r["H57dp"]["verdict"], r["H57de"]["verdict"]), ("SUPPORTED", "REFUTED"))


class SpareHoldGuard(unittest.TestCase):
    """H57d's hold 5 is the spare: it repeats a failed hold's arms, which must be named. Before
    H57dp amendment R1 a bare `hold.sh 5 perf` took the lock and ran a hold with no arms."""

    def run_hold(self, row, hold, mode, *arms):
        with tempfile.TemporaryDirectory() as g:
            env = dict(os.environ, G=g, LOCK=os.path.join(g, "missing", "live.lock"), H57D_ROW=row)
            r = subprocess.run(["bash", os.path.join(HERE, "hold.sh"), hold, mode, *arms], env=env,
                               capture_output=True, text=True, timeout=60)
            log = os.path.join(g, "results", "campaign.log")
            return r, os.listdir(g), open(log).read() if os.path.exists(log) else ""

    def test_bare_spare_and_smoke_holds_refused(self):
        for row, hold in (("H57d", "5"), ("H57w", "5"), ("H57dp", "5"), ("H57d", "smoke-p")):
            r, made, log = self.run_hold(row, hold, "perf")
            self.assertEqual(r.returncode, 2, f"{row} {hold}: {r.stdout}{r.stderr}")
            self.assertEqual(made, [], f"{row} {hold}")
            self.assertIn(f"registers no hold {hold}", r.stderr)

    def test_named_arms_pass_the_guard(self):
        # With arms named, hold.sh goes on to the lock (here a path that cannot be opened).
        for row, hold, arms in (("H57d", "5", ("Od", "Ld")), ("H57d", "smoke-p", ("Ld-mtp-u",))):
            r, made, log = self.run_hold(row, hold, "perf", *arms)
            self.assertNotEqual(r.returncode, 2, f"{row} {hold}: {r.stdout}{r.stderr}")
            self.assertIn(f"waiting for the lock (mode perf, arms: {' '.join(arms)})", log)
            self.assertNotIn("lock taken", log)


def stub_tools(d):
    """docker, kubectl and k3d stand-ins, so reproduce.sh runs with no container runtime."""
    for name, out in (("docker", "29.0.0"), ("kubectl", "Client Version: v1.37.1"), ("k3d", "k3d version v5.8.3")):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\necho '{out}'\n")
        os.chmod(p, 0o755)


class ReproduceHeader(unittest.TestCase):
    def test_engine_flags_block(self):
        # Every H57d reproduce.txt shows a SyntaxError where this block belongs.
        with tempfile.TemporaryDirectory() as d:
            b = os.path.join(d, "bin")
            os.makedirs(b)
            stub_tools(b)
            env = dict(os.environ, G=os.path.join(d, "g"), PATH=b + os.pathsep + os.environ["PATH"],
                       K3D_REAL=os.path.join(b, "k3d"))
            r = subprocess.run(["bash", os.path.join(HERE, "reproduce.sh")], env=env,
                               capture_output=True, text=True, timeout=120)
        for bad in ("SyntaxError", "Traceback"):
            self.assertNotIn(bad, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        i = lines.index("engine flags (Od in each arm's deploy.yaml):")
        want = [f"  {a}: {' '.join(v)}" for a, v in (("Ld", h57d.llamacpp_args(False)),
                                                      ("Ld-mtp", h57d.llamacpp_args(True)),
                                                      ("Ld-mtp-u", h57d.llamacpp_args(True, pinned=False)))]
        self.assertEqual(lines[i + 1:], want)
        self.assertIn("  llamacpp: " + h57d.LLAMACPP, lines)


if __name__ == "__main__":
    unittest.main()

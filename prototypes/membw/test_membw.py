"""Unit tests for the membw mechanism and simulator invariants. stdlib only.

Run: python3 -m unittest -v test_membw   (from prototypes/membw)
"""

import base64
import json
import os
import random
import tempfile
import unittest

import membw
import sim


class Roofline(unittest.TestCase):
    def test_kv_bytes_llama31_8b(self):  # U1
        self.assertEqual(membw.kv_bytes_per_token(32, 8, 128), 131072)

    def test_bytes_per_token_reads_whole_kv_cache(self):  # U1
        bpt = membw.bytes_per_token(8.0, 4, 32, 8, 128, ctx_tokens=4096)
        self.assertEqual(bpt, 4.0e9 + 4096 * 131072)
        self.assertGreater(membw.bytes_per_token(8.0, 4, 32, 8, 128, 8192), bpt)

    def test_greedy_demand_is_the_lower_ceiling(self):
        self.assertEqual(membw.greedy_demand_gbps(5e9, 25.0, 100.0), 25.0)   # bandwidth bound
        self.assertEqual(membw.greedy_demand_gbps(5e9, 25.0, 2.0), 10.0)     # compute bound

    def test_target_demand(self):
        self.assertAlmostEqual(membw.target_demand_gbps(5e9, 3.0), 15.0)


class Advertise(unittest.TestCase):
    def test_capacity_floor(self):  # U3
        self.assertEqual(membw.node_capacity(70.0), 59)
        self.assertEqual(membw.node_capacity(70.58), 59)

    def test_patch_escapes_slash(self):
        p = membw.node_capacity_patch(70.0)
        self.assertEqual(p, [{"op": "add", "path": "/status/capacity/k3d-gpu.io~1mem-bw-gbps", "value": "59"}])


def review(annotations, resources=None):
    c = {"name": "m", "image": "x"}
    if resources is not None:
        c["resources"] = resources
    return {"request": {"uid": "u1", "object": {"metadata": {"annotations": annotations},
                                                "spec": {"containers": [c]}}}}


ANNS = {"k3d-gpu.io/model-params-b": "8.03", "k3d-gpu.io/weight-bits": "4.5",
        "k3d-gpu.io/layers": "32", "k3d-gpu.io/kv-heads": "8", "k3d-gpu.io/head-dim": "128",
        "k3d-gpu.io/ctx-tokens": "4096", "k3d-gpu.io/device": "igpu",
        "k3d-gpu.io/compute-tok-s": "6"}


def apply_patch(doc, ops):
    for op in ops:
        assert op["op"] == "add"
        parts = [x.replace("~1", "/").replace("~0", "~") for x in op["path"].split("/")[1:]]
        cur = doc
        for k in parts[:-1]:
            cur = cur[int(k)] if isinstance(cur, list) else cur[k]
        cur[parts[-1]] = op["value"]
    return doc


class Webhook(unittest.TestCase):  # U2
    def test_adds_request_and_limit(self):
        rv = review(ANNS)
        out = membw.mutate(rv)
        self.assertEqual(out["kind"], "AdmissionReview")
        r = out["response"]
        self.assertTrue(r["allowed"])
        self.assertEqual(r["uid"], "u1")
        self.assertEqual(r["patchType"], "JSONPatch")
        pod = apply_patch(rv["request"]["object"], json.loads(base64.b64decode(r["patch"])))
        res = pod["spec"]["containers"][0]["resources"]
        # min(25 GB/s reach, 6 tok/s x 5.05 GB) = 25 -> "25"
        self.assertEqual(res["requests"]["k3d-gpu.io/mem-bw-gbps"], "25")
        self.assertEqual(res["limits"]["k3d-gpu.io/mem-bw-gbps"], "25")

    def test_keeps_existing_resources(self):
        rv = review(ANNS, {"requests": {"cpu": "2"}})
        r = membw.mutate(rv)["response"]
        pod = apply_patch(rv["request"]["object"], json.loads(base64.b64decode(r["patch"])))
        res = pod["spec"]["containers"][0]["resources"]
        self.assertEqual(res["requests"]["cpu"], "2")
        self.assertIn("k3d-gpu.io/mem-bw-gbps", res["limits"])

    def test_rounds_up(self):
        a = dict(ANNS, **{"k3d-gpu.io/compute-tok-s": "2"})   # 2 x 5.05 GB = 10.1 GB/s
        r = membw.mutate(review(a))["response"]
        ops = json.loads(base64.b64decode(r["patch"]))
        self.assertEqual(ops[-1]["value"], "11")

    def test_unannotated_pod_untouched(self):
        r = membw.mutate(review({"app": "web"}))["response"]
        self.assertNotIn("patch", r)
        self.assertTrue(r["allowed"])


class Admit(unittest.TestCase):
    def test_first_fit(self):
        self.assertEqual(membw.admit([25.0, 30.2, 10.0], 55), ([0, 2], [1]))


class GuardController(unittest.TestCase):
    def test_cuts_top_ranked_when_slow(self):
        g = membw.Guard(solo_itl=0.2, n_sandboxes=2)
        q = g.step([0.3] * 10, pressure=[1.0, 5.0])
        self.assertEqual(q, [1.0, 0.6])

    def test_relaxes_when_idle_or_ok(self):
        g = membw.Guard(solo_itl=0.2, n_sandboxes=1)
        g.q = [0.36]
        self.assertAlmostEqual(g.step([], [0.0])[0], 0.45)
        self.assertAlmostEqual(g.step([0.2] * 10, [0.0])[0], 0.5625)
        g.q = [0.9]
        self.assertEqual(g.step([], [0.0]), [1.0])

    def test_floor_then_next_sandbox(self):
        g = membw.Guard(solo_itl=0.2, n_sandboxes=2, floor=0.05)
        g.q = [1.0, 0.05]
        self.assertEqual(g.step([1.0], [0.0, 9.0]), [0.6, 0.05])


class GuardV2(unittest.TestCase):
    def g(self, **k):
        return membw.Guard(solo_itl=0.2, n_sandboxes=1, sensor="geomean", memory=True,
                           solo_log_sd=0.2, **k)

    def test_noise_does_not_trigger(self):
        # Five samples at solo +5% (inside the 8% slack): no cut, slow probe up.
        g = self.g()
        g.q = [0.5]
        q = g.step([(4 + t / 5, 0.21) for t in range(5)], [1.0], active=True, now=5)
        self.assertAlmostEqual(q[0], 0.51)

    def test_slow_cuts_once_then_ignores_stale_tokens(self):
        g = self.g()
        self.assertEqual(g.step([(4 + t / 5, 0.4) for t in range(5)], [1.0], active=True, now=5), [0.6])
        # Same (pre-cut) tokens again: too few fresh ones, so hold rather than cut again.
        self.assertEqual(g.step([(4 + t / 5, 0.4) for t in range(5)], [1.0], active=True, now=5.1), [0.6])

    def test_burst_onset_resumes_last_quota(self):
        g = self.g()
        g.step([(4 + t / 5, 0.4) for t in range(5)], [1.0], active=True, now=5)    # cut to 0.6
        for i in range(20):                                               # idle: relax to 1
            g.step([], [1.0], active=False, now=6 + i)
        self.assertEqual(g.q, [1.0])
        self.assertEqual(g.step([], [1.0], active=True, now=30), [0.6])    # new burst

    def test_no_memory_ablation_restarts_from_one(self):
        g = membw.Guard(solo_itl=0.2, n_sandboxes=1, sensor="geomean", memory=False, solo_log_sd=0.2)
        g.step([(4 + t / 5, 0.4) for t in range(5)], [1.0], active=True, now=5)
        for i in range(20):
            g.step([], [1.0], active=False, now=6 + i)
        self.assertEqual(g.step([], [1.0], active=True, now=30), [1.0])


class WebhookServer(unittest.TestCase):
    def test_handle_body_round_trip(self):
        out = json.loads(membw.handle_body(json.dumps(review(ANNS)).encode()))
        self.assertEqual(out["response"]["patchType"], "JSONPatch")

    def test_malformed_is_allowed(self):
        out = json.loads(membw.handle_body(b'{"request": {"uid": "u9", "object": {}}}'))
        self.assertEqual(out["response"], {"uid": "u9", "allowed": True})
        out = json.loads(membw.handle_body(b"not json"))
        self.assertTrue(out["response"]["allowed"])

    def test_manifests_selector_and_example_pod(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        here = os.path.dirname(os.path.abspath(__file__))
        docs = {d["kind"]: d for d in yaml.safe_load_all(open(os.path.join(here, "manifests.yaml")))}
        wh = docs["MutatingWebhookConfiguration"]["webhooks"][0]
        self.assertEqual(wh["failurePolicy"], "Ignore")
        pod = docs["Pod"]
        for k, v in wh["objectSelector"]["matchLabels"].items():
            self.assertEqual(pod["metadata"]["labels"][k], v)
        out = membw.mutate({"request": {"uid": "x", "object": pod}})
        self.assertIn("patch", out["response"])


class Cgroup(unittest.TestCase):  # U4
    def test_reads_psi_and_refaults_writes_cpu_max(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "memory.pressure"), "w") as f:
            f.write("some avg10=1.50 avg60=0.20 avg300=0.00 total=1\n"
                    "full avg10=0.10 avg60=0.00 avg300=0.00 total=1\n")
        with open(os.path.join(d, "memory.stat"), "w") as f:
            f.write("anon 1\nworkingset_refault_anon 10\nworkingset_refault_file 32\n")
        self.assertEqual(membw.read_cgroup_signals(d), (1.5, 42))
        membw.write_cpu_max(d, membw.cpu_max_string(0.25, 16))
        with open(os.path.join(d, "cpu.max")) as f:
            self.assertEqual(f.read(), "400000 100000")
        self.assertEqual(membw.cpu_max_string(1.0, 16), "max 100000")


class SimInvariants(unittest.TestCase):
    def test_waterfill_conserves_and_caps(self):
        a = sim.waterfill(65.0, [25.0, 58.5, 1.0], [1.0, 1.0, 1.0])
        self.assertAlmostEqual(sum(a), 65.0)
        self.assertAlmostEqual(a[0], 25.0)
        self.assertAlmostEqual(a[2], 1.0)
        self.assertEqual(sim.waterfill(100.0, [10.0, 20.0], [1.0, 1.0]), [10.0, 20.0])

    def test_no_contention_no_degradation(self):
        # Probe that the sim does not invent a slowdown: infinite DRAM, no power limit.
        w = sim.World(1e6, alpha=1.5, pl1=1e6)
        v = sim.Client("v", 25.0, 6.0, 5e9)
        s = sim.Client("s", 58.5)
        self.assertAlmostEqual(w.deliver([v])[0], w.deliver([v, s])[0], places=6)

    def test_guard_sees_only_noisy_observations(self):
        # Guard must not read World state: its step signature takes ITLs and scores only.
        import inspect
        self.assertEqual(list(inspect.signature(membw.Guard.step).parameters),
                         ["self", "itl_samples", "pressure", "active", "now"])

    def test_blind_guard_equals_naive(self):
        r = sim.s3(horizon=30.0)
        self.assertAlmostEqual(r["guard_blind"]["victim"], r["naive"]["victim"], places=6)

    def test_burst_trace_duty(self):
        tr = sim.burst_trace(0.4, 5000.0, random.Random(3))
        on = sum(b - a for a, b in tr)
        self.assertAlmostEqual(on / 5000.0, 0.4, delta=0.05)


if __name__ == "__main__":
    unittest.main()

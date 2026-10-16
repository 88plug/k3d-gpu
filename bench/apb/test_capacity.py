#!/usr/bin/env python3
"""Unit tests for bench/apb/capacity.py (stdlib only; no cluster, no real server).

The end-to-end tests drive capacity.main() against a local OpenAI-compatible
stub whose latency grows with the number of requests in flight, a fake
/proc + /sys tree (K3D_GPU_SYSROOT) and stub kubectl / nvidia-smi on PATH.

Run: python3 bench/apb/test_capacity.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apb  # noqa: E402
import capacity as cap  # noqa: E402

INF = float("inf")


class LoadStub:
    """OpenAI-compatible SSE stub. Each request sleeps base + per * (in-flight - 1)
    seconds, then streams one tool call for the first declared tool."""

    def __init__(self, base=0.0, per=0.0, fail=False):
        self.base, self.per, self.fail = base, per, fail
        self.inflight, self.peak, self.count = 0, 0, 0
        self.bodies = []
        self.lock = threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with stub.lock:
                    stub.inflight += 1
                    stub.count += 1
                    stub.peak = max(stub.peak, stub.inflight)
                    stub.bodies.append(body)
                    delay = stub.base + stub.per * (stub.inflight - 1)
                done = False

                def finish():
                    # Leave the in-flight count before the last byte goes out:
                    # the client may send its next turn as soon as it reads [DONE].
                    nonlocal done
                    if not done:
                        done = True
                        with stub.lock:
                            stub.inflight -= 1
                try:
                    time.sleep(delay)
                    if stub.fail:
                        finish()
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(b'{"error":"down"}')
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    fn = body["tools"][0]["function"]
                    args = {k: ("x" if v["type"] == "string" else 1)
                            for k, v in fn["parameters"]["properties"].items()}
                    for c in (
                        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": fn["name"], "arguments": ""}}]}}]},
                        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)}}]}}]},
                        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}},
                    ):
                        self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                    finish()
                    self.wfile.write(b"data: [DONE]\n\n")
                finally:
                    finish()

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v3/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def fake_host(root, pl1_w=65.0, throttle=100, rapl=True, psys=True):
    """A quiet two-CPU, one-package host under root."""
    write(f"{root}/proc/stat", "cpu  100 0 100 10000 0 0 0 0 0 0\n")
    write(f"{root}/proc/loadavg", "0.05 0.10 0.10 1/100 1\n")
    write(f"{root}/proc/cpuinfo", "processor\t: 0\nmodel name\t: Fake Intel(R) Core(TM) i5-0000\n\n"
                                  "processor\t: 1\nmodel name\t: Fake Intel(R) Core(TM) i5-0000\n")
    for c in (0, 1):
        write(f"{root}/sys/devices/system/cpu/cpu{c}/topology/physical_package_id", "0\n")
        write(f"{root}/sys/devices/system/cpu/cpu{c}/thermal_throttle/package_throttle_count", f"{throttle}\n")
    if rapl:
        z = f"{root}/sys/class/powercap/intel-rapl:0"
        write(f"{z}/name", "package-0\n")
        write(f"{z}/energy_uj", "1000000\n")
        write(f"{z}/max_energy_range_uj", "262143328850\n")
        write(f"{z}/constraint_0_name", "long_term\n")
        write(f"{z}/constraint_0_power_limit_uw", f"{int(pl1_w * 1e6)}\n")
        if psys:
            write(f"{root}/sys/class/powercap/intel-rapl:1/name", "psys\n")
            write(f"{root}/sys/class/powercap/intel-rapl:1/energy_uj", "5000000\n")


class EnergyTicker:
    """Adds watts * dt to the fake package counter every 50 ms."""

    def __init__(self, root, watts):
        self.path = f"{root}/sys/class/powercap/intel-rapl:0/energy_uj"
        self.watts, self.stop = watts, threading.Event()
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        while not self.stop.wait(0.05):
            with open(self.path) as f:
                v = int(f.read())
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                f.write(str(v + int(self.watts * 0.05 * 1e6)))
            os.replace(tmp, self.path)

    def close(self):
        self.stop.set()
        self.t.join()


def stub_bin(d, name, script):
    path = os.path.join(d, name)
    write(path, "#!/bin/sh\n" + script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def ovms_items(port_name="http", ready=1, device="CPU", limits=None, ns="k3d-gpu-agent"):
    dep = {
        "kind": "Deployment",
        "metadata": {"name": "ovms", "namespace": ns, "labels": {"app": "ovms"}},
        "spec": {"selector": {"matchLabels": {"app": "ovms"}}, "template": {
            "metadata": {"labels": {"app": "ovms", "k3d-gpu.io/cores": "performance"},
                         "annotations": {"k3d-gpu.io/agent-device": device}},
            "spec": {
                "initContainers": [{"name": "pull", "image": "openvino/model_server@sha256:aa",
                                    "args": ["--pull", f"--target_device={device}"]}],
                "containers": [{"name": "ovms", "image": "openvino/model_server@sha256:aa",
                                "args": ["--rest_port=8000", "--model_path=/models/OpenVINO/Qwen3-4B-int4-ov",
                                         "--model_name=Qwen3-4B-int4-ov"],
                                "ports": [{"name": "http", "containerPort": 8000}],
                                "resources": {"requests": {"cpu": "16"}, "limits": limits or {}}}]}}},
        "status": {"readyReplicas": ready},
    }
    svc = {"kind": "Service", "metadata": {"name": "ovms", "namespace": ns},
           "spec": {"selector": {"app": "ovms"},
                    "ports": [{"name": "http", "port": 8000, "targetPort": port_name}]}}
    return [dep, svc]


def vllm_items(ns="apb-vllm", gpu=True, args=None, command=None):
    res = {"limits": {"nvidia.com/gpu": 2}} if gpu else {"requests": {"cpu": "8"}}
    c = {"name": "vllm", "image": "vllm/vllm-openai@sha256:bb",
         "args": args if args is not None else ["--model=Qwen/Qwen3-4B-AWQ", "--served-model-name=Qwen/Qwen3-4B-AWQ", "--port=8000"],
         "ports": [{"name": "http", "containerPort": 8000}], "resources": res}
    if command:
        c["command"] = command
    dep = {"kind": "Deployment", "metadata": {"name": "vllm", "namespace": ns},
           "spec": {"template": {"metadata": {"labels": {"app": "vllm"}}, "spec": {"containers": [c]}}},
           "status": {"readyReplicas": 1}}
    svc = {"kind": "Service", "metadata": {"name": "vllm", "namespace": ns},
           "spec": {"selector": {"app": "vllm"}, "ports": [{"name": "http", "port": 8000, "targetPort": "http"}]}}
    return [dep, svc]


# ------------------------------------------------------------------ ramp grid
class StepsTest(unittest.TestCase):
    def test_default_grid_matches_protocol(self):
        self.assertEqual(cap.ramp_steps(32), [1, 2, 4, 8, 12, 16, 24, 32])

    def test_grid_ends_at_max(self):
        self.assertEqual(cap.ramp_steps(1), [1])
        self.assertEqual(cap.ramp_steps(10), [1, 2, 4, 8, 10])
        self.assertEqual(cap.ramp_steps(64), [1, 2, 4, 8, 12, 16, 24, 32, 48, 64])
        self.assertEqual(cap.ramp_steps(40), [1, 2, 4, 8, 12, 16, 24, 32, 40])

    def test_grid_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            cap.ramp_steps(0)


# ------------------------------------------------------------------ ramp loop
def fake_rep(p95_by_n, n_rows=60, error_at=None, record=None):
    """run_rep stand-in: a rep whose window requests all take p95_by_n[n] s."""
    def run(n, rep):
        if record is not None:
            record.append((n, rep))
        out, free, lat = [], [0.0] * n, p95_by_n[n]
        sessions = max(10, 5 * n)
        per = max(1, n_rows * 2 // sessions + 1)
        for s in range(sessions):
            a = min(range(n), key=lambda i: free[i])
            t = free[a]
            for k in range(per):
                err = "boom" if error_at == n else None
                out.append({"session": f"s{s}", "turn": k, "sent_at_s": t, "e2e_s": lat,
                            "error": err, "ttft_s": 0.1, "completion_tokens": 5,
                            "expect_tool": "f", "tool_valid": True})
                t += lat
            free[a] = t
        return out, {"agents": n, "epoch_start": 0.0, "epoch_end": 1.0}
    return run


class RampTest(unittest.TestCase):
    def test_stops_at_first_break(self):
        calls = []
        lat = {1: 1.0, 2: 2.0, 4: 5.0, 8: 12.0, 12: 30.0}
        steps = cap.run_ramp(fake_rep(lat, record=calls), [1, 2, 4, 8, 12], reps=2, stop_slo=10.0)
        self.assertEqual([s["agents"] for s in steps], [1, 2, 4, 8])
        self.assertEqual([s["verdict"] for s in steps], ["pass", "pass", "pass", "fail"])
        self.assertNotIn(12, [n for n, _ in calls])

    def test_runs_to_max_without_break(self):
        lat = {1: 1.0, 2: 1.0, 4: 1.0}
        steps = cap.run_ramp(fake_rep(lat), [1, 2, 4], reps=1, stop_slo=10.0)
        self.assertEqual([s["verdict"] for s in steps], ["pass"] * 3)

    def test_errors_break_the_ramp(self):
        lat = {1: 1.0, 2: 1.0, 4: 1.0}
        steps = cap.run_ramp(fake_rep(lat, error_at=2), [1, 2, 4], reps=1, stop_slo=10.0)
        self.assertEqual([s["agents"] for s in steps], [1, 2])
        self.assertEqual(steps[-1]["verdict"], "fail")
        self.assertTrue(any("error rate" in r for r in steps[-1]["reasons"]))

    def test_early_fail_skips_remaining_reps(self):
        calls = []
        lat = {1: 1.0, 2: 100.0}
        steps = cap.run_ramp(fake_rep(lat, record=calls), [1, 2, 4], reps=3, stop_slo=10.0)
        self.assertEqual(calls.count((2, 1)), 1)
        self.assertNotIn((2, 2), calls)
        self.assertEqual(steps[-1]["verdict"], "fail")

    def test_insufficient_adds_reps_then_stops(self):
        calls = []
        lat = {1: 1.0, 2: 1.0}
        # 10 rows per rep: never reaches pooled n >= 60 within max_reps.
        steps = cap.run_ramp(fake_rep(lat, n_rows=4, record=calls), [1, 2], reps=1,
                             stop_slo=10.0, max_reps=3)
        self.assertEqual(calls, [(1, 1), (1, 2), (1, 3)])
        self.assertEqual([s["verdict"] for s in steps], ["insufficient"])

    def test_insufficient_recovers_with_extra_reps(self):
        calls = []
        lat = {1: 1.0, 2: 1.0}
        steps = cap.run_ramp(fake_rep(lat, n_rows=30, record=calls), [1, 2], reps=1,
                             stop_slo=10.0, max_reps=6)
        self.assertEqual([s["verdict"] for s in steps], ["pass", "pass"])
        self.assertGreater(len(calls), 2)


# ------------------------------------------------------------------ APB
def step(n, verdict, p95):
    return {"agents": n, "verdict": verdict, "p95_s": p95, "_slo": 10.0}


class ApbTest(unittest.TestCase):
    def test_largest_n_with_every_smaller_step_passing(self):
        self.assertEqual(cap.agents_per_box([step(1, "pass", 1), step(2, "pass", 2),
                                             step(4, "fail", 12), step(8, "pass", 3)])[0], 2)

    def test_zero_when_first_step_fails(self):
        self.assertEqual(cap.agents_per_box([step(1, "fail", 11)]), (0, None))

    def test_insufficient_ends_the_count(self):
        self.assertEqual(cap.agents_per_box([step(1, "pass", 1), step(2, "insufficient", 2),
                                             step(4, "pass", 3)])[0], 1)

    def test_interpolates_between_last_pass_and_first_fail(self):
        best, interp = cap.agents_per_box([step(1, "pass", 2), step(2, "pass", 6), step(4, "fail", 14)])
        self.assertEqual(best, 2)
        self.assertAlmostEqual(interp, 3.0)

    def test_all_pass_reports_last(self):
        self.assertEqual(cap.agents_per_box([step(1, "pass", 1), step(2, "pass", 1)]), (2, None))


# ------------------------------------------------------------------ discovery
class DiscoverTest(unittest.TestCase):
    def test_ovms_agent_cpu(self):
        (c,) = cap.discover(ovms_items())
        self.assertEqual((c["namespace"], c["service"], c["port"]), ("k3d-gpu-agent", "ovms", 8000))
        self.assertEqual((c["server"], c["engine"], c["model"]), ("ovms", "cpu", "Qwen3-4B-int4-ov"))
        self.assertEqual(c["path"], "/v3/chat/completions")
        self.assertTrue(c["ready"])

    def test_ovms_igpu_from_annotation_and_resource(self):
        (c,) = cap.discover(ovms_items(device="GPU", limits={"gpu.intel.com/i915": 1}))
        self.assertEqual(c["engine"], "igpu")
        (c,) = cap.discover(ovms_items(device="NPU", limits={"npu.intel.com/accel": 1}))
        self.assertEqual(c["engine"], "npu")

    def test_vllm_nvidia(self):
        (c,) = cap.discover(vllm_items())
        self.assertEqual((c["server"], c["engine"], c["model"]), ("vllm", "nvidia", "Qwen/Qwen3-4B-AWQ"))
        self.assertEqual(c["path"], "/v1/chat/completions")

    def test_vllm_cpu_positional_model(self):
        (c,) = cap.discover(vllm_items(gpu=False, args=["serve", "Qwen/Qwen3-4B", "--port", "8000"]))
        self.assertEqual((c["engine"], c["model"]), ("cpu", "Qwen/Qwen3-4B"))
        (c,) = cap.discover(vllm_items(gpu=False, args=["--model", "m/x", "--served-model-name", "x"]))
        self.assertEqual(c["model"], "x")

    def test_gaudi_from_habana_resource(self):
        items = vllm_items(gpu=False)
        items[0]["spec"]["template"]["spec"]["containers"][0]["resources"] = {"limits": {"habana.ai/gaudi": 8}}
        (c,) = cap.discover(items)
        self.assertEqual(c["engine"], "gaudi")
        self.assertEqual(cap.choose([c], "gaudi")["engine"], "gaudi")
        with self.assertRaises(cap.CapacityError):
            cap.choose([c], "cpu")

    def test_unready_and_unrelated_are_reported(self):
        items = ovms_items(ready=0) + [{"kind": "Deployment", "metadata": {"name": "web", "namespace": "a"},
                                        "spec": {"template": {"metadata": {"labels": {"app": "web"}},
                                                              "spec": {"containers": [{"name": "n", "image": "nginx:1"}]}}}}]
        (c,) = cap.discover(items)
        self.assertFalse(c["ready"])

    def test_prefers_the_service_that_targets_the_server_port(self):
        dep, svc = ovms_items(ns="llm")
        other = {"kind": "Service", "metadata": {"name": "qwen3-predictor", "namespace": "llm"},
                 "spec": {"selector": {"app": "ovms"}, "ports": [{"port": 80, "targetPort": 8080}]}}
        (c,) = cap.discover([dep, other, svc])
        self.assertEqual((c["service"], c["port"]), ("ovms", 8000))

    def test_no_service_means_no_candidate(self):
        self.assertEqual(cap.discover(ovms_items()[:1]), [])

    def test_choose_auto_prefers_the_agent_endpoint(self):
        cands = cap.discover(ovms_items() + vllm_items())
        self.assertEqual(cap.choose(cands, "auto")["service"], "ovms")
        self.assertEqual(cap.choose(cands, "nvidia")["service"], "vllm")
        with self.assertRaises(cap.CapacityError):
            cap.choose(cands, "igpu")

    def test_choose_auto_ambiguous_without_agent(self):
        cands = cap.discover(vllm_items() + vllm_items(ns="other", gpu=False))
        with self.assertRaises(cap.CapacityError) as e:
            cap.choose(cands, "auto")
        self.assertIn("--endpoint", str(e.exception))
        self.assertEqual(cap.choose(cands, "auto", endpoint="other/vllm")["engine"], "cpu")

    def test_choose_refuses_unready(self):
        with self.assertRaises(cap.CapacityError):
            cap.choose(cap.discover(ovms_items(ready=0)), "auto")

    def test_choose_none(self):
        with self.assertRaises(cap.CapacityError) as e:
            cap.choose([], "auto")
        self.assertIn("agent up", str(e.exception))


class SecondaryApbTest(unittest.TestCase):
    """APB at a secondary SLO is only known up to --stop-slo: past it the ramp
    never ran the steps that could break the looser SLO."""

    def steps(self, *pairs):
        return [{"agents": n, "p95_s": p, "verdict": "pass" if p <= 10 else "fail"} for n, p in pairs]

    def test_tighter_slo_is_exact_and_looser_ones_are_not_reported(self):
        got = cap.secondary_apb(self.steps((1, 3.0), (2, 4.0), (4, 8.0), (8, 12.0)), stop_slo=10.0)
        self.assertEqual(got, {"5.0": {"agents_per_box": 2, "lower_bound": False}})

    def test_stop_slo_30_reports_every_secondary(self):
        got = cap.secondary_apb(self.steps((1, 3.0), (2, 4.0), (4, 8.0), (8, 12.0), (12, 31.0)),
                                stop_slo=30.0)
        self.assertEqual(sorted(got), ["15.0", "20.0", "30.0", "5.0"])
        self.assertEqual(got["15.0"], {"agents_per_box": 8, "lower_bound": False})
        self.assertEqual(got["30.0"], {"agents_per_box": 8, "lower_bound": False})

    def test_ramp_that_never_broke_is_a_lower_bound(self):
        got = cap.secondary_apb(self.steps((1, 1.0), (2, 1.0)), stop_slo=30.0)
        self.assertEqual(got["30.0"], {"agents_per_box": 2, "lower_bound": True})
        self.assertEqual(got["5.0"], {"agents_per_box": 2, "lower_bound": True})

    def test_insufficient_step_ends_the_count(self):
        st = self.steps((1, 1.0), (2, 1.0))
        st[1]["verdict"] = "insufficient"
        got = cap.secondary_apb(st, stop_slo=10.0)
        self.assertEqual(got["5.0"], {"agents_per_box": 1, "lower_bound": True})


# ------------------------------------------------------------------ quiet check
class QuietTest(unittest.TestCase):
    def test_quiet_box_passes(self):
        ok, checks = cap.quiet_verdict({"cpu_busy": 0.02, "pkg_w": 8.0, "pl1_w": 35.0,
                                        "gpus": [{"index": 0, "power_w": 10.0, "limit_w": 70.0, "util": 0}]})
        self.assertTrue(ok)
        self.assertTrue(all(c["ok"] for c in checks))

    def test_busy_cpu_refuses(self):
        ok, checks = cap.quiet_verdict({"cpu_busy": 0.4, "pkg_w": None, "pl1_w": None, "gpus": []})
        self.assertFalse(ok)
        self.assertEqual([c["check"] for c in checks if c["ok"] is False], ["cpu_busy"])

    def test_package_power_relative_to_pl1(self):
        ok, _ = cap.quiet_verdict({"cpu_busy": 0.0, "pkg_w": 30.0, "pl1_w": 35.0, "gpus": []})
        self.assertFalse(ok)
        ok, _ = cap.quiet_verdict({"cpu_busy": 0.0, "pkg_w": 30.0, "pl1_w": 253.0, "gpus": []})
        self.assertTrue(ok)

    def test_busy_gpu_refuses(self):
        ok, checks = cap.quiet_verdict({"cpu_busy": 0.0, "pkg_w": None, "pl1_w": None,
                                        "gpus": [{"index": 1, "power_w": 45.0, "limit_w": 50.0, "util": 97}]})
        self.assertFalse(ok)
        self.assertIn("gpu1_util", [c["check"] for c in checks if c["ok"] is False])

    def test_unreadable_power_is_skipped_not_failed(self):
        ok, checks = cap.quiet_verdict({"cpu_busy": 0.0, "pkg_w": None, "pl1_w": None, "gpus": []})
        self.assertTrue(ok)
        self.assertIn("pkg_power", [c["check"] for c in checks if c["ok"] is None])

    def test_cpu_busy_from_proc_stat(self):
        a = "cpu  100 0 100 800 0 0 0 0 0 0"
        b = "cpu  150 0 150 900 0 0 0 0 0 0"
        self.assertAlmostEqual(cap.cpu_busy(a, b), 0.5)
        self.assertEqual(cap.cpu_busy(a, a), 0.0)

    def test_cpu_busy_counts_guest_time_once(self):
        # guest/guest_nice are already inside user/nice (proc(5)); a busy VM
        # host must not read as more than 100% busy.
        a = "cpu  100 0 0 900 0 0 0 0 100 0"
        b = "cpu  200 0 0 900 0 0 0 0 200 0"
        self.assertAlmostEqual(cap.cpu_busy(a, b), 1.0)
        a = "cpu  100 0 100 800 0 0 0 0 50 0"
        b = "cpu  150 0 150 900 0 0 0 0 100 0"
        self.assertAlmostEqual(cap.cpu_busy(a, b), 0.5)


class HostReadTest(unittest.TestCase):
    """Two-socket host (Xeon): throttle counts once per package, RAPL sums
    every package-* zone and its PL1."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        for c, pkg, thr in ((0, 0, 10), (1, 0, 10), (2, 1, 7), (3, 1, 7)):
            d = f"{self.root}/sys/devices/system/cpu/cpu{c}"
            write(f"{d}/topology/physical_package_id", f"{pkg}\n")
            write(f"{d}/thermal_throttle/package_throttle_count", f"{thr}\n")
        for i, (name, pl1) in enumerate((("package-0", 250), ("package-1", 250), ("dram", 0))):
            z = f"{self.root}/sys/class/powercap/intel-rapl:{i}"
            write(f"{z}/name", name + "\n")
            write(f"{z}/constraint_0_name", "long_term\n")
            write(f"{z}/constraint_0_power_limit_uw", f"{pl1 * 1000000}\n")
        write(f"{self.root}/sys/class/powercap/intel-rapl:0:0/name", "dram\n")

    def test_throttle_counted_once_per_package(self):
        self.assertEqual(cap.throttle_count(self.root), 17)

    def test_rapl_sums_package_zones(self):
        z = cap.rapl_zones(self.root)
        self.assertEqual([os.path.basename(p) for p in z["pkg"]], ["intel-rapl:0", "intel-rapl:1"])
        self.assertEqual(z["pl1_w"], 500.0)
        self.assertIsNone(z["psys"])


class ConfoundsTest(unittest.TestCase):
    def rep(self, engine):
        return {"endpoint": {"engine": engine}, "idle": None, "steps": [], "drift_check": None,
                "max_agents": 8, "quiet_check": {"forced": False, "checks": []},
                "binding_limit": {"kind": "slo"}}

    def test_gaudi_energy_gap_is_stated(self):
        sampler = type("S", (), {"rapl_mode": "direct", "nvidia_seen": False})()
        c = cap.confounds(self.rep("gaudi"), False, sampler, True)
        self.assertTrue(any("Gaudi" in x for x in c))
        c = cap.confounds(self.rep("cpu"), False, sampler, True)
        self.assertFalse(any("Gaudi" in x for x in c))


class NvidiaQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = {k: os.environ.get(k) for k in ("K3D_GPU_SYSROOT", "K3D_GPU_NVIDIA_SMI")}
        os.environ["K3D_GPU_SYSROOT"] = os.path.join(self.tmp, "root")
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_falls_back_to_clocks_throttle_reasons(self):
        # Drivers older than the clocks_event_reasons rename reject the whole
        # query; the clocks_throttle_reasons names are accepted by both.
        os.environ["K3D_GPU_NVIDIA_SMI"] = stub_bin(self.tmp, "nvidia-smi", (
            'case "$*" in *clocks_event_reasons*) echo "Field is not a valid field" >&2; exit 2 ;; esac\n'
            "echo '0, 300.5, 350.00, 99, 80, Active, Not Active, Active, Not Active'\n"))
        s = cap.Sampler(self.tmp)
        (g,) = s._gpus()
        self.assertEqual((g["index"], g["power_w"], g["limit_w"]), (0, 300.5, 350.0))
        self.assertTrue(g["power_cap"] and g["thermal"])
        self.assertEqual(len(s._gpus()), 1)

    def test_no_gpu_stops_asking(self):
        os.environ["K3D_GPU_NVIDIA_SMI"] = stub_bin(self.tmp, "nvidia-smi", "exit 9\n")
        s = cap.Sampler(self.tmp)
        self.assertEqual(s._gpus(), [])
        self.assertIsNone(s.nvidia)


# ------------------------------------------------------------------ binding limit
class BindingTest(unittest.TestCase):
    def test_slo_break_with_thermal_and_power(self):
        steps = [{"agents": 1, "verdict": "pass", "reasons": [], "p95_s": 2.0},
                 {"agents": 2, "verdict": "fail", "reasons": ["p95 12.0 > SLO 10.0"], "p95_s": 12.0,
                  "hardware": {"package_throttle_events": 5000, "pkg_w_mean": 112.0, "pl1_w": 117.0,
                               "gpu": {}}}]
        b = cap.binding_limit(steps, max_agents=32)
        self.assertEqual((b["kind"], b["at_agents"]), ("slo", 2))
        self.assertIn("cpu_thermal_throttle", b["hardware"])
        self.assertIn("cpu_package_power_limit", b["hardware"])

    def test_max_reached(self):
        b = cap.binding_limit([{"agents": 4, "verdict": "pass", "reasons": [], "p95_s": 1.0,
                                "hardware": {}}], max_agents=4)
        self.assertEqual(b["kind"], "max_reached")

    def test_errors_and_tool_validity(self):
        b = cap.binding_limit([{"agents": 1, "verdict": "fail", "p95_s": 1.0, "hardware": {},
                                "reasons": ["error rate 3/100 > 0.01"]}], max_agents=8)
        self.assertEqual(b["kind"], "errors")
        b = cap.binding_limit([{"agents": 1, "verdict": "fail", "p95_s": 1.0, "hardware": {},
                                "reasons": ["tool validity 0.500 < N=1 0.900 - 0.1"]}], max_agents=8)
        self.assertEqual(b["kind"], "tool_validity")

    def test_insufficient_data(self):
        b = cap.binding_limit([{"agents": 1, "verdict": "pass", "reasons": [], "p95_s": 1.0, "hardware": {}},
                               {"agents": 2, "verdict": "insufficient", "p95_s": 1.0, "hardware": {},
                                "reasons": ["pooled n 40 < 60"]}], max_agents=8)
        self.assertEqual((b["kind"], b["at_agents"]), ("insufficient_data", 2))

    def test_gpu_power_cap(self):
        b = cap.binding_limit([{"agents": 8, "verdict": "fail", "p95_s": 11.0, "reasons": ["p95 11 > SLO 10"],
                                "hardware": {"gpu": {"0": {"power_cap_frac": 0.8, "thermal_frac": 0.0,
                                                           "power_w_mean": 69.0, "limit_w": 70.0}}}}],
                              max_agents=32)
        self.assertIn("gpu0_power_cap", b["hardware"])


# ------------------------------------------------------------------ end to end
class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)  # runs after the tests' own cleanups
        self.root = os.path.join(self.tmp, "root")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.env = {k: os.environ.get(k) for k in ("K3D_GPU_SYSROOT", "PATH")}
        os.environ["K3D_GPU_SYSROOT"] = self.root
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        self.stubs = []
        stub_bin(self.bin, "nvidia-smi", "exit 9\n")  # no NVIDIA GPU unless a test says so

    def tearDown(self):
        for s in self.stubs:
            s.close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def report(self):
        with open(os.path.join(self.tmp, "run", "capacity.json")) as f:
            return json.load(f)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cap.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def common(self, url, extra=()):
        return ["--url", url, "--model", "m", "--slo", "0.06", "--reps", "2", "--max", "8",
                "--time-scale", "0", "--quiet-s", "0.3", "--interval", "0.1",
                "--out", os.path.join(self.tmp, "run"), *extra]

    def test_ramp_breaks_at_slo_and_writes_json(self):
        fake_host(self.root)
        ticker = EnergyTicker(self.root, 20.0)
        self.addCleanup(ticker.close)
        stub = LoadStub(base=0.002, per=0.03)
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url))
        self.assertEqual(rc, 0, err)
        rep = self.report()
        self.assertEqual(rep["schema"], "k3d-gpu.capacity/1")
        self.assertEqual([s["agents"] for s in rep["steps"]], [1, 2, 4])
        self.assertEqual(rep["agents_per_box"], 2)
        self.assertEqual(rep["binding_limit"]["kind"], "slo")
        self.assertEqual(rep["binding_limit"]["at_agents"], 4)
        self.assertIn("AGENTS PER BOX: 2", out)
        s1 = rep["steps"][0]
        for k in ("p50_s", "p95_s", "p99_s", "n", "error_rate"):
            self.assertIsNotNone(s1[k], k)
        self.assertLessEqual(s1["p50_s"], s1["p95_s"])
        self.assertLessEqual(s1["p95_s"], s1["p99_s"])
        self.assertGreater(s1["j_per_turn"]["cpu_package"], 0)
        self.assertIsNotNone(rep["quiet_check"]["ok"])
        self.assertTrue(rep["quiet_check"]["ok"])
        self.assertIsNotNone(rep["drift_check"])
        self.assertTrue(rep["confounds"])
        self.assertLessEqual(stub.peak, 4)
        # every request carries the thinking-off extra body
        self.assertTrue(all(b.get("chat_template_kwargs") == {"enable_thinking": False} for b in stub.bodies))
        json.dumps(rep, allow_nan=False)

    def test_max_reached_is_a_lower_bound(self):
        fake_host(self.root, rapl=False)
        stub = LoadStub(base=0.001)
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url, ["--max", "2", "--slo", "5"]))
        self.assertEqual(rc, 0, err)
        rep = self.report()
        self.assertEqual(rep["agents_per_box"], 2)
        self.assertEqual(rep["binding_limit"]["kind"], "max_reached")
        self.assertIsNone(rep["steps"][0]["j_per_turn"]["cpu_package"])
        self.assertTrue(any("lower bound" in c for c in rep["confounds"]))
        self.assertTrue(any("RAPL" in c for c in rep["confounds"]))

    def test_refuses_a_busy_box(self):
        fake_host(self.root, pl1_w=10.0)
        ticker = EnergyTicker(self.root, 50.0)
        self.addCleanup(ticker.close)
        stub = LoadStub()
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url))
        self.assertEqual(rc, cap.EXIT_NOT_QUIET)
        self.assertEqual(stub.count, 0)
        rep = self.report()
        self.assertEqual(rep["status"], "refused")
        self.assertFalse(rep["quiet_check"]["ok"])
        self.assertIn("--force", err)

    def test_force_runs_a_busy_box_and_states_it(self):
        fake_host(self.root, pl1_w=10.0)
        ticker = EnergyTicker(self.root, 50.0)
        self.addCleanup(ticker.close)
        stub = LoadStub(base=0.001)
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url, ["--force", "--max", "1", "--slo", "5"]))
        self.assertEqual(rc, 0, err)
        rep = self.report()
        self.assertTrue(rep["quiet_check"]["forced"])
        self.assertTrue(any("not quiet" in c for c in rep["confounds"]))

    def test_dead_endpoint_aborts_after_warmup(self):
        fake_host(self.root)
        stub = LoadStub(fail=True)
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url))
        self.assertEqual(rc, cap.EXIT_ENDPOINT)
        self.assertLess(stub.count, 30)

    def test_auto_discovers_and_port_forwards(self):
        fake_host(self.root)
        stub = LoadStub(base=0.001)
        self.stubs.append(stub)
        listing = json.dumps({"kind": "List", "items": ovms_items()})
        write(os.path.join(self.tmp, "list.json"), listing)
        stub_bin(self.bin, "kubectl",
                 f'printf "%s\\n" "$*" >> "{self.tmp}/kubectl.log"\n'
                 f'case "$*" in\n'
                 f'  *port-forward*) echo "Forwarding from 127.0.0.1:{stub.port} -> 8000"; exec sleep 30 ;;\n'
                 f'  *get*) cat "{self.tmp}/list.json" ;;\n'
                 f'esac\n')
        stub_bin(self.bin, "nvidia-smi", "echo '0, 12.5, 70.00, 0, 40, Not Active, Not Active, Not Active, Not Active'\n")
        rc, out, err = self.run_main(["--slo", "5", "--max", "1", "--reps", "2", "--time-scale", "0",
                                      "--quiet-s", "0.3", "--interval", "0.1",
                                      "--out", os.path.join(self.tmp, "run")])
        self.assertEqual(rc, 0, err)
        rep = self.report()
        self.assertEqual(rep["endpoint"]["engine"], "cpu")
        self.assertEqual(rep["endpoint"]["model"], "Qwen3-4B-int4-ov")
        self.assertEqual(rep["endpoint"]["service"], "k3d-gpu-agent/ovms")
        self.assertTrue(all(b["model"] == "Qwen3-4B-int4-ov" for b in stub.bodies))
        with open(os.path.join(self.tmp, "kubectl.log")) as f:
            log = f.read()
        self.assertIn("-n k3d-gpu-agent port-forward svc/ovms :8000", log)
        self.assertIsNotNone(rep["steps"][0]["j_per_turn"]["gpu"])
        self.assertTrue(any("port-forward" in c for c in rep["confounds"]))

    def test_dry_run_sends_nothing(self):
        fake_host(self.root)
        stub = LoadStub()
        self.stubs.append(stub)
        rc, out, err = self.run_main(self.common(stub.url, ["--dry-run"]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(stub.count, 0)
        self.assertIn("plan", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)

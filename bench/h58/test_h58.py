#!/usr/bin/env python3
"""Tests for bench/h58 (EXPERIMENTS.md "H58 amendment R1"): h58.py's pin check, GPU interlock,
placement reading, clause scorers and squeeze, and run.sh end to end against stand-ins for
k3d-gpu, k3d, kubectl, docker, sudo, curl, git and the Hub API (no cluster, no network).
Python 3 standard library only."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h58 as H  # noqa: E402

GIB = 1024 ** 3


def deployment(name="ovms", fit="cpu 4x32768", device="CPU", gpu=False, model="OpenVINO/gemma-4-26b-a4b-it-int4-ov",
               ready=1, uid=None):
    res = {"limits": {"gpu.intel.com/xe": "1", "memory": "20Gi"}} if gpu else {"limits": {"memory": "20Gi"}}
    return {"kind": "Deployment", "metadata": {"name": name, "namespace": "k3d-gpu-agent", "uid": uid or "u-" + name},
            "spec": {"replicas": 1, "template": {
                "metadata": {"annotations": {"k3d-gpu.io/agent-fit": fit, "k3d-gpu.io/agent-device": device}},
                "spec": {"initContainers": [{"name": "pull", "args": ["--source_model=" + model,
                                                                      "--model_repository_path=/models"]}],
                         "containers": [{"name": name, "resources": res}]}}},
            "status": {"readyReplicas": ready}}


class Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.body


class PinsTest(unittest.TestCase):
    def opener(self, shas):
        def op(url, timeout=None):
            repo = url.split("/api/models/", 1)[1]
            if shas.get(repo) is None:
                raise OSError("unreachable")
            return Resp({"sha": shas[repo]})
        return op

    def test_equal_moved_unread(self):
        got, rc = H.check_pins(self.opener(dict(H.PINS)))
        self.assertEqual(rc, 0)
        self.assertTrue(all(v["equal"] for v in got.values()))
        moved = dict(H.PINS, **{H.QWEN27: "0" * 40})
        self.assertEqual(H.check_pins(self.opener(moved))[1], 3)
        unread = dict(H.PINS, **{H.FLASH: None})
        got, rc = H.check_pins(self.opener(unread))
        self.assertEqual(rc, 2)
        self.assertIn("error", got[H.FLASH])

    def test_p1_values(self):
        # Amendment P1's table.
        self.assertEqual(H.PINS[H.FLASH], "4c67bf686b7f7fd386bae6b07ab59e8ff1d5b897")
        self.assertEqual(H.PINS[H.GEMMA], "4d7ae4984b7db7de8f8457170b3f1a419ee76d52")
        self.assertEqual(H.PINS[H.QWEN27], "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0")

    def test_env_flags(self):
        self.assertEqual(H.env_flags({"PATH": "/x", "K3D_GPU_AGENT_DEVICE": "CPU", "K3DGPU_HOLD": "t"}),
                         ["K3D_GPU_AGENT_DEVICE"])


class InterlockTest(unittest.TestCase):
    def test_accel_keys(self):
        self.assertEqual(H.accel_keys(deployment(gpu=True)["spec"]["template"]["spec"]), ["gpu.intel.com/xe"])
        self.assertEqual(H.accel_keys(deployment()["spec"]["template"]["spec"]), [])
        self.assertEqual(H.accel_keys({"containers": [{}], "resourceClaims": [{"name": "g"}]}), ["resourceClaims"])
        self.assertEqual(H.accel_keys({"containers": [{"resources": {"requests": {"nvidia.com/gpu": 1}}}]}),
                         ["nvidia.com/gpu"])

    def test_pass_deletes_once_and_records(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        cpu = deployment("llamacpp", uid="a")
        gpu = deployment("ovms", fit="gpu 4x20480", device="GPU", gpu=True, model="OpenVINO/Qwen3-4B-int4-ov", uid="b")
        pod = {"kind": "Pod", "metadata": {"name": "p", "namespace": "default", "uid": "c"},
               "spec": {"containers": [{"resources": {"limits": {"gpu.intel.com/xe": "1"}}}]}}
        calls = []

        def run(args, timeout=20):
            calls.append(args)
            if "get" in args:
                return 0, json.dumps({"items": [cpu, gpu, pod]}), ""
            return 0, "", ""
        seen = set()
        rows = H.interlock_pass("/k/kubeconfig-gpu", "k3d-gpu", d, seen, run=run, clock=lambda: 100.0)
        self.assertEqual([(r["kind"], r["name"]) for r in rows], [("Deployment", "ovms"), ("Pod", "p")])
        self.assertEqual(rows[0]["fit"], "gpu 4x20480")
        kc = ["kubectl", "--kubeconfig", "/k/kubeconfig-gpu", "--context", "k3d-gpu", "-n", "k3d-gpu-agent"]
        self.assertIn(kc + ["scale", "deployment/ovms", "--replicas=0"], calls)
        self.assertIn(kc + ["delete", "deployment/ovms", "--wait=false"], calls)
        self.assertFalse(any("llamacpp" in " ".join(c) for c in calls))
        self.assertTrue(os.path.exists(os.path.join(d, "deployment-k3d-gpu-agent-ovms-100.json")))
        # The same objects again: nothing new.
        self.assertEqual(H.interlock_pass("/k/kubeconfig-gpu", "k3d-gpu", d, seen, run=run, clock=lambda: 101.0), [])
        self.assertEqual(len(H.read_jsonl(os.path.join(d, "events.jsonl"))), 2)
        with open(os.path.join(d, "beat")) as f:
            self.assertTrue(f.read().endswith("ok\n"))

    def test_device_plugins_and_hostpath_mounts_are_left_alone(self):
        # Amendment R2: run 1's interlock deleted the Intel GPU plugin pod (kube-system, /dev/dri
        # hostPath, no resource) 958 times, so the node advertised no card. k3d-gpu's own pods are
        # never targets; only workloads that ask for an accelerator resource or claim are.
        plugin = {"kind": "Pod", "metadata": {"name": "intel-gpu-plugin-wgblj", "namespace": "kube-system", "uid": "p1"},
                  "spec": {"containers": [{"name": "intel-gpu-plugin", "volumeMounts": [{"name": "dri-renderd129"}]}],
                           "volumes": [{"name": "dri-renderd129", "hostPath": {"path": "/dev/dri/renderD129"}},
                                       {"name": "dri-by-path", "hostPath": {"path": "/dev/dri/by-path"}}]}}
        exporter = {"kind": "Pod", "metadata": {"name": "intel-gpu-exporter-x", "namespace": "k3d-gpu-telemetry", "uid": "p2"},
                    "spec": {"containers": [{"name": "e"}], "volumes": [{"name": "d", "hostPath": {"path": "/dev/dri"}}]}}
        ks_gpu = {"kind": "Pod", "metadata": {"name": "probe", "namespace": "kube-system", "uid": "p3"},
                  "spec": {"containers": [{"resources": {"limits": {"gpu.intel.com/xe": "1"}}}]}}
        self.assertEqual(H.interlock_targets([plugin, exporter, ks_gpu]), [])
        self.assertEqual(H.accel_keys(plugin["spec"]), [])
        gpu = deployment("ovms", fit="gpu 4x20480", device="GPU", gpu=True, uid="g")
        self.assertEqual([t[2] for t in H.interlock_targets([plugin, gpu])], ["ovms"])

    def test_pass_unreachable_cluster(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        self.assertEqual(H.interlock_pass("/k/kubeconfig-gpu", "k3d-gpu", d, set(),
                                          run=lambda a, timeout=20: (1, "", "no context")), [])
        with open(os.path.join(d, "beat")) as f:
            self.assertIn("unreachable", f.read())


REFUSAL = ("\x1b[31mERROR:\x1b[0m fit: OpenVINO/gemma-4-26b-a4b-it-int4-ov does not fit this host: weights + "
           "overhead 15.3 GiB exceed the GPU budget 15.2 GiB; not starting a server that cannot fit "
           "(k3d-gpu fit --model OpenVINO/gemma-4-26b-a4b-it-int4-ov --explain shows the arithmetic)\n")


class PlacementTest(unittest.TestCase):
    def test_refused(self):
        p = H.placement(REFUSAL, deployment(), rc=1)
        self.assertEqual(p["placement"], "refused")
        self.assertEqual(p["served"], "OpenVINO/gemma-4-26b-a4b-it-int4-ov")
        self.assertIn("15.3 GiB", p["why"])
        self.assertFalse(p["landed"])
        self.assertEqual(H.clause_e(H.GEMMA, p)[0], "fails")

    def test_landed_cpu(self):
        p = H.placement("fit: x on CPU (AMX) via ovms, context 32768 x 4 seqs, host 20 GiB", deployment(), rc=0)
        self.assertEqual((p["placement"], p["device"], p["landed"]), ("cpu", "CPU", True))
        self.assertEqual(p["served"], "OpenVINO/gemma-4-26b-a4b-it-int4-ov")
        self.assertEqual(H.clause_e(H.GEMMA, p)[0], "holds")
        # Flash-Next on the CPU is not an experts-in-DRAM placement.
        flash = H.placement("", deployment(model="Intel/Qwen3.8-Flash-Next-W4A16-AutoRound"), rc=0)
        self.assertEqual(H.clause_e(H.FLASH, flash)[0], "fails")
        moe = H.placement("", deployment(fit="moe-offload 4x32768", model="Intel/Qwen3.8-Flash-Next-W4A16-AutoRound"), rc=0)
        self.assertEqual(H.clause_e(H.FLASH, moe)[0], "holds")

    def test_blocked(self):
        gpu = deployment(fit="gpu 4x20480", device="GPU", gpu=True, model="OpenVINO/Qwen3-4B-int4-ov")
        p = H.placement("", gpu, rc=1, blocked=True)
        self.assertEqual((p["placement"], p["blocked"], p["landed"]), ("gpu", True, False))
        self.assertEqual(p["accel"], ["gpu.intel.com/xe"])
        # Flash-Next's render serves another model on one GPU: fails whether or not it lands.
        self.assertEqual(H.clause_e(H.FLASH, p)[0], "fails")
        # Gemma's render on one GPU would hold had it landed: inconclusive.
        g = H.placement("", deployment(fit="gpu 1x4096", device="GPU", gpu=True), rc=1, blocked=True)
        self.assertEqual(H.clause_e(H.GEMMA, g)[0], "inconclusive")

    def test_error_ignores_a_stale_deployment(self):
        p = H.placement("waiting\nERROR: server not ready - kubectl logs\n", deployment(), rc=1)
        self.assertEqual(p["placement"], "error")
        self.assertIn("server not ready", p["why"])
        self.assertEqual(H.clause_e(H.QWEN27, p)[0], "fails")

    def test_served_model_forms(self):
        vllm = {"spec": {"template": {"spec": {"containers": [{"name": "vllm", "args": ["Qwen/Qwen3.8-27B", "--port=8000"]}]}}}}
        llama = {"spec": {"template": {"spec": {"containers": [{"name": "llamacpp", "args": ["--model", "/models/a.gguf"]}]}}}}
        self.assertEqual(H.served_model(vllm), "Qwen/Qwen3.8-27B")
        self.assertEqual(H.served_model(llama), "/models/a.gguf")
        self.assertEqual(H.served_model(deployment()), "OpenVINO/gemma-4-26b-a4b-it-int4-ov")


def turn(arm, c, ttft, e2e, status=200, error=None):
    return {"arm": arm, "completion_tokens": c, "ttft_s": ttft, "e2e_s": e2e, "status": status, "error": error}


class DecodeTest(unittest.TestCase):
    def test_rate(self):
        ts = [turn("Z", 21, 1.0, 2.0), turn("Z", 11, 0.5, 1.0), turn("Z", 1, 0.1, 0.2), turn("Z", 50, 1, 1),
              turn("Z", 30, 1, 2, status=500), turn("A1", 99, 0, 1)]
        rate, n = H.decode_rate(ts, "Z")
        self.assertEqual(n, 2)
        self.assertAlmostEqual(rate, 30 / 1.5)
        self.assertEqual(H.decode_rate([], "Z"), (None, 0))

    def test_success_skips_void(self):
        at = [{"arm": "Z", "outcome": o} for o in ("pass_budget", "fail_graded", "void", "over_turn", "pass_budget")]
        self.assertEqual(H.success(at, "Z"), (2, 4))

    def test_h50_baseline(self):
        # The committed H50 records give the numbers amendment R1 states.
        b = H.baseline()
        self.assertEqual((b["passed"], b["attempts"], b["turns"]), (19, 24, 54))
        self.assertAlmostEqual(b["decode_tok_s"], 20.50, places=2)
        self.assertAlmostEqual(b["success"], 19 / 24)


class SqueezeMathTest(unittest.TestCase):
    MEM = "MemTotal:       527000000 kB\nMemAvailable:   430000000 kB\nSwapTotal: 0 kB\n"

    def test_meminfo_psi(self):
        self.assertEqual(H.meminfo(self.MEM)["MemAvailable"], 430000000 * 1024)
        self.assertEqual(H.psi("some avg10=21.50 avg60=3.00 avg300=1.00 total=1\nfull avg10=6.10 avg60=0 avg300=0 total=2\n"),
                         {"some10": 21.5, "full10": 6.1})

    def test_bytes(self):
        fit = {"guard": {"mem_available_floor": 25 * GIB}}
        want = 430000000 * 1024 - 25 * GIB + H.SQUEEZE_MARGIN
        self.assertEqual(H.squeeze_bytes(fit, self.MEM), want)
        with self.assertRaises(ValueError):
            H.squeeze_bytes({}, self.MEM)
        with self.assertRaises(ValueError):
            H.squeeze_bytes({"guard": {"mem_available_floor": 600 * GIB}}, self.MEM)

    def test_stop_reason(self):
        pre = [{"name": "qwen38-a-hr", "running": True, "restarts": 0, "health": None}]
        ok = [{"name": "qwen38-a-hr", "running": True, "restarts": 0, "health": None}]
        self.assertIsNone(H.stop_reason(pre, ok, [], {}, {}, 100))
        self.assertIn("stopped", H.stop_reason(pre, [], [], {}, {}, 100))
        self.assertIn("restarted", H.stop_reason(pre, [dict(ok[0], restarts=1)], [], {}, {}, 100))
        self.assertIn("unhealthy", H.stop_reason(pre, [dict(ok[0], health="unhealthy")], [], {}, {}, 100))
        self.assertIn("twice", H.stop_reason(pre, ok, [], {}, {30000: 2}, 100))
        lat = [(95, 30000, 1.1, True), (98, 30000, 1.3, True), (99, 30001, 0.2, True)]
        self.assertIn(":30000 latency", H.stop_reason(pre, ok, lat, {30000: 0.5, 30001: 0.2}, {}, 100))
        self.assertIsNone(H.stop_reason(pre, ok, lat, {30000: 0.7, 30001: 0.2}, {}, 100))
        # Samples older than the window do not count.
        self.assertIsNone(H.stop_reason(pre, ok, lat, {30000: 0.5}, {}, 200))

    def test_oom_lines(self):
        pre = "[1.0] boot\n"
        post = pre + ("[9.0] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,"
                      "task_memcg=/system.slice/stress.scope,task=stress-ng-vm,pid=7,uid=0\n"
                      "[9.1] Out of memory: Killed process 7 (stress-ng-vm) total-vm:1kB\n"
                      "[9.2] oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=/x,"
                      "task_memcg=/system.slice/docker-ab.scope/kubepods/burstable/pod1/c,task=ovms,pid=8,uid=5000\n"
                      "[9.3] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,"
                      "task_memcg=/system.slice/docker-cd.scope,task=python3,pid=9,uid=0\n")
        kills = H.oom_kills(pre, post)
        self.assertEqual(len(kills), 3)
        out = H.outside_ours(kills)
        self.assertEqual(len(out), 1)
        self.assertIn("task=python3", out[0])


class RedactTest(unittest.TestCase):
    def test_secret_labels_are_blanked(self):
        ev = json.dumps({"Type": "container", "Action": "start", "Actor": {"ID": "x", "Attributes": {
            "name": "k3d-gpu-server-0", "k3d.cluster.token": "toyjlBfSsecret", "image": "k3s"}}})
        out = H.redact_events(ev + "\nnot json\n")
        self.assertNotIn("toyjlBfS", out)
        e = json.loads(out)
        self.assertEqual(e["Actor"]["Attributes"]["k3d.cluster.token"], "REDACTED")
        self.assertEqual(e["Actor"]["Attributes"]["name"], "k3d-gpu-server-0")
        self.assertEqual(out.count("\n"), 1)


class FakeProc:
    def __init__(self, polls, on_poll=None):
        self.left, self.on_poll, self.returncode = polls, on_poll, None

    def poll(self):
        if self.on_poll:
            self.on_poll(self.left)
        if self.left <= 0:
            self.returncode = 0
            return 0
        self.left -= 1
        return None

    def wait(self, timeout=None):
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self):
        self.returncode = -9


class RunSqueezeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state = os.path.join(self.tmp, "state")
        self.proc = os.path.join(self.tmp, "proc")
        os.makedirs(os.path.join(self.proc, "pressure"))
        os.makedirs(self.state)
        with open(os.path.join(self.state, "agent-fit.json"), "w") as f:
            json.dump({"guard": {"mem_available_floor": 25 * GIB}}, f)
        with open(os.path.join(self.proc, "meminfo"), "w") as f:
            f.write("MemAvailable:   430000000 kB\n")
        with open(os.path.join(self.proc, "pressure", "memory"), "w") as f:
            f.write("some avg10=0.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n")
        self.t = [1000.0]
        self.cmds = []

    def clock(self):
        return self.t[0]

    def sleep(self, s):
        self.t[0] += s

    def fake_run(self, args, capture_output=True, text=True, timeout=None):
        self.cmds.append(args)

        class P:
            returncode = 0
            stdout = ""
        if args[:2] == ["docker", "ps"]:
            P.stdout = "c1\n"
        elif args[:2] == ["docker", "inspect"]:
            P.stdout = json.dumps([{"Name": "/qwen38-a-hr", "RestartCount": 0, "State": {"Running": True, "OOMKilled": False}}])
        elif "dmesg" in args:
            P.stdout = "[1.0] boot\n"
        return P

    def opener(self, req, timeout=None):
        return Resp({"choices": [{"message": {"content": "OK"}}]})

    def test_guard_acts_then_the_squeeze_ends(self):
        glog = os.path.join(self.state, "agent-guard.log")
        with open(glog, "w") as f:
            f.write("agent guard: watching\n")

        def on_poll(left):
            if left == 995:
                with open(glog, "a") as f:
                    f.write("==> agent guard: MemAvailable 20.0 GiB < floor 25.0 GiB: emergency, straight to the "
                            "lowest rung: 1 seqs x 4096 tokens\n")
        procs = []

        def popen(cmd, stdout=None, stderr=None):
            procs.append(cmd)
            return FakeProc(1000, on_poll)
        st = H.run_squeeze(os.path.join(self.tmp, "sq"), "ovms", "/x/stress-ng", self.state, {"qwen38-a-hr"},
                           pre_s=20, step=5, probe_s=0.01, after_s=30, run=self.fake_run, popen=popen,
                           opener=self.opener, clock=self.clock, sleep=self.sleep, proc=self.proc)
        self.assertTrue(st["ran"])
        self.assertIsNone(st["stopped"])
        self.assertIsNotNone(st["guard_acted"])
        self.assertEqual(st["bytes"], 430000000 * 1024 - 25 * GIB + H.SQUEEZE_MARGIN)
        cmd = procs[0]
        self.assertEqual(cmd[:6], ["sudo", "-n", "systemd-run", "--quiet", "--scope", "--unit"])
        self.assertIn("MemoryMax=%d" % (st["bytes"] + 2 * GIB), cmd)
        self.assertEqual(cmd[cmd.index("choom"):cmd.index("choom") + 4], ["choom", "-n", "1000", "--"])
        self.assertIn(["sudo", "-n", "systemctl", "stop", st["unit"] + ".scope"], self.cmds)
        d = os.path.join(self.tmp, "sq")
        for f in ("status.json", "containers-pre.json", "containers-post.json", "dmesg-pre.txt", "dmesg-post.txt",
                  "mem.jsonl", "guard.log", "docker-events.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)
        self.assertIn("1 seqs x 4096 tokens", rd(os.path.join(d, "guard.log")))
        zero, adm, _ = H.clause_f(d)
        self.assertEqual((zero, adm), ("holds", "holds"))

    def test_no_floor_no_squeeze(self):
        os.remove(os.path.join(self.state, "agent-fit.json"))
        st = H.run_squeeze(os.path.join(self.tmp, "sq"), "ovms", "/x/stress-ng", self.state, {"qwen38-a-hr"},
                           pre_s=10, step=5, probe_s=0.01, after_s=10, run=self.fake_run, popen=None,
                           opener=self.opener, clock=self.clock, sleep=self.sleep, proc=self.proc)
        self.assertFalse(st["ran"])
        self.assertIn("no squeeze size", st["why"])
        self.assertEqual(H.clause_f(os.path.join(self.tmp, "sq"))[:2], ("inconclusive", "inconclusive"))


def rd(path):
    with open(path) as f:
        return f.read()


def rj(path):
    with open(path) as f:
        return json.load(f)


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)


def node(xe=4, dsa=3, amx=True):
    labels = {"feature.node.kubernetes.io/cpu-cpuid.%s" % f: "true" for f in H.AMX_LABELS} if amx else {}
    alloc = {"cpu": "96", "gpu.intel.com/xe": str(xe)}
    if dsa:
        alloc["dsa.intel.com/wq-user-shared"] = str(dsa)
    return {"items": [{"metadata": {"labels": labels}, "status": {"allocatable": alloc}}]}


class ScoreTest(unittest.TestCase):
    def out(self, gemma_landed=True, xe=4, squeeze=True, flash_fit="moe-offload 4x32768"):
        o = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, o)
        write(os.path.join(o, "pins.json"), {r: {"pin": p, "sha": p, "equal": True} for r, p in H.PINS.items()})
        for f in ("inventory-diff.txt", "clusters-pre.txt", "dirty.txt"):
            write(os.path.join(o, f), "")
        write(os.path.join(o, "commit.txt"), "abc\n")
        write(os.path.join(o, "commit-end.txt"), "abc\n")
        steps = [{"kind": "user", "cmd": f, "rc": 0, "env_k3d_gpu": []} for f in H.USER_FORMS]
        if not gemma_landed:
            steps[2]["rc"] = 1
        with open(os.path.join(o, "steps.jsonl"), "w") as f:
            f.write("".join(json.dumps(s) + "\n" for s in steps))
        write(os.path.join(o, "node.json"), node(xe=xe))
        gem = H.placement("", deployment(), rc=0) if gemma_landed else H.placement(REFUSAL, None, rc=1)
        write(os.path.join(o, "m-gemma", "placement.json"), gem)
        write(os.path.join(o, "m-qwen27b", "placement.json"),
              H.placement("", deployment(model="OpenVINO/Qwen3.8-27B-int4-ov"), rc=0))
        write(os.path.join(o, "m-flashnext", "placement.json"),
              H.placement("", deployment(fit=flash_fit, model="ggml-org/Qwen3.8-Flash-Next-GGUF"), rc=0))
        if gemma_landed:
            write(os.path.join(o, "b24", "status.json"), {"ran": True, "arm": "Z"})
            pd = os.path.join(o, "b24", "pairs")
            os.makedirs(pd)
            with open(os.path.join(pd, "attempts.jsonl"), "w") as f:
                for j in range(24):
                    f.write(json.dumps({"arm": "Z", "outcome": "pass_budget" if j < 19 else "fail_graded"}) + "\n")
            with open(os.path.join(pd, "turns.jsonl"), "w") as f:
                for _ in range(40):
                    f.write(json.dumps(turn("Z", 41, 1.0, 3.0)) + "\n")      # 20 tok/s
        else:
            write(os.path.join(o, "b24", "status.json"), {"ran": False, "why": "Gemma did not land"})
        sq = os.path.join(o, "squeeze")
        if squeeze:
            write(os.path.join(sq, "status.json"), {"ran": True, "stopped": None})
            write(os.path.join(sq, "containers-pre.json"), [{"name": "qwen38-a-hr", "running": True, "restarts": 0}])
            write(os.path.join(sq, "containers-post.json"), [{"name": "qwen38-a-hr", "running": True, "restarts": 0}])
            write(os.path.join(sq, "dmesg-pre.txt"), "")
            write(os.path.join(sq, "dmesg-post.txt"), "")
            write(os.path.join(sq, "docker-events.jsonl"), "")
            write(os.path.join(sq, "guard.log"), "agent guard: PSI some avg10 25.0 >= 20.0: 2 seqs x 16384 tokens\n")
        else:
            write(os.path.join(sq, "status.json"), {"ran": False, "why": "no agent of this run holds its model"})
        return o

    def test_supported(self):
        r = H.score(self.out())
        self.assertTrue(r["valid"])
        self.assertEqual(r["verdict"], "SUPPORTED", r["clauses"])
        # 20.0 tok/s >= 0.90 x 20.50, 19/24 >= 0.7917 - 0.05
        self.assertAlmostEqual(r["c"]["measured"]["decode_tok_s"], 20.0)

    def test_refuted_by_d(self):
        r = H.score(self.out(xe=2))
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertEqual(r["clauses"]["d"], "fails")
        self.assertIn("Arc Pro B70: gpu.intel.com/xe 2 of 4", r["d"]["missing"])

    def test_refused_gemma(self):
        r = H.score(self.out(gemma_landed=False, squeeze=False))
        c = r["clauses"]
        self.assertEqual((c["a"], c["b"], c["c"], c["e"], c["f_kills"], c["f_guard"]),
                         ("holds", "fails", "fails", "fails", "inconclusive", "inconclusive"))
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("k3d-gpu agent up --model google/gemma-4-26B-A4B-it exited 1", r["b"]["stops"])

    def test_flags_count(self):
        o = self.out()
        with open(os.path.join(o, "steps.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "user", "cmd": ["k3d-gpu", "up"], "rc": 0, "env_k3d_gpu": ["K3D_GPU_CLUSTER"]}) + "\n")
        r = H.score(o)
        self.assertEqual(r["clauses"]["a"], "fails")
        self.assertEqual(r["a"]["flags"], ["K3D_GPU_CLUSTER on k3d-gpu up"])

    def test_an_interlock_deletion_outside_the_agent_namespace_voids_the_run(self):
        o = self.out()
        write(os.path.join(o, "interlock", "events.jsonl"),
              json.dumps({"kind": "Pod", "namespace": "kube-system", "name": "intel-gpu-plugin-wgblj"}) + "\n")
        r = H.score(o)
        self.assertEqual(r["verdict"], "NOT VALID")
        self.assertIn("the GPU interlock deleted 1 object(s) outside k3d-gpu-agent (k3d-gpu's own)", r["invalid_why"])
        write(os.path.join(o, "interlock", "events.jsonl"),
              json.dumps({"kind": "Deployment", "namespace": "k3d-gpu-agent", "name": "ovms"}) + "\n")
        self.assertTrue(H.score(o)["valid"])

    def test_not_valid(self):
        o = self.out()
        write(os.path.join(o, "commit-end.txt"), "def\n")
        r = H.score(o)
        self.assertEqual(r["verdict"], "NOT VALID")
        self.assertIn("the commit changed during the run, or was not recorded", r["invalid_why"])

    def test_decode_short(self):
        o = self.out()
        pd = os.path.join(o, "b24", "pairs", "turns.jsonl")
        with open(pd, "w") as f:
            for _ in range(40):
                f.write(json.dumps(turn("Z", 41, 1.0, 4.0)) + "\n")     # 13.3 tok/s < 18.45
        r = H.score(o)
        self.assertEqual(r["clauses"]["c"], "fails")


# --- run.sh end to end ----------------------------------------------------------------------

STUB_K3D_GPU = r'''#!/usr/bin/env python3
import json, os, sys, time
S = os.environ["H58T_STATE"]
SCEN = os.environ.get("H58T_SCEN", "refuse")
GEMMA, QWEN27, FLASH = "google/gemma-4-26B-A4B-it", "Qwen/Qwen3.8-27B", "Intel/Qwen3.8-Flash-Next-W4A16-AutoRound"
a = sys.argv[1:]
with open(os.path.join(S, "k3d-gpu.calls"), "a") as f:
    f.write(json.dumps({"argv": a, "k3d_gpu_env": sorted(k for k in os.environ if k.startswith("K3D_GPU_"))}) + "\n")
D = os.path.join(S, "deploys")
os.makedirs(D, exist_ok=True)
def deploy(name, fit, device, gpu, model):
    res = {"limits": {"gpu.intel.com/xe": "1"}} if gpu else {"limits": {"memory": "20Gi"}}
    d = {"kind": "Deployment", "metadata": {"name": name, "namespace": "k3d-gpu-agent", "uid": "u%f" % time.time()},
         "spec": {"replicas": 1, "template": {"metadata": {"annotations": {"k3d-gpu.io/agent-fit": fit, "k3d-gpu.io/agent-device": device}},
                  "spec": {"initContainers": [{"name": "pull", "args": ["--source_model=" + model]}], "containers": [{"name": name, "resources": res}]}}},
         "status": {"readyReplicas": 1}}
    json.dump(d, open(os.path.join(D, name + ".json"), "w"))
st = os.path.join(os.environ["HOME"], ".local", "state", "k3d-gpu")
if a == ["--version"]:
    print("k3d-gpu 0.1.83"); sys.exit(0)
if a == ["up"]:
    open(os.path.join(S, "cluster"), "w").close()
    os.makedirs(st, exist_ok=True)
    with open(os.path.join(st, "kubeconfig-gpu"), "w") as f:
        f.write("apiVersion: v1\nusers: [{name: admin, user: {client-key-data: c2VjcmV0}}]\n")
    print("Accelerators: intel amx"); sys.exit(0)
if a == ["down"]:
    os.remove(os.path.join(S, "cluster")); sys.exit(0)
if a[:1] == ["detect"]:
    print("{}" if "--json" in a else "intel: 4 x Battlemage; amx"); sys.exit(0)
if a[:1] == ["plan"]:
    print("summary: picked x"); sys.exit(0)
if a[:2] == ["agent", "down"]:
    for f in os.listdir(D):
        os.remove(os.path.join(D, f))
    sys.exit(0)
if a[:3] == ["agent", "up", "--model"]:
    m = a[3]
    if m == GEMMA and SCEN == "land":
        deploy("ovms", "cpu 4x32768", "CPU", False, "OpenVINO/gemma-4-26b-a4b-it-int4-ov")
        os.makedirs(st, exist_ok=True)
        json.dump({"guard": {"mem_available_floor": 25 * 1024 ** 3}}, open(os.path.join(st, "agent-fit.json"), "w"))
        print("fit: OpenVINO/gemma-4-26b-a4b-it-int4-ov on CPU (AMX) via ovms, context 32768 x 4 seqs, host 20 GiB")
        sys.exit(0)
    if m in (GEMMA, QWEN27):
        print("ERROR: fit: OpenVINO/x-int4-ov does not fit this host: weights + overhead 15.7 GiB exceed the GPU budget 15.2 GiB; not starting a server that cannot fit")
        sys.exit(1)
    deploy("ovms", "gpu 4x20480", "GPU", True, "OpenVINO/Qwen3-4B-int4-ov")
    for _ in range(150):
        if not os.path.exists(os.path.join(D, "ovms.json")):
            break
        time.sleep(0.1)
    print("ERROR: server not ready - kubectl -n k3d-gpu-agent logs deploy/ovms"); sys.exit(1)
sys.exit(2)
'''

STUB_KUBECTL = r'''#!/usr/bin/env python3
import json, os, sys
S = os.environ["H58T_STATE"]
D = os.path.join(S, "deploys")
a, rest, i = sys.argv[1:], [], 0
while i < len(a):
    if a[i] in ("--context", "-n", "--kubeconfig"):
        i += 2; continue
    rest.append(a[i]); i += 1
if rest[:1] == ["config"]:
    print("k3d-gpu" if "get-contexts" in rest else "apiVersion: v1\nkind: Config"); sys.exit(0)
if rest[:1] == ["version"]:
    print("Client Version: v1.37.2"); sys.exit(0)
if not os.path.exists(os.path.join(S, "cluster")):
    print('error: context "k3d-gpu" does not exist', file=sys.stderr); sys.exit(1)
deps = [json.load(open(os.path.join(D, f))) for f in sorted(os.listdir(D))] if os.path.isdir(D) else []
plugin = {"kind": "Pod", "metadata": {"name": "intel-gpu-plugin-a", "namespace": "kube-system", "uid": "plugin"},
          "spec": {"containers": [{"name": "intel-gpu-plugin"}], "volumes": [{"name": "d", "hostPath": {"path": "/dev/dri/renderD129"}}]}}
if rest[:2] == ["get", "node"]:
    if any(x.startswith("jsonpath") for x in rest):
        sys.stdout.write("2"); sys.exit(0)
    labels = {"feature.node.kubernetes.io/cpu-cpuid.%s" % f: "true" for f in ("AMXTILE", "AMXBF16", "AMXINT8", "AMXFP16")}
    print(json.dumps({"items": [{"metadata": {"labels": labels}, "status": {"allocatable": {"cpu": "96", "gpu.intel.com/xe": "2"}}}]}))
    sys.exit(0)
if rest[:2] == ["get", "deployments,pods"] and os.environ.get("H58T_BLIND"):
    print("error: the server is unreachable", file=sys.stderr); sys.exit(1)
if rest[:2] == ["get", "deployments,pods"]:
    print(json.dumps({"items": deps + [plugin]})); sys.exit(0)
if rest[:2] == ["get", "deployments"]:
    print(json.dumps({"items": deps})); sys.exit(0)
if rest[:2] == ["get", "pods"]:
    print("NAMESPACE NAME READY"); sys.exit(0)
if rest[:1] == ["scale"]:
    sys.exit(0)
if rest[:1] == ["delete"]:
    kind, _, name = rest[1].partition("/")
    if name.startswith("intel-gpu-plugin"):
        open(os.path.join(S, "plugin-deleted"), "a").write(name + "\n")
    p = os.path.join(D, name + ".json")
    if kind == "deployment" and os.path.exists(p):
        os.remove(p)
    sys.exit(0)
sys.exit(0)
'''

STUB_K3D = r'''#!/usr/bin/env python3
import os, sys
S = os.environ["H58T_STATE"]
if sys.argv[1:3] == ["cluster", "list"]:
    print('[{"name": "gpu"}]' if os.path.exists(os.path.join(S, "cluster")) or os.environ.get("H58T_CLUSTER") else "[]")
elif sys.argv[1:2] == ["version"]:
    print("k3d version v5.9.0")
'''

STUB_DOCKER = r'''#!/usr/bin/env python3
import json, sys, time
a = sys.argv[1:]
if a[:2] == ["ps", "-q"]:
    print("c1")
elif a[:1] == ["inspect"]:
    print(json.dumps([{"Name": "/qwen38-a-hr", "Id": "c1" * 8, "Config": {"Image": "sglang"}, "RestartCount": 0,
                       "State": {"StartedAt": "2026-10-30", "Running": True, "OOMKilled": False}}]))
elif a[:1] == ["events"] and "--until" not in a:
    print(json.dumps({"Type": "container", "Action": "create", "Actor": {"ID": "n", "Attributes": {
        "name": "k3d-gpu-server-0", "k3d.cluster.token": "SECRETTOKEN"}}}), flush=True)
    time.sleep(600)
elif a[:1] == ["version"]:
    print("docker 29.8.0")
'''

STUB_CURL = r'''#!/usr/bin/env python3
import os, sys
S = os.environ["H58T_STATE"]
url = [x for x in sys.argv[1:] if x.startswith("http")][0]
c = os.path.join(S, "served")
n = int(open(c).read()) if os.path.exists(c) else 0
if url.endswith("/metrics"):
    print("sglang:num_requests_total{x=\"1\"} %d" % n)
else:
    open(c, "w").write(str(n + 1))
    print('{"choices": [{"message": {"content": "OK"}}]}')
'''

STUB_SH = {
    "sudo": '#!/bin/sh\n[ "$1" = -n ] && shift\n[ "$1" = dmesg ] && { echo "[1.0] boot"; exit 0; }\nexit 1\n',
    "git": '#!/bin/sh\ncase "$*" in *rev-parse*) echo 0123456789abcdef ;; esac\nexit 0\n',
    "helm": '#!/bin/sh\necho v4.3.1\n',
    "uv": '#!/bin/sh\necho uv 0.13.6\n',
    "stress-ng": '#!/bin/sh\necho "stress-ng, version 0.22.01 (gcc)"\n',
}

STUB_LIVE = r'''#!/usr/bin/env bash
pd=${LIVE_PAIRS_DIR:?}
mkdir -p "${pd}"
[ "$1" = H58 ] && [ "${LIVE_SOLO}" = Z ] && [ "${LIVE_MODE}" = solo ] && [ -s "${LIVE_Z_KUBECONFIG}" ] || exit 9
echo "${LIVE_Z_DEP}" > "${pd}/dep.txt"
for j in $(seq 0 23); do
    o=pass_budget; [ "${j}" -ge 20 ] && o=fail_graded
    echo "{\"arm\": \"Z\", \"j\": ${j}, \"outcome\": \"${o}\"}" >> "${pd}/attempts.jsonl"
    echo "{\"arm\": \"Z\", \"status\": 200, \"error\": null, \"completion_tokens\": 51, \"ttft_s\": 1.0, \"e2e_s\": 3.0}" >> "${pd}/turns.jsonl"
done
'''


class HubStub(http.server.BaseHTTPRequestHandler):
    shas = {}

    def do_GET(self):
        repo = self.path.split("/api/models/", 1)[1]
        body = json.dumps({"sha": self.shas.get(repo)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class RunShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        HubStub.shas = dict(H.PINS)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HubStub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.bin = os.path.join(self.tmp, "bin")
        self.state = os.path.join(self.tmp, "state")
        self.home = os.path.join(self.tmp, "home")
        for d in (self.bin, self.state, os.path.join(self.home, ".cache", "k3d-gpu")):
            os.makedirs(d)
        with open(os.path.join(self.home, ".cache", "k3d-gpu", "marker"), "w") as f:
            f.write("earlier metadata")
        stubs = dict(STUB_SH, **{"k3d-gpu": STUB_K3D_GPU, "kubectl": STUB_KUBECTL, "k3d": STUB_K3D, "docker": STUB_DOCKER,
                                 "curl": STUB_CURL})
        for name, body in stubs.items():
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(body.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            os.chmod(p, 0o755)
        live = os.path.join(self.tmp, "live.sh")
        with open(live, "w") as f:
            f.write(STUB_LIVE)
        ref = os.path.join(self.tmp, "inventory-ref.txt")
        with open(ref, "w") as f:
            f.write(subprocess.run(["bash", os.path.join(REPO, "bench", "eatflow", "crescent", "inventory.sh")],
                                   capture_output=True, text=True).stdout)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("K3D_GPU_")}
        self.env.update(PATH=self.bin + os.pathsep + os.environ["PATH"], HOME=self.home, H58T_STATE=self.state,
                        H58_K3D_GPU=os.path.join(self.bin, "k3d-gpu"), H58_LIVE=live, H58_INVENTORY_REF=ref,
                        H58_HF_API="http://127.0.0.1:%d/api/models" % self.srv.server_address[1],
                        H58_BENCH=os.path.join(self.tmp, "bench"), H58_SETTLE_S="0.05")
        self.out = os.path.join(self.tmp, "out")

    def run_sh(self, **env):
        e = dict(self.env, **env)
        return subprocess.run(["bash", os.path.join(HERE, "run.sh"), self.out], env=e, capture_output=True,
                              text=True, timeout=600)

    def steps(self):
        return H.read_jsonl(os.path.join(self.out, "steps.jsonl"))

    def test_refused_gemma_and_blocked_flash(self):
        p = self.run_sh()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        user = [s["cmd"] for s in self.steps() if s["kind"] == "user"]
        self.assertEqual(user, H.USER_FORMS)
        self.assertTrue(all(not s["env_k3d_gpu"] for s in self.steps()))
        kinds = [(s["kind"], s["cmd"][1]) for s in self.steps()]
        self.assertIn(("teardown", "agent"), kinds)
        self.assertIn(("teardown", "down"), kinds)
        # The launcher saw no K3D_GPU_* variable on any call.
        calls = H.read_jsonl(os.path.join(self.state, "k3d-gpu.calls"))
        self.assertTrue(calls and all(not c["k3d_gpu_env"] for c in calls))
        pl = {m: rj(os.path.join(self.out, d, "placement.json")) for m, (d, _) in H.MODELS.items()}
        self.assertEqual(pl[H.GEMMA]["placement"], "refused")
        self.assertEqual(pl[H.QWEN27]["placement"], "refused")
        self.assertEqual((pl[H.FLASH]["placement"], pl[H.FLASH]["blocked"]), ("gpu", True))
        self.assertFalse(os.path.exists(os.path.join(self.state, "plugin-deleted")))
        ev = rd(os.path.join(self.out, "docker-events.jsonl"))
        self.assertIn('"k3d.cluster.token": "REDACTED"', ev)
        self.assertNotIn("SECRETTOKEN", ev)
        ev = H.read_jsonl(os.path.join(self.out, "interlock", "events.jsonl"))
        self.assertEqual([(e["kind"], e["name"], e["keys"]) for e in ev], [("Deployment", "ovms", ["gpu.intel.com/xe"])])
        self.assertFalse(rj(os.path.join(self.out, "b24", "status.json"))["ran"])
        self.assertFalse(rj(os.path.join(self.out, "squeeze", "status.json"))["ran"])
        sc = rj(os.path.join(self.out, "score.json"))
        self.assertTrue(sc["valid"], sc["invalid_why"])
        self.assertEqual(sc["verdict"], "REFUTED")
        self.assertEqual(sc["clauses"], {"a": "holds", "b": "fails", "c": "fails", "d": "fails", "e": "fails",
                                         "f_kills": "inconclusive", "f_guard": "inconclusive"})
        # The cache set aside before the run is back, and the run's own went into OUT.
        self.assertEqual(rd(os.path.join(self.home, ".cache", "k3d-gpu", "marker")), "earlier metadata")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cache", "k3d-gpu.h58-aside")))
        self.assertIn("restored", rd(os.path.join(self.out, "cache-aside.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.state, "cluster")))
        # The cluster's kubeconfig (k3d-gpu's state directory) is not among the records.
        found = [os.path.join(r, f) for r, _, fs in os.walk(self.out) for f in fs if "kubeconfig" in f]
        self.assertEqual(found, [])
        self.assertTrue(os.path.isdir(os.path.join(self.out, "k3d-gpu-dirs", "2-k3d-gpu")))
        for f in ("frontdoor-pre.txt", "frontdoor-post.txt"):
            self.assertEqual(rd(os.path.join(self.out, f)).count("pass=1"), 2, f)

    def test_gemma_lands_on_the_cpu(self):
        p = self.run_sh(H58T_SCEN="land")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        b24 = rj(os.path.join(self.out, "b24", "status.json"))
        self.assertTrue(b24["ran"], b24)
        self.assertEqual(rd(os.path.join(self.out, "b24", "pairs", "dep.txt")).strip(), "ovms")
        self.assertFalse(os.path.exists(os.path.join(self.out, "b24", "kubeconfig")))
        sc = rj(os.path.join(self.out, "score.json"))
        self.assertEqual(sc["clauses"]["c"], "holds", sc["c"])
        self.assertEqual(sc["clauses"]["b"], "holds")
        self.assertEqual(sc["clauses"]["d"], "fails")
        self.assertEqual(sc["verdict"], "REFUTED")
        self.assertIn(("harness", "bench/eatflow/live.sh"), [(s["kind"], s["cmd"][0]) for s in self.steps()])

    def test_a_blind_interlock_stops_before_any_agent_up(self):
        p = self.run_sh(H58T_BLIND="1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("the GPU interlock is not reading the cluster: stop", rd(os.path.join(self.out, "run.log")))
        cmds = [s["cmd"] for s in self.steps()]
        self.assertFalse(any(c[1:3] == ["agent", "up"] for c in cmds), cmds)
        self.assertIn(["k3d-gpu", "down"], cmds)
        self.assertFalse(os.path.exists(os.path.join(self.state, "cluster")))

    def test_moved_pin_does_not_start(self):
        HubStub.shas[H.GEMMA] = "f" * 40
        p = self.run_sh()
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("P1 pin check rc=3", rd(os.path.join(self.out, "not-started.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "steps.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".cache", "k3d-gpu", "marker")))

    def test_existing_cluster_or_flag_does_not_start(self):
        p = self.run_sh(H58T_CLUSTER="1")
        self.assertEqual(p.returncode, 3)
        self.assertIn("k3d clusters exist", rd(os.path.join(self.out, "not-started.txt")))
        shutil.rmtree(self.out)
        p = self.run_sh(K3D_GPU_AGENT_DEVICE="CPU")
        self.assertEqual(p.returncode, 3)
        self.assertIn("K3D_GPU_AGENT_DEVICE", rd(os.path.join(self.out, "not-started.txt")))


class StressNgTest(unittest.TestCase):
    def test_check_refuses_a_missing_pin(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = subprocess.run(["bash", os.path.join(HERE, "stress-ng.sh"), "check"], env=dict(os.environ, H58_BENCH=tmp),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("does not point at", p.stdout)

    def test_check_accepts_the_pinned_build(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        d = os.path.join(tmp, "opt", "stress-ng-0.22.01")
        os.makedirs(d)
        os.makedirs(os.path.join(tmp, "bin"))
        b = os.path.join(d, "stress-ng")
        with open(b, "w") as f:
            f.write('#!/bin/sh\necho "stress-ng, version 0.22.01 (gcc 15.2.0, x86_64 Linux 7.0.0)"\n')
        os.chmod(b, 0o755)
        subprocess.run("sha256sum %s > %s.sha256" % (b, b), shell=True, check=True)
        os.symlink(b, os.path.join(tmp, "bin", "stress-ng"))
        p = subprocess.run(["bash", os.path.join(HERE, "stress-ng.sh"), "check"], env=dict(os.environ, H58_BENCH=tmp),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        with open(b, "a") as f:
            f.write("# changed\n")
        p = subprocess.run(["bash", os.path.join(HERE, "stress-ng.sh"), "check"], env=dict(os.environ, H58_BENCH=tmp),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("differs from its recorded sha256", p.stdout)


class ShellcheckTest(unittest.TestCase):
    def test_scripts(self):
        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck not on PATH")
        p = subprocess.run([sc, "-S", "warning", os.path.join(HERE, "run.sh"), os.path.join(HERE, "stress-ng.sh")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == "__main__":
    unittest.main()

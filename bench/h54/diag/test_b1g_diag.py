#!/usr/bin/env python3
"""Tests for bench/h54/diag/b1g_diag.py (the UNSCORED B1g stall diagnostic's helpers): the
render reproduces B1g's committed container (args, env, chat template), the thread snapshot
reads a live process tree, and the stall watcher records a stall against a stub server whose
token counter stops. No docker, no network beyond 127.0.0.1."""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b1g_diag  # noqa: E402

DEPLOY = os.path.join(HERE, "..", "results", "2026-10-21", "H54a", "hold2", "B1g", "deploy.yaml")

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


@unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
class Render(unittest.TestCase):
    def test_render_matches_the_committed_deployment(self):
        docs = [d for d in yaml.safe_load_all(open(DEPLOY)) if d]
        dep = next(d for d in docs if d.get("kind") == "Deployment")
        (c,) = dep["spec"]["template"]["spec"]["containers"]
        with tempfile.TemporaryDirectory() as t:
            env, args = b1g_diag.render(DEPLOY, t)
            self.assertEqual(json.load(open(os.path.join(t, "args.json"))), [str(a) for a in c["args"]])
            lines = open(os.path.join(t, "env.list")).read().splitlines()
            self.assertIn("VLLM_CPU_KVCACHE_SPACE=40", lines)
            self.assertIn("VLLM_RPC_TIMEOUT=100000", lines)
            self.assertIn("TRITON_CACHE_DIR=/tmp/.triton", lines)
            self.assertFalse(any(ln.startswith("VLLM_CPU_OMP_THREADS_BIND=") for ln in lines))
            tmpl = os.path.join(t, "templates", "tool_chat_template_gemma4.jinja")
            cm = next(d for d in docs if d.get("kind") == "ConfigMap" and "chat-templates" in d["metadata"]["name"])
            self.assertEqual(hashlib.sha256(open(tmpl, "rb").read()).hexdigest(),
                             hashlib.sha256(cm["data"]["tool_chat_template_gemma4.jinja"].encode()).hexdigest())
            r = json.load(open(os.path.join(t, "render.json")))
            self.assertIsNone(r["resources"])
            self.assertTrue(r["securityContext"]["readOnlyRootFilesystem"])
            self.assertEqual(r["securityContext"]["runAsUser"], 1001)

    def test_render_refuses_another_image(self):
        with tempfile.TemporaryDirectory() as t:
            bad = os.path.join(t, "deploy.yaml")
            with open(bad, "w") as f:
                f.write(open(DEPLOY).read().replace("b0e6f8604413a8bf", "0000000000000000"))
            with self.assertRaises(SystemExit):
                b1g_diag.render(bad, os.path.join(t, "out"))


class Snap(unittest.TestCase):
    def test_snapshot_of_a_live_tree(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with tempfile.TemporaryDirectory() as t:
                rec = b1g_diag.snap(os.getpid(), os.path.join(t, "s.json"))
                pids = [p["pid"] for p in rec["procs"]]
                self.assertIn(os.getpid(), pids)
                self.assertIn(child.pid, pids)
                me = next(p for p in rec["procs"] if p["pid"] == os.getpid())
                self.assertTrue(me["threads"])
                th = me["threads"][0]
                for k in ("tid", "state", "ticks", "cpu", "wchan", "syscall", "vcs", "ncs", "stack"):
                    self.assertIn(k, th)
        finally:
            child.kill()
            child.wait()


class RunningThreads(unittest.TestCase):
    def test_only_engine_threads_in_state_r(self):
        rec = {"procs": [
            {"comm": "VLLM::Worker", "threads": [{"tid": 1, "state": "R"}, {"tid": 2, "state": "S"}]},
            {"comm": "VLLM::EngineCor", "threads": [{"tid": 3, "state": "S"}]},
            {"comm": "python3", "threads": [{"tid": 4, "state": "R"}]}]}
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "threads-b.json")
            json.dump(rec, open(f, "w"))
            self.assertEqual(b1g_diag.running_threads(f), [(1, "VLLM::Worker")])
            self.assertEqual(b1g_diag.running_threads(os.path.join(t, "missing.json")), [])


class Stub(http.server.BaseHTTPRequestHandler):
    gen = [0.0]
    frozen = [False]

    def do_GET(self):
        if not self.frozen[0]:
            self.gen[0] += 10
        body = ("# HELP x\n"
                f'vllm:generation_tokens_total{{model_name="m"}} {self.gen[0]}\n'
                'vllm:num_requests_running{model_name="m"} 1.0\n').encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Watch(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        Stub.gen[0], Stub.frozen[0] = 0.0, False

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _watch(self, t, client, root):
        b1g_diag.STALL_S, b1g_diag.STEP_S, b1g_diag.AFTER_S = 0.6, 0.2, 2.0
        b1g_diag._sudo_self = lambda *a: subprocess.CompletedProcess(a, 0)   # no sudo in tests
        b1g_diag.PYSPY = ""
        return b1g_diag.watch(self.url, "/nonexistent/cpu.stat", t, client, root)

    def test_metrics_parse(self):
        m = b1g_diag.metrics(self.url)
        self.assertEqual(m["running"], 1.0)
        self.assertGreater(m["gen"], 0)

    def test_frozen_counter_is_a_stall(self):
        Stub.frozen[0] = True
        with tempfile.TemporaryDirectory() as t:
            rc = self._watch(t, os.getpid(), os.getpid())
            self.assertEqual(rc, 1)
            s = json.load(open(os.path.join(t, "stall.json")))
            self.assertIn("onset_ts", s)
            self.assertTrue(s["container_alive_at_end"])
            rows = [json.loads(x) for x in open(os.path.join(t, "watch.jsonl"))]
            self.assertTrue(all(r["running"] == 1.0 for r in rows))

    def test_moving_counter_ends_with_the_client(self):
        client = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        with tempfile.TemporaryDirectory() as t:
            rc = self._watch(t, client.pid, os.getpid())
            client.wait()
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(t, "nostall.json")))
            self.assertFalse(os.path.exists(os.path.join(t, "stall.json")))


if __name__ == "__main__":
    unittest.main()

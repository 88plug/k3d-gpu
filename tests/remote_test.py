#!/usr/bin/env python3
"""Tests for console/remote.py: remote OpenAI-compatible endpoints.

No network: a stub OpenAI server on 127.0.0.1 checks the bearer key, and a
second one stands in for "another host" a redirect points at. Every key here
is fake (sk-test-...).

  python3 tests/remote_test.py              # all tests
  UPDATE_GOLDEN=1 python3 tests/remote_test.py   # rewrite tests/remote/*.golden
"""

import base64
import contextlib
import http.server
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "console"))
import remote  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "remote")
KEY = "sk-test-0123456789abcdef"
KEY_B64 = base64.b64encode(KEY.encode()).decode()
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}


def golden(name, text):
    path = os.path.join(GOLDEN, name)
    if os.environ.get("UPDATE_GOLDEN"):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    with open(path) as f:
        return f.read()


class Stub:
    """OpenAI stub. models: the served ids; reply: the chat message; it records
    every request's path and Authorization header in .seen."""

    def __init__(self, models=("zai-org/GLM-5.3",), key=KEY, reply=None, redirect_to=None):
        self.models, self.key, self.seen = list(models), key, []
        self.reply = reply or {"content": "OK", "finish_reason": "stop"}
        self.redirect_to = redirect_to
        # path -> fn(auth header) -> (status, raw body bytes, [(header, value)]): canned
        # answers that bypass the key check (error pages, gateways, portals).
        self.routes = {}
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, headers=()):
                raw = json.dumps(body).encode()
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def handle_any(self, body=None):
                stub.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if self.path in stub.routes:
                    code, raw, headers = stub.routes[self.path](self.headers.get("Authorization") or "")
                    self.send_response(code)
                    for k, v in headers:
                        self.send_header(k, v)
                    if not any(k.lower() == "content-length" for k, _ in headers):
                        self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if stub.redirect_to:
                    self.send(302, {}, [("Location", stub.redirect_to + "/v1/models")])
                    return
                if stub.key and self.headers.get("Authorization") != f"Bearer {stub.key}":
                    self.send(401, {"error": {"message": "invalid api key"}})
                    return
                if self.path == "/v1/models":
                    self.send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in stub.models]})
                elif self.path == "/v1/chat/completions":
                    r = stub.reply
                    msg = {"role": "assistant", "content": r.get("content")}
                    if r.get("reasoning"):
                        msg["reasoning"] = r["reasoning"]
                    self.send(200, {"choices": [{"index": 0, "message": msg, "finish_reason": r["finish_reason"]}],
                                    "usage": {"completion_tokens": r.get("tokens", 2)}})
                else:
                    self.send(404, {"error": {"message": "not found"}})

            def do_GET(self):
                self.handle_any()

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.handle_any(json.loads(self.rfile.read(n) or b"{}"))

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def cli(*argv, env=None):
    """Runs remote.main in-process; returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = remote.main(list(argv), env=dict(env or {}))
    return rc, out.getvalue(), err.getvalue()


class Normalize(unittest.TestCase):
    TABLE = [
        ("https://llm.example.net", "https://llm.example.net/v1"),
        ("https://llm.example.net/", "https://llm.example.net/v1"),
        ("https://llm.example.net/v1", "https://llm.example.net/v1"),
        ("https://llm.example.net/v1/", "https://llm.example.net/v1"),
        ("  https://llm.example.net/v1  ", "https://llm.example.net/v1"),
        ("HTTPS://LLM.Example.net/v1", "https://llm.example.net/v1"),
        ("https://llm.example.net:8443/v1", "https://llm.example.net:8443/v1"),
        ("https://api.example.com/openai/v1", "https://api.example.com/openai/v1"),
        ("http://127.0.0.1:8000/v3", "http://127.0.0.1:8000/v3"),
        ("http://localhost:8000", "http://localhost:8000/v1"),
        ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
        ("http://ovms.k3d-gpu-agent.svc:8000/v3", "http://ovms.k3d-gpu-agent.svc:8000/v3"),
        ("http://vllm.k3d-gpu-agent.svc.cluster.local:8000/v1", "http://vllm.k3d-gpu-agent.svc.cluster.local:8000/v1"),
    ]
    BAD = [
        ("", "empty"),
        ("llm.example.net/v1", "http"),
        ("ftp://llm.example.net/v1", "http"),
        ("https://user:pw@llm.example.net/v1", "user"),
        ("https://llm.example.net/v1?x=1", "query"),
        ("https:///v1", "host"),
        ("https://llm.example.net:abc/v1", "port"),
    ]

    def test_table(self):
        for raw, want in self.TABLE:
            with self.subTest(raw=raw):
                self.assertEqual(remote.normalize_url(raw)[0], want)

    def test_chat_completions_suffix_is_stripped_with_a_warning(self):
        url, warns = remote.normalize_url("https://llm.example.net/v1/chat/completions")
        self.assertEqual(url, "https://llm.example.net/v1")
        self.assertTrue(any("chat/completions" in w for w in warns))

    def test_rejects(self):
        for raw, why in self.BAD:
            with self.subTest(raw=raw):
                with self.assertRaises(remote.RemoteError) as cm:
                    remote.normalize_url(raw)
                self.assertIn(why, str(cm.exception).lower())


class Precedence(unittest.TestCase):
    def r(self, env, flag=None):
        return remote.resolve_base(flag, env)

    def test_flag_beats_every_env(self):
        env = {"OPENAI_BASE_URL": "https://a.example/v1", "OPENAI_API_BASE": "https://b.example/v1",
               "OPENAI_HOST": "https://c.example"}
        self.assertEqual(self.r(env, "https://f.example"), ("https://f.example/v1", "--remote"))

    def test_env_order(self):
        self.assertEqual(self.r({"OPENAI_BASE_URL": "https://a.example/v1", "OPENAI_API_BASE": "https://b.example/v1",
                                 "OPENAI_HOST": "https://c.example"}), ("https://a.example/v1", "OPENAI_BASE_URL"))
        self.assertEqual(self.r({"OPENAI_API_BASE": "https://b.example/v1", "OPENAI_HOST": "https://c.example"}),
                         ("https://b.example/v1", "OPENAI_API_BASE"))
        self.assertEqual(self.r({"OPENAI_HOST": "https://c.example"}), ("https://c.example/v1", "OPENAI_HOST"))

    def test_empty_values_are_unset(self):
        self.assertEqual(self.r({"OPENAI_BASE_URL": "", "OPENAI_HOST": "https://c.example"}),
                         ("https://c.example/v1", "OPENAI_HOST"))

    def test_nothing_configured(self):
        with self.assertRaises(remote.RemoteError) as cm:
            self.r({})
        self.assertIn("OPENAI_BASE_URL", str(cm.exception))

    def test_key_file_beats_env_and_reads_env_file_form(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k.env")
            with open(p, "w") as f:
                f.write("# team key\nOPENAI_BASE_URL=https://x.example/v1\nOPENAI_API_KEY=sk-test-fromfile\n")
            os.chmod(p, 0o600)
            self.assertEqual(remote.load_key(p, {"OPENAI_API_KEY": "sk-test-env"}), ("sk-test-fromfile", p))
            with open(p, "w") as f:
                f.write("sk-test-bare\n")
            self.assertEqual(remote.load_key(p, {}), ("sk-test-bare", p))
            self.assertEqual(remote.load_key(None, {"OPENAI_API_KEY": "sk-test-env"}),
                             ("sk-test-env", "OPENAI_API_KEY"))
            self.assertEqual(remote.load_key(None, {}), (None, None))

    def test_key_file_readable_by_others_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k")
            with open(p, "w") as f:
                f.write("sk-test-x\n")
            os.chmod(p, 0o644)
            with self.assertRaises(remote.RemoteError) as cm:
                remote.load_key(p, {})
            self.assertIn("0600", str(cm.exception))

    def test_mask(self):
        self.assertEqual(remote.mask("sk-test-0123456789abcdef"), "sk-…cdef")
        self.assertEqual(remote.mask("abcdefghijklmnop"), "…mnop")
        self.assertEqual(remote.mask("short"), "…")
        self.assertEqual(remote.mask(None), "none")


class Live(unittest.TestCase):
    def setUp(self):
        self.stub = Stub()
        self.env = {"OPENAI_BASE_URL": self.stub.url, "OPENAI_API_KEY": KEY}

    def tearDown(self):
        self.stub.close()

    def test_check_reports_host_models_latency_and_masked_key(self):
        rc, out, err = cli("check", env=self.env)
        self.assertEqual(rc, 0, out + err)
        for want in ("127.0.0.1", "zai-org/GLM-5.3", "auth      ok", "sk-…cdef", "finish_reason=stop", " s"):
            self.assertIn(want, out)
        self.assertNotIn(KEY, out + err)
        chat = [s for s in self.stub.seen if s["path"] == "/v1/chat/completions"]
        self.assertEqual(len(chat), 1)
        self.assertEqual(chat[0]["body"]["max_tokens"], 16)
        self.assertNotEqual(chat[0]["body"]["temperature"], 0)
        self.assertNotIn("logprobs", chat[0]["body"])
        self.assertEqual(len(self.stub.seen), 2, "check sends exactly GET /models and one chat")

    def test_check_json(self):
        rc, out, _ = cli("check", "--json", env=self.env)
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["models"], ["zai-org/GLM-5.3"])
        self.assertEqual(d["auth"], "ok")
        self.assertEqual(d["key"], "sk-…cdef")
        self.assertNotIn(KEY, out)

    def test_bad_key_is_an_auth_failure_without_echoing_it(self):
        env = dict(self.env, OPENAI_API_KEY="sk-test-wrong-key-99999")
        rc, out, err = cli("check", env=env)
        self.assertNotEqual(rc, 0)
        self.assertIn("401", out + err)
        self.assertNotIn("sk-test-wrong-key-99999", out + err)
        self.assertFalse(any(s["path"] == "/v1/chat/completions" for s in self.stub.seen))

    def test_no_key_for_a_remote_https_host_is_an_error(self):
        rc, _, err = cli("resolve", env={"OPENAI_BASE_URL": "https://gaudi.example/v1"})
        self.assertNotEqual(rc, 0)
        self.assertIn("needs a key", err)

    def test_max_tokens_is_capped(self):
        rc, _, err = cli("check", "--max-tokens", "500", env=self.env)
        self.assertNotEqual(rc, 0)
        self.assertIn("64", err)

    def test_empty_reasoning_only_reply_is_reported_not_passed_silently(self):
        self.stub.reply = {"content": "", "reasoning": "Let me think", "finish_reason": "length", "tokens": 16}
        rc, out, _ = cli("check", env=self.env)
        self.assertEqual(rc, 0)
        self.assertIn("empty", out)
        self.assertIn("finish_reason=length", out)
        self.assertIn("warn", out)

    def test_model_auto_pick_and_list(self):
        rc, out, _ = cli("resolve", "--probe", env=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().split("\t"), [self.stub.url, "zai-org/GLM-5.3", "OPENAI_BASE_URL"])
        self.stub.models = ["a", "b"]
        rc, _, err = cli("resolve", "--probe", env=self.env)
        self.assertNotEqual(rc, 0)
        self.assertIn("a, b", err)
        self.assertIn("--model", err)

    def test_model_flag_and_env(self):
        self.stub.models = ["a", "b"]
        rc, out, _ = cli("resolve", "--probe", "--model", "b", env=self.env)
        self.assertEqual(out.split("\t")[1], "b")
        rc, out, _ = cli("resolve", "--probe", env=dict(self.env, OPENAI_MODEL="a"))
        self.assertEqual(out.split("\t")[1], "a")
        # A model the server does not list (an alias it routes) is tried, with a warning.
        rc, out, err = cli("resolve", "--probe", "--model", "zzz", env=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(out.split("\t")[1], "zzz")
        self.assertIn("not listed", err)

    def test_resolve_without_probe_sends_nothing(self):
        rc, out, _ = cli("resolve", "--model", "m", env=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual(self.stub.seen, [])

    def test_redirect_to_another_host_never_carries_the_key(self):
        other = Stub(key=None)
        try:
            # localhost and 127.0.0.1 are different hosts to a client.
            self.stub.redirect_to = f"http://localhost:{other.port}"
            rc, out, err = cli("check", env=self.env)
            self.assertNotEqual(rc, 0)
            self.assertIn("redirect", (out + err).lower())
            self.assertEqual(other.seen, [], "the redirect target was contacted")
        finally:
            other.close()

    def test_redaction_cli_subprocess(self):
        """The key never shows in any output of any subcommand run as a process."""
        env = dict(CLEAN_ENV, **self.env)
        for argv in (["check"], ["check", "--json"], ["resolve", "--probe"], ["models"], ["show"]):
            p = subprocess.run([sys.executable, os.path.join(ROOT, "console", "remote.py")] + argv,
                               env=env, capture_output=True, text=True, timeout=30)
            with self.subTest(argv=argv):
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn(KEY, p.stdout + p.stderr)
                self.assertNotIn(KEY_B64, p.stdout + p.stderr)

    def test_local_http_never_goes_through_a_proxy(self):
        # A proxy that would see the key: nothing listens there, so using it fails.
        rc, out, err = cli("check", env=dict(self.env, HTTP_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9"))
        self.assertEqual(rc, 0, out + err)

class Manifests(unittest.TestCase):
    def test_secret_golden(self):
        a = remote.parse(["secret", "--namespace", "k3d-gpu-agent", "--model", "zai-org/GLM-5.3"])
        ep = remote.Endpoint(a, {"OPENAI_BASE_URL": "https://gaudi.example.net/v1", "OPENAI_API_KEY": KEY})
        out = remote.secret_manifest(ep, "k3d-gpu-agent", "zai-org/GLM-5.3")
        self.assertEqual(out, golden("secret.golden", out))
        self.assertNotIn(KEY, out, "the raw key is only ever base64 data, never stringData")
        self.assertIn(KEY_B64, out)
        self.assertIn("app.kubernetes.io/managed-by: k3d-gpu", out)

    def test_secret_goes_to_kubectl_stdin_never_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "kubectl.log")
            with open(os.path.join(d, "kubectl"), "w") as f:
                f.write(f"#!/bin/sh\necho \"argv $*\" >> {log}\ncat >> {log}\n")
            os.chmod(os.path.join(d, "kubectl"), 0o755)
            env = dict(CLEAN_ENV, PATH=d + os.pathsep + os.environ["PATH"],
                       OPENAI_BASE_URL="https://gaudi.example.net/v1", OPENAI_API_KEY=KEY)
            p = subprocess.run([sys.executable, os.path.join(ROOT, "console", "remote.py"), "secret",
                                "--namespace", "ns1", "--model", "m"], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn(KEY_B64, p.stdout + p.stderr)
            self.assertNotIn(KEY, p.stdout + p.stderr)
            with open(log) as f:
                got = f.read()
            argv = got.splitlines()[0]
            self.assertEqual(argv, "argv apply --server-side --force-conflicts --field-manager=k3d-gpu -f -")
            self.assertNotIn(KEY_B64, argv)
            self.assertIn(f"OPENAI_API_KEY: {KEY_B64}", got)

    def test_exec_hands_the_key_in_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            kf = os.path.join(d, "k")
            with open(kf, "w") as f:
                f.write("OPENAI_API_KEY=sk-test-exec-000000\n")
            os.chmod(kf, 0o600)
            p = subprocess.run([sys.executable, os.path.join(ROOT, "console", "remote.py"), "exec", "--remote",
                                "https://gaudi.example.net", "--api-key-file", kf, "--", sys.executable, "-c",
                                "import os, sys; sys.exit(0 if os.environ['OPENAI_API_KEY'] == 'sk-test-exec-000000' else 1)"],
                               env=CLEAN_ENV, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout, "")

    def test_secret_needs_a_model_or_probe(self):
        rc, _, err = cli("secret", "--namespace", "x",
                         env={"OPENAI_BASE_URL": "https://gaudi.example.net/v1", "OPENAI_API_KEY": KEY})
        self.assertNotEqual(rc, 0)

    def test_chat_ui_reads_the_secret(self):
        src = os.path.join(ROOT, "share", "agent-chat-ui.yml")
        rc, out, err = cli("chat-ui", src, "--model", "zai-org/GLM-5.3",
                           env={"OPENAI_BASE_URL": "https://gaudi.example.net/v1", "OPENAI_API_KEY": KEY})
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, golden("chat-ui.golden", out))
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY_B64, out)
        body = "".join(ln for ln in out.splitlines(True) if not ln.lstrip().startswith("#"))
        self.assertNotIn("ovms.k3d-gpu-agent.svc", body)
        self.assertIn("secretKeyRef: {name: k3d-gpu-llm, key: OPENAI_API_KEY}", out)
        self.assertIn("secretKeyRef: {name: k3d-gpu-llm, key: OPENAI_BASE_URL}", out)
        self.assertIn('name: ENABLE_PERSISTENT_CONFIG\n              value: "False"', out)
        self.assertIn('name: DEFAULT_MODELS\n              value: "zai-org/GLM-5.3"', out)

    def test_sandbox_egress_is_only_the_endpoint(self):
        src = os.path.join(ROOT, "share", "agent-sandbox-k3d.yml")
        rc, out, err = cli("sandbox-profile", src, "--addrs", "104.21.77.178,172.67.210.235,2606:4700:3030::6815:4db2",
                           env={"OPENAI_BASE_URL": "https://gaudi.example.net/v1", "OPENAI_API_KEY": KEY})
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, golden("sandbox.golden", out))
        egress = out.split("    egress:\n", 1)[1].split("  podTemplate:", 1)[0]
        self.assertNotIn("0.0.0.0/0", egress)
        self.assertNotIn("::/0", egress)
        self.assertNotIn("kube-dns", egress, "no DNS egress: hostAliases pin the name")
        self.assertNotIn("port: 53", egress)
        self.assertEqual(egress.count("cidr:"), 3)
        for c in ("104.21.77.178/32", "172.67.210.235/32", "2606:4700:3030::6815:4db2/128"):
            self.assertIn(c, egress)
        self.assertEqual(egress.count("port: 443"), 1)
        self.assertIn("hostAliases:", out)
        self.assertIn("hostnames: [gaudi.example.net]", out)
        self.assertIn("envFrom:\n        - secretRef:\n            name: k3d-gpu-llm", out)
        self.assertNotIn(KEY, out)

    def test_sandbox_refuses_private_endpoints(self):
        src = os.path.join(ROOT, "share", "agent-sandbox-k3d.yml")
        rc, _, err = cli("sandbox-profile", src, "--addrs", "10.1.2.3",
                         env={"OPENAI_BASE_URL": "https://llm.corp.example/v1", "OPENAI_API_KEY": KEY})
        self.assertNotEqual(rc, 0)
        self.assertIn("public", err)

    def test_sandbox_port_follows_the_url(self):
        src = os.path.join(ROOT, "share", "agent-sandbox-k3d.yml")
        rc, out, _ = cli("sandbox-profile", src, "--addrs", "93.184.215.14",
                         env={"OPENAI_BASE_URL": "https://llm.example.org:8443/v1", "OPENAI_API_KEY": KEY})
        self.assertEqual(rc, 0)
        self.assertIn("port: 8443", out)
        self.assertNotIn("port: 443\n", out.split("podTemplate")[0])


def run_cli(argv, env):
    """remote.py as a process (CLEAN_ENV + env); returns the CompletedProcess."""
    return subprocess.run([sys.executable, os.path.join(ROOT, "console", "remote.py")] + argv,
                          env=dict(CLEAN_ENV, **env), capture_output=True, text=True, timeout=30)


def json_body(obj):
    return json.dumps(obj).encode()


class Hostile(unittest.TestCase):
    """Endpoints, gateways and portals that misbehave: the key never reaches any
    output, and every failure is a clean message with exit 4, never a traceback."""

    SECRET = "sk-test-SECRETVALUE1234567"

    def setUp(self):
        self.stub = Stub(key=None)
        self.env = {"OPENAI_BASE_URL": self.stub.url, "OPENAI_API_KEY": self.SECRET}

    def tearDown(self):
        self.stub.close()

    def assertClean(self, text):
        self.assertNotIn(self.SECRET, text)
        self.assertNotIn(self.SECRET[:14], text, "a partial key prefix leaked")
        self.assertNotIn("Traceback", text)

    def test_json_error_echoing_the_authorization_header_is_scrubbed(self):
        self.stub.routes["/v1/models"] = lambda auth: (
            500, json_body({"error": {"message": f"bad token {auth}"}}), [("Content-Type", "application/json")])
        for argv in (["models"], ["check"], ["check", "--json"], ["resolve", "--probe"]):
            p = run_cli(argv, self.env)
            with self.subTest(argv=argv):
                self.assertEqual(p.returncode, 4, p.stderr)
                self.assertClean(p.stdout + p.stderr)
                self.assertIn("bad token Bearer sk-", p.stdout + p.stderr)

    def test_chat_error_echoing_the_header_is_scrubbed(self):
        self.stub.routes["/v1/chat/completions"] = lambda auth: (
            502, json_body({"error": {"message": f"upstream refused {auth}"}}), [])
        for argv in (["check"], ["check", "--json"]):
            p = run_cli(argv, self.env)
            with self.subTest(argv=argv):
                self.assertEqual(p.returncode, 4)
                self.assertClean(p.stdout + p.stderr)

    def test_non_json_body_with_the_key_across_the_cut_is_scrubbed(self):
        for pad in range(160, 200):
            self.stub.routes["/v1/models"] = lambda auth, pad=pad: (
                500, ("x" * pad + auth + " trailing").encode(), [("Content-Type", "text/plain")])
            p = run_cli(["models"], self.env)
            with self.subTest(pad=pad):
                self.assertEqual(p.returncode, 4)
                self.assertClean(p.stdout + p.stderr)

    def test_key_with_a_newline_is_refused_by_name_only(self):
        p = run_cli(["check"], dict(self.env, OPENAI_API_KEY=self.SECRET + "\nline2"))
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertClean(p.stdout + p.stderr)
        self.assertNotIn("line2", p.stdout + p.stderr)
        self.assertIn("OPENAI_API_KEY", p.stderr)
        self.assertEqual(self.stub.seen, [], "nothing is sent with a malformed key")

    def test_key_file_with_inner_whitespace_or_control_chars_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            kf = os.path.join(d, "k")
            for body in (f"OPENAI_API_KEY={self.SECRET} tail\n", f"OPENAI_API_KEY={self.SECRET}\x07\n",
                         f"OPENAI_API_KEY={self.SECRET}…\n"):
                with open(kf, "w") as f:
                    f.write(body)
                os.chmod(kf, 0o600)
                p = run_cli(["check", "--api-key-file", kf], {"OPENAI_BASE_URL": self.stub.url})
                with self.subTest(body=repr(body)):
                    self.assertEqual(p.returncode, 2, p.stderr)
                    self.assertClean(p.stdout + p.stderr)
                    self.assertIn(kf, p.stderr)

    def test_models_as_a_top_level_list_is_a_clean_error(self):
        self.stub.routes["/v1/models"] = lambda auth: (200, json_body([{"id": "a"}]), [])
        p = run_cli(["check"], self.env)
        self.assertEqual(p.returncode, 4, p.stderr)
        self.assertClean(p.stdout + p.stderr)
        self.assertIn("model list", p.stderr)

    def test_truncated_body_is_a_clean_error(self):
        self.stub.routes["/v1/models"] = lambda auth: (200, b'{"data": [', [("Content-Length", "500")])
        p = run_cli(["models"], self.env)
        self.assertEqual(p.returncode, 4, p.stderr)
        self.assertClean(p.stdout + p.stderr)

    def test_rate_limit_is_named_with_retry_after(self):
        self.stub.routes["/v1/models"] = lambda auth: (
            429, json_body({"error": {"message": "slow down"}}), [("Retry-After", "7")])
        p = run_cli(["check", "--json"], self.env)
        self.assertEqual(p.returncode, 4)
        self.assertIn("rate limit", p.stderr.lower())
        self.assertIn("Retry-After 7", p.stderr)
        self.assertNotEqual(json.loads(p.stdout)["auth"], "rejected")

    def test_html_portal_on_models_is_not_auth_ok(self):
        self.stub.routes["/v1/models"] = lambda auth: (
            200, b"<html><body>Sign in to the guest network</body></html>", [("Content-Type", "text/html")])
        p = run_cli(["check", "--json"], self.env)
        self.assertEqual(p.returncode, 4)
        self.assertNotEqual(json.loads(p.stdout)["auth"], "ok")
        self.assertIn("not JSON", p.stderr)

    def test_no_models_route_with_an_explicit_model_goes_on_to_the_chat(self):
        self.stub.routes["/v1/models"] = lambda auth: (404, json_body({"error": {"message": "no route"}}), [])
        rc, out, err = cli("check", "--model", "zai-org/GLM-5.3", env=self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("chat      ok", out)
        self.assertIn("auth      ok", out)
        self.assertIn("no GET /models", out)
        self.assertTrue(any(s["path"] == "/v1/chat/completions" for s in self.stub.seen))
        rc, out, err = cli("resolve", "--probe", env=dict(self.env, OPENAI_MODEL="m1"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.split("\t")[1], "m1")
        self.assertIn("no GET /models", err)
        self.stub.routes["/v1/models"] = lambda auth: (405, b"", [])
        rc, out, err = cli("check", "--model", "zai-org/GLM-5.3", env=self.env)
        self.assertEqual(rc, 0, out + err)

    def test_no_models_route_without_a_model_still_fails(self):
        self.stub.routes["/v1/models"] = lambda auth: (404, json_body({"error": {"message": "no route"}}), [])
        rc, _, err = cli("check", env=self.env)
        self.assertEqual(rc, 4)
        self.assertIn("--model", err)

    def test_no_traceback_from_an_unexpected_error_and_no_key(self):
        def boom(ep, a):
            raise RuntimeError(f"unexpected with {ep.key}")
        saved = remote.COMMANDS["show"]
        remote.COMMANDS["show"] = (boom, "load")
        try:
            rc, out, err = cli("show", env=self.env)
        finally:
            remote.COMMANDS["show"] = saved
        self.assertEqual(rc, 4)
        self.assertClean(out + err)
        self.assertIn("RuntimeError", err)


class Policy(unittest.TestCase):
    """Where plain http is allowed, and which hosts count as yours."""

    def test_keyless_http_to_a_lan_or_docker_host_is_allowed(self):
        for url in ("http://192.168.1.10:11434/v1", "http://host.k3d.internal:8000/v1", "http://10.0.0.5:8000/v1"):
            rc, out, err = cli("resolve", "--model", "m", env={"OPENAI_BASE_URL": url})
            with self.subTest(url=url):
                self.assertEqual(rc, 0, err)
                self.assertTrue(out.startswith(url + "\t"))

    def test_http_to_a_public_host_needs_no_key_check_and_never_takes_one(self):
        rc, _, err = cli("resolve", "--model", "m", env={"OPENAI_BASE_URL": "http://llm.example.net/v1"})
        self.assertEqual(rc, 2)
        self.assertIn("needs a key", err)
        rc, _, err = cli("resolve", "--model", "m", env={"OPENAI_BASE_URL": "http://llm.example.net/v1",
                                                       "OPENAI_API_KEY": KEY})
        self.assertEqual(rc, 2)
        self.assertIn("clear text", err)
        self.assertNotIn(KEY, err)

    def test_keyed_http_to_a_private_host_warns(self):
        rc, _, err = cli("resolve", "--model", "m", env={"OPENAI_BASE_URL": "http://192.168.1.10:8000/v1",
                                                       "OPENAI_API_KEY": KEY})
        self.assertEqual(rc, 0, err)
        self.assertIn("clear text", err)
        self.assertIn("warn", err)
        rc, _, err = cli("resolve", "--model", "m", env={"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1",
                                                       "OPENAI_API_KEY": KEY})
        self.assertEqual((rc, err), (0, ""))

    def test_is_local(self):
        mine = ["http://127.0.0.1:8000/v3", "http://localhost:8000", "http://[::1]:8000/v1",
                "http://ovms.k3d-gpu-agent.svc:8000/v3", "http://vllm.ns.svc.cluster.local:8000/v1",
                "http://foo.cluster.local/v1", "http://10.0.0.5:8000/v1", "http://192.168.1.10:11434/v1",
                "http://172.16.4.4/v1", "http://169.254.1.1/v1", "http://host.k3d.internal:8000/v1",
                "http://[fd00::1]:8000/v1"]
        theirs = ["https://gaudi.example.net/v1", "https://gaudi.example.net?x=.svc", "https://evil.svc.example.com/v1",
                  "https://127.0.0.1.example.com/v1", "https://8.8.8.8/v1", "not a url", "",
                  "https://gaudi.example.net/#.svc", "https://u@127.0.0.1.example.com/"]
        for u in mine:
            with self.subTest(u=u):
                self.assertEqual(cli("is-local", u)[0], 0)
        for u in theirs:
            with self.subTest(u=u):
                self.assertEqual(cli("is-local", u)[0], 1)


class Normalize2(unittest.TestCase):
    def test_more_forms(self):
        for raw, want in [("https://h.example/v1/v1", "https://h.example/v1"),
                          ("https://h.example/v1/models", "https://h.example/v1"),
                          ("https://h.example/v1/models/", "https://h.example/v1"),
                          ("https://h.example:443/v1", "https://h.example/v1"),
                          ("http://127.0.0.1:80/v1", "http://127.0.0.1/v1"),
                          ("https://h.example:80/v1", "https://h.example:80/v1")]:
            with self.subTest(raw=raw):
                self.assertEqual(remote.normalize_url(raw)[0], want)

    def test_port_zero_is_refused(self):
        with self.assertRaises(remote.RemoteError) as cm:
            remote.normalize_url("https://h.example:0/v1")
        self.assertIn("port", str(cm.exception))

    def test_openai_host_and_base_path(self):
        r = remote.resolve_base
        self.assertEqual(r(None, {"OPENAI_HOST": "https://h.example/openai"}),
                         ("https://h.example/openai/v1", "OPENAI_HOST"))
        self.assertEqual(r(None, {"OPENAI_HOST": "https://h.example/v1"}), ("https://h.example/v1", "OPENAI_HOST"))
        self.assertEqual(r(None, {"OPENAI_HOST": "https://h.example", "OPENAI_BASE_PATH": "v1/chat/completions"}),
                         ("https://h.example/v1", "OPENAI_HOST"))
        self.assertEqual(r(None, {"OPENAI_HOST": "https://h.example/", "OPENAI_BASE_PATH": "/api/v2/chat/completions"}),
                         ("https://h.example/api/v2", "OPENAI_HOST"))
        # OPENAI_BASE_PATH goes with OPENAI_HOST only.
        self.assertEqual(r(None, {"OPENAI_BASE_URL": "https://a.example/v1", "OPENAI_BASE_PATH": "x/y"}),
                         ("https://a.example/v1", "OPENAI_BASE_URL"))


class Files(unittest.TestCase):
    def test_module_is_executable(self):
        self.assertTrue(os.stat(os.path.join(ROOT, "console", "remote.py")).st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()

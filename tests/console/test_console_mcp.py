#!/usr/bin/env python3
"""Console slice S6 and S7 against the real launcher and the real MCP server.

S6: the MCP tools hardware and plan_launch return what `k3d-gpu facts --json`
    and `k3d-gpu plan --json` print, byte for byte, on every fixture host, and
    plan_launch's hash is what `k3d-gpu plan --hash` prints.
S7: every MCP tool that is not read-only is refused without a plan hash the
    human confirmed in the client: no elicitation capability, a legacy session,
    a stale or wrong hash, a plan with no engine, a declined or unconfirmed
    answer, a forged, reused or mismatched requestState. Only an accepted
    confirmation of the current plan's hash runs `k3d-gpu apply`, and that
    reaches `kubectl apply` with the plan's model and device.

The launcher runs on the fixture sysroots (K3D_GPU_SYSROOT) with docker, k3d
and kubectl stubbed; a kubectl stub records every call. No cluster, no network.

Usage: tests/console/test_console_mcp.py [-v]
Stdlib only.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LAUNCHER = os.path.join(REPO, "scripts", "k3d-gpu")
MCP = os.path.join(REPO, "scripts", "k3d-gpu-mcp")
FIXTURES = os.path.join(REPO, "tests", "fixtures")
GREEN_HOST = "raptorlake-nvidia"  # i9-13900T: the two H6 cells match it

MODERN = "2026-07-28"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPS = "io.modelcontextprotocol/clientCapabilities"
ELICIT = {"elicitation": {"form": {}}}
KUBECTL_WRITES = {"apply", "create", "delete", "patch", "replace", "label", "annotate", "scale", "edit"}

KUBECTL = """#!/bin/sh
echo "$*" >> "$STUB_DIR/kubectl.calls"
case "$*" in
    "apply -f -") cat > "$STUB_DIR/manifest" ;;
    *" exec deployment/ovms -c ovms -- curl "*) echo '{"choices":[{"message":{"content":"stub"}}]}' ;;
esac
exit 0
"""
# Logs the launcher's argv, then runs the real launcher.
WRAPPER = """#!/bin/sh
echo "$*" >> "$STUB_DIR/launcher.calls"
exec "%s" "$@"
""" % LAUNCHER


def write_exec(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def hosts():
    return sorted(d for d in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, d, "sysroot")))


class World:
    """A stub directory, a console directory and the environment for one host."""

    def __init__(self, host, green=False, matrix=None):
        self.dir = tempfile.mkdtemp(prefix="console-mcp-")
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        for tool in ("docker", "k3d"):
            write_exec(os.path.join(bindir, tool), "#!/bin/sh\nexit 0\n")
        write_exec(os.path.join(bindir, "kubectl"), KUBECTL)
        write_exec(os.path.join(self.dir, "k3d-gpu"), WRAPPER)
        console = os.path.join(self.dir, "console")
        shutil.copytree(os.path.join(REPO, "console"), console,
                        ignore=shutil.ignore_patterns("__pycache__"))
        if green or matrix:
            shutil.copy(os.path.join(HERE, matrix or "matrix-green.json"), os.path.join(console, "matrix.v1.json"))
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], STUB_DIR=self.dir,
                        K3D_GPU_SYSROOT=os.path.join(FIXTURES, host, "sysroot"),
                        K3D_GPU_CONSOLE=console, K3D_GPU_BIN=os.path.join(self.dir, "k3d-gpu"),
                        XDG_STATE_HOME=os.path.join(self.dir, "state"), NO_COLOR="1")
        self.env.pop("K3D_GPU_EXPERIMENTAL", None)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cli(self, *args):
        p = subprocess.run([LAUNCHER] + list(args), env=self.env, capture_output=True, timeout=300)
        if p.returncode != 0:
            raise AssertionError("k3d-gpu %s: exit %d: %s" % (" ".join(args), p.returncode, p.stderr.decode()))
        return p.stdout

    def calls(self, name):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    def session(self, requests):
        """Replies to the request lines, by id."""
        data = "".join(json.dumps(r) + "\n" for r in requests)
        p = subprocess.run([MCP], input=data.encode(), env=self.env, capture_output=True, timeout=600)
        out = {}
        for line in p.stdout.decode().splitlines():
            msg = json.loads(line)
            out[msg["id"]] = msg
        return out


def meta(caps=None):
    return {META_VERSION: MODERN, META_CAPS: caps or {}}


def call(rid, name, args=None, caps=None, **extra):
    params = {"_meta": meta(caps), "name": name, "arguments": args or {}}
    params.update(extra)
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params}


def one(world, request):
    return world.session([request])[request["id"]]["result"]


def writes(world):
    """Write calls that reached the launcher or kubectl."""
    return ([c for c in world.calls("launcher.calls") if c.split()[:1] == ["apply"]]
            + [c for c in world.calls("kubectl.calls") if KUBECTL_WRITES & set(c.split())])


class S6Equality(unittest.TestCase):
    """MCP hardware and plan_launch equal the CLI --json, every fixture host."""

    def test_every_fixture(self):
        names = hosts()
        self.assertGreaterEqual(len(names), 14)
        for host in names:
            with self.subTest(host=host):
                w = World(host)
                try:
                    facts = w.cli("facts", "--json")
                    plan = w.cli("plan", "--json")
                    phash = w.cli("plan", "--hash").decode().strip()
                    r = w.session([call(1, "hardware"), call(2, "plan_launch")])
                    hw, pl = r[1]["result"], r[2]["result"]
                    self.assertFalse(hw["isError"])
                    self.assertEqual(hw["content"][0]["text"].encode("utf-8"), facts)
                    self.assertEqual(hw["structuredContent"], json.loads(facts))
                    self.assertEqual(len(hw["content"]), 1)
                    self.assertFalse(pl["isError"])
                    self.assertEqual(pl["content"][0]["text"].encode("utf-8"), plan)
                    self.assertEqual(pl["structuredContent"], json.loads(plan))
                    self.assertEqual(pl["_meta"]["k3d-gpu.io/planHash"], phash)
                    self.assertIn("plan hash: " + phash, pl["content"][1]["text"])
                    self.assertEqual(writes(w), [])
                finally:
                    w.close()

    def test_green_plan_with_options(self):
        w = World(GREEN_HOST, green=True)
        try:
            for args, cli in (({}, []), ({"device": "gpu", "port": 9000}, ["--device", "gpu", "--port", "9000"]),
                              ({"engine": "llama.cpp"}, ["--engine", "llama.cpp"]),
                              ({"model": "OpenVINO/Qwen3-4B-int4-ov"}, ["--model", "OpenVINO/Qwen3-4B-int4-ov"])):
                with self.subTest(args=args):
                    plan = w.cli("plan", "--json", *cli)
                    phash = w.cli("plan", "--hash", *cli).decode().strip()
                    r = one(w, call(1, "plan_launch", args))
                    self.assertFalse(r["isError"])
                    self.assertEqual(r["content"][0]["text"].encode("utf-8"), plan)
                    self.assertEqual(r["_meta"]["k3d-gpu.io/planHash"], phash)
            self.assertEqual(json.loads(w.cli("plan", "--json"))["decision"], "green")
        finally:
            w.close()

    def test_bad_plan_arguments(self):
        w = World(GREEN_HOST, green=True)
        try:
            for args, want in (({"device": "tpu"}, "device must be one of"),
                               ({"port": "9000"}, "port must be an integer"),
                               ({"port": 0}, "port must be 1-65535"),
                               ({"port": True}, "port must be an integer"),
                               ({"engine": ""}, "engine must not be empty"),
                               ({"bind": "0.0.0.0"}, "unknown argument(s): bind")):
                with self.subTest(args=args):
                    r = one(w, call(1, "plan_launch", args))
                    self.assertTrue(r["isError"])
                    self.assertIn(want, r["content"][0]["text"])
        finally:
            w.close()


class S7WriteGate(unittest.TestCase):
    """No write over MCP without a human-confirmed plan hash."""

    @classmethod
    def setUpClass(cls):
        cls.w = World(GREEN_HOST, green=True)
        cls.hash = cls.w.cli("plan", "--hash").decode().strip()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def setUp(self):
        for name in ("launcher.calls", "kubectl.calls", "manifest"):
            path = os.path.join(self.w.dir, name)
            if os.path.exists(path):
                os.remove(path)

    def refused(self, result, want):
        self.assertTrue(result.get("isError"), result)
        self.assertIn("refused", result["content"][0]["text"])
        self.assertIn(want, result["content"][0]["text"])
        self.assertEqual(writes(self.w), [])

    def ask(self, args=None):
        """The input_required result of a first apply call."""
        r = one(self.w, call(1, "apply", args or {"plan_hash": self.hash}, caps=ELICIT))
        self.assertEqual(r["resultType"], "input_required", r)
        return r

    def test_only_apply_writes_and_it_needs_a_hash(self):
        r = self.w.session([{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta()}}])
        tools = r[1]["result"]["tools"]
        writers = [t for t in tools if not t["annotations"]["readOnlyHint"]]
        self.assertEqual([t["name"] for t in writers], ["apply"])
        for t in writers:
            self.assertIn("plan_hash", t["inputSchema"]["required"])
            self.assertTrue(t["annotations"]["destructiveHint"])
            self.assertNotIn("bind", t["inputSchema"]["properties"])
        names = {t["name"] for t in tools}
        self.assertNotIn("share", names)
        self.assertTrue({"hardware", "plan_launch", "apply"} <= names)

    def test_every_read_tool_writes_nothing(self):
        r = self.w.session([call(i, n) for i, n in enumerate(
            ["accelerators", "agent_endpoint", "cluster_health", "hardware", "plan_launch"], 1)]
            + [call(9, "recommend_placement", {"workload": "llm"})])
        self.assertEqual(len(r), 6)
        self.assertEqual(writes(self.w), [])

    def test_missing_or_malformed_hash(self):
        for args, want in (({}, "missing required argument 'plan_hash'"),
                           ({"plan_hash": "abc"}, "plan_hash must match"),
                           ({"plan_hash": self.hash.upper()}, "plan_hash must match")):
            with self.subTest(args=args):
                r = one(self.w, call(1, "apply", args, caps=ELICIT))
                self.assertTrue(r["isError"])
                self.assertIn(want, r["content"][0]["text"])
                self.assertEqual(writes(self.w), [])

    def test_no_elicitation_capability(self):
        for caps in ({}, {"elicitation": {"url": {}}}, {"sampling": {}}):
            with self.subTest(caps=caps):
                r = one(self.w, call(1, "apply", {"plan_hash": self.hash}, caps=caps))
                self.refused(r, "declares no form elicitation")
                self.assertIn("k3d-gpu apply --plan-hash " + self.hash, r["content"][0]["text"])

    def test_bare_elicitation_capability_means_form(self):
        r = one(self.w, call(1, "apply", {"plan_hash": self.hash}, caps={"elicitation": {}}))
        self.assertEqual(r["resultType"], "input_required")

    def test_legacy_session(self):
        r = self.w.session([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25", "capabilities": {"elicitation": {}},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "apply", "arguments": {"plan_hash": self.hash}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"_meta": {META_VERSION: "2025-11-25", META_CAPS: ELICIT},
                        "name": "apply", "arguments": {"plan_hash": self.hash}}}])
        self.refused(r[2]["result"], "negotiated an older protocol")
        self.refused(r[3]["result"], "negotiated an older protocol")

    def test_wrong_hash(self):
        other = "sha256:" + "0" * 64
        r = one(self.w, call(1, "apply", {"plan_hash": other}, caps=ELICIT))
        self.refused(r, "the plan now hashes to " + self.hash)
        self.assertNotIn("inputRequests", r)

    def test_hash_of_other_options(self):
        # The hash of the default plan does not authorize the --device gpu plan.
        r = one(self.w, call(1, "apply", {"plan_hash": self.hash, "device": "gpu"}, caps=ELICIT))
        self.refused(r, "the plan now hashes to")

    def test_unmeasured_plan(self):
        w = World(GREEN_HOST)
        try:
            h = w.cli("plan", "--hash").decode().strip()
            r = one(w, call(1, "apply", {"plan_hash": h}, caps=ELICIT))
            self.assertIn("names no engine", r["content"][0]["text"])
            self.assertEqual(writes(w), [])
        finally:
            w.close()

    def test_asks_the_human_first(self):
        r = self.ask()
        req = r["inputRequests"]["confirm_plan"]
        self.assertEqual(req["method"], "elicitation/create")
        self.assertEqual(req["params"]["mode"], "form")
        self.assertIn(self.hash, req["params"]["message"])
        self.assertIn("raptorlake-s.cpu-pcores.ovms.qwen3-4b-int4", req["params"]["message"])
        schema = req["params"]["requestedSchema"]
        self.assertEqual(schema["required"], ["confirm"])
        self.assertEqual(schema["properties"]["confirm"]["type"], "boolean")
        self.assertIsInstance(r["requestState"], str)
        self.assertNotIn("content", r)
        self.assertEqual(writes(self.w), [])

    def session_flow(self, steps):
        """Drive one server process through apply calls. steps: [(args, answer or
        None, state)], where state is None, the index of an earlier reply whose
        requestState to echo, or a function of that list of replies."""
        proc = subprocess.Popen([MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=self.w.env, text=True)
        replies = []
        try:
            for i, (args, answer, state) in enumerate(steps, 1):
                extra = {}
                if isinstance(state, int):
                    extra["requestState"] = replies[state]["requestState"]
                elif state is not None:
                    extra["requestState"] = state(replies)
                if answer is not None:
                    extra["inputResponses"] = {"confirm_plan": answer}
                proc.stdin.write(json.dumps(call(i, "apply", args, caps=ELICIT, **extra)) + "\n")
                proc.stdin.flush()
                replies.append(json.loads(proc.stdout.readline())["result"])
        finally:
            proc.stdin.close()
            proc.wait(timeout=60)
            proc.stdout.close()
        return replies

    def test_decline_and_cancel(self):
        h = {"plan_hash": self.hash}
        for answer in ({"action": "decline"}, {"action": "cancel"},
                       {"action": "accept", "content": {"confirm": False}},
                       {"action": "accept"}, {"action": "accept", "content": {"confirm": "true"}},
                       "accept"):
            with self.subTest(answer=answer):
                r = self.session_flow([(h, None, None), (h, answer, 0)])
                self.assertEqual(r[0]["resultType"], "input_required")
                self.refused(r[1], "did not confirm")

    def test_no_answer(self):
        h = {"plan_hash": self.hash}
        r = self.session_flow([(h, None, None), (h, None, 0)])
        self.refused(r[1], "no answer")

    def test_forged_state(self):
        h = {"plan_hash": self.hash}
        accept = {"action": "accept", "content": {"confirm": True}}

        def parts(r):
            return r[0]["requestState"].split(".")
        for name, forge in (("garbage", lambda r: "x"), ("two parts", lambda r: "a.b"),
                            ("reversed mac", lambda r: parts(r)[0] + "." + parts(r)[1][::-1]),
                            ("swapped", lambda r: parts(r)[1] + "." + parts(r)[0]),
                            ("appended", lambda r: r[0]["requestState"] + "x"),
                            ("not a string", lambda r: {"state": r[0]["requestState"]})):
            with self.subTest(forgery=name):
                r = self.session_flow([(h, None, None), (h, accept, forge)])
                self.refused(r[1], "not issued by this server")

    def test_expired_confirmation(self):
        # A state this server signed is refused once its exp has passed. The
        # server module runs in this process so the test can sign such a state.
        import importlib.machinery
        import importlib.util
        import time
        loader = importlib.machinery.SourceFileLoader("k3d_gpu_mcp", MCP)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("k3d_gpu_mcp", loader))
        saved = dict(os.environ)
        os.environ.update(self.w.env)
        before = writes(self.w)
        try:
            loader.exec_module(mod)
            args = {"plan_hash": self.hash}
            state = mod.sign_state({"tool": "apply", "args": mod.args_digest(args),
                                    "exp": int(time.time()) - 1, "nonce": "expired"})
            ctx = {"legacy": False, "caps": ELICIT, "requestState": state,
                   "inputResponses": {"confirm_plan": {"action": "accept", "content": {"confirm": True}}}}
            with self.assertRaisesRegex(mod.ToolError, "confirmation expired"):
                mod.Apply()(args, ctx)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(writes(self.w), before)

    def test_state_from_another_session(self):
        # A confirmation is bound to the server process that asked; each
        # session() call spawns a new one.
        accept = {"action": "accept", "content": {"confirm": True}}
        state = self.ask()["requestState"]
        r = one(self.w, call(2, "apply", {"plan_hash": self.hash}, caps=ELICIT,
                             requestState=state, inputResponses={"confirm_plan": accept}))
        self.refused(r, "not issued by this server")

    def test_state_bound_to_its_arguments(self):
        # The confirmation of the default plan does not carry over to another
        # valid plan (here the --device gpu one, with its own correct hash).
        h = {"plan_hash": self.hash}
        other = {"plan_hash": self.w.cli("plan", "--hash", "--device", "gpu").decode().strip(), "device": "gpu"}
        accept = {"action": "accept", "content": {"confirm": True}}
        r = self.session_flow([(h, None, None), (other, accept, 0)])
        self.assertEqual(r[0]["resultType"], "input_required")
        self.refused(r[1], "different apply request")

    def test_confirmed_hash_applies_once(self):
        h = {"plan_hash": self.hash}
        accept = {"action": "accept", "content": {"confirm": True}}
        r = self.session_flow([(h, None, None), (h, accept, 0), (h, accept, 0)])
        self.assertEqual(r[0]["resultType"], "input_required")
        ok = r[1]
        self.assertFalse(ok["isError"], ok)
        self.assertTrue(ok["structuredContent"]["applied"])
        self.assertEqual(ok["structuredContent"]["cell"], "raptorlake-s.cpu-pcores.ovms.qwen3-4b-int4")
        launcher_writes = [c for c in self.w.calls("launcher.calls") if c.startswith("apply")]
        self.assertEqual(launcher_writes, ["apply --plan-hash " + self.hash])
        self.assertIn("apply -f -", self.w.calls("kubectl.calls"))
        with open(os.path.join(self.w.dir, "manifest"), encoding="utf-8") as f:
            manifest = f.read()
        self.assertIn("--source_model=OpenVINO/Qwen3-4B-int4-ov", manifest)
        self.assertIn("--target_device=CPU", manifest)
        # The same confirmation does not run a second time.
        self.assertTrue(r[2]["isError"])
        self.assertIn("already used", r[2]["content"][0]["text"])
        self.assertEqual(len([c for c in self.w.calls("launcher.calls") if c.startswith("apply")]), 1)

    def test_plan_changed_after_confirmation(self):
        # The human confirmed, then the matrix changed: the recomputed plan no
        # longer hashes the same, so nothing runs.
        h = {"plan_hash": self.hash}
        matrix = os.path.join(self.w.env["K3D_GPU_CONSOLE"], "matrix.v1.json")
        with open(matrix, encoding="utf-8") as f:
            saved = f.read()
        proc = subprocess.Popen([MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=self.w.env, text=True)
        try:
            proc.stdin.write(json.dumps(call(1, "apply", h, caps=ELICIT)) + "\n")
            proc.stdin.flush()
            state = json.loads(proc.stdout.readline())["result"]["requestState"]
            doc = json.loads(saved)
            doc["rows"][0]["metrics"]["decode_tok_s"]["median"] = 1.0
            with open(matrix, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            proc.stdin.write(json.dumps(call(2, "apply", h, caps=ELICIT, requestState=state,
                                             inputResponses={"confirm_plan": {
                                                 "action": "accept", "content": {"confirm": True}}})) + "\n")
            proc.stdin.flush()
            r = json.loads(proc.stdout.readline())["result"]
        finally:
            proc.stdin.close()
            proc.wait(timeout=60)
            proc.stdout.close()
            with open(matrix, "w", encoding="utf-8") as f:
                f.write(saved)
        self.refused(r, "the plan now hashes to")


class CliApply(unittest.TestCase):
    """`k3d-gpu apply` is the write the MCP tool runs; it checks the hash itself."""

    @classmethod
    def setUpClass(cls):
        cls.w = World(GREEN_HOST, green=True)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def apply(self, *args):
        return subprocess.run([LAUNCHER, "apply"] + list(args), env=self.w.env, capture_output=True,
                              text=True, timeout=300)

    def test_hash_is_sha256_of_json(self):
        import hashlib
        doc = self.w.cli("plan", "--json")
        self.assertEqual(self.w.cli("plan", "--hash").decode().strip(),
                         "sha256:" + hashlib.sha256(doc).hexdigest())

    def test_refusals(self):
        h = self.w.cli("plan", "--hash").decode().strip()
        for args, want in (([], "--plan-hash sha256:<64 hex> is required"),
                           (["--plan-hash", "sha256:" + "0" * 64], "the plan now hashes to " + h),
                           (["--plan-hash", h, "--device", "gpu"], "the plan now hashes to"),
                           (["--plan-hash", h, "--bind", "0.0.0.0"], "unknown apply option: --bind"),
                           (["--plan-hash"], "--plan-hash needs a value")):
            with self.subTest(args=args):
                p = self.apply(*args)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(want, p.stderr)
        self.assertEqual(writes(self.w), [])

    def test_unmeasured_refused(self):
        w = World(GREEN_HOST)
        try:
            h = w.cli("plan", "--hash").decode().strip()
            p = subprocess.run([LAUNCHER, "apply", "--plan-hash", h], env=w.env, capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("the plan has no engine: unmeasured: no green cell for this box; quickstart and agent up still pick by device", p.stderr)
            self.assertEqual(writes(w), [])
        finally:
            w.close()

    def refused_on(self, world, want):
        self.addCleanup(world.close)
        h = world.cli("plan", "--hash").decode().strip()
        p = subprocess.run([LAUNCHER, "apply", "--plan-hash", h], env=world.env, capture_output=True,
                           text=True, timeout=300)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(want, p.stderr)
        self.assertEqual(writes(world), [])

    def test_fix_decision_refused(self):
        # A GPU the firmware gatekeeper refuses and no green cell elsewhere: the
        # plan's decision is "fix", which names no engine to deploy.
        self.refused_on(World("raptorlake-stale-guc"),
                        "the plan has no engine: gpu:0000:00:02.0 refused: GuC i915/tgl_guc_70.1.1.bin")

    def test_engine_without_apply_path_refused(self):
        # The fallback picks llama.cpp on Vulkan; agent up only runs OVMS.
        self.refused_on(World("raptorlake-stale-guc", matrix="matrix-fallback.json"),
                        "no apply path for engine llama.cpp yet")

    def test_apply_deploys_the_confirmed_plan(self):
        # The manifest carries the plan's image, model, device and P-core count,
        # not whatever agent up would pick on its own.
        w = World(GREEN_HOST, green=True)
        self.addCleanup(w.close)
        doc = json.loads(w.cli("plan", "--json"))
        h = w.cli("plan", "--hash").decode().strip()
        # A backend from the user's shell does not replace the plan's engine.
        env = dict(w.env, K3D_GPU_AGENT_BACKEND="vllm")
        p = subprocess.run([LAUNCHER, "apply", "--plan-hash", h], env=env, capture_output=True,
                           text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(w.dir, "manifest"), encoding="utf-8") as f:
            manifest = f.read()
        self.assertIn("image: " + doc["engine"]["image"], manifest)
        self.assertIn("--source_model=" + doc["model"]["name"], manifest)
        self.assertIn("--target_device=" + doc["engine"]["device"], manifest)
        self.assertIn('cpu: "%d"' % doc["placement"]["cpu_count"], manifest)
        self.assertIn("--rest_port=%d" % doc["port"], manifest)

    def test_port_agent_up_cannot_serve_refused(self):
        # agent up serves on 8000 only; a confirmed endpoint on another port
        # would not be what runs, so apply refuses before any write.
        h = self.w.cli("plan", "--hash", "--port", "9000").decode().strip()
        p = self.apply("--plan-hash", h, "--port", "9000")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("refused, the plan's endpoint port 9000", p.stderr)
        self.assertEqual(writes(self.w), [])

    def test_gpu_plan_applies_on_gpu(self):
        h = self.w.cli("plan", "--hash", "--device", "gpu").decode().strip()
        p = self.apply("--plan-hash", h, "--device", "gpu")
        # The stub cluster advertises no Intel GPU, so agent up stops before any
        # write; what matters is the device apply asked for.
        self.assertIn("K3D_GPU_AGENT_DEVICE=GPU", p.stdout)
        self.assertIn("advertises no Intel GPU", p.stderr)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"] if "-v" in sys.argv else [sys.argv[0]])

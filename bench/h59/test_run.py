#!/usr/bin/env python3
"""Tests for the H59-family runner (bench/h59: run.py, arm.sh, hold.sh, hold-run.sh, prep.sh;
EXPERIMENTS.md "H59 amendment R1"). Offline: `k3d-gpu agent manifest` renders against the fit
fixtures (tests/fixtures/fit, tests/fixtures/hf) with a kubectl stub that answers only for the
hold's own context, so a render that misses the hold's cluster is caught; arm.sh runs against a
local OpenAI-compatible server with kubectl and docker stubbed, and the records it writes are
scored by h59.py, so the runner's N=4 holds feed the registered bootstrap-ratio scorer end to
end. No cluster, no network."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h59  # noqa: E402
import run  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "fit")
HOSTS = {"desktop": "desktop-i9-nogpu.json", "wildct": "desktop-i9-nogpu.json", "crescent": "xeon6741p-4xb70.json"}
NODE_IMAGE = "cryptoandcoffee/k3d-gpu:0.1.80@sha256:" + "a" * 64

KUBECTL = r'''#!/usr/bin/env python3
"""kubectl stand-in: the launcher's calls name --context and are answered only for
k3d-${STUB_CLUSTER} ("context was not found" otherwise, as a hold's own kubeconfig answers);
the runner's own calls name no context."""
import os, shutil, sys
a = sys.argv[1:]
ctx = None
while a and a[0] == "--context":
    ctx, a = a[1], a[2:]
d = os.environ["STUB_DIR"]
with open(os.path.join(d, "kubectl.log"), "a") as f:
    f.write(f"ctx={ctx} {' '.join(a)}\n")
if ctx is not None and ctx != "k3d-" + os.environ.get("STUB_CLUSTER", ""):
    sys.exit(f"error: context was not found for specified context: {ctx}")
s = " ".join(a)
if s == "get nodes -l k3d-gpu.io/hybrid-cpu=true -o name":
    if os.environ.get("STUB_HYBRID"):
        print("node/k3d-x-server-0")
elif "allocatable" in s:
    print(os.environ.get("STUB_ALLOC", "cpu 32"))
elif a[:2] == ["apply", "-f"]:
    shutil.copy(a[2], os.path.join(d, "applied.yaml"))
    sys.exit(int(os.environ.get("STUB_APPLY_RC", "0")))
elif "port-forward" in a:
    os.execvp("sleep", ["sleep", "600"])
elif "{.items[*].status.phase}" in s:
    print("Running")
elif "{.items[0].metadata.name}" in s:
    print("pod-0", end="")
elif "{.spec.containers[0].name}" in s:
    print("server", end="")
elif "{.spec.containers[0].image}" in s:
    print("image@sha256:0 imageid", end="")
elif "-o json" in s:
    print("{}")
elif "exec" in a:
    print("cpuset 0-15\nCpus_allowed_list:\t0-15")
'''

DOCKER = r'''#!/bin/sh
case "$1" in
    inspect) echo deadbeef ;;
    top) exit 1 ;;
esac
exit 0
'''


def jl(path):
    with open(path) as f:
        return json.load(f)


def rd(path):
    with open(path) as f:
        return f.read()


def put(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


class Fake(http.server.ThreadingHTTPServer):
    """OpenAI-compatible stand-in: /v1 (L) and /v3 (O) stream at their own inter-token gap; a
    non-streamed chat answers one fixed sentence. Every request body is kept."""
    daemon_threads = True

    def __init__(self, itl):
        self.itl, self.bodies = itl, []
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self.reply({"object": "list", "data": [{"id": "gemma"}]})
        self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.bodies.append((self.path, body))
        itl = self.server.itl[self.path.split("/")[1]]
        n = body.get("max_tokens") or 16
        if not body.get("stream"):
            return self.reply({"choices": [{"index": 0, "message": {"role": "assistant", "content": "The answer is 0."},
                                            "finish_reason": "stop"}], "usage": {"prompt_tokens": 9, "completion_tokens": 5}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(obj):
            raw = f"data: {obj if isinstance(obj, str) else json.dumps(obj)}\n\n".encode()
            self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
            self.wfile.flush()
        chunk({"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]})
        for i in range(n):
            time.sleep(itl)
            chunk({"choices": [{"index": 0, "delta": {"content": f"t{i} "}, "finish_reason": "length" if i == n - 1 else None}]})
        if (body.get("stream_options") or {}).get("include_usage"):
            chunk({"choices": [], "usage": {"prompt_tokens": 1600, "completion_tokens": n}})
        chunk("[DONE]")
        self.wfile.write(b"0\r\n\r\n")


def serve(itl):
    srv = Fake(itl)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Box:
    """A scratch host: fit fixtures, a sysroot (hybrid i9 or the Xeon), kubectl and docker stubs,
    a fake RAPL zone, the campaign dir G with prep's drafter Job record."""

    def __init__(self, host, cluster="k3dgpu-d-h59-1", stub_cluster=None):
        self.d = tempfile.mkdtemp(prefix="h59-")
        d = self.d
        self.host, self.cluster = host, cluster
        put(os.path.join(d, "bin", "kubectl"), KUBECTL, 0o755)
        put(os.path.join(d, "bin", "docker"), DOCKER, 0o755)
        os.makedirs(os.path.join(d, "cache"))
        for n in os.listdir(os.path.join(FIX, "models")):
            src = os.path.join(FIX, "models", n)
            os.symlink(src, os.path.join(d, "cache", f"{n}@main"))
            rev = jl(os.path.join(src, "weights.json")).get("source", "").partition("@")[2]
            if rev:
                os.symlink(src, os.path.join(d, "cache", f"{n}@{rev}"))
        # O's render pins K3D_GPU_AGENT_REVISION to OVB's commit, and the launcher also reads the
        # requested base repo's format at that revision (agent_model_format). On a host that read
        # finds no such commit and the format is unknown; here it reads the base's own metadata,
        # which is not GGUF either, so the render takes the same path without the network.
        os.symlink(os.path.join(FIX, "models", "google__gemma-4-26B-A4B-it"),
                   os.path.join(d, "cache", f"google__gemma-4-26B-A4B-it@{run.OVB_REV}"))
        sysd = os.path.join(d, "root", "sys", "devices")
        hybrid = run.PROFILES[host]["pcores"] > 0
        for c in range(32 if hybrid else 96):
            if hybrid:
                sib = f"{c // 2 * 2}-{c // 2 * 2 + 1}" if c < 16 else str(c)
            else:
                sib = f"{c % 48},{c % 48 + 48}"
            put(os.path.join(sysd, "system", "cpu", f"cpu{c}", "topology", "core_cpus_list"), sib + "\n")
        if hybrid:
            put(os.path.join(sysd, "cpu_core", "cpus"), "0-15\n")
            put(os.path.join(sysd, "cpu_atom", "cpus"), "16-31\n")
        rapl = os.path.join(d, "powercap", "intel-rapl:0")
        put(os.path.join(rapl, "name"), "package-0\n")
        put(os.path.join(rapl, "energy_uj"), "123456789\n")
        put(os.path.join(rapl, "max_energy_range_uj"), "262143328850\n")
        os.makedirs(os.path.join(d, "home"))
        os.makedirs(os.path.join(d, "stub"))
        put(os.path.join(d, "kubeconfig"), "")
        self.g = os.path.join(d, "g")
        self.env = {
            "PATH": os.path.join(d, "bin") + ":/usr/local/bin:/usr/bin:/bin", "HOME": os.path.join(d, "home"),
            "LANG": "C.UTF-8", "K3D_GPU_SYSROOT": os.path.join(d, "root"), "K3D_GPU_FIT_CACHE": os.path.join(d, "cache"),
            "XDG_STATE_HOME": os.path.join(d, "state"), "XDG_CACHE_HOME": os.path.join(d, "xdg"),
            "K3D_GPU_HF_FIXTURES": os.path.join(ROOT, "tests", "fixtures", "hf"),
            "K3D_GPU_AUTOQUANT_CACHE": os.path.join(d, "no-autoquant.json"),
            "K3D_GPU_FIT_HOST": os.path.join(FIX, "hosts", HOSTS[host]),
            "KUBECONFIG": os.path.join(d, "kubeconfig"), "STUB_DIR": os.path.join(d, "stub"),
            "STUB_CLUSTER": stub_cluster or cluster, "STUB_ALLOC": "cpu 32" if hybrid else "cpu 64",
            "REPO": ROOT, "G": self.g, "CLUSTER": cluster, "H59_PROFILE": host,
            "ENERGY_POWERCAP": os.path.join(d, "powercap"), "H59_IDLE_S": "1",
        }
        if hybrid:
            self.env["STUB_HYBRID"] = "1"

    def prep_job(self):
        """prep.sh's drafter record: the conversion Job of an L render with no cluster."""
        out = os.path.join(self.d, "prep")
        r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "prep-render", out], env=self.env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + rd(os.path.join(out, "agent.err"))
        dst = os.path.join(self.g, "models", run.DRAFT_DIR)
        os.makedirs(dst, exist_ok=True)
        shutil.copy(os.path.join(out, "job.json"), os.path.join(dst, "prep-job.json"))
        return jl(os.path.join(out, "job.json"))

    def render(self, arm, out, **extra):
        env = dict(self.env, **extra)
        return subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "render", arm, out], env=env,
                              capture_output=True, text=True)

    def close(self):
        shutil.rmtree(self.d, ignore_errors=True)


class Tables(unittest.TestCase):
    def test_orders_as_registered(self):
        self.assertEqual(run.ORDERS, {1: ["O", "L"], 2: ["L", "O"], 3: ["O", "L"], 4: ["L", "O"]})
        self.assertEqual([run.hold(str(k)) for k in (1, 2, 3, 4)],
                         [("perf", ["O", "L"]), ("perf", ["L", "O"]), ("perf", ["O", "L"]), ("quality", ["L", "O"])])
        self.assertEqual(run.hold("5", "2"), ("perf", ["L", "O"]))
        self.assertEqual(run.hold("6", "4"), ("quality", ["L", "O"]))
        self.assertEqual(run.hold("smoke"), ("perf", ["O", "L"]))
        for bad in (("5", None), ("7", None), ("01", None), ("1", "2"), ("5", "5"), ("0", None), ("smoke", "1")):
            with self.assertRaises(SystemExit):
                run.hold(*bad)

    def test_profiles_name_each_rows_cpu_class(self):
        rows = {}
        for line in rd(os.path.join(ROOT, "EXPERIMENTS.md")).splitlines():
            if line.startswith("| H59"):
                rows[line.split("|")[1].strip()] = line
        for host, p in run.PROFILES.items():
            self.assertIn(p["cpu"], rows[p["row"]], host)
        self.assertEqual({p["row"] for p in run.PROFILES.values()}, {"H59", "H59d", "H59w"})

    def test_host_rules(self):
        c, d, w = run.PROFILES["crescent"], run.PROFILES["desktop"], run.PROFILES["wildct"]
        # N=16 on crescent only; H54's void rule there, H57d's load gate on the two clients.
        self.assertIn("n16", c["phases"].split(","))
        self.assertNotIn("n16", d["phases"].split(","))
        self.assertNotIn("n16", w["phases"].split(","))
        self.assertEqual((c["load_rule"], d["load_rule"], w["load_rule"]), ("h54-void", "h57d", "h57d"))
        # Hold-start load1: H54's 12, H57d's 24, wildct's 12; caps: H54's 2400 s, H57d's 4500 s.
        self.assertEqual((c["load1_max"], d["load1_max"], w["load1_max"]), (12, 24, 12))
        self.assertEqual((c["cap_s"], d["cap_s"], w["cap_s"]), (2400, 4500, 4500))
        self.assertEqual((c["arms_end_s"], d["arms_end_s"]), (2370, 4470))
        self.assertEqual((c["vendors"], d["vendors"], w["vendors"]), ("amx", "hybrid", "auto,-nvidia,-intel"))
        self.assertEqual(w["k3s_args"], run.W3)
        self.assertTrue(c["prefix"].startswith("k3dgpu-g-") and d["prefix"].startswith("k3dgpu-d-")
                        and w["prefix"].startswith("k3dgpu-w-"))
        self.assertEqual((c["pcores"], d["pcores"], w["pcores"]), (0, 16, 16))

    def test_render_env_scrubs_and_pins(self):
        shell = {"K3D_GPU_AGENT_BACKEND": "vllm", "K3D_GPU_AGENT_MODEL": "x/y", "K3D_GPU_LLAMA_CPU_IMAGE": "i",
                 "K3D_GPU_MODEL_OPT": "off", "K3D_GPU_FIT": "off", "K3D_GPU_CONTEXT": "k3d-gpu", "PATH": "/bin",
                 "K3D_GPU_FIT_HOST": "/h.json", "KUBECONFIG": "/k"}
        o = run.render_env("O", shell, "k3dgpu-d-h59-2")
        lo = run.render_env("L", shell, "k3dgpu-d-h59-2")
        for e in (o, lo):
            for k in ("K3D_GPU_AGENT_BACKEND", "K3D_GPU_AGENT_MODEL", "K3D_GPU_LLAMA_CPU_IMAGE", "K3D_GPU_MODEL_OPT",
                      "K3D_GPU_FIT", "K3D_GPU_CONTEXT"):
                self.assertNotIn(k, e)
            self.assertEqual(e["K3D_GPU_CLUSTER"], "k3dgpu-d-h59-2")
            self.assertEqual((e["K3D_GPU_AGENT_DEVICE"], e["K3D_GPU_TUNE"], e["PATH"], e["KUBECONFIG"]), ("CPU", "off", "/bin", "/k"))
        self.assertEqual(o["K3D_GPU_AGENT_REVISION"], run.OVB_REV)
        self.assertNotIn("K3D_GPU_AGENT_DRAFT", o)
        self.assertEqual((lo["K3D_GPU_AGENT_REVISION"], lo["K3D_GPU_AGENT_DRAFT"]), (run.GGUF_REV, "mtp"))

    def test_docker_argv_runs_the_jobs_container(self):
        spec = {"name": "gguf-convert-x", "image": run.LLAMA_FULL_IMAGE, "command": ["bash", "/scripts/convert.sh"],
                "args": [run.DRAFT_REPO, run.DRAFT_REV, "/models/" + run.DRAFT_DIR, "4294967296", "Q4_0", "--outtype", "bf16"],
                "env": {"GGUF_CONVERT_PIP": "transformers==5.21.0 x==1", "HOME": "/tmp"},
                "resources": {"limits": {"memory": "64Gi"}}}
        a = run.docker_argv(spec, "/g/models", "/g/prep/drafter")
        self.assertEqual(a[a.index("--memory") + 1], "64g")
        self.assertIn("GGUF_CONVERT_PIP=transformers==5.21.0 x==1", a)
        self.assertIn("/g/models:/models", a)
        self.assertEqual(a[-8:], ["/scripts/convert.sh"] + spec["args"])
        with self.assertRaises(SystemExit):
            run.docker_argv(dict(spec, image="ghcr.io/ggml-org/llama.cpp:full-b11749"), "/m", "/s")


class Render(unittest.TestCase):
    """`agent manifest --model` through run.py render, offline, for each host profile."""

    def setUp(self):
        self.box = Box("desktop")
        self.job = self.box.prep_job()
        self.out = os.path.join(self.box.d, "out")

    def tearDown(self):
        self.box.close()

    def docs(self, arm, name="deploy.yaml"):
        return run.load_docs(os.path.join(self.out, arm, name))

    def test_both_arms_render_on_the_pcore_balloon(self):
        for arm in ("O", "L"):
            r = self.box.render(arm, os.path.join(self.out, arm))
            self.assertEqual(r.returncode, 0, r.stderr)
            tpl = run.deployment(self.docs(arm))["spec"]["template"]
            c = tpl["spec"]["containers"][0]
            self.assertEqual(tpl["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
            self.assertEqual(str(c["resources"]["requests"]["cpu"]), "16")
            self.assertEqual(jl(os.path.join(self.out, arm, "check.json"))["problems"], [])
            env = rd(os.path.join(self.out, arm, "render-env.txt"))
            self.assertIn("K3D_GPU_CLUSTER=k3dgpu-d-h59-1\n", env)
        log = rd(os.path.join(self.box.d, "stub", "kubectl.log"))
        self.assertIn("ctx=k3d-k3dgpu-d-h59-1 get nodes -l k3d-gpu.io/hybrid-cpu=true -o name", log)

    def test_a_render_that_misses_the_holds_cluster_is_refused(self):
        # H57dp's Od deviation: the launcher's context is not the hold's, the node queries fail,
        # and the render has no P-core label and 1 CPU. The check stops the arm before apply.
        box = Box("desktop", stub_cluster="some-other-cluster")
        try:
            box.prep_job()
            r = box.render("O", os.path.join(box.d, "o"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not performance (the P-core balloon)", r.stderr)
            self.assertIn("CPU request '1'", r.stderr)
        finally:
            box.close()

    def test_l_stage_drops_only_the_download_and_the_done_conversion(self):
        r = self.box.render("L", os.path.join(self.out, "L"))
        self.assertEqual(r.returncode, 0, r.stderr)
        raw, staged = self.docs("L", "agent.yaml"), self.docs("L")
        self.assertEqual(sorted(d["kind"] for d in raw),
                         ["ConfigMap", "Deployment", "Job", "Namespace", "PersistentVolumeClaim", "Service"])
        self.assertEqual(sorted(d["kind"] for d in staged), ["Deployment", "Namespace", "Service"])
        rc, sc = (run.deployment(x)["spec"]["template"]["spec"]["containers"][0] for x in (raw, staged))
        ra = [str(x) for x in rc["args"]]
        i = ra.index("--model-url")
        self.assertEqual(sc["args"], ra[:i] + ra[i + 2:])
        self.assertEqual((sc["image"], sc["resources"]), (rc["image"], rc["resources"]))
        a = sc["args"]
        self.assertEqual(run.value(a, "--model"), run.GGUF_PATH)
        self.assertEqual(run.value(a, "--spec-draft-model"), run.DRAFT_PATH)
        self.assertEqual(run.value(a, "--spec-type"), "draft-mtp")
        self.assertEqual((run.value(a, "--threads"), run.value(a, "--threads-batch")), ("8", "16"))
        self.assertTrue(all(m["readOnly"] for m in sc["volumeMounts"] if m["name"] == "models"))
        vol = run.deployment(staged)["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(vol, [{"name": "models", "hostPath": {"path": "/var/apb-models", "type": "Directory"}}])
        self.assertNotIn("initContainers", run.deployment(staged)["spec"]["template"]["spec"])
        fit = jl(os.path.join(self.out, "L", "fit.json"))
        self.assertEqual((fit["threads"], fit["threads-batch"]), ("8", "16"))
        self.assertTrue(fit["fit"].startswith("cpu "))

    def test_o_stage_keeps_the_pull_container(self):
        r = self.box.render("O", os.path.join(self.out, "O"))
        self.assertEqual(r.returncode, 0, r.stderr)
        raw, staged = self.docs("O", "agent.yaml"), self.docs("O")
        rs, ss = (run.deployment(x)["spec"]["template"]["spec"] for x in (raw, staged))
        self.assertEqual(ss["initContainers"], rs["initContainers"])
        self.assertEqual(ss["containers"], rs["containers"])
        self.assertEqual(ss["volumes"], [{"name": "models", "hostPath": {"path": "/var/apb-models", "type": "Directory"}}])
        fit = jl(os.path.join(self.out, "O", "fit.json"))
        self.assertIsNotNone(fit["max_num_seqs"])
        self.assertFalse(any(x.startswith("--draft") for x in fit["pull_args"]))

    def test_render_needs_prep_s_drafter_job(self):
        os.remove(os.path.join(self.box.g, "models", run.DRAFT_DIR, "prep-job.json"))
        r = self.box.render("L", os.path.join(self.out, "L"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no prep-job.json", r.stderr)
        put(os.path.join(self.box.g, "models", run.DRAFT_DIR, "prep-job.json"), json.dumps(dict(self.job, image="other")))
        r = self.box.render("L", os.path.join(self.out, "L2"))
        self.assertIn("differs from the one prep.sh ran", r.stderr)

    def test_prep_job_is_pr182s_conversion(self):
        self.assertEqual(self.job["image"], run.LLAMA_FULL_IMAGE)
        self.assertEqual(self.job["args"], [run.DRAFT_REPO, run.DRAFT_REV, "/models/" + run.DRAFT_DIR, "4294967296",
                                            "Q4_0", "--outtype", "bf16"])
        self.assertIn("transformers==5.21.0", self.job["env"]["GGUF_CONVERT_PIP"])

    def test_check_names_each_deviation(self):
        for arm in ("O", "L"):
            self.assertEqual(self.box.render(arm, os.path.join(self.out, arm)).returncode, 0)
        base = {arm: self.docs(arm) for arm in ("O", "L")}

        def bad(arm, edit):
            docs = json.loads(json.dumps(base[arm]))
            edit(run.deployment(docs)["spec"]["template"])
            return run.check(arm, "desktop", docs)

        def c(t):
            return t["spec"]["containers"][0]
        self.assertEqual(run.check("O", "desktop", base["O"]), [])
        self.assertEqual(run.check("L", "desktop", base["L"]), [])
        self.assertTrue(bad("L", lambda t: c(t).update(image="ghcr.io/ggml-org/llama.cpp:server-b11749")))
        self.assertTrue(bad("L", lambda t: c(t)["args"].remove("--spec-type")))
        self.assertTrue(bad("L", lambda t: c(t)["args"].extend(["--model-url", "https://x"])))
        self.assertTrue(bad("L", lambda t: c(t)["args"].__setitem__(c(t)["args"].index("--threads") + 1, "16")))
        self.assertTrue(bad("L", lambda t: [m.pop("readOnly") for m in c(t)["volumeMounts"]]))
        self.assertTrue(bad("O", lambda t: t["spec"]["initContainers"][0]["args"].append("--draft_source_model=x")))
        self.assertTrue(bad("O", lambda t: t["metadata"]["labels"].pop("k3d-gpu.io/cores")))
        self.assertTrue(bad("O", lambda t: c(t)["resources"]["requests"].update(cpu="1")))
        self.assertTrue(bad("O", lambda t: c(t)["resources"]["limits"].update({"gpu.intel.com/xe": 1})))
        self.assertTrue(bad("O", lambda t: t["spec"]["initContainers"][0]["args"].remove("--target_device=CPU")))
        # On crescent no hybrid balloon is expected: the P-core label is a deviation there.
        self.assertIn("on a host with no hybrid balloon", " ".join(run.check("O", "crescent", base["O"])))


class Crescent(unittest.TestCase):
    def test_xeon_render_has_no_balloon(self):
        box = Box("crescent", cluster="k3dgpu-g-h59-1")
        try:
            box.prep_job()
            for arm in ("O", "L"):
                r = box.render(arm, os.path.join(box.d, arm))
                self.assertEqual(r.returncode, 0, r.stderr)
                tpl = run.deployment(run.load_docs(os.path.join(box.d, arm, "deploy.yaml")))["spec"]["template"]
                self.assertNotIn("k3d-gpu.io/cores", tpl["metadata"]["labels"])
        finally:
            box.close()


def phases_line(**k):
    return json.dumps(k) + "\n"


class Gate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def arm(self, hold, arm, tries, status="measured"):
        ad = os.path.join(self.d, f"hold{hold}", arm)
        os.makedirs(ad, exist_ok=True)
        put(os.path.join(ad, "status.json"), json.dumps({"status": status}))
        with open(os.path.join(ad, "phases.jsonl"), "w") as f:
            f.write(phases_line(phase="n1", t0=0, t1=1, check={"void": True, "reason": "n1 does not decide"}))
            for i, v in enumerate(tries):
                f.write(phases_line(phase="n4", **{"try": i + 1}, t0=1, t1=2, check={"void": v, "reason": f"cpu {v}"}))

    def test_matched_when_every_n4_ends_clean(self):
        for k in (1, 2, 3):
            self.arm(k, "O", [False])
            self.arm(k, "L", [True, False])
        g = run.gate(self.d)
        self.assertTrue(g["matched"], g)
        self.assertEqual(g["checked"], 6)

    def test_void_twice_is_unmatched(self):
        for k in (1, 2, 3):
            self.arm(k, "O", [False])
            self.arm(k, "L", [False])
        self.arm(2, "L", [True, True])
        g = run.gate(self.d)
        self.assertFalse(g["matched"])
        self.assertIn("hold 2 L", g["reason"])

    def test_spare_stands_in_and_unmeasured_arms_are_skipped(self):
        for k in (1, 2, 3):
            self.arm(k, "O", [False])
            self.arm(k, "L", [False])
        self.arm(3, "L", [True, True])
        self.arm(5, "O", [False])
        self.arm(5, "L", [False])
        put(os.path.join(self.d, "hold5", "repeats"), "3\n")
        self.assertTrue(run.gate(self.d)["matched"])
        self.arm(1, "O", [True, True], status="failed")
        self.assertTrue(run.gate(self.d)["matched"])

    def test_missing_check_is_unmatched(self):
        for k in (1, 2, 3):
            self.arm(k, "O", [False])
            self.arm(k, "L", [False])
        with open(os.path.join(self.d, "hold1", "O", "phases.jsonl"), "w") as f:
            f.write(phases_line(phase="n4", t0=1, t1=2))
        self.assertFalse(run.gate(self.d)["matched"])


class Prefix(unittest.TestCase):
    def test_six_agents_one_shared_prefix(self):
        srv = serve({"v1": 0.0, "v3": 0.0})
        out = os.path.join(tempfile.mkdtemp(), "prefix.json")
        try:
            rec = run.prefix(f"http://127.0.0.1:{srv.server_address[1]}/v1", "gemma", 5901, out)
        finally:
            srv.shutdown()
            srv.server_close()
        bodies = [b for _, b in srv.bodies]
        self.assertEqual(len(bodies), 6)
        self.assertEqual(len({json.dumps(b["messages"][0]) for b in bodies}), 1)
        self.assertEqual(len({json.dumps(b["tools"]) for b in bodies}), 1)
        self.assertEqual(len({b["messages"][1]["content"] for b in bodies}), 6)
        for b in bodies:
            self.assertEqual((b["model"], b["max_tokens"], b["stream"]), ("gemma", 8, True))
            self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual([a["agent"] for a in rec["agents"]], ["a1", "a2", "a3", "a4", "a5", "a6"])
        self.assertIsNotNone(rec["added_ttft_median_s"])
        self.assertEqual(jl(out)["seed"], 5901)


def run_arm(box, arm, hold_dir, mode, holdnum, port, **extra):
    env = dict(box.env, MODE=mode, DEADLINE=str(int(time.time()) + 5000), HOLDNUM=str(holdnum), H59_PORT=str(port))
    env.update(extra)
    r = subprocess.run(["bash", os.path.join(HERE, "arm.sh"), arm, os.path.join(hold_dir, arm)], env=env,
                       capture_output=True, text=True, timeout=600)
    return r


def synth_load(hold_dir, arms, foreign=1.0):
    """H57d's sampler records over the hold's phases: P-core busy and node CPU, matched."""
    t = []
    for a in arms:
        for line in rd(os.path.join(hold_dir, a, "phases.jsonl")).splitlines():
            r = json.loads(line)
            t += [r["t0"], r["t1"]]
    with open(os.path.join(hold_dir, "load.jsonl"), "w") as f:
        ts = min(t) - 1
        while ts < max(t) + 1:
            f.write(json.dumps({"ts": ts, "seconds": 0.2, "pcore_busy": 6.0 + foreign, "node_cpu": 6.0}) + "\n")
            ts += 0.2


class EndToEnd(unittest.TestCase):
    """arm.sh, as hold-run.sh runs it, for the registered holds of a desktop row: three
    performance holds in their orders and the quality hold; then h59.py scores the records."""

    def test_holds_feed_the_scorer(self):
        box = Box("desktop")
        srv = serve({"v1": 0.0008, "v3": 0.0016})   # L decodes about twice as fast as O
        try:
            box.prep_job()
            res = os.path.join(box.g, "results", "H59d")
            for k in (1, 2, 3, 4):
                mode, arms = run.hold(str(k))
                hd = os.path.join(res, f"hold{k}")
                for a in arms:
                    r = run_arm(box, a, hd, mode, k, srv.server_address[1], CLUSTER=box.cluster)
                    st = jl(os.path.join(hd, a, "status.json"))
                    self.assertEqual(st["status"], "measured", f"hold {k} {a}: {st} {r.stderr[-2000:]}")
                if mode == "perf":
                    synth_load(hd, arms)
            for k in (1, 2, 3):
                ad = os.path.join(res, f"hold{k}", "L")
                for f in ("load-n1.json", "load-n4.json", "load-ttft.json", "prefix.json", "cpus.txt", "fit.json",
                          "deploy.yaml", "energy-n4.json", "energy-idle.json"):
                    self.assertTrue(os.path.exists(os.path.join(ad, f)), f"hold {k} L {f}")
                self.assertFalse(os.path.exists(os.path.join(ad, "load-n16.json")))
                n4 = jl(os.path.join(ad, "load-n4.json"))["phases"][0]
                self.assertEqual((n4["concurrency"], len(n4["samples"])), (4, 16))
                self.assertEqual([json.loads(x)["phase"] for x in rd(os.path.join(ad, "phases.jsonl")).splitlines()],
                                 ["idle", "n1", "n4", "ttft", "prefix"])
            q = jl(os.path.join(res, "hold4", "O", "quality.json"))
            self.assertEqual((q["n"], q["errors"]), (50, 0))
            rep = h59.score(res, "h57d")
            self.assertEqual(rep["verdict"], "SUPPORTED", json.dumps({k: v for k, v in rep.items() if k != "arms"}))
            self.assertEqual([rep["arms"][a]["n4_requests"] for a in ("L", "O")], [48, 48])
            self.assertGreater(rep["ci"][0], 1.2)
            self.assertTrue(rep["all_holds_faster"])
            self.assertTrue(rep["load_gate"]["matched"])
            # The scorer's CLI on the same records.
            cli = subprocess.run([sys.executable, os.path.join(HERE, "h59.py"), "score", res, "--load", "h57d"],
                                 capture_output=True, text=True)
            self.assertEqual(json.loads(cli.stdout)["verdict"], "SUPPORTED")
        finally:
            srv.shutdown()
            srv.server_close()
            box.close()

    def test_crescent_n16_and_the_void_rerun(self):
        box = Box("crescent", cluster="k3dgpu-g-h59-1")
        srv = serve({"v1": 0.0004, "v3": 0.0004})
        try:
            box.prep_job()
            serving = os.path.join(box.d, "serving.jsonl")
            now = time.time()
            with open(serving, "w") as f:   # non-campaign CPU 9 CPU-s/s throughout: every phase is void
                for i in range(-20, 900):
                    f.write(json.dumps({"ts": now + i, "seconds": 1, "serving_cpu": 9.0, "aiperf_procs": 0}) + "\n")
            hd = os.path.join(box.g, "results", "H59", "hold1")
            r = run_arm(box, "L", hd, "perf", 1, srv.server_address[1], SERVING=serving)
            st = jl(os.path.join(hd, "L", "status.json"))
            self.assertEqual(st["status"], "measured", r.stderr[-2000:])
            ph = [json.loads(x) for x in rd(os.path.join(hd, "L", "phases.jsonl")).splitlines()]
            self.assertEqual([p["phase"] for p in ph if p["phase"] == "n4"], ["n4", "n4"])
            self.assertTrue(all(p["check"]["void"] for p in ph if p["phase"] == "n4"))
            for f in ("load-n16.json", "load-n4-void1.json", "energy-idle-n4.json"):
                self.assertTrue(os.path.exists(os.path.join(hd, "L", f)), f)
            n16 = jl(os.path.join(hd, "L", "load-n16.json"))["phases"][0]
            self.assertEqual((n16["concurrency"], len(n16["samples"])), (16, 32))
            g = run.gate(os.path.join(box.g, "results", "H59"))
            self.assertFalse(g["matched"])
            self.assertIn("hold 1 L", g["reason"])
        finally:
            srv.shutdown()
            srv.server_close()
            box.close()

    def test_render_refusal_and_not_reached(self):
        box = Box("desktop", stub_cluster="elsewhere")
        try:
            box.prep_job()
            hd = os.path.join(box.d, "hold1")
            run_arm(box, "O", hd, "perf", 1, 1)
            st = jl(os.path.join(hd, "O", "status.json"))
            self.assertEqual(st["status"], "failed")
            self.assertIn("render not the registered arm", st["reason"])
            self.assertFalse(os.path.exists(os.path.join(box.d, "stub", "applied.yaml")))
            r = run_arm(box, "O", os.path.join(box.d, "hold2"), "perf", 2, 1, DEADLINE=str(int(time.time()) + 100))
            st = jl(os.path.join(box.d, "hold2", "O", "status.json"))
            self.assertEqual(st["status"], "not_reached", r.stderr)
        finally:
            box.close()


class HoldGuards(unittest.TestCase):
    """hold.sh refuses, before the lock, what would spend a hold on the wrong thing."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = os.path.join(self.d, "g")
        put(os.path.join(self.d, "cpuinfo"), "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n")
        os.makedirs(os.path.join(self.g, "bin"))
        shutil.copy(os.path.join(HERE, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        m = os.path.join(self.g, "models")
        for p in run.models_ready(self.g):
            put(p, "x")
        self.assertEqual(run.models_ready(self.g), [])
        # flock stands in so a hold that passes every guard stops at the lock (exit 4).
        put(os.path.join(self.d, "bin", "flock"), "#!/bin/sh\nexit 1\n", 0o755)
        self.env = {"PATH": os.path.join(self.d, "bin") + ":/usr/bin:/bin", "HOME": self.d, "G": self.g,
                    "H59_IMAGE": NODE_IMAGE, "H59_CPUINFO": os.path.join(self.d, "cpuinfo"),
                    "LOCK": os.path.join(self.d, "lock")}
        self.res = os.path.join(self.g, "results", "H59d")
        self.m = m

    def tearDown(self):
        shutil.rmtree(self.d)

    def hold(self, *args, **env):
        return subprocess.run(["bash", os.path.join(HERE, "hold.sh"), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=60)

    def ran(self, k, statuses):
        hd = os.path.join(self.res, f"hold{k}")
        put(os.path.join(hd, "rc"), "0\n")
        for a, s in statuses.items():
            put(os.path.join(hd, a, "status.json"), json.dumps({"status": s}))

    def test_guards_pass_to_the_lock(self):
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_wrong_cpu_class(self):
        r = self.hold("wildct", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not the i7-13700", r.stderr)

    def test_unregistered_holds(self):
        for args in (("desktop", "7"), ("desktop", "5"), ("desktop", "01"), ("desktop", "1", "2"), ("nowhere", "1")):
            self.assertEqual(self.hold(*args).returncode, 2, args)

    def test_image_pinned_by_digest(self):
        self.assertEqual(self.hold("desktop", "1", H59_IMAGE="cryptoandcoffee/k3d-gpu:0.1.80").returncode, 2)

    def test_shim_and_models(self):
        os.remove(os.path.join(self.g, "bin", "k3d"))
        self.assertIn("k3d-shim.sh", self.hold("desktop", "1").stderr)
        shutil.copy(os.path.join(HERE, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        os.remove(os.path.join(self.m, run.DRAFT_DIR, "prep-job.json"))
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("prep.sh", r.stderr)

    def test_a_hold_runs_once(self):
        self.ran(2, {"L": "measured", "O": "failed"})
        r = self.hold("desktop", "2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("a failed hold counts", r.stderr)

    def test_spares_repeat_a_failed_hold_only(self):
        self.assertEqual(self.hold("desktop", "5", "2").returncode, 2)          # hold 2 has not run
        self.ran(2, {"L": "measured", "O": "measured"})
        self.assertEqual(self.hold("desktop", "5", "2").returncode, 2)          # nothing failed in it
        self.ran(3, {"O": "measured", "L": "not_deliverable"})
        self.assertEqual(self.hold("desktop", "5", "3").returncode, 4)          # passes to the lock
        self.assertFalse(os.path.exists(os.path.join(self.res, "hold5", "repeats")))

    def test_node_image_is_the_rows(self):
        put(os.path.join(self.res, "node-image"), "cryptoandcoffee/k3d-gpu:0.1.79@sha256:" + "b" * 64 + "\n")
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("node image", r.stderr)


class Shim(unittest.TestCase):
    def test_volume_only_on_this_campaigns_clusters(self):
        d = tempfile.mkdtemp()
        try:
            put(os.path.join(d, "k3d"), "#!/bin/sh\necho \"$@\"\n", 0o755)
            env = {"PATH": "/usr/bin:/bin", "K3D_REAL": os.path.join(d, "k3d"), "H59_G": "/g", "H59_PREFIX": "k3dgpu-d-h59-"}
            shim = os.path.join(HERE, "k3d-shim.sh")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-d-h59-1", "--x"], env=env,
                                 capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-d-h59-1 --x --volume=/g/models:/var/apb-models@server:*")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-d-h57d-1"], env=env, capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-d-h57d-1")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for the H63 runner (bench/h63: run.py, arm.sh, hold.sh, hold-run.sh, prep.sh, k3d-shim.sh,
reproduce.sh; EXPERIMENTS.md "H63 amendment R1" and "H63w amendment R1"). Offline: Od and Ls render through the real
`k3d-gpu agent manifest` against the fit fixtures with H59's kubectl stand-in, which answers only
for the hold's own context; arm.sh runs every registered hold against a local OpenAI-compatible
server and h63.py scores the records; hold.sh's guards, prep.sh's steps (with `hf` and `docker`
stand-ins) and the k3d shim run as the shell scripts. The drafters' registered sha256 cannot be
built offline, so the subprocess tests run run.py through a stand-in that sets the expected
digests to the stand-in files' (H63_TEST_PATCH); the in-process tests patch the same constants.
No cluster, no network."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h63  # noqa: E402  (puts bench/h59, bench/h57d and bench/h54 on the path)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load("h63run", os.path.join(HERE, "run.py"))
T59 = _load("h59_test_run", os.path.join(ROOT, "bench", "h59", "test_run.py"))
h59run, h59, h57d = h63.h59run, h63.h59, h63.h57d
put, rd, jl, serve, synth_load = T59.put, T59.rd, T59.jl, T59.serve, T59.synth_load
NODE_IMAGE = "cryptoandcoffee/k3d-gpu:0.1.101@sha256:" + "a" * 64
CLUSTER = "k3dgpu-d-h63-1"


def sha(b):
    return hashlib.sha256(b).hexdigest()


# Stand-in model files: the drafters and Od's graph.pbtxt, with the digests the patch expects.
LS_BYTES, LDMTP_BYTES, GRAPH_BYTES = b"ls-drafter\n", b"ld-mtp-drafter\n", b"graph\n"
PATCH = {"LS_DRAFTER": {"sha256": sha(LS_BYTES), "bytes": len(LS_BYTES)},
         "LDMTP_DRAFTER": {"sha256": sha(LDMTP_BYTES), "bytes": len(LDMTP_BYTES)},
         "OD_GRAPH_SHA_PREFIX": sha(GRAPH_BYTES)[:8]}
RUN_STANDIN = '''import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("h63_run_real", {real!r})
m = importlib.util.module_from_spec(spec)
sys.modules["h63_run_real"] = m
spec.loader.exec_module(m)
for k, v in json.loads(os.environ.get("H63_TEST_PATCH", "{{}}")).items():
    setattr(m, k, v)
if __name__ == "__main__":
    sys.exit(m.main(sys.argv[1:]))
globals().update({{k: getattr(m, k) for k in dir(m) if not k.startswith("__")}})
'''


def tree(d):
    """A repository view whose bench/h63/run.py is the stand-in (every other path links to this
    checkout), for the scripts that call run.py by path."""
    t = os.path.join(d, "repo")
    os.makedirs(os.path.join(t, "bench", "h63"))
    for n in os.listdir(ROOT):
        if n not in ("bench", ".git"):
            os.symlink(os.path.join(ROOT, n), os.path.join(t, n))
    for n in os.listdir(os.path.join(ROOT, "bench")):
        if n != "h63":
            os.symlink(os.path.join(ROOT, "bench", n), os.path.join(t, "bench", n))
    for n in os.listdir(HERE):
        if n not in ("run.py", "__pycache__"):
            os.symlink(os.path.join(HERE, n), os.path.join(t, "bench", "h63", n))
    put(os.path.join(t, "bench", "h63", "run.py"), RUN_STANDIN.format(real=os.path.join(HERE, "run.py")))
    return t


def stage_models(g):
    """prep.sh's layout with stand-in files: the GGUF (and the link Ls reads it through), OVB's
    language model, both drafters and Od's graph.pbtxt."""
    m = os.path.join(g, "models")
    put(os.path.join(m, R.H57D_GGUF_DIR, R.GGUF_FILE), "gguf")
    os.makedirs(os.path.dirname(os.path.join(m, R.LS_GGUF_LINK)), exist_ok=True)
    os.symlink(f"../{R.H57D_GGUF_DIR}", os.path.join(m, R.LS_GGUF_LINK))
    for f in h59run.OVB_LM:
        put(os.path.join(m, R.OVB, f), "ovb")
    with open(os.path.join(m, R.OD_GRAPH), "wb") as f:
        f.write(GRAPH_BYTES)
    for rel, b in ((R.LDMTP_DRAFT, LDMTP_BYTES), (os.path.join(R.LS_DRAFT_DIR, "model.gguf"), LS_BYTES)):
        os.makedirs(os.path.dirname(os.path.join(m, rel)), exist_ok=True)
        with open(os.path.join(m, rel), "wb") as f:
            f.write(b)


def patched():
    return mock.patch.multiple(R, **PATCH)


class Box(T59.Box):
    """H59's scratch host (fit fixtures, hybrid sysroot, kubectl and docker stand-ins, a fake
    RAPL zone) with H63's environment and stand-in model files."""

    def __init__(self, cluster=CLUSTER, stub_cluster=None):
        super().__init__("desktop", cluster=cluster, stub_cluster=stub_cluster)
        self.env.update({"H63_PROFILE": "desktop", "H63_IDLE_S": "1", "H63_TEST_PATCH": json.dumps(PATCH)})
        stage_models(self.g)


REGISTERED_LS_ARGS = [
    "--model", R.LS_GGUF_PATH, "--alias", "gemma", "--host", "0.0.0.0", "--port", "8000", "--jinja",
    "--reasoning", "off", "-c", "262144", "-np", "1", "-ngl", "0", "--cache-ram", "0", "-ctxcp", "4",
    "--threads", "8", "--threads-batch", "16", "--load-mode", "mmap+mlock", "--spec-draft-model", R.LS_DRAFT_PATH,
    "--spec-type", "draft-mtp"]


class Tables(unittest.TestCase):
    def test_orders_as_registered(self):
        self.assertIs(R.ORDERS, h63.ORDERS)
        self.assertEqual(R.ORDERS, {1: ["Od", "Ld-mtp", "Ls"], 2: ["Ld-mtp", "Ls", "Od"], 3: ["Ls", "Od", "Ld-mtp"],
                                    4: ["Ls", "Od", "Ld-mtp"]})
        # Each arm once in each position over the performance holds.
        for pos in range(3):
            self.assertEqual(sorted(R.ORDERS[k][pos] for k in (1, 2, 3)), sorted(R.ARMS))
        self.assertEqual([R.hold(str(k))[0] for k in (1, 2, 3, 4)], ["perf", "perf", "perf", "quality"])
        self.assertEqual(R.hold("5", "2"), ("perf", ["Ld-mtp", "Ls", "Od"]))
        self.assertEqual(R.hold("6", "4"), ("quality", ["Ls", "Od", "Ld-mtp"]))
        self.assertEqual(R.hold("smoke"), ("perf", ["Od", "Ld-mtp", "Ls"]))
        for bad in (("5", None), ("7", None), ("01", None), ("1", "2"), ("5", "5"), ("0", None), ("smoke", "1")):
            with self.assertRaises(SystemExit):
                R.hold(*bad)

    def test_profiles_name_each_rows_cpu_class(self):
        rows = {line.split("|")[1].strip(): line for line in rd(os.path.join(ROOT, "EXPERIMENTS.md")).splitlines()
                if line.startswith("| H63")}
        for host, p in R.PROFILES.items():
            self.assertIn(p["cpu"], rows[p["row"]], host)
        self.assertEqual({p["row"] for p in R.PROFILES.values()}, {"H63d", "H63w"})

    def test_host_rules_as_registered(self):
        d, w = R.PROFILES["desktop"], R.PROFILES["wildct"]
        self.assertEqual((d["load1_max"], d["disk_gb"], d["mem_gib"], d["cap_s"], d["arms_end_s"]), (24, 15, 20, 4500, 4470))
        self.assertEqual((d["vendors"], d["k3s_args"], d["prefix"], d["pcores"]), ("hybrid", h59run.C1, "k3dgpu-d-h63-", 16))
        self.assertEqual((w["load1_max"], w["vendors"], w["k3s_args"], w["prefix"]), (12, "auto,-nvidia,-intel", h59run.W3, "k3dgpu-w-h63-"))
        # The GPU co-tenants: read on the desktop; on wildct no GPU is read (rule W8).
        self.assertEqual((d["gpus"], w["gpus"]), ("read", "none"))
        self.assertEqual(R.LOAD_PHASES, {"warmup": (3, 1, 256), "n1": (8, 1, 256), "n4": (16, 4, 256), "ttft": (4, 1, 16)})

    def test_render_env_scrubs_and_pins(self):
        shell = {"K3D_GPU_AGENT_BACKEND": "vllm", "K3D_GPU_AGENT_MODEL": "x/y", "K3D_GPU_LLAMA_CPU_IMAGE": "i",
                 "K3D_GPU_MODEL_OPT": "off", "K3D_GPU_FIT": "off", "K3D_GPU_CONTEXT": "k3d-gpu", "K3D_GPU_TUNE": "on",
                 "K3D_GPU_AGENT_MAX_NUM_SEQS": "4", "PATH": "/bin", "KUBECONFIG": "/k"}
        od, ls = R.render_env("Od", shell, "k3dgpu-d-h63-2"), R.render_env("Ls", shell, "k3dgpu-d-h63-2")
        agent = lambda e: {k: v for k, v in e.items() if k.startswith("K3D_GPU_")}  # noqa: E731
        self.assertEqual(agent(od), {
            "K3D_GPU_AGENT_DEVICE": "CPU", "K3D_GPU_AGENT_MODEL": "OpenVINO/gemma-4-26b-a4b-it-int4-ov",
            "K3D_GPU_AGENT_MODEL_NAME": "gemma", "K3D_GPU_AGENT_DRAFT": "off", "K3D_GPU_AUTOQUANT": "off",
            "K3D_GPU_AGENT_IMAGE": "openvino/model_server:2026.4.1-gpu@sha256:"
                                   "a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58",
            "K3D_GPU_CLUSTER": "k3dgpu-d-h63-2"})
        self.assertEqual(agent(ls), {
            "K3D_GPU_AGENT_DEVICE": "CPU", "K3D_GPU_AGENT_DRAFT": "mtp", "K3D_GPU_AGENT_MAX_NUM_SEQS": "1",
            "K3D_GPU_AGENT_REVISION": "d1c082be9cf3c8a514acf63b8761f4b41935842e", "K3D_GPU_AGENT_MODEL_NAME": "gemma",
            "K3D_GPU_TUNE": "off", "K3D_GPU_CLUSTER": "k3dgpu-d-h63-2",
            "K3D_GPU_LLAMA_CPU_IMAGE": "ghcr.io/ggml-org/llama.cpp:server-b11960@sha256:"
                                       "8052c13edd987de8858f8150031e025ac3d66823f09cf3ec8220899c4abce220"})
        self.assertEqual((od["PATH"], od["KUBECONFIG"]), ("/bin", "/k"))

    def test_registered_pins(self):
        self.assertEqual(R.LDMTP_IMAGE, "ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:"
                                        "9fbaa06d7f4449e2ae0d02b40e0431f7c212dcf231c9e3a7777a6afc2cc8f312")
        self.assertEqual(R.LDMTP_DRAFTER, {"sha256": "8eb21d5b7efad6e9761b81066e0b094b73623c60e46ba4c0925cdbbf49c93ca9",
                                           "bytes": 321145696})
        self.assertEqual(R.LS_DRAFTER, {"sha256": "f6f80c368d692c9e78ed673c6785827ca21c332e8cdab0263ce4dcdf0b184018",
                                        "bytes": 321145312})
        job = jl(os.path.join(R.LS_DRAFTER_JOB, "job.json"))
        self.assertEqual((job["name"], job["image"]), ("gguf-convert-b459c9c78d78", h59run.LLAMA_FULL_IMAGE))
        self.assertTrue(os.path.exists(os.path.join(R.LS_DRAFTER_JOB, "convert.sh")))
        self.assertEqual(R.OD_GRAPH_SHA_PREFIX, "c57ecf35")

    def test_flags_and_the_affinity_check(self):
        self.assertEqual(R.flags(["--model", "/m", "-c", "262144", "--jinja", "-np", "1", "--x", "-1"]),
                         ["--model", "-c", "--jinja", "-np", "--x"])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "cpus.txt")
            put(p, "cpuset 0-15\nserver threads by affinity:\n" + "".join(f"      1 {c}\n" for c in range(0, 16, 2))
                + "     30 0-15\n")
            a = R.affinity(p)
            self.assertTrue(a["as_registered"])
            self.assertFalse(a["gates"])
            put(p, "cpuset 0-15\nserver threads by affinity:\n     30 0-15\n")
            self.assertFalse(R.affinity(p)["as_registered"])
            self.assertFalse(R.affinity(os.path.join(d, "none.txt"))["as_registered"])
        finally:
            shutil.rmtree(d)


class Render(unittest.TestCase):
    """Od and Ls through the launcher, offline; Ld-mtp through h57d.py."""

    def setUp(self):
        self.box = Box()
        self.out = os.path.join(self.box.d, "out")

    def tearDown(self):
        self.box.close()

    def render(self, arm, **extra):
        with patched():
            R.render(arm, os.path.join(self.out, arm), dict(self.box.env, **extra))
        return h59run.load_docs(os.path.join(self.out, arm, "deploy.yaml"))

    def test_ls_is_the_registered_offline_render(self):
        docs = self.render("Ls")
        dep = h59run.deployment(docs)
        c = dep["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(c["args"], REGISTERED_LS_ARGS)
        self.assertEqual(c["image"], R.LLAMA_CPU_IMAGE)
        self.assertEqual(dep["spec"]["template"]["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
        self.assertEqual(str(c["resources"]["requests"]["cpu"]), "16")
        self.assertEqual({d["kind"] for d in docs}, {"Namespace", "Deployment", "Service"})
        self.assertNotIn("initContainers", dep["spec"]["template"]["spec"])
        self.assertTrue(all(m["readOnly"] for m in c["volumeMounts"] if m["name"] == "models"))
        # The staging changes where Ls reads its files, nothing it serves: the raw render's server
        # arguments, less --model-url and its value, in the same order.
        raw = h59run.deployment(h59run.load_docs(os.path.join(self.out, "Ls", "agent.yaml")))["spec"]["template"]["spec"]["containers"][0]
        i = raw["args"].index("--model-url")
        self.assertEqual(raw["args"][:i] + raw["args"][i + 2:], c["args"])
        self.assertEqual((raw["image"], raw["resources"]), (c["image"], c["resources"]))
        rec = jl(os.path.join(self.out, "Ls", "check.json"))
        self.assertEqual(rec["problems"], [])
        fit = jl(os.path.join(self.out, "Ls", "fit.json"))
        self.assertEqual((fit["np"], fit["c"], fit["fit"]), ("1", "262144", "cpu 1x262144"))
        env = rd(os.path.join(self.out, "Ls", "render-env.txt"))
        self.assertIn("K3D_GPU_AGENT_MAX_NUM_SEQS=1\n", env)
        self.assertIn(f"K3D_GPU_CLUSTER={CLUSTER}\n", env)

    def test_od_is_h57ws_render_with_the_image_named(self):
        docs = self.render("Od")
        dep = h59run.deployment(docs)
        ps = dep["spec"]["template"]["spec"]
        c = ps["containers"][0]
        self.assertEqual(c["image"], R.OVMS_AGENT_IMAGE)
        self.assertNotIn("initContainers", ps)
        self.assertIn("--cache_dir=/ov-cache", c["args"])
        self.assertIn(f"--model_path=/models/{R.OVB}", c["args"])
        self.assertTrue(all(m["readOnly"] for m in c["volumeMounts"] if m["name"] == "models"))
        self.assertIn({"name": "ov-cache", "emptyDir": {}}, ps["volumes"])
        self.assertEqual(dep["spec"]["template"]["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
        self.assertEqual(str(c["resources"]["requests"]["cpu"]), "16")
        # Exactly H57d's chain on the same render: stage.py ovms, then h57d.py od-ro.
        raw = h59run.load_docs(os.path.join(self.out, "Od", "agent.yaml"))
        self.assertEqual(docs, h57d.od_readonly(h59run._apb_stage().stage("ovms", raw)))
        self.assertEqual(jl(os.path.join(self.out, "Od", "check.json"))["problems"], [])

    def test_ld_mtp_is_h57ds_render(self):
        docs = self.render("Ld-mtp")
        self.assertEqual(docs, h57d.render("Ld-mtp"))
        a = h59run.deployment(docs)["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(a[a.index("-C") + 1], "0x5555")
        self.assertEqual(a[a.index("--cpu-strict") + 1], "1")
        self.assertEqual(a[a.index("-md") + 1], f"/models/{R.LDMTP_DRAFT}")
        self.assertFalse(os.path.exists(os.path.join(self.out, "Ld-mtp", "agent.yaml")))

    def test_a_render_that_misses_the_holds_cluster_is_refused(self):
        box = Box(stub_cluster="k3dgpu-d-h63-9")   # the launcher's context answers for another cluster
        try:
            for arm in ("Od", "Ls"):
                with patched(), self.assertRaises(SystemExit) as e:
                    R.render(arm, os.path.join(box.d, arm), box.env)
                self.assertIn("not performance", str(e.exception), arm)
        finally:
            box.close()

    def test_ls_drafter_is_checked_by_sha256(self):
        with open(os.path.join(self.box.g, "models", R.LS_DRAFT_DIR, "model.gguf"), "wb") as f:
            f.write(b"another build\n")
        with patched(), self.assertRaises(SystemExit) as e:
            R.render("Ls", os.path.join(self.out, "Ls"), self.box.env)
        self.assertIn("--spec-draft-model", str(e.exception))

    def test_check_names_each_deviation(self):
        ls = self.render("Ls")
        od = self.render("Od")

        def mutate(docs, f):
            d = json.loads(json.dumps(docs))
            f(h59run.deployment(d), d)
            return d

        def c(dep):
            return dep["spec"]["template"]["spec"]["containers"][0]

        def set_arg(opt, v):
            def f(dep, _):
                a = c(dep)["args"]
                a[a.index(opt) + 1] = v
            return f

        g = self.box.g
        with patched():
            self.assertEqual(R.check("Ls", "desktop", ls, g), [])
            cases = {
                "image": lambda dep, _: c(dep).update(image=h57d.LLAMACPP),
                "-np": set_arg("-np", "2"),
                "--threads": set_arg("--threads", "16"),
                "--reasoning": set_arg("--reasoning", "on"),
                "--spec-type": set_arg("--spec-type", "draft-eagle"),
                "outside the registered render": lambda dep, _: c(dep)["args"].append("--no-mmap"),
                "no --jinja": lambda dep, _: c(dep)["args"].remove("--jinja"),
                "read-write": lambda dep, _: [m.update(readOnly=False) for m in c(dep)["volumeMounts"]],
                "init containers": lambda dep, _: dep["spec"]["template"]["spec"].update(initContainers=[{"name": "wait-gguf"}]),
                "Job": lambda dep, d: d.append({"kind": "Job", "metadata": {"name": "gguf-convert-x"}}),
                "GPU": lambda dep, _: c(dep)["resources"]["limits"].update({"nvidia.com/gpu": 1}),
                "not performance": lambda dep, _: dep["spec"]["template"]["metadata"]["labels"].pop("k3d-gpu.io/cores"),
                "CPU request": lambda dep, _: c(dep)["resources"]["requests"].update(cpu="1"),
            }
            for want, f in cases.items():
                bad = R.check("Ls", "desktop", mutate(ls, f), g)
                self.assertTrue(any(want in b for b in bad), (want, bad))
            self.assertEqual(R.check("Od", "desktop", od, g), [])
            for want, f in {
                "image": lambda dep, _: c(dep).update(image=h59run.OVMS_IMAGE.replace("a928", "b928")),
                "draft": lambda dep, _: c(dep)["args"].append("--draft_source_model=x"),
                "init containers": lambda dep, _: dep["spec"]["template"]["spec"].update(initContainers=[{"name": "pull"}]),
                "not performance": lambda dep, _: dep["spec"]["template"]["metadata"]["labels"].pop("k3d-gpu.io/cores"),
                "agent device": lambda dep, _: dep["spec"]["template"]["metadata"]["annotations"].update(
                    {"k3d-gpu.io/agent-device": "GPU"}),
            }.items():
                bad = R.check("Od", "desktop", mutate(od, f), g)
                self.assertTrue(any(want in b for b in bad), (want, bad))
            ld = h57d.render("Ld-mtp")
            self.assertEqual(R.check("Ld-mtp", "desktop", ld, g), [])
            self.assertEqual(R.check("Ld-mtp", "desktop", mutate(ld, lambda dep, _: c(dep)["args"].remove("--cpu-strict")), g),
                             ["not h57d.py render Ld-mtp"])


class ModelFiles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = os.path.join(self.d, "g")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_prep_layout_is_ready_and_each_file_is_needed(self):
        stage_models(self.g)
        with patched():
            self.assertEqual(R.models_ready(self.g), [])
            m = os.path.join(self.g, "models")
            for rel in (os.path.join(R.H57D_GGUF_DIR, R.GGUF_FILE), os.path.join(R.OVB, h59run.OVB_LM[1]),
                        R.LDMTP_DRAFT, os.path.join(R.LS_DRAFT_DIR, "model.gguf"), R.OD_GRAPH):
                os.rename(os.path.join(m, rel), os.path.join(self.d, "aside"))
                self.assertTrue(R.models_ready(self.g), rel)
                os.rename(os.path.join(self.d, "aside"), os.path.join(m, rel))
            self.assertEqual(R.models_ready(self.g), [])

    def test_drafters_and_graph_by_sha256_and_size(self):
        stage_models(self.g)
        m = os.path.join(self.g, "models")
        with patched():
            self.assertEqual(R.drafter_check(self.g), {"Ld-mtp": None, "Ls": None})
            with open(os.path.join(m, R.LDMTP_DRAFT), "ab") as f:
                f.write(b"x")
            self.assertIn("bytes", R.drafter_check(self.g)["Ld-mtp"])
            with open(os.path.join(m, R.LS_DRAFT_DIR, "model.gguf"), "wb") as f:
                f.write(b"ls-drafteR\n")   # same size, another digest
            self.assertIn("sha256", R.drafter_check(self.g)["Ls"])
            with open(os.path.join(m, R.OD_GRAPH), "wb") as f:
                f.write(b"another graph\n")
            self.assertTrue(any("graph.pbtxt" in p for p in R.models_ready(self.g)))
        # Unpatched, the registered builds are what the check wants (the size is read first).
        self.assertIn(f"not {R.LS_DRAFTER['bytes']}", R.drafter_check(self.g)["Ls"])
        with mock.patch.object(R, "LS_DRAFTER", dict(R.LS_DRAFTER, bytes=len(LS_BYTES))):
            self.assertIn(f"not {R.LS_DRAFTER['sha256']}", R.drafter_check(self.g)["Ls"])

    def test_weights_check_reads_the_committed_lists(self):
        h57d_list, h59_list = R.WEIGHT_LISTS[0][0], R.WEIGHT_LISTS[1][0]
        self.assertTrue(h57d_list.endswith("bench/h57d/results/2026-10-21/weights.sha256"))
        self.assertTrue(h59_list.endswith("bench/h59/results/2026-11-10-wildct/weights.sha256"))
        n, bad = R.weights_check(self.d)
        # H57d's 10 lines and H59w's 22 OVB lines; nothing is downloaded here.
        self.assertEqual((n, len(bad)), (32, 32))
        m = os.path.join(self.d, "m")
        put(os.path.join(m, "a", "x.bin"), "x")
        put(os.path.join(m, "OpenVINO/gemma-4-26b-a4b-it-int4-ov/y.bin"), "y")
        lst1, lst2 = os.path.join(self.d, "l1"), os.path.join(self.d, "l2")
        put(lst1, f"{sha(b'x')}  a/x.bin  hub-lfs-match\n")
        put(lst2, f"{sha(b'y')}  OpenVINO/gemma-4-26b-a4b-it-int4-ov/y.bin\n{sha(b'z')}  gguf/other/model.gguf\n")
        self.assertEqual(R.weights_check(m, ((lst1, ""), (lst2, R.OVB + "/"))), (2, []))
        put(os.path.join(m, "a", "x.bin"), "changed")
        n, bad = R.weights_check(m, ((lst1, ""), (lst2, R.OVB + "/")))
        self.assertEqual((n, len(bad)), (2, 1))


def run_arm(box, repo, arm, hold_dir, mode, port, **extra):
    env = dict(box.env, REPO=repo, MODE=mode, DEADLINE=str(int(time.time()) + 5000), H63_PORT=str(port))
    env.update(extra)
    return subprocess.run(["bash", os.path.join(repo, "bench", "h63", "arm.sh"), arm, os.path.join(hold_dir, arm)],
                          env=env, capture_output=True, text=True, timeout=600)


class EndToEnd(unittest.TestCase):
    """arm.sh, as hold-run.sh runs it, for the registered holds of H63d: three performance holds in
    their orders and the quality hold; then h63.py scores the records."""

    def test_holds_feed_the_scorer(self):
        box = Box()
        repo = tree(box.d)
        srv = serve({"v1": 0.0008, "v3": 0.0016})   # Ls and Ld-mtp decode about twice as fast as Od
        try:
            res = os.path.join(box.g, "results", "H63d")
            for k in (1, 2, 3, 4):
                mode, arms = R.hold(str(k))
                hd = os.path.join(res, f"hold{k}")
                for a in arms:
                    r = run_arm(box, repo, a, hd, mode, srv.server_address[1], CLUSTER=box.cluster)
                    st = jl(os.path.join(hd, a, "status.json"))
                    self.assertEqual(st["status"], "measured", f"hold {k} {a}: {st} {r.stderr[-2000:]}")
                if mode == "perf":
                    synth_load(hd, arms)
            for k in (1, 2, 3):
                for a in R.ARMS:
                    ad = os.path.join(res, f"hold{k}", a)
                    for f in ("load-n1.json", "load-n4.json", "load-ttft.json", "cpus.txt", "fit.json", "deploy.yaml",
                              "check.json", "energy-idle.json", "energy-n1.json", "boot.json"):
                        self.assertTrue(os.path.exists(os.path.join(ad, f)), f"hold {k} {a} {f}")
                    self.assertFalse(os.path.exists(os.path.join(ad, "prefix.json")))
                    self.assertEqual([json.loads(x)["phase"] for x in rd(os.path.join(ad, "phases.jsonl")).splitlines()],
                                     ["idle", "warmup", "n1", "n4", "ttft"])
                    for ph, n, c in (("warmup", 3, 1), ("n1", 8, 1), ("n4", 16, 4), ("ttft", 4, 1)):
                        rec = jl(os.path.join(ad, f"load-{ph}.json"))["phases"][0]
                        self.assertEqual((rec["concurrency"], len(rec["samples"])), (c, n), f"hold {k} {a} {ph}")
                    self.assertEqual(jl(os.path.join(ad, "boot.json"))["limit_s"], 900)
                self.assertTrue(os.path.exists(os.path.join(res, f"hold{k}", "Ld-mtp", "affinity.json")))
                self.assertTrue(os.path.exists(os.path.join(res, f"hold{k}", "Od", "graph.pbtxt")))
            for a in R.ARMS:
                q = jl(os.path.join(res, "hold4", a, "quality.json"))
                self.assertEqual((q["n"], q["errors"]), (50, 0), a)
            self.registered_requests(srv.bodies)
            rep = h63.score(res)
            self.assertEqual(rep["verdict"], "SUPPORTED", json.dumps({k: v for k, v in rep.items() if k != "arms"}))
            self.assertEqual([rep["arms"][a]["n1_requests"] for a in ("Ls", "Od", "Ld-mtp")], [24, 24, 24])
            self.assertGreater(rep["ci"][0], 1.2)
            self.assertTrue(rep["all_holds_faster"])
            self.assertTrue(rep["load_gate"]["matched"])
            cli = subprocess.run([sys.executable, os.path.join(HERE, "h63.py"), "score", res], capture_output=True, text=True)
            self.assertEqual(json.loads(cli.stdout)["verdict"], "SUPPORTED", cli.stderr)
        finally:
            srv.shutdown()
            srv.server_close()
            box.close()

    def registered_requests(self, bodies):
        """The requests as registered (H63 pre-registration, Measurement 2-3): per arm and
        performance hold 3 warmup, 8 N=1 and 16 N=4 requests streamed at max_tokens 256 with
        ignore_eos and temperature 0, 4 TTFT requests on H54's prompt at max_tokens 16; GSM8K items
        0-49 at max_tokens 1024, temperature 0, thinking off. Every load prompt is unique. Ls and
        Ld-mtp both answer on /v1, Od on /v3."""
        ttft = rd(os.path.join(ROOT, "bench", "h54", "ttft-prompt.txt")).rstrip("\n")
        for api, arms in (("v1", 2), ("v3", 1)):
            b = [x for p, x in bodies if p.split("/")[1] == api]
            load = [x for x in b if x.get("stream")]
            gen = [x for x in load if x["max_tokens"] == 256]
            tt = [x for x in load if x["max_tokens"] == 16]
            self.assertEqual((len(gen), len(tt), len(load)), (arms * 3 * 27, arms * 3 * 4, arms * 3 * 31), api)
            self.assertTrue(all(x.get("ignore_eos") is True and x["temperature"] == 0 for x in gen), api)
            self.assertTrue(all(x["temperature"] == 0 and x["messages"][0]["content"].endswith(ttft) for x in tt), api)
            prompts = [x["messages"][0]["content"] for x in load]
            self.assertEqual(len(set(prompts)), len(prompts), api)
            gsm = [x for x in b if not x.get("stream") and x.get("max_tokens") == 1024]
            self.assertEqual(len(gsm), arms * 50, api)
            self.assertTrue(all(x["temperature"] == 0 and x["chat_template_kwargs"] == {"enable_thinking": False}
                                for x in gsm), api)

    def test_render_refusal_and_not_reached(self):
        box = Box(stub_cluster="k3dgpu-d-h63-9")
        repo = tree(box.d)
        try:
            hd = os.path.join(box.g, "results", "H63d", "hold1")
            run_arm(box, repo, "Ls", hd, "perf", 1, CLUSTER=box.cluster)
            st = jl(os.path.join(hd, "Ls", "status.json"))
            self.assertEqual(st["status"], "failed")
            self.assertIn("not performance", st["reason"])
            self.assertFalse(os.path.exists(os.path.join(box.d, "stub", "applied.yaml")))
            run_arm(box, repo, "Od", hd, "perf", 1, CLUSTER=box.cluster, DEADLINE=str(int(time.time()) + 600))
            self.assertEqual(jl(os.path.join(hd, "Od", "status.json"))["status"], "not_reached")
            run_arm(box, repo, "Od", os.path.join(hd, "q"), "quality", 1, CLUSTER=box.cluster,
                    DEADLINE=str(int(time.time()) + 599))
            self.assertEqual(jl(os.path.join(hd, "q", "Od", "status.json"))["status"], "not_reached")
        finally:
            box.close()


class HoldGuards(unittest.TestCase):
    """hold.sh refuses, before the lock, what would spend a hold on the wrong thing."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = tree(self.d)
        self.g = os.path.join(self.d, "g")
        put(os.path.join(self.d, "cpuinfo"), "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n")
        os.makedirs(os.path.join(self.g, "bin"))
        shutil.copy(os.path.join(HERE, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        stage_models(self.g)
        # flock stands in so a hold that passes every guard stops at the lock (exit 4).
        put(os.path.join(self.d, "bin", "flock"), "#!/bin/sh\nexit 1\n", 0o755)
        self.env = {"PATH": os.path.join(self.d, "bin") + ":/usr/bin:/bin", "HOME": self.d, "G": self.g,
                    "H63_IMAGE": NODE_IMAGE, "H63_CPUINFO": os.path.join(self.d, "cpuinfo"),
                    "LOCK": os.path.join(self.d, "lock"), "H63_TEST_PATCH": json.dumps(PATCH)}
        self.res = os.path.join(self.g, "results", "H63d")

    def tearDown(self):
        shutil.rmtree(self.d)

    def hold(self, *args, **env):
        return subprocess.run(["bash", os.path.join(self.repo, "bench", "h63", "hold.sh"), *args],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=60)

    def ran(self, k, statuses):
        hd = os.path.join(self.res, f"hold{k}")
        put(os.path.join(hd, "rc"), "0\n")
        for a, s in statuses.items():
            put(os.path.join(hd, a, "status.json"), json.dumps({"status": s}))

    def test_guards_pass_to_the_lock(self):
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_wildct_guards_pass_to_the_lock(self):
        # H63w (H63w amendment R1): the i7-13700, exactly; its holds' records under results/H63w.
        put(os.path.join(self.d, "cpuinfo"), "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i7-13700\n")
        r = self.hold("wildct", "1")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.g, "results", "H63w", "hold1")))
        r = self.hold("wildct", "smoke")
        self.assertEqual(r.returncode, 4, r.stderr)
        for name in ("13th Gen Intel(R) Core(TM) i7-13700K", "13th Gen Intel(R) Core(TM) i9-13900T"):
            put(os.path.join(self.d, "cpuinfo"), f"processor\t: 0\nmodel name\t: {name}\n")
            r = self.hold("wildct", "1")
            self.assertEqual(r.returncode, 2, (name, r.stderr))
            self.assertIn("not the i7-13700", r.stderr)
        self.assertEqual(self.hold("nowhere", "1").returncode, 2)

    def cotenants(self, gpus):
        """hold.sh's own cotenants() with the profile's gpus value, docker and nvidia-smi stand-ins."""
        put(os.path.join(self.d, "bin", "docker"), "#!/bin/sh\n[ \"$1\" = ps ] && echo c1 || echo /prod-1 running t restarts=0\n", 0o755)
        put(os.path.join(self.d, "bin", "nvidia-smi"), f"#!/bin/sh\ntouch {self.d}/nvidia-smi.ran\necho 0, GPU, 1 MiB\n", 0o755)
        fn = subprocess.run(["sed", "-n", "/^cotenants() {/,/^}/p", os.path.join(HERE, "hold.sh")], capture_output=True,
                            text=True).stdout
        self.assertIn("cotenants()", fn)
        return subprocess.run(["bash", "-c", fn + "\ncotenants"], env=dict(self.env, GPUS=gpus, ROW="H63w"),
                              capture_output=True, text=True, timeout=30)

    def test_wildct_reads_no_gpu(self):
        r = self.cotenants(R.PROFILES["wildct"]["gpus"])
        self.assertIn("/prod-1 running t restarts=0", r.stdout)
        self.assertIn("GPUs: not read (H63w, rule W8)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.d, "nvidia-smi.ran")))
        r = self.cotenants(R.PROFILES["desktop"]["gpus"])
        self.assertIn("0, GPU, 1 MiB", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.d, "nvidia-smi.ran")))

    def test_cpu_class_is_the_exact_model_number(self):
        for name, ok in (("13th Gen Intel(R) Core(TM) i9-13900TE", False), ("13th Gen Intel(R) Core(TM) i9-13900", False),
                         ("13th Gen Intel(R) Core(TM) i7-13700", False), ("13th Gen Intel(R) Core(TM) i9-13900T", True)):
            put(os.path.join(self.d, "cpuinfo"), f"processor\t: 0\nmodel name\t: {name}\n")
            r = self.hold("desktop", "1")
            self.assertEqual(r.returncode, 4 if ok else 2, (name, r.stderr))

    def test_unregistered_holds(self):
        for args in (("desktop", "7"), ("desktop", "5"), ("desktop", "01"), ("desktop", "1", "2"), ("nowhere", "1")):
            self.assertEqual(self.hold(*args).returncode, 2, args)

    def test_image_pinned_by_digest(self):
        self.assertEqual(self.hold("desktop", "1", H63_IMAGE=NODE_IMAGE.split("@")[0]).returncode, 2)

    def test_shim_and_models(self):
        os.remove(os.path.join(self.g, "bin", "k3d"))
        self.assertIn("k3d-shim.sh", self.hold("desktop", "1").stderr)
        shutil.copy(os.path.join(HERE, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        with open(os.path.join(self.g, "models", R.LS_DRAFT_DIR, "model.gguf"), "wb") as f:
            f.write(b"another build\n")
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("prep.sh", r.stderr)
        # Unpatched, the stand-in drafters are not the registered builds either.
        stage_models(os.path.join(self.d, "g2"))

    def test_a_hold_runs_once(self):
        self.ran(2, {"Ls": "measured", "Od": "failed"})
        r = self.hold("desktop", "2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("a failed hold counts", r.stderr)

    def test_spares_repeat_a_hold_that_left_ls_or_od_not_measured(self):
        self.assertEqual(self.hold("desktop", "5", "2").returncode, 2)          # hold 2 has not run
        self.ran(2, {"Ls": "measured", "Od": "measured", "Ld-mtp": "measured"})
        self.assertEqual(self.hold("desktop", "5", "2").returncode, 2)          # nothing failed in it
        self.ran(2, {"Ls": "measured", "Od": "measured", "Ld-mtp": "not_deliverable"})
        r = self.hold("desktop", "5", "2")
        self.assertEqual(r.returncode, 2, r.stderr)                              # only Ld-mtp, never gated
        self.assertIn("Ls and Od were measured", r.stderr)
        self.ran(3, {"Ls": "not_deliverable", "Od": "measured", "Ld-mtp": "measured"})
        self.assertEqual(self.hold("desktop", "5", "3").returncode, 4)          # passes to the lock
        self.ran(1, {"Od": "failed", "Ld-mtp": "measured", "Ls": "measured"})
        self.assertEqual(self.hold("desktop", "5", "1").returncode, 4)
        self.assertFalse(os.path.exists(os.path.join(self.res, "hold5", "repeats")))

    def test_spare_six_follows_spare_five(self):
        self.ran(1, {"Od": "measured", "Ld-mtp": "measured", "Ls": "failed"})
        self.assertEqual(self.hold("desktop", "6", "1").returncode, 2)          # spare 5 has not run
        self.ran(5, {"Od": "measured", "Ld-mtp": "failed", "Ls": "measured"})
        put(os.path.join(self.res, "hold5", "repeats"), "1\n")
        r = self.hold("desktop", "6", "1")
        self.assertEqual(r.returncode, 2, r.stderr)                              # spare 5 measured Ls and Od
        self.assertIn("spare 5", r.stderr)
        self.ran(5, {"Od": "measured", "Ld-mtp": "measured", "Ls": "failed"})
        self.assertEqual(self.hold("desktop", "6", "1").returncode, 4)          # spare 5 failed too

    def test_an_interrupted_hold_counts(self):
        hd = os.path.join(self.res, "hold3")
        put(os.path.join(hd, "started"), "2026-11-12T00:00:00Z\n")
        put(os.path.join(hd, "Od", "status.json"), json.dumps({"status": "measured"}))
        r = self.hold("desktop", "3")
        self.assertEqual(r.returncode, 2)
        self.assertIn("interrupted or not", r.stderr)
        self.assertEqual(self.hold("desktop", "5", "3").returncode, 4)

    def test_node_image_is_the_rows(self):
        put(os.path.join(self.res, "node-image"), "cryptoandcoffee/k3d-gpu:0.1.99@sha256:" + "b" * 64 + "\n")
        r = self.hold("desktop", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("node image", r.stderr)


HF_STANDIN = r'''#!/usr/bin/env python3
"""hf stand-in: `hf download REPO [FILE] --revision REV --local-dir DIR` writes the files the
committed lists name, each holding "<repo>/<file>"."""
import json, os, sys
a = sys.argv[1:]
assert a[0] == "download", a
rev, out = a[a.index("--revision") + 1], a[a.index("--local-dir") + 1]
pos = [x for i, x in enumerate(a[1:], 1) if not x.startswith("--") and a[i - 1] not in ("--revision", "--local-dir")]
repo, files = pos[0], pos[1:] or json.loads(os.environ["HF_FILES"])[pos[0]]
with open(os.path.join(os.environ["STUB_DIR"], "hf.log"), "a") as f:
    f.write(" ".join(a) + "\n")
for n in files:
    p = os.path.join(out, n)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(f"{repo}/{n}")
os.makedirs(os.path.join(out, ".cache", "huggingface"), exist_ok=True)
'''

DOCKER_STANDIN = r'''#!/usr/bin/env python3
"""docker stand-in for prep.sh: maps -v HOST:CTR and writes what each container would."""
import hashlib, json, os, re, sys
a = sys.argv[1:]
with open(os.path.join(os.environ["STUB_DIR"], "docker.log"), "a") as f:
    f.write(json.dumps(a) + "\n")
if not a or a[0] != "run":
    sys.exit(0)
vols = {}
for i, x in enumerate(a):
    if x == "-v":
        host, ctr = a[i + 1].split(":")[:2]
        vols[ctr] = host
def host(p):
    for ctr, h in sorted(vols.items(), key=lambda kv: -len(kv[0])):
        if p == ctr or p.startswith(ctr + "/"):
            return h + p[len(ctr):]
    raise SystemExit(f"unmapped {p}")
def write(p, b):
    os.makedirs(os.path.dirname(host(p)), exist_ok=True)
    with open(host(p), "wb") as f:
        f.write(b)
s = " ".join(a)
if "--quantize" in a:                                       # H57d's convert.sh, step 2
    i = a.index("--quantize")
    write(a[i + 2], os.environ["LDMTP_BYTES"].encode())
elif "convert_hf_to_gguf.py" in s and "--entrypoint" in a and "sh" in a:   # step 1
    write(re.search(r"--outfile (\S+)", s).group(1), b"bf16")
elif "--pull" in a:                                         # Od's graph.pbtxt
    src = [x for x in a if x.startswith("--source_model=")][0].split("=", 1)[1]
    write(f"/models/{src}/graph.pbtxt", os.environ["GRAPH_BYTES"].encode())
elif "/scripts/convert.sh" in a:                            # Ls's drafter: the committed Job
    i = a.index("/scripts/convert.sh")
    out = a[i + 3]
    b = os.environ["LS_BYTES"].encode()
    write(out + "/model.gguf", b)
    write(out + "/manifest.json", json.dumps({"source": a[i + 1] + "@" + a[i + 2], "convert": "--outtype bf16",
                                               "quantize": a[i + 5], "sha256": hashlib.sha256(b).hexdigest(),
                                               "bytes": len(b)}).encode())
else:
    raise SystemExit(f"unexpected docker run {a}")
'''


class Prep(unittest.TestCase):
    """prep.sh with `hf` and `docker` stand-ins and the committed sha256 lists replaced by lists of
    the stand-in files: the layout, the checks, both drafters, the lock around every step."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = tree(self.d)
        self.g = os.path.join(self.d, "g")
        stub = os.path.join(self.d, "stub")
        os.makedirs(stub)
        put(os.path.join(self.d, "bin", "hf"), HF_STANDIN, 0o755)
        put(os.path.join(self.d, "bin", "docker"), DOCKER_STANDIN, 0o755)
        # The files each repository's download leaves, as the committed lists name them.
        ovb = [ln.split()[1][len(R.OVB) + 1:] for ln in rd(R.WEIGHT_LISTS[1][0]).splitlines()
               if ln.split()[1].startswith(R.OVB + "/")]
        files = {R.DRAFT_REPO: ["model.safetensors", "tokenizer.json", "config.json"], R.OVB: ovb}
        lists = os.path.join(self.d, "lists")
        h57 = []
        for ln in rd(R.WEIGHT_LISTS[0][0]).splitlines():
            rel = ln.split()[1]
            top, _, f = rel.partition("/")
            repo = {R.H57D_GGUF_DIR: R.GGUF_REPO, R.H57D_DRAFT_SRC: R.DRAFT_REPO}.get(top, R.OVB)
            name = rel[len(R.OVB) + 1:] if repo == R.OVB else f
            h57.append(f"{sha(f'{repo}/{name}'.encode())}  {rel}  hub-lfs-match\n")
        put(os.path.join(lists, "h57d"), "".join(h57))
        put(os.path.join(lists, "h59"), "".join(f"{sha(f'{R.OVB}/{n}'.encode())}  {R.OVB}/{n}\n" for n in ovb))
        patch = dict(PATCH, WEIGHT_LISTS=[[os.path.join(lists, "h57d"), ""], [os.path.join(lists, "h59"), R.OVB + "/"]])
        self.env = {"PATH": os.path.join(self.d, "bin") + ":/usr/bin:/bin", "HOME": self.d, "G": self.g,
                    "LOCK": os.path.join(self.d, "lock"), "STUB_DIR": stub, "H63_TEST_PATCH": json.dumps(patch),
                    "HF_FILES": json.dumps(files), "LS_BYTES": LS_BYTES.decode(), "LDMTP_BYTES": LDMTP_BYTES.decode(),
                    "GRAPH_BYTES": GRAPH_BYTES.decode()}
        self.stub = stub

    def tearDown(self):
        shutil.rmtree(self.d)

    def prep(self, **env):
        return subprocess.run(["bash", os.path.join(self.repo, "bench", "h63", "prep.sh"), "desktop"],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=300)

    def test_prep_leaves_what_the_holds_check(self):
        r = self.prep()
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        with patched():
            self.assertEqual(R.models_ready(self.g), [])
        m = os.path.join(self.g, "models")
        self.assertEqual(os.readlink(os.path.join(m, R.LS_GGUF_LINK)), f"../{R.H57D_GGUF_DIR}")
        self.assertEqual(rd(os.path.join(m, R.H57D_GGUF_DIR, ".pinned-revision")).split(), [R.GGUF_REPO, R.GGUF_REV])
        self.assertEqual(rd(os.path.join(m, R.H57D_DRAFT_SRC, ".pinned-revision")).split(), [R.DRAFT_REPO, R.DRAFT_REV])
        self.assertEqual(jl(os.path.join(m, R.LS_DRAFT_DIR, "prep-job.json")), jl(os.path.join(R.LS_DRAFTER_JOB, "job.json")))
        self.assertEqual(jl(os.path.join(self.g, "prep", "drafter-check.json")), {"Ld-mtp": "ok", "Ls": "ok"})
        w = rd(os.path.join(self.g, "weights.sha256"))
        for rel in (f"{R.H57D_GGUF_DIR}/{R.GGUF_FILE}", R.LDMTP_DRAFT, f"{R.LS_DRAFT_DIR}/model.gguf", R.OD_GRAPH):
            self.assertIn(f"  {rel}\n", w)
        self.assertNotIn(".cache", w)
        # Only the GGUF file of its repository, at the registered revisions.
        hf = rd(os.path.join(self.stub, "hf.log")).splitlines()
        self.assertEqual(len(hf), 3)
        self.assertTrue(hf[0].startswith(f"download {R.GGUF_REPO} {R.GGUF_FILE} --revision {R.GGUF_REV}"), hf[0])
        self.assertIn(f"--revision {R.DRAFT_REV}", hf[1])
        self.assertIn(f"--revision {R.OVB_REV}", hf[2])
        # The committed Job ran once, with its image and pins; H57d's conversion in full-b11749.
        runs = [json.loads(x) for x in rd(os.path.join(self.stub, "docker.log")).splitlines()]
        job = jl(os.path.join(R.LS_DRAFTER_JOB, "job.json"))
        ls_runs = [x for x in runs if "/scripts/convert.sh" in x]
        self.assertEqual(len(ls_runs), 1)
        self.assertIn(job["image"], ls_runs[0])
        self.assertIn("GGUF_CONVERT_PIP=" + job["env"]["GGUF_CONVERT_PIP"], ls_runs[0])
        self.assertTrue(any("ghcr.io/ggml-org/llama.cpp:full-b11749" in " ".join(x) for x in runs))
        # A second run downloads again (hf leaves what is there) but builds nothing twice.
        r = self.prep()
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        runs2 = [json.loads(x) for x in rd(os.path.join(self.stub, "docker.log")).splitlines()]
        self.assertEqual(len(runs2), len(runs))

    def test_a_weight_mismatch_stops_before_any_build(self):
        r = self.prep(HF_FILES=json.dumps({R.DRAFT_REPO: ["model.safetensors"], R.OVB: ["openvino_language_model.bin"]}))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.stub, "docker.log")))

    def test_a_drafter_that_is_not_the_registered_build_stops_the_sub_row(self):
        r = self.prep(LS_BYTES="ls-drafteR\n")
        self.assertEqual(r.returncode, 3, r.stderr[-2000:])
        self.assertIn("not the registered build", r.stderr)
        self.assertIn("sha256", rd(os.path.join(self.g, "prep", "drafter-check.json")))

    def test_every_step_waits_for_the_lock(self):
        # Hold the lock: prep.sh's first step waits on it (flock -w), so it has not downloaded yet.
        lock = os.path.join(self.d, "lock")
        holder = subprocess.Popen(["flock", lock, "sleep", "30"])
        try:
            time.sleep(0.5)
            p = subprocess.Popen(["bash", os.path.join(self.repo, "bench", "h63", "prep.sh"), "desktop"], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(3)
            self.assertIsNone(p.poll())
            self.assertFalse(os.path.exists(os.path.join(self.stub, "hf.log")))
            p.kill()
            p.wait()
        finally:
            holder.kill()
            holder.wait()

    def test_wildct_prep_in_the_profiles_directory(self):
        # H63w (H63w amendment R1): with G unset, the wildct profile's directory under HOME; the same steps.
        env = {k: v for k, v in self.env.items() if k != "G"}
        r = subprocess.run(["bash", os.path.join(self.repo, "bench", "h63", "prep.sh"), "wildct"], env=env,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        g = os.path.join(self.d, "k3dgpu-bench-wildct", "h63")
        with patched():
            self.assertEqual(R.models_ready(g), [])
        self.assertEqual(jl(os.path.join(g, "prep", "drafter-check.json")), {"Ld-mtp": "ok", "Ls": "ok"})
        self.assertFalse(os.path.exists(self.g))
        self.assertEqual(len(rd(os.path.join(self.stub, "hf.log")).splitlines()), 3)

    def test_an_unknown_host_downloads_nothing(self):
        r = subprocess.run(["bash", os.path.join(self.repo, "bench", "h63", "prep.sh"), "nowhere"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.stub, "hf.log")))


class Shim(unittest.TestCase):
    def test_volume_read_only_on_this_campaigns_clusters(self):
        d = tempfile.mkdtemp()
        try:
            put(os.path.join(d, "k3d"), "#!/bin/sh\necho \"$@\"\n", 0o755)
            env = {"PATH": "/usr/bin:/bin", "K3D_REAL": os.path.join(d, "k3d"), "H63_G": "/g", "H63_PREFIX": "k3dgpu-d-h63-"}
            shim = os.path.join(HERE, "k3d-shim.sh")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-d-h63-1", "--x"], env=env,
                                 capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-d-h63-1 --x --volume=/g/models:/var/apb-models:ro@server:*")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-d-h59-1"], env=env, capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-d-h59-1")
        finally:
            shutil.rmtree(d)


class Reproduce(unittest.TestCase):
    def test_header_runs_with_stand_in_tools(self):
        d = tempfile.mkdtemp()
        try:
            for tool in ("docker", "kubectl", "k3d"):
                put(os.path.join(d, "bin", tool), "#!/bin/sh\necho stand-in\n", 0o755)
            g = os.path.join(d, "g")
            put(os.path.join(g, "weights.sha256"), "abc  x\n")
            put(os.path.join(g, "prep", "drafter-check.json"), '{"Ld-mtp": "ok", "Ls": "ok"}\n')
            put(os.path.join(g, "models", R.H57D_GGUF_DIR, ".pinned-revision"), f"{R.GGUF_REPO} {R.GGUF_REV}\n")
            env = {"PATH": os.path.join(d, "bin") + ":/usr/bin:/bin", "G": g, "K3D_REAL": os.path.join(d, "bin", "k3d"),
                   "H63_IMAGE": NODE_IMAGE, "H63_PROFILE": "desktop", "HOME": d}
            r = subprocess.run(["bash", os.path.join(HERE, "reproduce.sh")], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            for want in (f"  Od: {R.OVMS_AGENT_IMAGE}", f"  Ls: {R.LLAMA_CPU_IMAGE}", f"  Ld-mtp: {R.LDMTP_IMAGE}",
                         f"  node: {NODE_IMAGE}", f"{R.H57D_GGUF_DIR}/ {R.GGUF_REPO} {R.GGUF_REV}",
                         'drafter check: {"Ld-mtp": "ok", "Ls": "ok"}', "  abc  x", "engine flags (Ld-mtp",
                         R.LS_DRAFTER["sha256"]):
                self.assertIn(want, r.stdout)
            self.assertNotIn("Traceback", r.stdout + r.stderr)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for H59r's runner (bench/h59r; EXPERIMENTS.md "H59r pre-registration"): H59's runner on
crescent with llama.cpp b12110. Offline, on bench/h59/test_run.py's stand-ins (fit fixtures, a
kubectl stub that answers only for the hold's own context, a local OpenAI-compatible server):

- run.py's tables are bench/h59/run.py's but for the registered changes (the two llama.cpp
  images, the conversion's pip set, O's backend, the crescent profile's row, directory and
  cluster prefix);
- each shell file is bench/h59's but for the lines listed here;
- on a Xeon 6741P host, both arms render and pass the check, and their staged renders equal H59's
  recorded crescent renders but for the fit's values and L's image;
- arm.sh's records for holds 1-4 are scored by H59's own scorer (bench/h59/h59.py) after
  `run.py gate`;
- hold.sh's guards and the shim.

The launcher the renders read is a copy of this tree's whose llama.cpp images and conversion pip
set are H59r's registered ones (as tests/h59_runner_test.sh does for H59), so a later pin bump on
main does not break these tests; a hold on such a main stops at `run.py check` instead.
No cluster, no network."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
H59 = os.path.join(ROOT, "bench", "h59")
sys.path.insert(0, HERE)
import run  # noqa: E402  (bench/h59r/run.py; it loads bench/h59/run.py as run.h59run)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# H59's runner tables as H59 registered them (a fresh copy, not the one run.py changes), and
# H59's test stand-ins (Box, the kubectl and docker stubs, the stub server). bench/h59 is
# historical: neither file changes.
H59RUN = _load("h59_run_registered", os.path.join(H59, "run.py"))
T = _load("h59_test_run", os.path.join(H59, "test_run.py"))
import h59  # noqa: E402  (bench/h59/h59.py, on the path bench/h59/run.py adds)

RECORDS = os.path.join(H59, "results", "2026-11-01", "H59")   # H59's crescent run
NODE_IMAGE = "cryptoandcoffee/k3d-gpu:0.1.107@sha256:" + "a" * 64
CPUINFO = "".join(f"processor\t: {c}\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Xeon(R) 6741P\n"
                  f"flags\t\t: fpu sse2 avx2 avx512f amx_tile amx_int8 amx_bf16\n\n" for c in range(96))
# The node label H59's crescent node carried (NFD's AMX label; O's render selects it).
AMX_QUERY = "get nodes -l feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true -o name"
KUBECTL = T.KUBECTL.replace('elif "allocatable" in s:',
                            f'elif s == "{AMX_QUERY}":\n    print("node/k3d-x-server-0")\nelif "allocatable" in s:', 1)
assert KUBECTL != T.KUBECTL

put, rd, jl = T.put, T.rd, T.jl
# The drafter H59 served: prep's build on crescent (H59 run, Reproduce header).
H59_DRAFTER = {"sha256": "f6f80c368d692c9e78ed673c6785827ca21c332e8cdab0263ce4dcdf0b184018", "bytes": 321145312}


def put_drafter_manifest(g, m):
    """The drafter's manifest.json as convert.sh writes it, naming sha256 and bytes."""
    put(os.path.join(g, "models", run.DRAFT_DIR, "manifest.json"),
        json.dumps({"source": f"{run.DRAFT_REPO}@{run.DRAFT_REV}", "convert": "--outtype bf16", "quantize": "Q4_0", **m}))

# ---- the launcher with H59r's pins ---------------------------------------------------------------

TREE = None


def setUpModule():
    """A copy of this tree whose launcher carries H59r's registered llama.cpp images and
    conversion pip set; every other entry is a link into the tree."""
    global TREE
    TREE = tempfile.mkdtemp(prefix="h59r-tree-")
    for n in os.listdir(ROOT):
        if n not in (".git", "scripts"):
            os.symlink(os.path.join(ROOT, n), os.path.join(TREE, n))
    os.makedirs(os.path.join(TREE, "scripts"))
    for n in os.listdir(os.path.join(ROOT, "scripts")):
        if n != "k3d-gpu":
            os.symlink(os.path.join(ROOT, "scripts", n), os.path.join(TREE, "scripts", n))
    text = rd(os.path.join(ROOT, "scripts", "k3d-gpu"))
    for pattern, line in (
            (r'^LLAMA_CPU_IMAGE="\$\{K3D_GPU_LLAMA_CPU_IMAGE:-[^}]*\}"$',
             f'LLAMA_CPU_IMAGE="${{K3D_GPU_LLAMA_CPU_IMAGE:-{run.LLAMA_CPU_IMAGE}}}"'),
            (r'^LLAMA_FULL_IMAGE="\$\{K3D_GPU_LLAMA_FULL_IMAGE:-[^}]*\}"$',
             f'LLAMA_FULL_IMAGE="${{K3D_GPU_LLAMA_FULL_IMAGE:-{run.LLAMA_FULL_IMAGE}}}"'),
            (r'^GGUF_CONVERT_PIP="[^"]*"$', f'GGUF_CONVERT_PIP="{run.GGUF_CONVERT_PIP}"')):
        text, n = re.subn(pattern, lambda _m, line=line: line, text, flags=re.M)
        assert n == 1, f"scripts/k3d-gpu has {n} lines matching {pattern}, not 1"
    put(os.path.join(TREE, "scripts", "k3d-gpu"), text, 0o755)


def tearDownModule():
    shutil.rmtree(TREE, ignore_errors=True)


class Box(T.Box):
    """H59's test host as crescent: the Xeon 6741P in /proc/cpuinfo, the node's AMX label, 96
    allocatable CPUs; H59r's runner and the launcher with H59r's pins."""

    def __init__(self, cluster="k3dgpu-g-h59r-1", stub_cluster=None):
        super().__init__("crescent", cluster=cluster, stub_cluster=stub_cluster)
        put(os.path.join(self.d, "bin", "kubectl"), KUBECTL, 0o755)
        put(os.path.join(self.d, "root", "proc", "cpuinfo"), CPUINFO)
        self.env.update(REPO=TREE, STUB_ALLOC="cpu 96")

    def prep_job(self):
        out = os.path.join(self.d, "prep")
        r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "prep-render", out], env=self.env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + rd(os.path.join(out, "agent.err"))
        dst = os.path.join(self.g, "models", run.DRAFT_DIR)
        os.makedirs(dst, exist_ok=True)
        shutil.copy(os.path.join(out, "job.json"), os.path.join(dst, "prep-job.json"))
        return jl(os.path.join(out, "job.json"))

    def render(self, arm, out, code=None, **extra):
        """run.py render ARM OUT; code, when given, runs first in the runner's process (to change a table)."""
        env = dict(self.env, **extra)
        if code is None:
            cmd = [sys.executable, os.path.join(HERE, "run.py"), "render", arm, out]
        else:
            cmd = [sys.executable, "-c", f"import sys; sys.path.insert(0, {HERE!r}); import run; {code}; "
                   "sys.exit(run.main(sys.argv[1:]))", "render", arm, out]
        return subprocess.run(cmd, env=env, capture_output=True, text=True)


def mask_fit(docs):
    """A staged render with the fit's values (O's --max_num_seqs and --cache_size, L's -c and -np,
    the memory requests and limits, the fit annotation) and llama.cpp's image masked."""
    docs = json.loads(json.dumps(docs))
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        tpl = d["spec"]["template"]
        ann = tpl["metadata"].get("annotations") or {}
        if "k3d-gpu.io/agent-fit" in ann:
            ann["k3d-gpu.io/agent-fit"] = "<fit>"
        for c in (tpl["spec"].get("initContainers") or []) + tpl["spec"]["containers"]:
            a = c.get("args") or []
            for i, x in enumerate(a):
                if str(x).startswith(("--max_num_seqs=", "--cache_size=")):
                    a[i] = str(x).split("=")[0] + "=<fit>"
                if x in ("-c", "-np") and i + 1 < len(a):
                    a[i + 1] = "<fit>"
            if c.get("image", "").startswith("ghcr.io/ggml-org/llama.cpp:"):
                c["image"] = "<llama.cpp image>"
            for k in ("requests", "limits"):
                r = (c.get("resources") or {}).get(k) or {}
                if "memory" in r:
                    r["memory"] = "<fit>"
    return docs


# ---- the tables ----------------------------------------------------------------------------------

B12110_CPU = ("ghcr.io/ggml-org/llama.cpp:server-b12110@sha256:"
              "1145e8de3111dc9358c6d8daffb755c4cf8a862d93391c26ba521b9a810bbe3e")
B12110_FULL = ("ghcr.io/ggml-org/llama.cpp:full-b12110@sha256:"
               "93573d882ea21d1b77712a33162934daaabcdf3a076dfff91a9ed3c2aed8cd41")
PIP = "transformers==5.22.2 huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0"
H59_PIP = "transformers==5.21.0 huggingface-hub==2.3.0 tokenizers==0.23.3 safetensors==0.8.0"   # H59 amendment R1


class Tables(unittest.TestCase):
    def test_the_pins_are_the_ones_404_merged(self):
        # release v0.9.0 (tag b12110, commit cd959bae), the images #404 pinned; the conversion's
        # pip set is the launcher's since #416, not the one H59 amendment R1 registered.
        self.assertEqual((run.LLAMA_CPU_IMAGE, run.LLAMA_FULL_IMAGE), (B12110_CPU, B12110_FULL))
        self.assertEqual(run.GGUF_CONVERT_PIP, PIP)
        self.assertNotEqual(run.GGUF_CONVERT_PIP, H59_PIP)
        self.assertEqual(run.OVMS_IMAGE, H59RUN.OVMS_IMAGE)
        self.assertNotEqual(run.LLAMA_CPU_IMAGE, H59RUN.LLAMA_CPU_IMAGE)
        self.assertNotEqual(run.LLAMA_FULL_IMAGE, H59RUN.LLAMA_FULL_IMAGE)

    def test_everything_else_is_h59s(self):
        for name in ("COMMON_ENV", "ORDERS", "MODES", "SPARES", "LOAD_PHASES", "SCRUB", "SCRUB_PREFIXES", "C1",
                     "MODELS", "NS", "SERVED", "GGUF_REPO", "GGUF_REV", "GGUF_FILE", "GGUF_PATH", "DRAFT_REPO",
                     "DRAFT_REV", "DRAFT_DIR", "DRAFT_PATH", "OVB", "OVB_REV", "OVB_LM", "OVMS_IMAGE"):
            self.assertEqual(getattr(run.h59run, name), getattr(H59RUN, name), name)
        self.assertEqual(run.ARMS["L"], H59RUN.ARMS["L"])
        o = dict(run.ARMS["O"], env={})
        self.assertEqual(o, H59RUN.ARMS["O"])
        self.assertEqual(run.ARMS["O"]["env"], {"K3D_GPU_AGENT_BACKEND": "ovms"})

    def test_one_profile_crescent_as_h59s_but_row_directory_and_prefix(self):
        self.assertEqual(set(run.PROFILES), {"crescent"})
        c, h = run.PROFILES["crescent"], H59RUN.PROFILES["crescent"]
        self.assertEqual({k for k in h if c[k] != h[k]}, {"row", "g", "prefix"})
        self.assertEqual((c["row"], c["g"], c["prefix"]), ("H59r", "~/k3dgpu-h59r", "k3dgpu-g-h59r-"))
        # Neither campaign's cluster names start with the other's prefix, so hold.sh's cleanup of
        # its own clusters never takes the other's.
        self.assertFalse((c["prefix"] + "1").startswith(h["prefix"]))
        self.assertFalse((h["prefix"] + "1").startswith(c["prefix"]))

    def test_the_runner_steps_read_h59rs_tables(self):
        # bench/h59/run.py's functions read its module's globals when called; run.py sets them.
        for name in ("LLAMA_CPU_IMAGE", "LLAMA_FULL_IMAGE", "PROFILES", "ARMS"):
            self.assertIs(getattr(run.h59run, name), getattr(run, name), name)
        self.assertEqual(run.h59run.profile("crescent")["row"], "H59r")
        with self.assertRaises(SystemExit):
            run.h59run.profile("desktop")

    def test_profile_cli(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "profile", "crescent"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("row=H59r", r.stdout.splitlines())
        self.assertIn("load_rule=h54-void", r.stdout.splitlines())
        for host in ("desktop", "wildct"):
            r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "profile", host], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, host)

    def test_the_row_names_the_class_the_rule_matches(self):
        mo = _load("model_opt", os.path.join(ROOT, "console", "model_opt.py"))
        rows = mo.ledger_rows(rd(os.path.join(ROOT, "EXPERIMENTS.md")))
        self.assertTrue("H59r" in rows, "no H59r row in the Hypotheses table")
        verdict, text = rows["H59r"]
        self.assertIn(verdict, ("PENDING", "SUPPORTED", "REFUTED", "INCONCLUSIVE"))
        self.assertTrue(mo.family_of("H59r", "H59"))
        self.assertEqual(mo.row_classes(text), ["6741p"])

    def test_the_drafter_must_be_h59s(self):
        # H59's drafter (prep's build on crescent, H59 run); a desktop build at full-b12110 with the
        # current pip set and with R1's gave the same file (H59r pre-registration).
        self.assertEqual(run.DRAFTER, H59_DRAFTER)
        g = tempfile.mkdtemp()
        try:
            for p in run.h59run.models_ready(g):
                put(p, "x")
            self.assertEqual(len(run.models_ready(g)), 1)          # the manifest reads "x"
            put_drafter_manifest(g, H59_DRAFTER)
            self.assertEqual(run.models_ready(g), [])
            for bad in ({"sha256": "1" * 64, "bytes": H59_DRAFTER["bytes"]}, dict(H59_DRAFTER, bytes=1)):
                put_drafter_manifest(g, bad)
                why = run.models_ready(g)
                self.assertEqual(len(why), 1, why)
                self.assertIn("not H59's", why[0])
            os.remove(os.path.join(g, "models", run.DRAFT_DIR, "model.gguf"))
            self.assertEqual(run.models_ready(g), [os.path.join(g, "models", run.DRAFT_DIR, "model.gguf")])
            r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "models", g], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("missing", r.stdout)
        finally:
            shutil.rmtree(g)

    def test_docker_argv_checks_the_conversion_pins(self):
        spec = {"name": "gguf-convert-x", "image": run.LLAMA_FULL_IMAGE, "command": ["bash", "/scripts/convert.sh"],
                "args": [run.DRAFT_REPO, run.DRAFT_REV, "/models/" + run.DRAFT_DIR, "4294967296", "Q4_0", "--outtype", "bf16"],
                "env": {"GGUF_CONVERT_PIP": PIP, "HOME": "/tmp"}, "resources": {"limits": {"memory": "64Gi"}}}
        a = run.docker_argv(spec, "/g/models", "/g/prep/drafter")
        self.assertIn(run.LLAMA_FULL_IMAGE, a)
        self.assertIn("GGUF_CONVERT_PIP=" + PIP, a)
        with self.assertRaises(SystemExit):
            run.docker_argv(dict(spec, env={"GGUF_CONVERT_PIP": H59_PIP}), "/m", "/s")
        with self.assertRaises(SystemExit):
            run.docker_argv(dict(spec, image=H59RUN.LLAMA_FULL_IMAGE), "/m", "/s")
        d = tempfile.mkdtemp()
        try:
            put(os.path.join(d, "job.json"), json.dumps(dict(spec, env={"GGUF_CONVERT_PIP": H59_PIP})))
            r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "docker-argv", os.path.join(d, "job.json"),
                                "/m", "/s"], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("GGUF_CONVERT_PIP", r.stderr)
        finally:
            shutil.rmtree(d)


# ---- the shell runner: H59's files but for these lines -------------------------------------------

SHELL = ("hold.sh", "hold-run.sh", "arm.sh", "prep.sh", "reproduce.sh", "k3d-shim.sh")


def header(name):
    return [f"# H59r's copy of bench/h59/{name} (EXPERIMENTS.md \"H59r pre-registration\"): the same steps,",
            "# with bench/h59r/run.py's tables (crescent only, row H59r, llama.cpp b12110). test_h59r.py",
            "# checks that every other line equals bench/h59's."]


CHANGED = {
    "hold.sh": [
        ("# the one the row's first hold ran, a ${G}/bin/k3d that is not bench/h59/k3d-shim.sh, and models",
         "# the one the row's first hold ran, a ${G}/bin/k3d that is not bench/h59r/k3d-shim.sh, and models"),
        ('cmp -s "${HERE}/k3d-shim.sh" "${G}/bin/k3d" || { echo "hold.sh: ${G}/bin/k3d is not bench/h59/k3d-shim.sh '
         '(install it first)" >&2; exit 2; }',
         'cmp -s "${HERE}/k3d-shim.sh" "${G}/bin/k3d" || { echo "hold.sh: ${G}/bin/k3d is not bench/h59r/k3d-shim.sh '
         '(install it first)" >&2; exit 2; }'),
        ('python3 "${RUN}" models "${G}" >&2 || { echo "hold.sh: run bench/h59/prep.sh ${HOST} first" >&2; exit 2; }',
         'python3 "${RUN}" models "${G}" >&2 || { echo "hold.sh: run bench/h59r/prep.sh ${HOST} first" >&2; exit 2; }'),
    ],
    "hold-run.sh": [],
    "arm.sh": [
        ("D=${REPO:?}/bench/h59", "D=${REPO:?}/bench/h59r"),
        ('        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H59 ${ARM} ${name}" \\',
         '        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H59r ${ARM} ${name}" \\'),
    ],
    "prep.sh": [
        ("#      its conversion Job's container (PR #182's path: full-b11960, its pip pins, convert_hf_to_gguf",
         "#      its conversion Job's container (PR #182's path: full-b12110, its pip pins, convert_hf_to_gguf"),
    ],
    "reproduce.sh": [
        ("G=${G:-${HOME}/k3dgpu-h59}", "G=${G:-${HOME}/k3dgpu-h59r}"),
        ('echo "# H59 Reproduce header (${H59_PROFILE:-?}), $(date -u +%FT%TZ)"',
         'echo "# H59r Reproduce header (${H59_PROFILE:-?}), $(date -u +%FT%TZ)"'),
    ],
    "k3d-shim.sh": [
        ("G=${H59_G:-${HOME}/k3dgpu-h59}", "G=${H59_G:-${HOME}/k3dgpu-h59r}"),
    ],
}


class Copies(unittest.TestCase):
    def test_each_shell_file_is_h59s_but_for_the_listed_lines(self):
        for name in SHELL:
            old = rd(os.path.join(H59, name)).split("\n")
            for a, b in CHANGED[name]:
                self.assertEqual(old.count(a), 1, (name, a))
                old[old.index(a)] = b
            want = old[:1] + header(name) + old[1:]
            self.assertEqual(rd(os.path.join(HERE, name)).split("\n"), want, name)
            self.assertTrue(os.access(os.path.join(HERE, name), os.X_OK) == os.access(os.path.join(H59, name), os.X_OK), name)

    def test_the_scorer_and_report_are_h59s(self):
        # H59r is scored by bench/h59/h59.py and reported by bench/h59/report.py: no copy here.
        for name in ("h59.py", "report.py", "h59r.py"):
            self.assertFalse(os.path.exists(os.path.join(HERE, name)), name)


# ---- renders on crescent -------------------------------------------------------------------------

class Render(unittest.TestCase):
    def setUp(self):
        self.box = Box()
        self.job = self.box.prep_job()
        self.out = os.path.join(self.box.d, "out")

    def tearDown(self):
        self.box.close()

    def docs(self, arm):
        return run.load_docs(os.path.join(self.out, arm, "deploy.yaml"))

    def test_both_arms_render_and_pass_the_check(self):
        for arm in ("O", "L"):
            r = self.box.render(arm, os.path.join(self.out, arm))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(jl(os.path.join(self.out, arm, "check.json"))["problems"], [])
        c = run.deployment(self.docs("L"))["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(c["image"], run.LLAMA_CPU_IMAGE)
        c = run.deployment(self.docs("O"))["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(run.untagged(c["image"]), run.untagged(run.OVMS_IMAGE))
        env = rd(os.path.join(self.out, "O", "render-env.txt")).splitlines()
        self.assertIn("K3D_GPU_AGENT_BACKEND=ovms", env)
        self.assertIn("K3D_GPU_CLUSTER=k3dgpu-g-h59r-1", env)
        self.assertNotIn("K3D_GPU_AGENT_BACKEND=ovms", rd(os.path.join(self.out, "L", "render-env.txt")).splitlines())

    def test_renders_are_h59s_crescent_renders_but_for_the_build_and_the_fit(self):
        for arm in ("O", "L"):
            r = self.box.render(arm, os.path.join(self.out, arm))
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = run.load_docs(os.path.join(RECORDS, "hold1", arm, "deploy.yaml"))
            self.assertEqual(mask_fit(self.docs(arm)), mask_fit(rec), arm)
        # L's server flags, in order, are H59's but for -c and -np.
        a = run.args_of(run.deployment(self.docs("L"))["spec"]["template"]["spec"]["containers"][0])
        h = run.args_of(run.deployment(run.load_docs(os.path.join(RECORDS, "hold1", "L", "deploy.yaml")))
                        ["spec"]["template"]["spec"]["containers"][0])
        self.assertEqual([x for i, x in enumerate(a) if i == 0 or a[i - 1] not in ("-c", "-np")],
                         [x for i, x in enumerate(h) if i == 0 or h[i - 1] not in ("-c", "-np")])

    def test_o_without_the_backend_is_the_ledgers_llama_cpp(self):
        # Since H59 (SUPPORTED for the 6741P), `agent manifest --model google/gemma-4-26B-A4B-it`
        # with no backend serves llama.cpp on this CPU class: H59's O render, unchanged, is not O.
        r = self.box.render("O", os.path.join(self.out, "O"), code="run.ARMS['O']['env'].clear()")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("llama.cpp is this CPU class's engine in EXPERIMENTS.md",
                      rd(os.path.join(self.out, "O", "agent.err")))

    def test_prep_job_is_the_b12110_conversion(self):
        self.assertEqual(self.job["image"], run.LLAMA_FULL_IMAGE)
        self.assertEqual(self.job["env"]["GGUF_CONVERT_PIP"], run.GGUF_CONVERT_PIP)
        self.assertEqual(self.job["args"], [run.DRAFT_REPO, run.DRAFT_REV, "/models/" + run.DRAFT_DIR, "4294967296",
                                            "Q4_0", "--outtype", "bf16"])
        self.assertEqual(self.job["command"], ["bash", "/scripts/convert.sh"])

    def test_h59s_l_render_fails_h59rs_check(self):
        rec = run.load_docs(os.path.join(RECORDS, "hold1", "L", "deploy.yaml"))
        bad = run.check("L", "crescent", rec)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("server-b11960", bad[0])

    def test_render_needs_the_prep_job(self):
        os.remove(os.path.join(self.box.g, "models", run.DRAFT_DIR, "prep-job.json"))
        r = self.box.render("L", os.path.join(self.out, "L"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prep-job.json", r.stderr)


# ---- arm.sh's records scored by H59's scorer -----------------------------------------------------

def run_arm(box, arm, hold_dir, mode, holdnum, port, **extra):
    env = dict(box.env, MODE=mode, DEADLINE=str(int(time.time()) + 5000), HOLDNUM=str(holdnum), H59_PORT=str(port))
    env.update(extra)
    return subprocess.run(["bash", os.path.join(HERE, "arm.sh"), arm, os.path.join(hold_dir, arm)], env=env,
                          capture_output=True, text=True, timeout=900)


class EndToEnd(unittest.TestCase):
    """arm.sh for holds 1-4 on crescent, as hold-run.sh runs it; then `run.py gate` and H59's
    scorer (bench/h59/h59.py) on the records."""

    def test_holds_feed_h59s_scorer(self):
        box = Box()
        srv = T.serve({"v1": 0.0003, "v3": 0.0006})   # L decodes about twice as fast as O
        try:
            box.prep_job()
            serving = os.path.join(box.d, "serving.jsonl")
            now = time.time()
            with open(serving, "w") as f:   # quiet host: non-campaign CPU 1 CPU-s/s, no aiperf process
                for i in range(-30, 3000):
                    f.write(json.dumps({"ts": now + i, "seconds": 1, "serving_cpu": 1.0, "aiperf_procs": 0}) + "\n")
            res = os.path.join(box.g, "results", "H59r")
            for k in (1, 2, 3, 4):
                mode, arms = run.h59run.hold(str(k))
                hd = os.path.join(res, f"hold{k}")
                for a in arms:
                    r = run_arm(box, a, hd, mode, k, srv.server_address[1], SERVING=serving)
                    st = jl(os.path.join(hd, a, "status.json"))
                    self.assertEqual(st["status"], "measured", f"hold {k} {a}: {st} {r.stderr[-2000:]}")
            for k in (1, 2, 3):
                for a in ("L", "O"):
                    ad = os.path.join(res, f"hold{k}", a)
                    self.assertEqual([json.loads(x)["phase"] for x in rd(os.path.join(ad, "phases.jsonl")).splitlines()],
                                     ["idle", "n1", "n4", "n16", "ttft", "prefix"])
                    for ph, n, c in (("warmup", 3, 1), ("n1", 8, 1), ("n4", 16, 4), ("n16", 32, 16), ("ttft", 4, 1)):
                        rec = jl(os.path.join(ad, f"load-{ph}.json"))
                        self.assertEqual((rec["phases"][0]["concurrency"], len(rec["phases"][0]["samples"])), (c, n),
                                         f"hold {k} {a} {ph}")
                        self.assertEqual(rec["config"]["label"], f"H59r {a} {ph}")
                    self.assertEqual(jl(os.path.join(ad, "boot.json"))["limit_s"], 900)
            for api in ("v1", "v3"):
                b = [x for p, x in srv.bodies if p.split("/")[1] == api]
                load = [x for x in b if x.get("stream") and "tools" not in x]
                self.assertEqual(len([x for x in load if x["max_tokens"] == 256]), 3 * 59, api)
                self.assertEqual(len([x for x in load if x["max_tokens"] == 16]), 3 * 4, api)
                self.assertTrue(all(x["temperature"] == 0 for x in load), api)
                self.assertEqual(len([x for x in b if "tools" in x]), 3 * 6, api)
                gsm = [x for x in b if not x.get("stream") and x.get("max_tokens") == 1024]
                self.assertEqual(len(gsm), 50, api)
            g = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "gate", res], capture_output=True, text=True)
            self.assertEqual(g.returncode, 0, g.stderr)
            self.assertTrue(json.loads(g.stdout)["matched"], g.stdout)
            gate = os.path.join(box.d, "gate.json")
            put(gate, g.stdout)
            s = subprocess.run([sys.executable, os.path.join(H59, "h59.py"), "score", res, "--load", gate],
                               capture_output=True, text=True)
            self.assertEqual(s.returncode, 0, s.stderr)
            rep = json.loads(s.stdout)
            self.assertEqual(rep["verdict"], "SUPPORTED", json.dumps({k: v for k, v in rep.items() if k != "arms"}))
            self.assertEqual([rep["arms"][a]["n4_requests"] for a in ("L", "O")], [48, 48])
            self.assertTrue(rep["all_holds_faster"])
            self.assertEqual(h59.score(res, json.loads(g.stdout))["verdict"], "SUPPORTED")
        finally:
            srv.shutdown()
            srv.server_close()
            box.close()


# ---- hold.sh's guards and the shim ---------------------------------------------------------------

class HoldGuards(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = os.path.join(self.d, "g")
        put(os.path.join(self.d, "cpuinfo"), CPUINFO)
        os.makedirs(os.path.join(self.g, "bin"))
        shutil.copy(os.path.join(HERE, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        for p in run.h59run.models_ready(self.g):
            put(p, "x")
        put_drafter_manifest(self.g, H59_DRAFTER)
        self.assertEqual(run.models_ready(self.g), [])
        # flock stands in so a hold that passes every guard stops at the lock (exit 4).
        put(os.path.join(self.d, "bin", "flock"), "#!/bin/sh\nexit 1\n", 0o755)
        self.env = {"PATH": os.path.join(self.d, "bin") + ":/usr/bin:/bin", "HOME": self.d, "G": self.g,
                    "H59_IMAGE": NODE_IMAGE, "H59_CPUINFO": os.path.join(self.d, "cpuinfo")}

    def tearDown(self):
        shutil.rmtree(self.d)

    def hold(self, *args, **env):
        return subprocess.run(["bash", os.path.join(HERE, "hold.sh"), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=60)

    def test_guards_pass_to_the_lock_and_records_are_h59rs(self):
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.g, "results", "H59r", "hold1")))
        self.assertIn("== H59r-hold1 waiting for the lock", rd(os.path.join(self.g, "results", "campaign.log")))
        self.assertTrue(os.path.exists(os.path.join(self.d, "k3dgpu-bench.lock")))   # crescent's shared lock

    def test_other_hosts_and_classes(self):
        for host in ("desktop", "wildct", "nowhere"):
            self.assertEqual(self.hold(host, "1").returncode, 2, host)
        put(os.path.join(self.d, "cpuinfo"), "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n")
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not the 6741P that H59r registers", r.stderr)

    def test_h59s_shim_is_not_h59rs(self):
        shutil.copy(os.path.join(H59, "k3d-shim.sh"), os.path.join(self.g, "bin", "k3d"))
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bench/h59r/k3d-shim.sh", r.stderr)

    def test_image_pinned_by_digest_and_the_rows(self):
        self.assertEqual(self.hold("crescent", "1", H59_IMAGE=NODE_IMAGE.split("@")[0]).returncode, 2)
        put(os.path.join(self.g, "results", "H59r", "node-image"), "cryptoandcoffee/k3d-gpu:0.1.83@sha256:" + "b" * 64 + "\n")
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("node image", r.stderr)

    def test_a_hold_runs_once_and_spares_repeat_a_failed_hold(self):
        hd = os.path.join(self.g, "results", "H59r", "hold2")
        put(os.path.join(hd, "rc"), "0\n")
        for a, s in (("L", "measured"), ("O", "not_deliverable")):
            put(os.path.join(hd, a, "status.json"), json.dumps({"status": s}))
        self.assertEqual(self.hold("crescent", "2").returncode, 2)
        self.assertEqual(self.hold("crescent", "5", "2").returncode, 4)
        self.assertEqual(self.hold("crescent", "7").returncode, 2)

    def test_models_from_prep(self):
        os.remove(os.path.join(self.g, "models", run.DRAFT_DIR, "prep-job.json"))
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bench/h59r/prep.sh", r.stderr)

    def test_a_drafter_other_than_h59s_stops_the_hold(self):
        put_drafter_manifest(self.g, {"sha256": "0" * 64, "bytes": H59_DRAFTER["bytes"]})
        r = self.hold("crescent", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not H59's", r.stderr)


class Shim(unittest.TestCase):
    def test_volume_only_on_h59rs_clusters(self):
        d = tempfile.mkdtemp()
        try:
            put(os.path.join(d, "k3d"), "#!/bin/sh\necho \"$@\"\n", 0o755)
            env = {"PATH": "/usr/bin:/bin", "K3D_REAL": os.path.join(d, "k3d"), "H59_G": "/g",
                   "H59_PREFIX": run.PROFILES["crescent"]["prefix"]}
            shim = os.path.join(HERE, "k3d-shim.sh")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-g-h59r-1", "--x"], env=env,
                                 capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-g-h59r-1 --x --volume=/g/models:/var/apb-models@server:*")
            out = subprocess.run(["bash", shim, "cluster", "create", "k3dgpu-g-h59-1"], env=env, capture_output=True,
                                 text=True).stdout
            self.assertEqual(out.strip(), "cluster create k3dgpu-g-h59-1")
        finally:
            shutil.rmtree(d)


class Reproduce(unittest.TestCase):
    def test_header_names_h59rs_images(self):
        d = tempfile.mkdtemp()
        try:
            r = subprocess.run(["bash", os.path.join(HERE, "reproduce.sh")], capture_output=True, text=True, timeout=60,
                               env={"PATH": "/usr/bin:/bin", "HOME": d, "G": os.path.join(d, "g"), "H59_PROFILE": "crescent",
                                    "H59_IMAGE": NODE_IMAGE, "K3D_REAL": "/bin/true"})
            out = r.stdout.splitlines()
            self.assertTrue(out[0].startswith("# H59r Reproduce header (crescent)"), out[:1])
            self.assertIn(f"  llama.cpp: {run.LLAMA_CPU_IMAGE}", out)
            self.assertIn(f"  llama.cpp (drafter conversion): {run.LLAMA_FULL_IMAGE}", out)
            self.assertIn(f"  ovms: {run.OVMS_IMAGE}", out)
            self.assertIn(f"  node: {NODE_IMAGE}", out)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

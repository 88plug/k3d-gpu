#!/usr/bin/env python3
"""run.py: the H63d and H63w runner's tables and steps (EXPERIMENTS.md "H63 pre-registration" and
"H63 amendment R1"). The verdict is h63.py's; this file serves the shell runner (hold.sh,
hold-run.sh, arm.sh, prep.sh).

  run.py profile HOST                 the host profile as KEY=value lines (desktop, wildct)
  run.py arms                         the arm table, orders and render environments as JSON
  run.py field ARM KEY                one field of an arm (for arm.sh)
  run.py hold HOLD [REPEATS]          "MODE ARM ARM ARM" of a registered hold: 1-3 perf and 4
                                      quality in h63.py's orders, the spares 5 and 6 repeating
                                      hold REPEATS in its order, smoke as hold 1
  run.py spare-ok RESULTS K [SPARE]   exit 0 when hold K ran and the record standing for it (hold
                                      K, or spare 5 once it repeated K) left Ls or Od not measured;
                                      spare 6 only after spare 5
  run.py cpu HOST CPUINFO             exit 0 when the CPU class (the model number the optimizer
                                      matches a ledger row by) is the profile's, exactly
  run.py models G                     exit 0 when prep.sh left every file the arms read in
                                      G/models and both drafters are the registered builds
  run.py weights-check MODELS         exit 0 when every file the committed sha256 lists name is
                                      under MODELS with that sha256 (H57d's list, H59w's OVB lines)
  run.py drafter-check G              the two drafters against their registered sha256 and size
  run.py render ARM OUTDIR            render the arm in the hold's cluster (REPO, CLUSTER,
                                      KUBECONFIG, H63_PROFILE, G from the environment), stage and
                                      check it: agent.yaml, agent.err, render-env.txt (Od, Ls),
                                      deploy.yaml, check.json, fit.json
  run.py check ARM HOST DEPLOY [G]    exit 1, naming each problem, when a staged render is not the
                                      registered arm on that host
  run.py affinity CPUS_TXT            Ld-mtp's thread affinity against H57dp amendment R1's check
                                      (reported, never gated)
  run.py docker-argv JOB MODELS SCRIPTS
                                      `docker run` of the committed drafter Job (H59's prep Job)

Arms (H63 pre-registration, "Arms"): Od is H57w's (k3d-gpu `agent manifest` of OpenVINO's int4
IR, stage.py ovms, H57d's read-only transform) with the launcher's OVMS pin named; Ld-mtp is
`h57d.py render Ld-mtp`, unchanged; Ls is `agent manifest --model` of Google's QAT GGUF at one
sequence with the QAT MTP drafter, staged as H59 amendment R1 stages L.

Python 3 standard library, except render/check (PyYAML)."""

import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h63  # noqa: E402  (ORDERS, GATED; it reads bench/h59/run.py by path and puts h59, h57d and h54 on the path)

h59run = h63.h59run   # bench/h59/run.py: render steps, the staging of L, the CPU class, docker_argv
h59 = h63.h59         # bench/h59/h59.py: hold_dirs, status
h57d = h63.h57d       # bench/h57d/h57d.py: Ld-mtp's render, Od's read-only transform, the sampler

MODELS = "/var/apb-models"   # the node path the k3d shim binds the campaign's G/models to
NS = "k3d-gpu-agent"
SERVED = "gemma"

# Model files at the revisions H57d, H57w and H59w used (H63 pre-registration, "Models"). The
# campaign keeps H57d's layout; Ls reads the same GGUF through the launcher's gguf/<repo>@<rev>
# path, a link into it.
GGUF_REPO, GGUF_REV, GGUF_FILE = h59run.GGUF_REPO, h59run.GGUF_REV, h59run.GGUF_FILE
DRAFT_REPO, DRAFT_REV = h59run.DRAFT_REPO, h59run.DRAFT_REV
OVB, OVB_REV = h59run.OVB, h59run.OVB_REV
H57D_GGUF_DIR = "google_gemma-4-26B-A4B-it-qat-q4_0-gguf"
H57D_DRAFT_SRC = "google_gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"
LS_GGUF_LINK = f"gguf/{h59run.slug(GGUF_REPO, GGUF_REV)}"
LS_GGUF_PATH = h59run.GGUF_PATH            # /models/gguf/<repo>@<rev>/gemma-4-26B_q4_0-it.gguf
LS_DRAFT_DIR = h59run.DRAFT_DIR            # gguf/<drafter repo>@<rev>
LS_DRAFT_PATH = h59run.DRAFT_PATH          # /models/gguf/<drafter repo>@<rev>/model.gguf
LDMTP_DRAFT = h57d.DRAFTER_GGUF            # k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf/...q4_0.gguf
OD_GRAPH = f"{OVB}/graph.pbtxt"

# The two drafters as registered: each built once per host and equal to these, or the sub-row
# stops before its smoke. Ld-mtp's: bench/h57d/convert.sh (full-b11749, transformers 5.20.2), H57d's
# build. Ls's: the Job H59's prep ran, as committed (full-b11960, transformers 5.21.0).
LDMTP_DRAFTER = {"sha256": "8eb21d5b7efad6e9761b81066e0b094b73623c60e46ba4c0925cdbbf49c93ca9", "bytes": 321145696}
LS_DRAFTER = {"sha256": "f6f80c368d692c9e78ed673c6785827ca21c332e8cdab0263ce4dcdf0b184018", "bytes": 321145312}
LS_DRAFTER_JOB = os.path.join(REPO, "bench", "h59", "results", "2026-11-10-wildct", "prep", "drafter")
# Od's graph.pbtxt as bench/h57d/convert.sh writes it (H57d's and H57dp's, sha256 c57ecf35...).
OD_GRAPH_SHA_PREFIX = "c57ecf35"
# The committed sha256 lists every downloaded file is checked against, each with the path prefix
# of the lines read: H57d's list whole (its 10 LFS files, in the layout this campaign keeps), and
# H59w's lines for OVB (its other two lines name the GGUF, which H57d's list covers, and the
# drafter, which drafter_check covers, in H59's layout).
WEIGHT_LISTS = ((os.path.join(REPO, "bench", "h57d", "results", "2026-10-21", "weights.sha256"), ""),
                (os.path.join(REPO, "bench", "h59", "results", "2026-11-10-wildct", "weights.sha256"), OVB + "/"))

# Engine images. Od: the launcher's OVMS pin today, named in the render so a later bump of that
# pin does not change Od. Ls: H59w's L image. Ld-mtp: h57d.py's (server-b11749).
OVMS_AGENT_IMAGE = ("openvino/model_server:2026.4.1-gpu@sha256:"
                    "a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58")
LLAMA_CPU_IMAGE = h59run.LLAMA_CPU_IMAGE   # server-b11960@sha256:8052c13e...
LDMTP_IMAGE = h57d.LLAMACPP                # server-b11749@sha256:9fbaa06d...

# Arms: where each one answers. deploy: k3d-gpu (an `agent manifest` render) or h57d (h57d.py).
ARMS = {
    "Od": {"deploy": "k3d-gpu", "name": "ovms", "svc": "ovms", "ns": NS, "port": 8000, "api": "/v3"},
    "Ld-mtp": {"deploy": "h57d", "name": "ld-mtp", "svc": "ld-mtp", "ns": h57d.NS, "port": 8080, "api": "/v1"},
    "Ls": {"deploy": "k3d-gpu", "name": "llamacpp", "svc": "llamacpp", "ns": NS, "port": 8000, "api": "/v1"},
}
# The registered render environments (H63 pre-registration, "Arms"), with K3D_GPU_CLUSTER set to
# the hold's cluster at render time. Od as H57w ran it (bench/h57d/arm.sh) plus the OVMS image;
# Ls with no other agent variable.
RENDER_ENV = {
    "Od": {"K3D_GPU_AGENT_DEVICE": "CPU", "K3D_GPU_AGENT_MODEL": OVB, "K3D_GPU_AGENT_MODEL_NAME": SERVED,
           "K3D_GPU_AGENT_DRAFT": "off", "K3D_GPU_AUTOQUANT": "off", "K3D_GPU_AGENT_IMAGE": OVMS_AGENT_IMAGE},
    "Ls": {"K3D_GPU_AGENT_DEVICE": "CPU", "K3D_GPU_AGENT_DRAFT": "mtp", "K3D_GPU_AGENT_MAX_NUM_SEQS": "1",
           "K3D_GPU_AGENT_REVISION": GGUF_REV, "K3D_GPU_AGENT_MODEL_NAME": SERVED, "K3D_GPU_TUNE": "off",
           "K3D_GPU_LLAMA_CPU_IMAGE": LLAMA_CPU_IMAGE},
}
RENDER_ARGS = {"Od": [], "Ls": ["--model", GGUF_REPO]}
# Ls's server flags as the registered offline render gives them: the settings column of the arm
# table plus the four every llama.cpp render carries (--model, --alias, --host, --port). A flag
# outside this set fails the check.
LS_FLAGS = ("--model", "--alias", "--host", "--port", "--jinja", "--reasoning", "-c", "-np", "-ngl",
            "--cache-ram", "-ctxcp", "--threads", "--threads-batch", "--load-mode", "--spec-draft-model",
            "--spec-type")
LS_SWITCHES = ("--jinja",)   # flags that take no value
LS_VALUES = {"--model": LS_GGUF_PATH, "--alias": SERVED, "--reasoning": "off", "-np": "1", "-ngl": "0",
             "--cache-ram": "0", "-ctxcp": "4", "--load-mode": "mmap+mlock", "--spec-draft-model": LS_DRAFT_PATH,
             "--spec-type": "draft-mtp"}

# Holds (h63.py's orders): performance holds 1-3, quality hold 4; spares 5 and 6 repeat a hold
# that left Ls or Od not measured, in its order (H59 amendment R1's rule, as H63 registers it).
ORDERS = h63.ORDERS
MODES = {1: "perf", 2: "perf", 3: "perf", 4: "quality"}
SPARES = (5, 6)

# Host profiles (H63 pre-registration, "Hosts"). cpu: the model number /proc/cpuinfo must name;
# pcores: the P-core balloon's CPUs a render must request; gpus: whether hold.sh reads the GPU
# co-tenants (desktop: read, before and after each hold; wildct: none, rule W8 reads no GPU).
PROFILES = {
    "desktop": {"row": "H63d", "cpu": "i9-13900T", "g": "~/k3dgpu-h63", "prefix": "k3dgpu-d-h63-",
                "load1_max": 24, "mem_gib": 20, "disk_gb": 15, "cap_s": 4500, "arms_end_s": 4470,
                "vendors": "hybrid", "k3s_args": h59run.C1, "pcores": 16, "gpus": "read"},
    "wildct": {"row": "H63w", "cpu": "i7-13700", "g": "~/k3dgpu-bench-wildct/h63", "prefix": "k3dgpu-w-h63-",
               "load1_max": 12, "mem_gib": 20, "disk_gb": 15, "cap_s": 4500, "arms_end_s": 4470,
               "vendors": "auto,-nvidia,-intel", "k3s_args": h59run.W3, "pcores": 16, "gpus": "none"},
}
# Phases (H63 pre-registration, "Measurement"): (requests, concurrency, max_tokens).
LOAD_PHASES = {"warmup": (3, 1, 256), "n1": (8, 1, 256), "n4": (16, 4, 256), "ttft": (4, 1, 16)}


def jload(path):
    with open(path) as f:
        return json.load(f)


def profile(host):
    if host not in PROFILES:
        raise SystemExit(f"run.py: unknown host profile {host!r} (desktop, wildct)")
    return PROFILES[host]


def hold(n, repeats=None):
    """(mode, arms) of a registered hold; SystemExit for anything else."""
    if n == "smoke" and repeats is None:
        return "perf", ORDERS[1]
    if n in [str(k) for k in ORDERS] and repeats is None:
        return MODES[int(n)], ORDERS[int(n)]
    if n in [str(k) for k in SPARES] and repeats in [str(k) for k in ORDERS]:
        return MODES[int(repeats)], ORDERS[int(repeats)]
    raise SystemExit(f"run.py hold: no registered hold {n!r}{' repeating ' + repr(repeats) if repeats else ''} "
                     "(1-3 perf, 4 quality, a spare 5 or 6 names the hold it repeats)")


def ran(d):
    """A hold ran once it started (hold.sh writes `started` when the floors pass) or recorded its rc."""
    return any(os.path.exists(os.path.join(d, f)) for f in ("started", "rc"))


def spare_ok(results, k, spare=None):
    """H59 amendment R1's spare rule over H63's orders, as the pre-registration amends it: a spare
    repeats hold k only when hold k ran and the record standing for it (hold k, or spare 5 once it
    repeated hold k) left Ls or Od not measured. A spare stands in for its hold for every arm, so a
    hold where only Ld-mtp (never gated) was not measured gets no spare. Spare 6 runs only after
    spare 5."""
    hold(str(k))
    d = os.path.join(results, f"hold{k}")
    if not ran(d):
        return False, f"hold {k} has not run"
    if str(spare) == str(SPARES[1]) and not ran(os.path.join(results, f"hold{SPARES[0]}")):
        return False, f"spare {SPARES[1]} runs only after spare {SPARES[0]}"
    cur = h59.hold_dirs(results)[int(k)]
    where = f"hold {k}" if cur == d else f"spare {os.path.basename(cur)[4:]}, which repeated hold {k},"
    off = [a for a in h63.GATED if h59.status(os.path.join(cur, a)) != "measured"]
    if not off:
        return False, f"Ls and Od were measured in {where}"
    return True, f"{where} left {' and '.join(off)} not measured"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 24), b""):
            h.update(c)
    return h.hexdigest()


def file_problem(path, want):
    """None when path is a file with want's sha256 and size, else what differs."""
    if not os.path.isfile(path):
        return f"{path} missing"
    size = os.path.getsize(path)
    if size != want["bytes"]:
        return f"{path} is {size} bytes, not {want['bytes']}"
    got = sha256(path)
    if got != want["sha256"]:
        return f"{path} sha256 {got}, not {want['sha256']}"
    return None


def drafter_check(g):
    """{arm: problem or None} for the two drafters under G/models."""
    m = os.path.join(g, "models")
    return {"Ld-mtp": file_problem(os.path.join(m, LDMTP_DRAFT), LDMTP_DRAFTER),
            "Ls": file_problem(os.path.join(m, LS_DRAFT_DIR, "model.gguf"), LS_DRAFTER)}


def graph_problem(g):
    p = os.path.join(g, "models", OD_GRAPH)
    if not os.path.isfile(p):
        return f"{p} missing (bench/h57d/convert.sh writes it)"
    got = sha256(p)
    return None if got.startswith(OD_GRAPH_SHA_PREFIX) else f"{p} sha256 {got}, not {OD_GRAPH_SHA_PREFIX}..."


def models_ready(g):
    """Problems with the files the staged arms read under G/models (prep.sh's layout); [] when none."""
    m = os.path.join(g, "models")
    want = [os.path.join(m, H57D_GGUF_DIR, GGUF_FILE), os.path.join(m, LS_GGUF_PATH[len("/models/"):]),
            *(os.path.join(m, OVB, f) for f in h59run.OVB_LM)]
    bad = [f"{p} missing" for p in want if not os.path.exists(p)]
    bad += [p for p in drafter_check(g).values() if p]
    gp = graph_problem(g)
    return bad + ([gp] if gp else [])


def weights_check(models, lists=None):
    """Every file the committed lists name (`<sha256>  <path>[  note]` lines whose path starts with
    the list's prefix) is under models with that sha256. Returns (files checked, problems)."""
    checked, bad = 0, []
    for lst, prefix in WEIGHT_LISTS if lists is None else lists:
        with open(lst) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2 or not parts[1].startswith(prefix):
                    continue
                want, rel = parts[0], parts[1]
                p = os.path.join(models, rel)
                checked += 1
                if not os.path.isfile(p):
                    bad.append(f"{rel} missing")
                elif sha256(p) != want:
                    bad.append(f"{rel} sha256 differs from the committed list {lst}")
    return checked, bad


def cpu_class(cpuinfo):
    return h59run.cpu_class(cpuinfo)


# ---- renders -------------------------------------------------------------------------------------

def render_env(arm, environ, cluster):
    """The environment `agent manifest` runs in for Od and Ls: every K3D_GPU_AGENT_*,
    K3D_GPU_LLAMA_* and fit or ledger variable of the calling shell dropped (H59 amendment R1's
    scrub), then the registered variables and the hold's cluster."""
    env = {k: v for k, v in environ.items() if not k.startswith(h59run.SCRUB_PREFIXES) and k not in h59run.SCRUB
           and k != "K3D_GPU_TUNE"}
    env.update(RENDER_ENV[arm])
    # The hold's cluster: `agent manifest` reads the node's hybrid label and allocatable CPU through
    # context k3d-${K3D_GPU_CLUSTER}; without it the arm renders with no P-core label and 1 CPU
    # (H57d amendment R2).
    env["K3D_GPU_CLUSTER"] = cluster
    return env


def stage(arm, docs):
    """Od: stage.py ovms, then H57d's read-only transform (h57d.py od-ro: no --pull init
    container, the models mount read-only, OVMS's compiled-model cache in an emptyDir). Ls: H59
    amendment R1's staging of L (the PVC becomes the read-only hostPath; the conversion ConfigMap
    and Job, the wait-gguf init container and --model-url are dropped). Everything else as rendered."""
    if arm == "Od":
        return h57d.od_readonly(h59run._apb_stage().stage("ovms", json.loads(json.dumps(docs))))
    if arm == "Ls":
        return h59run.stage("L", docs)
    raise SystemExit(f"run.py stage: {arm} is not an agent manifest render")


def args_of(c):
    return [str(x) for x in c.get("args") or []]


def flags(args):
    """The option names in a llama-server argument list: every argument that starts with '-' and
    is not a number (no value the launcher renders starts with '-')."""
    return [x for x in args if x.startswith("-") and not x.lstrip("-").replace(".", "").isdigit()]


def check(arm, host, docs, g=None):
    """Problems that make a staged render not the registered arm on this host; [] when none.
    g: the campaign dir, for Ls's drafter sha256 (the file --spec-draft-model names, read where
    the k3d shim binds it from)."""
    p = profile(host)
    bad = []
    try:
        dep = h59run.deployment(docs)
    except SystemExit as e:
        return [str(e)]
    want = ARMS[arm]
    if dep["metadata"].get("name") != want["name"] or dep["metadata"].get("namespace") != want["ns"]:
        bad.append(f"Deployment {dep['metadata'].get('namespace')}/{dep['metadata'].get('name')}, "
                   f"not {want['ns']}/{want['name']}")
    tpl = dep["spec"]["template"]
    ps = tpl["spec"]
    c = ps["containers"][0]
    a = args_of(c)
    if arm == "Ld-mtp":
        if docs != h57d.render("Ld-mtp"):
            bad.append("not h57d.py render Ld-mtp")
        return bad
    ann = tpl["metadata"].get("annotations") or {}
    if ann.get("k3d-gpu.io/agent-device") != "CPU":
        bad.append(f"agent device {ann.get('k3d-gpu.io/agent-device')!r}, not CPU")
    if arm == "Od":
        if h59run.untagged(c.get("image")) != h59run.untagged(OVMS_AGENT_IMAGE):
            bad.append(f"image {c.get('image')}, not {OVMS_AGENT_IMAGE}")
        if f"--model_path=/models/{OVB}" not in a:
            bad.append(f"server does not serve /models/{OVB}")
        if f"--model_name={SERVED}" not in a:
            bad.append(f"server does not name the model {SERVED}")
        if any(x.startswith("--draft") for x in a):
            bad.append("Od renders a draft (registered: no draft)")
        if ps.get("initContainers"):
            bad.append("init containers left in (Od serves the graph.pbtxt convert.sh wrote, read-only)")
    elif arm == "Ls":
        if h59run.untagged(c.get("image")) != h59run.untagged(LLAMA_CPU_IMAGE):
            bad.append(f"image {c.get('image')}, not {LLAMA_CPU_IMAGE}")
        for opt, v in LS_VALUES.items():
            if h59run.value(a, opt) != v:
                bad.append(f"{opt} {h59run.value(a, opt)!r}, not {v!r}")
        for opt in LS_SWITCHES:
            if opt not in a:
                bad.append(f"no {opt}")
        for opt in ("-c", "--threads", "--threads-batch"):
            if h59run.value(a, opt) is None or not str(h59run.value(a, opt)).isdigit():
                bad.append(f"no numeric {opt}")
        extra = sorted(set(flags(a)) - set(LS_FLAGS))
        if extra:
            bad.append(f"server flags outside the registered render: {' '.join(extra)}")
        if p["pcores"]:
            if h59run.value(a, "--threads") != str(p["pcores"] // 2):
                bad.append(f"--threads {h59run.value(a, '--threads')}, not the {p['pcores'] // 2} physical P-cores")
            if h59run.value(a, "--threads-batch") != str(p["pcores"]):
                bad.append(f"--threads-batch {h59run.value(a, '--threads-batch')}, not {p['pcores']}")
        if any(x.get("name") == "models" and not x.get("readOnly") for x in c.get("volumeMounts") or []):
            bad.append("models mounted read-write")
        if ps.get("initContainers"):
            bad.append("init containers left in")
        if any(d.get("kind") in ("Job", "ConfigMap") for d in docs):
            bad.append("the drafter conversion Job or its ConfigMap is left in (prep.sh built the drafter)")
        if g is not None and h59run.value(a, "--spec-draft-model") == LS_DRAFT_PATH:
            prob = file_problem(os.path.join(g, "models", LS_DRAFT_PATH[len("/models/"):]), LS_DRAFTER)
            if prob:
                bad.append(f"--spec-draft-model: {prob}")
    if not any(v.get("name") == "models" and (v.get("hostPath") or {}).get("path") == MODELS for v in ps.get("volumes") or []):
        bad.append(f"models volume is not hostPath {MODELS}")
    if any(d.get("kind") == "PersistentVolumeClaim" for d in docs):
        bad.append("a PersistentVolumeClaim is left in")
    req = (c.get("resources") or {}).get("requests") or {}
    lim = (c.get("resources") or {}).get("limits") or {}
    if any("gpu" in k or "nvidia" in k for k in list(req) + list(lim)):
        bad.append("a GPU resource is requested")
    label = (tpl["metadata"].get("labels") or {}).get("k3d-gpu.io/cores")
    if p["pcores"]:
        if label != "performance":
            bad.append(f"pod label k3d-gpu.io/cores {label!r}, not performance (the P-core balloon)")
        if str(req.get("cpu")) != str(p["pcores"]):
            bad.append(f"CPU request {req.get('cpu')!r}, not the {p['pcores']} P-core CPUs")
    return bad


def fit_record(arm, docs):
    """The fit and serving settings the arm ran with, from the staged render."""
    dep = h59run.deployment(docs)
    tpl = dep["spec"]["template"]
    c = tpl["spec"]["containers"][0]
    a = args_of(c)
    rec = {"arm": arm, "fit": (tpl["metadata"].get("annotations") or {}).get("k3d-gpu.io/agent-fit"),
           "requests": (c.get("resources") or {}).get("requests"), "limits": (c.get("resources") or {}).get("limits"),
           "image": c.get("image"), "args": a}
    if arm == "Ls":
        for k in ("-c", "-np", "-ngl", "--threads", "--threads-batch", "--load-mode"):
            rec[k.lstrip("-")] = h59run.value(a, k)
    return rec


def render(arm, out, environ=None):
    """Render, stage, check and record one arm in the hold's cluster; SystemExit with the reason
    when the render fails or is not the registered arm."""
    environ = os.environ if environ is None else environ
    host, cluster = environ.get("H63_PROFILE", ""), environ.get("CLUSTER", "")
    repo = environ.get("REPO", REPO)
    g = os.path.expanduser(environ.get("G", ""))
    if not cluster:
        raise SystemExit("run.py render: CLUSTER (the hold's cluster) is not set")
    profile(host)
    os.makedirs(out, exist_ok=True)
    rec = {"arm": arm, "host": host, "problems": []}
    if ARMS[arm]["deploy"] == "h57d":
        docs = h57d.render(arm)
    else:
        env = render_env(arm, environ, cluster)
        with open(os.path.join(out, "render-env.txt"), "w") as f:
            f.writelines(f"{k}={env[k]}\n" for k in sorted(env) if k.startswith("K3D_GPU_") or k == "KUBECONFIG")
        cmd = [os.path.join(repo, "scripts", "k3d-gpu"), "agent", "manifest", *RENDER_ARGS[arm]]
        with open(os.path.join(out, "agent.yaml"), "w") as fo, open(os.path.join(out, "agent.err"), "w") as fe:
            rc = subprocess.run(cmd, env=env, stdout=fo, stderr=fe).returncode
        if rc != 0:
            raise SystemExit(f"render failed: agent manifest exited {rc}")
        docs = stage(arm, h59run.load_docs(os.path.join(out, "agent.yaml")))
    h59run.dump_docs(docs, os.path.join(out, "deploy.yaml"))
    rec["problems"] = check(arm, host, docs, g or None)
    with open(os.path.join(out, "fit.json"), "w") as f:
        json.dump(fit_record(arm, docs), f, indent=1)
    with open(os.path.join(out, "check.json"), "w") as f:
        json.dump(rec, f, indent=1)
    if rec["problems"]:
        raise SystemExit("render not the registered arm: " + "; ".join(rec["problems"]))


def affinity(cpus_txt):
    """H57dp amendment R1's check for Ld-mtp (reported, never gated in H63): its single-CPU
    threads sit on exactly CPUs 0, 2, ..., 14."""
    got = h57d.single_cpu_threads(cpus_txt)
    return {"single_cpu_threads": got, "want_cpus": h57d.MASK_CPUS,
            "as_registered": got is not None and set(got) == set(h57d.MASK_CPUS), "gates": False}


def header():
    """The Reproduce header's image, model and drafter lines (reproduce.sh)."""
    job = os.path.relpath(os.path.join(LS_DRAFTER_JOB, "job.json"), REPO)
    return "\n".join([
        f"  Od: {OVMS_AGENT_IMAGE}", f"  Ld-mtp: {LDMTP_IMAGE}", f"  Ls: {LLAMA_CPU_IMAGE}",
        "  Ld-mtp drafter conversion: bench/h57d/convert.sh (full-b11749, transformers 5.20.2)",
        f"  Ls drafter conversion: the committed Job {job}",
        f"models: {GGUF_REPO}@{GGUF_REV} ({GGUF_FILE}), {OVB}@{OVB_REV}, {DRAFT_REPO}@{DRAFT_REV}",
        f"drafters as registered: Ld-mtp {LDMTP_DRAFTER['sha256']} {LDMTP_DRAFTER['bytes']} bytes; "
        f"Ls {LS_DRAFTER['sha256']} {LS_DRAFTER['bytes']} bytes",
        "engine flags (Ld-mtp, h57d.py render): " + " ".join(h57d.llamacpp_args(True))])


# The names prep.sh reads (run.py vars): the registered repositories, revisions and paths.
PREP_VARS = ("GGUF_REPO", "GGUF_REV", "GGUF_FILE", "DRAFT_REPO", "DRAFT_REV", "OVB", "OVB_REV", "H57D_GGUF_DIR",
             "H57D_DRAFT_SRC", "LS_GGUF_LINK", "LS_DRAFT_DIR", "LDMTP_DRAFT", "LS_DRAFTER_JOB")


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    if cmd == "profile":
        for k, v in profile(a[0]).items():
            print(f"{k}={v}")
    elif cmd == "arms":
        print(json.dumps({"arms": ARMS, "orders": ORDERS, "modes": MODES, "render_env": RENDER_ENV,
                          "phases": LOAD_PHASES, "ls_flags": LS_FLAGS,
                          "ld_mtp_args": h57d.llamacpp_args(True)}, indent=1))
    elif cmd == "field":
        print(ARMS[a[0]][a[1]])
    elif cmd == "vars":
        for k in PREP_VARS:
            print(f"{k}={globals()[k]}")
    elif cmd == "header":
        print(header())
    elif cmd == "hold":
        mode, arms = hold(a[0], a[1] if len(a) > 1 else None)
        print(mode, *arms)
    elif cmd == "spare-ok":
        ok, why = spare_ok(a[0], a[1], a[2] if len(a) > 2 else None)
        print(why)
        return 0 if ok else 1
    elif cmd == "cpu":
        got = cpu_class(a[1])
        print(got)
        return 0 if got.lower() == profile(a[0])["cpu"].lower() else 1
    elif cmd == "models":
        bad = models_ready(os.path.expanduser(a[0]))
        for b in bad:
            print(b)
        return 1 if bad else 0
    elif cmd == "weights-check":
        n, bad = weights_check(a[0])
        for b in bad:
            print(b)
        print(f"{n} files checked, {len(bad)} problems")
        return 1 if bad or not n else 0
    elif cmd == "drafter-check":
        res = drafter_check(os.path.expanduser(a[0]))
        print(json.dumps({k: v or "ok" for k, v in res.items()}, indent=1))
        return 1 if any(res.values()) else 0
    elif cmd == "render":
        render(a[0], a[1])
    elif cmd == "check":
        bad = check(a[0], a[1], h59run.load_docs(a[2]), a[3] if len(a) > 3 else None)
        for b in bad:
            print(b)
        return 1 if bad else 0
    elif cmd == "affinity":
        print(json.dumps(affinity(a[0]), indent=1))
    elif cmd == "docker-argv":
        print("\n".join(h59run.docker_argv(jload(a[0]), a[1], a[2])))
    else:
        raise SystemExit(f"run.py: unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""run.py: the H59, H59d and H59w runner's tables and steps (EXPERIMENTS.md "H59
pre-registration" and "H59 amendment R1"). The verdict is h59.py's; this file serves the shell
runner (hold.sh, hold-run.sh, arm.sh, prep.sh).

  run.py profile HOST                 the host profile as KEY=value lines (crescent, desktop, wildct)
  run.py hold HOLD [REPEATS]          "MODE ARM ARM" of a registered hold: 1-3 perf, 4 quality,
                                      the spares 5 and 6 repeating hold REPEATS in its order,
                                      smoke as hold 1
  run.py spare-ok RESULTS K           exit 0 when hold K ran and one of its arms was not measured
  run.py models G                     exit 0 when prep.sh left every file the arms read in G/models
  run.py render ARM OUTDIR            `k3d-gpu agent manifest --model` for the arm in the hold's
                                      cluster (REPO, CLUSTER, KUBECONFIG, H59_PROFILE, G from the
                                      environment), then stage and check: agent.yaml, agent.err,
                                      render-env.txt, deploy.yaml, check.json, fit.json
  run.py stage ARM IN OUT             the staged render: models from the node's /var/apb-models
  run.py check ARM HOST DEPLOY        exit 1, naming each problem, when a staged render is not the
                                      registered arm on that host
  run.py job RENDER OUTDIR            an L render's drafter conversion Job: convert.sh + job.json
                                      (prep.sh runs it once per host; holds then skip the Job)
  run.py gate RESULTS                 crescent's load verdict for `h59.py score --load GATE.json`:
                                      H54's void rule over the N=4 phases of the performance holds
  run.py prefix URL MODEL SEED OUT    the prefix-reuse phase: 6 agents in sequence on one shared
                                      prefix (H25's shape, bench/shared-prefix/probe.py)

Python 3 standard library, except render/stage/check/job (PyYAML)."""

import importlib.util
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h59  # noqa: E402  (hold_dirs, status: the scorer's own reading of the records)

MODELS = "/var/apb-models"   # the node path the k3d shim binds the campaign's G/models to
NS = "k3d-gpu-agent"
SERVED = "gemma"

# The registered model files (H59 pre-registration, "Model").
GGUF_REPO, GGUF_REV = "google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "d1c082be9cf3c8a514acf63b8761f4b41935842e"
GGUF_FILE = "gemma-4-26B_q4_0-it.gguf"
DRAFT_REPO, DRAFT_REV = "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant", "9537141506fe8875b3ed45b264af13580cb29166"
OVB, OVB_REV = "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "4e5885c3f03844f5887982def09708bdc9f53f7a"

# The engine images `agent manifest` pins at the runner's commit (PR #182's llama.cpp backend:
# server-b11960 on the CPU, full-b11960 for the drafter conversion; OVMS 2026.4.1).
OVMS_IMAGE = "openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58"
LLAMA_CPU_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-b11960@sha256:8052c13edd987de8858f8150031e025ac3d66823f09cf3ec8220899c4abce220"
LLAMA_FULL_IMAGE = "ghcr.io/ggml-org/llama.cpp:full-b11960@sha256:7afc81bfe7a93de287324044fe86ccc70b1b7ac8832ed8e6ea658e11fb448ddf"


def slug(repo, rev):
    """`k3d-gpu`'s model-cache directory name for repo@rev (gguf_slug)."""
    return f"{repo.replace('/', '__')}@{rev}"


GGUF_PATH = f"/models/gguf/{slug(GGUF_REPO, GGUF_REV)}/{GGUF_FILE}"
DRAFT_DIR = f"gguf/{slug(DRAFT_REPO, DRAFT_REV)}"
DRAFT_PATH = f"/models/{DRAFT_DIR}/model.gguf"

# Arms O and L as registered: `agent manifest --model` with K3D_GPU_AGENT_DEVICE=CPU, and for L
# K3D_GPU_AGENT_DRAFT=mtp. The rest of the render environment is fixed for both (COMMON_ENV).
ARMS = {
    "O": {"model": "google/gemma-4-26B-A4B-it", "revision": OVB_REV, "env": {},
          "engine": "ovms", "deploy": "ovms", "svc": "ovms", "port": 8000, "api": "/v3"},
    "L": {"model": GGUF_REPO, "revision": GGUF_REV, "env": {"K3D_GPU_AGENT_DRAFT": "mtp"},
          "engine": "llama.cpp", "deploy": "llamacpp", "svc": "llamacpp", "port": 8000, "api": "/v1"},
}
# K3D_GPU_AGENT_REVISION pins the fit's metadata read to the commit the weights were fetched at;
# the served name is the runner's; K3D_GPU_TUNE=off keeps a host's own tuned profile out.
COMMON_ENV = {"K3D_GPU_AGENT_DEVICE": "CPU", "K3D_GPU_AGENT_MODEL_NAME": SERVED, "K3D_GPU_TUNE": "off"}
# Variables a render never inherits from the shell that starts a hold: each one would change
# what `agent manifest` renders (backend, model, images, variant, ledger, fit mode, context).
SCRUB_PREFIXES = ("K3D_GPU_AGENT_", "K3D_GPU_LLAMA_")
SCRUB = ("K3D_GPU_MODEL_OPT", "K3D_GPU_MODEL_OPT_LEDGER", "K3D_GPU_AUTOQUANT", "K3D_GPU_FIT", "K3D_GPU_FIT_RUNG",
         "K3D_GPU_FIT_FILE", "K3D_GPU_CONTEXT", "K3D_GPU_KUBECONFIG", "K3D_GPU_BALLOONS")

# Holds: performance holds 1-3 in the rotated orders, the quality hold 4 in (L, O); the spares 5
# and 6 repeat a failed hold's arms in its order (H59 pre-registration, "Holds and caps").
ORDERS = {1: ["O", "L"], 2: ["L", "O"], 3: ["O", "L"], 4: ["L", "O"]}
MODES = {1: "perf", 2: "perf", 3: "perf", 4: "quality"}
SPARES = (5, 6)

# The C1 kubelet thresholds (crescent amendment C1) and wildct's absolute form (rule W3).
C1 = "--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%"
W3 = "--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<2Gi,imagefs.available<2Gi,nodefs.inodesFree<1%"

# Host profiles. Each host keeps its own shared lock, start rule, floors, hold length and load
# rule (H59 pre-registration: H54's on crescent, H57d's on the desktop, the wildct
# pre-registration's on wildct). cpu: the model number /proc/cpuinfo must name (the row's CPU
# class); pcores: the P-core balloon's CPUs the render must request (0: no hybrid balloon).
PROFILES = {
    "crescent": {"row": "H59", "cpu": "6741P", "g": "~/k3dgpu-h59", "prefix": "k3dgpu-g-h59-",
                 "lock": "~/k3dgpu-bench.lock", "load1_max": 12, "aiperf": 1, "disk": "pct97", "mem_gib": 0,
                 "cap_s": 2400, "arms_end_s": 2370, "vendors": "amx", "k3s_args": C1,
                 "sampler": "serving", "load_rule": "h54-void", "phases": "n1,n4,n16,ttft,prefix",
                 "pcores": 0},
    "desktop": {"row": "H59d", "cpu": "i9-13900T", "g": "~/k3dgpu-h59", "prefix": "k3dgpu-d-h59-",
                "lock": "", "load1_max": 24, "aiperf": 0, "disk": "free15", "mem_gib": 20,
                "cap_s": 4500, "arms_end_s": 4470, "vendors": "hybrid", "k3s_args": C1,
                "sampler": "pcore", "load_rule": "h57d", "phases": "n1,n4,ttft,prefix", "pcores": 16},
    "wildct": {"row": "H59w", "cpu": "i7-13700", "g": "~/k3dgpu-bench-wildct/h59", "prefix": "k3dgpu-w-h59-",
               "lock": "", "load1_max": 12, "aiperf": 0, "disk": "free15", "mem_gib": 20,
               "cap_s": 4500, "arms_end_s": 4470, "vendors": "auto,-nvidia,-intel", "k3s_args": W3,
               "sampler": "pcore", "load_rule": "h57d", "phases": "n1,n4,ttft,prefix", "pcores": 16},
}
# Phases: (name, requests, concurrency, max_tokens) for bench/llm_load.py, H54's requests.
LOAD_PHASES = {"warmup": (3, 1, 256), "n1": (8, 1, 256), "n4": (16, 4, 256), "n16": (32, 16, 256),
               "ttft": (4, 1, 16)}


def jload(path):
    with open(path) as f:
        return json.load(f)


def profile(host):
    if host not in PROFILES:
        raise SystemExit(f"run.py: unknown host profile {host!r} (crescent, desktop, wildct)")
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


def spare_ok(results, k):
    """A spare repeats hold k only when hold k ran (its rc is recorded) and one of its arms was
    not measured."""
    d = os.path.join(results, f"hold{k}")
    if not os.path.exists(os.path.join(d, "rc")):
        return False, f"hold {k} has not run"
    mode, arms = hold(str(k))
    if all(h59.status(os.path.join(d, a)) == "measured" for a in arms):
        return False, f"every arm of hold {k} was measured"
    return True, f"hold {k} has an arm not measured"


def models_ready(g):
    """The files the staged arms read, under G/models (prep.sh's layout); [] when all are there."""
    m = os.path.join(g, "models")
    want = [os.path.join(m, GGUF_PATH[len("/models/"):]), os.path.join(m, DRAFT_DIR, "model.gguf"),
            os.path.join(m, DRAFT_DIR, "manifest.json"), os.path.join(m, DRAFT_DIR, "prep-job.json"),
            os.path.join(m, OVB, "openvino_model.xml"), os.path.join(m, OVB, "openvino_model.bin")]
    return [p for p in want if not os.path.exists(p)]


# ---- YAML render steps ---------------------------------------------------------------------------

def load_docs(path):
    import yaml
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def dump_docs(docs, path):
    import yaml
    with open(path, "w") as f:
        yaml.safe_dump_all(docs, f, sort_keys=False)


def deployment(docs):
    ds = [d for d in docs if d.get("kind") == "Deployment"]
    if len(ds) != 1:
        raise SystemExit(f"run.py: the render holds {len(ds)} Deployments, not 1")
    return ds[0]


def _apb_stage():
    spec = importlib.util.spec_from_file_location("apb_stage", os.path.join(REPO, "bench", "apb", "stage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stage(arm, docs):
    """O: `stage.py ovms` (the PVC becomes the node's /var/apb-models; OVMS's --pull init
    container writes graph.pbtxt there from this hold's own arguments, and finds the weights
    prep.sh fetched at the pinned commit, so it downloads nothing). L: the PVC becomes the same
    hostPath, mounted read-only; the drafter's conversion ConfigMap and Job and the init container
    that waits for them are dropped (prep.sh ran that Job once, so each is a no-op), and so is
    `--model-url` with its value (llama-server's download source; the GGUF is already at --model).
    Image, every other argument, requests, limits and labels are as rendered."""
    docs = json.loads(json.dumps(docs))
    if arm == "O":
        return _apb_stage().stage("ovms", docs)
    if arm != "L":
        raise SystemExit(f"run.py stage: unknown arm {arm}")
    out = []
    for d in docs:
        kind, name = d.get("kind"), (d.get("metadata") or {}).get("name", "")
        if kind == "PersistentVolumeClaim" or (kind == "ConfigMap" and name == "gguf-convert") \
                or (kind == "Job" and name.startswith("gguf-convert-")):
            continue
        if kind == "Deployment":
            ps = d["spec"]["template"]["spec"]
            init = [c["name"] for c in ps.get("initContainers") or []]
            if init not in ([], ["wait-gguf"]):
                raise SystemExit(f"run.py stage L: unexpected init containers {init}")
            ps.pop("initContainers", None)
            for v in ps["volumes"]:
                if v["name"] == "models":
                    v.pop("persistentVolumeClaim", None)
                    v["hostPath"] = {"path": MODELS, "type": "Directory"}
            c = ps["containers"][0]
            for m in c["volumeMounts"]:
                if m["name"] == "models":
                    m["readOnly"] = True
            a = c["args"]
            if "--model-url" in a:
                i = a.index("--model-url")
                del a[i:i + 2]
        out.append(d)
    return out


def args_of(c):
    return [str(x) for x in c.get("args") or []]


def value(args, opt):
    """The argument after opt (llama-server takes an option's value from the next argument)."""
    return args[args.index(opt) + 1] if opt in args and args.index(opt) + 1 < len(args) else None


def check(arm, host, docs):
    """Problems that make a staged render not the registered arm on this host; [] when none."""
    p = profile(host)
    bad = []
    try:
        dep = deployment(docs)
    except SystemExit as e:
        return [str(e)]
    want = ARMS[arm]
    if dep["metadata"].get("name") != want["deploy"] or dep["metadata"].get("namespace") != NS:
        bad.append(f"Deployment {dep['metadata'].get('namespace')}/{dep['metadata'].get('name')}, "
                   f"not {NS}/{want['deploy']}")
    tpl = dep["spec"]["template"]
    ps = tpl["spec"]
    c = ps["containers"][0]
    a = args_of(c)
    ann = (tpl["metadata"].get("annotations") or {})
    if ann.get("k3d-gpu.io/agent-device") != "CPU":
        bad.append(f"agent device {ann.get('k3d-gpu.io/agent-device')!r}, not CPU")
    if arm == "O":
        if c.get("image") != OVMS_IMAGE:
            bad.append(f"image {c.get('image')}, not {OVMS_IMAGE}")
        pull = [x for x in ps.get("initContainers") or [] if x["name"] == "pull"]
        pa = args_of(pull[0]) if pull else []
        for need in ("--pull", f"--source_model={OVB}", "--target_device=CPU", "--task=text_generation",
                     f"--model_name={SERVED}"):
            if need not in pa:
                bad.append(f"pull container lacks {need}")
        if any(x.startswith("--draft_") for x in pa + a):
            bad.append("O renders a draft (registered: no draft)")
        if f"--model_path=/models/{OVB}" not in a:
            bad.append(f"server does not serve /models/{OVB}")
    else:
        if c.get("image") != LLAMA_CPU_IMAGE:
            bad.append(f"image {c.get('image')}, not {LLAMA_CPU_IMAGE}")
        for opt, v in (("--model", GGUF_PATH), ("--spec-draft-model", DRAFT_PATH), ("--spec-type", "draft-mtp"),
                       ("--alias", SERVED), ("--reasoning", "off")):
            if value(a, opt) != v:
                bad.append(f"{opt} {value(a, opt)!r}, not {v!r}")
        for opt in ("--jinja",):
            if opt not in a:
                bad.append(f"no {opt}")
        for opt in ("-c", "-np", "--threads", "--threads-batch"):
            if value(a, opt) is None or not str(value(a, opt)).isdigit():
                bad.append(f"no numeric {opt}")
        if "--model-url" in a:
            bad.append("--model-url left in (a hold never downloads)")
        if any(x.get("name") == "models" and not x.get("readOnly") for x in c.get("volumeMounts") or []):
            bad.append("models mounted read-write")
        if ps.get("initContainers"):
            bad.append("init containers left in")
        if p["pcores"] and value(a, "--threads") != str(p["pcores"] // 2):
            bad.append(f"--threads {value(a, '--threads')}, not the {p['pcores'] // 2} physical P-cores")
    if not any(v.get("name") == "models" and (v.get("hostPath") or {}).get("path") == MODELS for v in ps.get("volumes") or []):
        bad.append(f"models volume is not hostPath {MODELS}")
    if any(d.get("kind") == "PersistentVolumeClaim" for d in docs):
        bad.append("a PersistentVolumeClaim is left in")
    req = ((c.get("resources") or {}).get("requests") or {})
    lim = ((c.get("resources") or {}).get("limits") or {})
    if any("gpu" in k or "nvidia" in k for k in list(req) + list(lim)):
        bad.append("a GPU resource is requested")
    label = (tpl["metadata"].get("labels") or {}).get("k3d-gpu.io/cores")
    if p["pcores"]:
        # The P-core balloon (H57dp's deviation: a render that misses the hold's cluster gets no
        # hybrid node, so no label and 1 CPU).
        if label != "performance":
            bad.append(f"pod label k3d-gpu.io/cores {label!r}, not performance (the P-core balloon)")
        if str(req.get("cpu")) != str(p["pcores"]):
            bad.append(f"CPU request {req.get('cpu')!r}, not the {p['pcores']} P-core CPUs")
        if arm == "L" and value(a, "--threads-batch") != str(p["pcores"]):
            bad.append(f"--threads-batch {value(a, '--threads-batch')}, not {p['pcores']}")
    elif label is not None:
        bad.append(f"pod label k3d-gpu.io/cores {label!r} on a host with no hybrid balloon")
    return bad


def fit_record(arm, docs):
    """The fit the arm ran with, from the staged render."""
    dep = deployment(docs)
    tpl = dep["spec"]["template"]
    ps = tpl["spec"]
    c = ps["containers"][0]
    a = args_of(c)
    rec = {"arm": arm, "fit": (tpl["metadata"].get("annotations") or {}).get("k3d-gpu.io/agent-fit"),
           "requests": (c.get("resources") or {}).get("requests"), "limits": (c.get("resources") or {}).get("limits"),
           "image": c.get("image"), "args": a}
    if arm == "O":
        pull = [x for x in ps.get("initContainers") or [] if x["name"] == "pull"]
        pa = args_of(pull[0]) if pull else []
        rec["pull_args"] = pa
        for k in ("max_num_seqs", "cache_size", "enable_prefix_caching"):
            v = [x.split("=", 1)[1] for x in pa if x.startswith(f"--{k}=")]
            rec[k] = v[0] if v else None
    else:
        for k in ("-c", "-np", "-ngl", "--threads", "--threads-batch", "--load-mode"):
            rec[k.lstrip("-")] = value(a, k)
    return rec


def job_spec(docs):
    """The drafter conversion Job of an L render (image, command, args, env) and its script."""
    jobs = [d for d in docs if d.get("kind") == "Job" and d["metadata"]["name"].startswith("gguf-convert-")]
    cms = [d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "gguf-convert"]
    if len(jobs) != 1 or len(cms) != 1:
        raise SystemExit(f"run.py job: the render holds {len(jobs)} conversion Jobs and {len(cms)} ConfigMaps, not 1 and 1")
    c = jobs[0]["spec"]["template"]["spec"]["containers"][0]
    spec = {"name": jobs[0]["metadata"]["name"], "image": c["image"], "command": c["command"],
            "args": [str(x) for x in c["args"]], "env": {e["name"]: str(e["value"]) for e in c.get("env") or []},
            "resources": c.get("resources")}
    return spec, cms[0]["data"]["convert.sh"]


def write_job(render_yaml, outdir):
    spec, script = job_spec(load_docs(render_yaml))
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "job.json"), "w") as f:
        json.dump(spec, f, indent=1)
    with open(os.path.join(outdir, "convert.sh"), "w") as f:
        f.write(script)
    return spec


def prep_render(outdir):
    """prep.sh's L render, with no cluster (the conversion Job does not depend on one): a scratch
    kubeconfig keeps every kubectl call off the host's clusters. Writes agent.yaml, agent.err,
    job.json and convert.sh."""
    repo = os.environ.get("REPO", REPO)
    os.makedirs(outdir, exist_ok=True)
    env = render_env("L", os.environ, "h59-prep")
    env["KUBECONFIG"] = os.path.join(outdir, "kubeconfig")
    open(env["KUBECONFIG"], "a").close()
    cmd = [os.path.join(repo, "scripts", "k3d-gpu"), "agent", "manifest", "--model", ARMS["L"]["model"]]
    with open(os.path.join(outdir, "agent.yaml"), "w") as fo, open(os.path.join(outdir, "agent.err"), "w") as fe:
        rc = subprocess.run(cmd, env=env, stdout=fo, stderr=fe).returncode
    if rc != 0:
        raise SystemExit(f"prep render failed: agent manifest exited {rc}")
    return write_job(os.path.join(outdir, "agent.yaml"), outdir)


def docker_argv(spec, models, scripts):
    """`docker run` of the conversion Job's container on this host: its image, command, arguments
    and environment, the campaign's models at /models and the Job's script at /scripts, run as
    the campaign user, under the Job's memory limit."""
    if spec["image"] != LLAMA_FULL_IMAGE:
        raise SystemExit(f"run.py: the conversion Job runs {spec['image']}, not {LLAMA_FULL_IMAGE}")
    if spec["command"] != ["bash", "/scripts/convert.sh"]:
        raise SystemExit(f"run.py: unexpected conversion command {spec['command']}")
    argv = ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}"]
    lim = ((spec.get("resources") or {}).get("limits") or {}).get("memory")
    if lim:
        argv += ["--memory", lim.replace("Gi", "g")]
    for k, v in spec["env"].items():
        argv += ["-e", f"{k}={v}"]
    argv += ["-v", f"{models}:/models", "-v", f"{scripts}:/scripts:ro", "--entrypoint", "bash", spec["image"],
             "/scripts/convert.sh", *spec["args"]]
    return argv


def render_env(arm, environ, cluster):
    env = {k: v for k, v in environ.items()
           if not k.startswith(SCRUB_PREFIXES) and k not in SCRUB}
    env.update(COMMON_ENV)
    env.update(ARMS[arm]["env"])
    env["K3D_GPU_AGENT_REVISION"] = ARMS[arm]["revision"]
    # The hold's cluster: `agent manifest` reads the node's hybrid label and allocatable CPU
    # through context k3d-${K3D_GPU_CLUSTER}; without it that is k3d-gpu, which a hold's own
    # kubeconfig does not hold (H57dp's Od deviation).
    env["K3D_GPU_CLUSTER"] = cluster
    return env


def render(arm, out):
    """Render, stage, check and record one arm in the hold's cluster; exit 1 with the reason
    when the render fails or is not the registered arm."""
    host, cluster = os.environ.get("H59_PROFILE", ""), os.environ.get("CLUSTER", "")
    repo = os.environ.get("REPO", REPO)
    if not cluster:
        raise SystemExit("run.py render: CLUSTER (the hold's cluster) is not set")
    profile(host)
    os.makedirs(out, exist_ok=True)
    env = render_env(arm, os.environ, cluster)
    with open(os.path.join(out, "render-env.txt"), "w") as f:
        f.writelines(f"{k}={env[k]}\n" for k in sorted(env) if k.startswith("K3D_GPU_") or k == "KUBECONFIG")
    cmd = [os.path.join(repo, "scripts", "k3d-gpu"), "agent", "manifest", "--model", ARMS[arm]["model"]]
    with open(os.path.join(out, "agent.yaml"), "w") as fo, open(os.path.join(out, "agent.err"), "w") as fe:
        rc = subprocess.run(cmd, env=env, stdout=fo, stderr=fe).returncode
    if rc != 0:
        raise SystemExit(f"render failed: agent manifest exited {rc}")
    docs = load_docs(os.path.join(out, "agent.yaml"))
    rec = {"arm": arm, "host": host, "problems": []}
    if arm == "L":
        spec, _ = job_spec(docs)
        rec["job"] = spec
        g = os.path.expanduser(os.environ.get("G", ""))
        pj = os.path.join(g, "models", DRAFT_DIR, "prep-job.json")
        if not g or not os.path.exists(pj):
            rec["problems"].append(f"no prep-job.json for the drafter at {pj}")
        elif jload(pj) != spec:
            rec["problems"].append("the render's drafter conversion Job differs from the one prep.sh ran")
    staged = stage(arm, docs)
    dump_docs(staged, os.path.join(out, "deploy.yaml"))
    rec["problems"] += check(arm, host, staged)
    fit = fit_record(arm, staged)
    seqs = fit.get("max_num_seqs") if arm == "O" else fit.get("np")
    # Reported, not gated: the arm runs at the fit's sequences, as registered.
    rec["notes"] = [f"the fit gives {seqs} sequences (< 4): the N=4 phase queues"] \
        if seqs is not None and str(seqs).isdigit() and int(seqs) < 4 else []
    with open(os.path.join(out, "fit.json"), "w") as f:
        json.dump(fit, f, indent=1)
    with open(os.path.join(out, "check.json"), "w") as f:
        json.dump(rec, f, indent=1)
    if rec["problems"]:
        raise SystemExit("render not the registered arm: " + "; ".join(rec["problems"]))


# ---- crescent's load rule ------------------------------------------------------------------------

def gate(results):
    """H54's rule on crescent (amendment R4): a phase whose idle window read non-campaign CPU
    above 4 CPU-s/s, or that saw an aiperf process, is void and re-run once. The row's load is
    unmatched when an N=4 phase of a performance hold (a spare standing in for the hold it
    repeats) was void on both tries, or carries no void check."""
    hd = h59.hold_dirs(results)
    void, checked = [], 0
    for k in h59.PERF_HOLDS:
        for arm in ("O", "L"):
            ad = os.path.join(hd[k], arm)
            if h59.status(ad) != "measured":
                continue
            p = os.path.join(ad, "phases.jsonl")
            tries = []
            if os.path.exists(p):
                with open(p) as f:
                    tries = [r for r in (json.loads(x) for x in f if x.strip()) if r.get("phase") == "n4"]
            last = tries[-1] if tries else None
            if not last or "check" not in last:
                void.append(f"hold {k} {arm}: no void check for N=4")
            elif last["check"].get("void"):
                void.append(f"hold {k} {arm}: N=4 void on {len(tries)} tries ({last['check'].get('reason')})")
            checked += 1
    if void:
        return {"matched": False, "reason": "; ".join(void), "rule": "H54's void rule over the N=4 phases",
                "checked": checked}
    return {"matched": True, "reason": f"no N=4 phase void after its re-run ({checked} checked)",
            "rule": "H54's void rule over the N=4 phases", "checked": checked}


# ---- the prefix-reuse phase ----------------------------------------------------------------------

def _probe():
    spec = importlib.util.spec_from_file_location("h25_probe", os.path.join(REPO, "bench", "shared-prefix", "probe.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prefix(url, model, seed, out):
    """H25's phase S without its control: agents a1-a6 in sequence, each the byte-identical
    system text and 12 tool schemas plus its own 96-token message, max_tokens 8, thinking off,
    streamed. The score is the median first-chunk TTFT of agents 2-6."""
    probe = _probe()
    probe.MODEL = model
    p = probe.gen(seed)
    chat = url.rstrip("/") + "/chat/completions"
    agents = [dict(probe.request(chat, p["system"], u, p["tools"], 8), agent=f"a{n + 1}") for n, u in enumerate(p["a"])]
    added = [a["ttft"] for a in agents[1:] if a["ttft"] is not None]
    rec = {"seed": seed, "model": model, "agents": agents, "first_ttft_s": agents[0]["ttft"],
           "added_ttft_median_s": statistics.median(added) if len(added) == 5 else None,
           "prompt_tokens": sorted({str(a["prompt_tokens"]) for a in agents})}
    with open(out, "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    if cmd == "profile":
        for k, v in profile(a[0]).items():
            print(f"{k}={v}")
    elif cmd == "arms":
        print(json.dumps({"arms": ARMS, "orders": ORDERS, "modes": MODES, "common_env": COMMON_ENV,
                          "phases": LOAD_PHASES}, indent=1))
    elif cmd == "field":
        print(ARMS[a[0]][a[1]])
    elif cmd == "hold":
        mode, arms = hold(a[0], a[1] if len(a) > 1 else None)
        print(mode, *arms)
    elif cmd == "spare-ok":
        ok, why = spare_ok(a[0], a[1])
        print(why)
        return 0 if ok else 1
    elif cmd == "models":
        missing = models_ready(os.path.expanduser(a[0]))
        for m in missing:
            print(f"missing {m}")
        return 1 if missing else 0
    elif cmd == "render":
        render(a[0], a[1])
    elif cmd == "stage":
        dump_docs(stage(a[0], load_docs(a[1])), a[2])
    elif cmd == "check":
        bad = check(a[0], a[1], load_docs(a[2]))
        for b in bad:
            print(b)
        return 1 if bad else 0
    elif cmd == "job":
        write_job(a[0], a[1])
    elif cmd == "prep-render":
        prep_render(a[0])
    elif cmd == "docker-argv":
        print("\n".join(docker_argv(jload(a[0]), a[1], a[2])))
    elif cmd == "gate":
        print(json.dumps(gate(a[0]), indent=1))
    elif cmd == "prefix":
        rec = prefix(a[0], a[1], int(a[2]), a[3])
        print(json.dumps({"added_ttft_median_s": rec["added_ttft_median_s"], "first_ttft_s": rec["first_ttft_s"]}))
    else:
        raise SystemExit(f"run.py: unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

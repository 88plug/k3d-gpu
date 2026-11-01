#!/usr/bin/env python3
"""Facts about a k3d-gpu cluster in, the configuration Intel's Enterprise AI Solutions
installer (EAS, es_auto_installer.sh) needs for it out.

Pure: reads a facts JSON (gathered read-only from the cluster and the host), the pins
file and the flags, and writes, under --out:

  k3d-gpu-overrides.yaml   ansible extra vars, passed last to `install` so they win
  global_config.edit.yaml  keys for env/k3d-gpu/global_config.yaml (existing_kubernetes
                           is read from that file only)
  models.edit.yaml         edits to env/k3d-gpu/models.yaml, which model-manager reads:
                           defaults merge; each models entry matches a catalog entry by
                           name, its env merges and its args_append goes after its args
  repos.<layer>.yaml       the layer manifest's revs, as commit SHAs
  choices.txt              one line per choice: <fact> -> <choice> [<source>]

and prints choices.txt. --explain prints each source as a full permalink instead.
A refusal prints REFUSE lines on stderr, writes nothing (and removes these files if a
previous run left them in --out) and exits 3; bad input exits 2.
Strings are written double-quoted; booleans and lists keep their YAML types, because
ansible-core 2.19 rejects a quoted "false" in a conditional.

  eas-config.py --facts FACTS --out DIR [--layer inference|agent-toolkit|erag]
                [--eas-ref PROFILE|SHA] [--ei-ref SHA] [--eat-ref SHA]
                [--model NAME] [--pins FILE] [--explain]
  eas-config.py --list-sources

Facts (schema k3d-gpu.eas-facts/v1), each gathered read-only:
  cluster                  the k3d-gpu cluster name
  server_version           the API server's gitVersion
  docker_rootless          docker info lists the rootless security option
  nri_socket               /var/run/nri/nri.sock exists in the server node container
  containerd_cni_bin_dirs  crictl info .config.cni.binDirs in the server node container
  host                     {mem_available_kib, inotify_max_user_instances, inotify_max_user_watches}
  nodes                    [{name, labels, annotations, allocatable}] (kubectl get nodes)
  namespaces               [name] (kubectl get namespaces)
  daemonsets               [{namespace, name, labels, annotations}] (kubectl get daemonsets -A)
  agent_sandbox            null without the sandboxes.agents.x-k8s.io CRD, else {controller_image}
  fit                      {Hugging Face id: the `k3d-gpu fit --engine vllm --device cpu --json`
                           document, or null when fit could not size it}

Python standard library only.
"""

import argparse
import json
import os
import re
import sys

FACTS_SCHEMA = "k3d-gpu.eas-facts/v1"
PINS_SCHEMA = "k3d-gpu.eas-pins/v1"
ENV_NAME = "k3d-gpu"                # es_auto_installer.sh init <layer> --env k3d-gpu
KUBE_MOUNT = "/kube/config"         # where the bastion mounts the cluster's kubeconfig
EAT_SANDBOX_VERSION = "v0.5.0"      # EAT config.yaml:56 at 036b662
K3D_GPU_SANDBOX_NS = "agent-sandbox"  # share/agent-sandbox-k3d.yml: only k3d-gpu's profile makes it
AMX_LABEL = "feature.node.kubernetes.io/cpu-cpuid.AMXBF16"
K3S_ADDON = "k3s.cattle.io/v1, Kind=Addon"
LAYER_REPOS = {"inference": ["ei"], "agent-toolkit": ["ei", "eat"]}
REPO_LAYER = {"ei": "inference", "eat": "agent-toolkit"}

# EAT's single-node sizing: a node needs the model's memory request plus about 17 GiB
# for the rest of the stack, 16 GiB for Qwen3Guard and 2 GiB for the router.
STACK_GIB, GUARD_GIB, ROUTER_GIB = 17, 16, 2
# EAT's default model, then its documented 64 GiB fallbacks, in its order: (catalog name,
# Hugging Face id, memory GiB, VLLM_CPU_KVCACHE_SPACE GiB, source of both in the catalog).
# The same at EI 3e8a244 and 170eb3a. The catalog's memory is the pod's request and its
# limit (model_manager/templates/llm-inference-service.yaml), while k3d-gpu fit sizes the
# KV cache against the host, so fit may lower the catalog's KV cache but never raise it.
# fit sizes it for its own --max-model-len, and the catalog sets none (vLLM then serves
# the model's own context, 131072 tokens for Llama 3.1), so a lowered KV cache carries
# fit's --max-model-len: vLLM will not start when one request does not fit the cache.
CANDIDATES = [
    ("qwen2-5-coder-14b", "Qwen/Qwen2.5-Coder-14B-Instruct", 96, 40, "kv_qwen14"),
    ("qwen2-5-coder-7b", "Qwen/Qwen2.5-Coder-7B-Instruct", 64, 40, "kv_qwen7"),
    ("llama-3-1-8b", "meta-llama/Llama-3.1-8B-Instruct", 64, 40, "kv_llama8"),
]

_EAS = "https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/"
_EI = "https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/"
_EAT = "https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/"
# k3s v1.37.1+k3s1, the Dockerfile's K3S_TAG.
_K3S = "https://github.com/k3s-io/k3s/blob/356c025475bbefaa09cf0d7e48e8e2adbbde57db/"
_K3D_GPU = "https://github.com/88plug/k3d-gpu/blob/d997ea891cd7288eead48d428886607590299a9b/"

# Every source a choice can cite: a permalink at the tree it was read from (2026-10-29).
SOURCES = {
    "byo_kubeconfig": _EAS + "es_auto_installer.sh#L1342-L1358",
    "istio_k3d": _K3D_GPU + "docs/intel/upstream-eat.md#L222",
    "istio_k3s": _K3D_GPU + "docs/intel/upstream-eat.md#L221",
    "istio_default": _K3D_GPU + "integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch#L8-L9",
    "istio_profiles": _K3D_GPU + "integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch#L6-L11",
    "metallb": _EAS + "roles/metallb/tasks/install.yaml#L23-L31",
    "gateway_lb": _EAS + "roles/metallb/tasks/install.yaml#L94",
    "cpu_baked": _K3D_GPU + "docs/intel/upstream-eat.md#L231",
    "cpu_rootless": _K3S + "pkg/agent/templates/templates.go#L322-L325",
    "cpu_best_effort": _EAS + "playbooks/includes/preflight.yaml#L264-L270",
    "cpu_nri": _EAS + "configs/defaults/global_config.yaml#L99-L104",
    "overlay_0003": _EAS + "roles/nri_cpu_balloons/tasks/install_advanced.yaml#L197-L205",
    "nfd": _EI + "roles/nfd/tasks/install.yaml#L15-L25",
    "amx_yes": _EI + "model_manager/models.yaml#L20",
    "amx_no": _EI + "model_manager/core/renderer.py#L90-L95",
    "sgl_kernel": "https://github.com/vllm-project/vllm/blob/2cf0a6915ce544dc493a0990f2ea38d81601128a/"
                  "vllm/model_executor/layers/utils.py#L238-L244",
    "kv_max_len": "https://github.com/vllm-project/vllm/blob/2cf0a6915ce544dc493a0990f2ea38d81601128a/"
                  "vllm/v1/core/kv_cache_utils.py#L747-L767",
    "cluster_cidr": _K3S + "pkg/cli/cmds/server.go#L137-L141",
    "service_cidr": _K3S + "pkg/cli/cmds/server.go#L142-L146",
    "kata": _EAS + "roles/kata_containers/tasks/install.yaml#L9-L22",
    "accel_cpu": _EAS + "configs/defaults/global_config.yaml#L106",
    "accel_xpu": _EAT + "README.md#L36",
    "sandbox": _EAT + "config.yaml#L56",
    "sizing": _EAT + "docs/single-node-deployment.md#L84",
    "autodeploy": _EAT + "config.yaml#L32-L35",
    "autodeploy_ei": _EI + "roles/llm_services/defaults/main.yaml#L84",
    "kv_qwen14": _EI + "model_manager/models.yaml#L302-L311",
    "kv_qwen7": _EI + "model_manager/models.yaml#L262-L271",
    "kv_llama8": _EI + "model_manager/models.yaml#L148-L156",
    "guardrails": _EAT + "config.yaml#L25-L26",
    "hybrid_router": _EAT + "config.yaml#L27-L28",
    "cores": _EAT + "docs/prerequisites.md#L7",
    "inotify": _EAT + "docs/prerequisites.md#L22-L32",
    "erag_gpu": "https://github.com/intel/enterprise-rag/blob/319eb4652565fed42de9d601c61f9862b3759dfb/"
                "deployment/pipelines/chatqna_intel_gpu/config.yaml#L8-L15",
    "ei_runtimes": "https://github.com/intel/enterprise-inference/tree/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/"
                   "model_manager/runtimes",
    "eat1_gateway": _K3D_GPU + "integrations/eat/eat-remote.sh#L42",
    "eat1_ingress": _K3D_GPU + "integrations/eat/eat-check.sh#L39-L43",
}

INOTIFY_MIN = (("max_user_instances", 8192), ("max_user_watches", 1048576))
OUTPUTS = ("choices.txt", "k3d-gpu-overrides.yaml", "global_config.edit.yaml", "models.edit.yaml")
EAT_MIN_CORES = 48


class InputError(Exception):
    pass


def short(url):
    """The short form of a permalink: owner/repo@sha7 path:lines; anything else as is."""
    m = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/(?:blob|tree)/([0-9a-f]{40})/([^#]+)"
                     r"(?:#L(\d+)(?:-L(\d+))?)?", url)
    if m:
        repo, sha, path, a, b = m.groups()
        lines = "" if a is None else ":" + a + ("-" + b if b else "")
        return f"{repo}@{sha[:7]} {path}{lines}"
    return url


def gib(quantity):
    """A Kubernetes memory quantity in GiB."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGTPE]i|[kMGTPE])?", str(quantity))
    if not m:
        raise InputError(f"memory quantity {quantity!r} is not a Kubernetes quantity")
    n, unit = float(m.group(1)), m.group(2) or ""
    mult = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
            "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}[unit]
    return n * mult / 2**30


def cpus(quantity):
    q = str(quantity)
    return int(q[:-1]) / 1000 if q.endswith("m") else float(q)


def num(x):
    """125.33 -> '125.3', 512.0 -> '512'."""
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def node_args(node):
    """k3s.io/node-args as a list. k3s writes each --flag=value as two entries, the flag
    and its value (pkg/nodeconfig/nodeconfig.go#L30-L51 at v1.37.1+k3s1)."""
    raw = (node.get("annotations") or {}).get("k3s.io/node-args")
    if not raw:
        return []
    try:
        args = json.loads(raw)
    except ValueError as e:
        raise InputError(f"node {node.get('name')}: k3s.io/node-args is not JSON: {e}") from e
    if not isinstance(args, list):
        raise InputError(f"node {node.get('name')}: k3s.io/node-args is not a JSON list")
    return args


def flag_values(args, flag):
    vals = []
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            vals.append(args[i + 1])
    return vals


def owner(ds):
    """Who created a DaemonSet: a k3s addon (what k3d-gpu bakes), a Helm release, or neither.
    k3d-gpu bakes the nri-plugins chart's rendered output, so its DaemonSet carries Helm's
    labels too; the k3s addon annotation decides."""
    ann = ds.get("annotations") or {}
    if ann.get("objectset.rio.cattle.io/owner-gvk") == K3S_ADDON:
        return "k3s addon " + ann.get("objectset.rio.cattle.io/owner-name", "?")
    if ann.get("meta.helm.sh/release-name"):
        return "Helm release " + ann["meta.helm.sh/release-name"]
    return "another owner"


def image_tag(image):
    ref = (image or "").split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[1] if ":" in name else "unknown"


def load_json(path, what):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {what} {path}: {e}") from e


def resolve_pins(pins, eas_ref, ei_ref, eat_ref):
    """(profile name, {name: row}) for the eas, ei and eat checkouts."""
    if pins.get("schema") != PINS_SCHEMA:
        raise InputError(f"pins file schema is {pins.get('schema')!r}, want {PINS_SCHEMA}")
    rows = pins["pins"]
    profiles = sorted({r["profile"] for r in rows if r["name"] in ("eas", "ei", "eat")})
    sha = re.compile(r"[0-9a-f]{40}")
    ref = eas_ref or pins["default_profile"]
    if ref in profiles:
        profile = ref
        chosen = {r["name"]: dict(r) for r in rows if r["profile"] == ref and r["name"] in ("eas", "ei", "eat")}
    elif sha.fullmatch(ref):
        profile = pins["default_profile"]
        chosen = {r["name"]: dict(r) for r in rows if r["profile"] == profile and r["name"] in ("eas", "ei", "eat")}
        chosen["eas"].update(ref=ref, sha=ref, source="--eas-ref")
        profile = "custom"
    else:
        raise InputError(f"--eas-ref {ref} is not a profile in pins.json ({', '.join(profiles)}) and not a "
                         "commit SHA; resolve it first (git ls-remote)")
    for name, given in (("ei", ei_ref), ("eat", eat_ref)):
        if given is None:
            continue
        if not sha.fullmatch(given):
            raise InputError(f"--{name}-ref {given} is not a commit SHA; resolve it first (git ls-remote)")
        chosen[name].update(ref=given, sha=given, source=f"--{name}-ref")
    return profile, chosen


class Plan:
    def __init__(self):
        self.lines = []      # (fact, choice, SOURCES key, permalink or plain text)
        self.warns = []
        self.refusals = []
        self.overrides = []  # (key, python value)
        self.require_amx = None
        self.model_env = []  # (model, env key, value)
        self.model_args = []  # (model, [args appended to its catalog args])

    def choose(self, fact, choice, src):
        self.lines.append((fact, choice, src))

    def warn(self, fact, advice, src):
        self.warns.append(("WARN: " + fact, advice, src))

    def refuse(self, fact, reason, src):
        self.refusals.append(("REFUSE: " + fact, reason, src))

    def set(self, key, value):
        self.overrides.append((key, value))


def clear_outputs(out):
    """Remove this generator's files from a previous run, so a stale config is never applied."""
    if not os.path.isdir(out):
        return
    for name in os.listdir(out):
        if name in OUTPUTS or re.fullmatch(r"repos\.[a-z-]+\.yaml", name):
            os.remove(os.path.join(out, name))


def src_url(src):
    return SOURCES.get(src, src)


def render(entries, explain):
    out = []
    for fact, choice, src in entries:
        url = src_url(src)
        shown = url if explain else short(url)
        out.append(f"{fact} -> {choice} [{shown}]")
    return out


def yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(yaml_scalar(x) for x in v) + "]"
    return json.dumps(str(v))


def plan(facts, layer, pins, model_flag):
    p = Plan()
    nodes = facts["nodes"]
    namespaces = set(facts.get("namespaces") or [])
    daemonsets = facts.get("daemonsets") or []
    args = [node_args(n) for n in nodes]

    # --- refusals: collected, then nothing is written if any ---
    if layer == "erag":
        p.refuse("--layer erag", "not wired yet: ERAG's Intel GPU flavour chatqna_intel_gpu is EXPERIMENTAL and "
                 "needs kubernetes_accelerator=intel-gpu", "erag_gpu")
        p.refuse("--layer erag", "enterprise-inference ships no vllm-xpu runtime for it (model_manager/runtimes "
                 "holds openvino and vllm)", "ei_runtimes")
    disabled = {c for a in args for v in flag_values(a, "--disable") for c in v.split(",")}
    if "servicelb" in disabled:
        p.refuse("k3s.io/node-args has --disable servicelb", "recreate the cluster without --disable=servicelb: "
                 "EAS's gateway needs a LoadBalancer address", "gateway_lb")
    bin_dirs = facts.get("containerd_cni_bin_dirs") or []
    # Istio's cni chart mounts one bin dir per platform profile: k3d /bin (a symlink to
    # /usr/bin in k3d-gpu's node image), k3s /var/lib/rancher/k3s/data/cni, none /opt/cni/bin.
    istio = None
    for d in bin_dirs:
        if d in ("/usr/bin", "/bin"):
            istio = ("k3d", "(needs overlay 0002)", "istio_k3d")
        elif d.startswith("/var/lib/rancher/k3s/data"):
            istio = ("k3s", "(needs overlay 0002)", "istio_k3s")
        elif d == "/opt/cni/bin":
            istio = ("", "(Istio's default CNI paths)", "istio_default")
        if istio:
            break
    if istio is None:
        p.refuse(f"containerd CNI bin_dirs {json.dumps(bin_dirs)}", "matches no Istio platform profile EAS can "
                 "pass (k3d: /bin, k3s: /var/lib/rancher/k3s/data/cni, default: /opt/cni/bin), so ambient "
                 "mode could not chain into the CNI", "istio_profiles")
    if "genai-gateway" in namespaces:
        p.refuse("namespace genai-gateway (EAT 1.x LiteLLM, eat-up.sh)", "tear the EAT 1.x stack down first "
                 "(k3d-gpu down resets the cluster): both stacks would own LiteLLM", "eat1_gateway")
    if "ingress-nginx" in namespaces:
        p.refuse("namespace ingress-nginx (EAT 1.x ingress, eat-up.sh)", "remove it first: its controller binds "
                 "host ports 80 and 443, which EAS's gateway takes through ServiceLB", "eat1_ingress")
    sandbox = facts.get("agent_sandbox")  # null without the agent-sandbox CRD
    if layer == "agent-toolkit" and sandbox:
        tag = image_tag(sandbox.get("controller_image"))
        if K3D_GPU_SANDBOX_NS in namespaces:
            p.refuse(f"agent-sandbox {tag} from k3d-gpu sandbox up (namespace {K3D_GPU_SANDBOX_NS})",
                     f"run 'k3d-gpu sandbox down' first: EAT installs agent-sandbox {EAT_SANDBOX_VERSION} over "
                     "the same CRDs", "sandbox")
        elif tag != EAT_SANDBOX_VERSION:
            p.refuse(f"agent-sandbox {tag} installed", f"remove it first: EAT installs agent-sandbox "
                     f"{EAT_SANDBOX_VERSION} over the same CRDs", "sandbox")
    if p.refusals:
        return p

    # --- choices ---
    p.choose(f"cluster {facts['cluster']}, kubeconfig mounted read-only at {KUBE_MOUNT}",
             f"existing_kubernetes: {KUBE_MOUNT}", "byo_kubeconfig")

    platform = istio[0] or '""'
    p.choose(f"containerd CNI bin_dirs {json.dumps(bin_dirs)}", f"istio_platform: {platform} {istio[1]}", istio[2])
    p.set("istio_platform", istio[0])

    p.choose("ServiceLB on (no --disable servicelb in k3s.io/node-args)", "metallb_enabled: false", "metallb")
    p.set("metallb_enabled", False)

    balloons = [d for d in daemonsets if d.get("name") == "nri-resource-policy-balloons"]
    baked = [d for d in balloons if not owner(d).startswith("Helm release")]
    if baked:
        d = baked[0]
        p.choose(f"DaemonSet {d['namespace']}/{d['name']} from {owner(d)}",
                 "kubernetes_cpu_policy: best-effort (one NRI policy per node)", "cpu_baked")
        policy = "best-effort"
    elif facts.get("docker_rootless"):
        p.choose("rootless Docker (k3s turns containerd NRI off in a user namespace)",
                 "kubernetes_cpu_policy: best-effort", "cpu_rootless")
        policy = "best-effort"
    elif not facts.get("nri_socket"):
        p.choose("no /var/run/nri/nri.sock in the node (containerd NRI off)",
                 "kubernetes_cpu_policy: best-effort", "cpu_best_effort")
        policy = "best-effort"
    else:
        if balloons:
            d = balloons[0]
            p.choose(f"DaemonSet {d['namespace']}/{d['name']} from {owner(d)}",
                     "kubernetes_cpu_policy: nri-balloons (EAS upgrades its own release)", "cpu_nri")
        else:
            p.choose("no NRI policy on the node, containerd NRI on",
                     "kubernetes_cpu_policy: nri-balloons (EAS installs the balloons policy)", "cpu_nri")
        p.choose("kubernetes_cpu_policy nri-balloons",
                 "overlay 0003: needed (pins the nri-plugins chart EAS installs unpinned)", "overlay_0003")
        policy = "nri-balloons"
    p.set("kubernetes_cpu_policy", policy)

    # EI's role skips the install only for a Helm release named node-feature-discovery.
    nfd = [d for d in daemonsets if d.get("namespace") == "node-feature-discovery"]
    if nfd and owner(nfd[0]) != "Helm release node-feature-discovery":
        d = nfd[0]
        p.choose(f"DaemonSet {d['namespace']}/{d['name']} from {owner(d)}",
                 "nfd_enabled: false (EI would install a second NFD)", "nfd")
        p.set("nfd_enabled", False)
    elif nfd:
        d = nfd[0]
        p.choose(f"DaemonSet {d['namespace']}/{d['name']} from {owner(d)}",
                 "nfd_enabled: true (EI's own release; its role skips the install)", "nfd")
        p.set("nfd_enabled", True)
    else:
        p.choose("no NFD in namespace node-feature-discovery", "nfd_enabled: true (EI installs NFD)", "nfd")
        p.set("nfd_enabled", True)

    amx = [n["name"] for n in nodes if (n.get("labels") or {}).get(AMX_LABEL) == "true"]
    if amx:
        p.choose(f"node {', '.join(amx)} labelled {AMX_LABEL}=true", "defaults.require_amx: true", "amx_yes")
    else:
        p.choose(f"no node labelled {AMX_LABEL}=true",
                 "defaults.require_amx: false (true leaves model pods Pending)", "amx_no")
        p.choose("no node with AMXBF16", "VLLM_CPU_SGL_KERNEL: as shipped (vLLM v0.28.0 checks for AMX before "
                 "it uses the SGL kernel)", "sgl_kernel")
    p.require_amx = bool(amx)

    for flag, key, default, src in (("--cluster-cidr", "kubernetes_cluster_cidr", "10.42.0.0/16", "cluster_cidr"),
                                    ("--service-cidr", "kubernetes_service_cidr", "10.43.0.0/16", "service_cidr")):
        given = [v for a in args for v in flag_values(a, flag)]
        if given:
            cidr = given[0].split(",")[0]
            p.choose(f"k3s.io/node-args {flag} {given[0]}", f"{key}: {cidr}", src)
        else:
            cidr = default
            p.choose(f"no {flag} in k3s.io/node-args", f"{key}: {cidr} (the k3s default)", src)
        p.set(key, cidr)

    p.choose("bring-your-own cluster", "kata_containers_enabled: false", "kata")
    p.set("kata_containers_enabled", False)

    devices = []
    for n in nodes:
        for res, val in sorted((n.get("allocatable") or {}).items()):
            if (res.startswith("gpu.intel.com/") or res == "habana.ai/gaudi") and str(val) not in ("0", ""):
                devices.append(f"{res} allocatable {val}")
    if devices:
        xpu = any(d.startswith("gpu.intel.com/") for d in devices)
        p.choose(", ".join(devices), "kubernetes_accelerator: cpu (serve the device with k3d-gpu agent up)",
                 "accel_xpu" if xpu else "accel_cpu")
    else:
        p.choose("no accelerator resources", "kubernetes_accelerator: cpu", "accel_cpu")
    p.set("kubernetes_accelerator", "cpu")

    node_mem = max(gib((n.get("allocatable") or {}).get("memory", "0")) for n in nodes)
    fits = facts.get("fit") or {}

    def kv_of(hf):
        """(KV cache GiB, the --max-model-len fit sized it for) from fit's flags, or None."""
        doc = fits.get(hf)
        if not doc or not doc.get("fits"):
            return None
        flags = dict(f.split("=", 1) for f in doc.get("flags") or [] if "=" in f)
        if "VLLM_CPU_KVCACHE_SPACE" not in flags:
            return None
        try:
            kv = int(flags["VLLM_CPU_KVCACHE_SPACE"])
        except ValueError:
            raise InputError(f"k3d-gpu fit for {hf}: VLLM_CPU_KVCACHE_SPACE={flags['VLLM_CPU_KVCACHE_SPACE']} "
                             "is not a whole number of GiB") from None
        ctx = flags.get("--max-model-len", "")
        if not ctx.isdigit():
            raise InputError(f"k3d-gpu fit for {hf}: a KV cache with no --max-model-len, the context it was "
                             "sized for")
        return kv, int(ctx)

    def kv_choice(name, hf, mem, catalog_kv, src):
        got = kv_of(hf)
        if got is None:
            return
        value, ctx = got
        fact = f"k3d-gpu fit {hf} --engine vllm --device cpu sizes the KV cache at {value} GiB"
        if value < catalog_kv:
            p.choose(fact, f"{name} env.VLLM_CPU_KVCACHE_SPACE: {value} (catalog {catalog_kv})", src)
            p.model_env.append((name, "VLLM_CPU_KVCACHE_SPACE", str(value)))
            p.choose(f"k3d-gpu fit sized {name}'s {value} GiB KV cache for --max-model-len {ctx}",
                     f"{name} args_append: --max-model-len {ctx} (the catalog sets no --max-model-len, and vLLM "
                     "will not start when one request at max_model_len does not fit the KV cache)", "kv_max_len")
            p.model_args.append((name, ["--max-model-len", str(ctx)]))
        else:
            p.choose(fact, f"{name} env.VLLM_CPU_KVCACHE_SPACE: {catalog_kv} as shipped (fit only lowers it: the "
                     f"pod's memory limit is the catalog's {mem}Gi, not the host)", src)

    if layer == "agent-toolkit":
        if sandbox:
            p.choose(f"agent-sandbox {EAT_SANDBOX_VERSION} installed (EAT's own)", "agent_sandbox_enabled: true",
                     "sandbox")
        else:
            p.choose("no agent-sandbox CRD", f"agent_sandbox_enabled: true (EAT installs agent-sandbox "
                     f"{EAT_SANDBOX_VERSION})", "sandbox")
        p.set("agent_sandbox_enabled", True)

    overhead = STACK_GIB + GUARD_GIB + ROUTER_GIB
    if model_flag:
        p.choose(f"--model {model_flag}", f"llm_services_autodeploy_models: [{model_flag}]",
                 "autodeploy" if layer == "agent-toolkit" else "autodeploy_ei")
        p.set("llm_services_autodeploy_models", [model_flag])
        for name, hf, mem, catalog_kv, src in CANDIDATES:
            if name != model_flag:
                continue
            kv_choice(name, hf, mem, catalog_kv, src)
            need = mem + overhead
            if need > node_mem:
                p.warn(f"--model {name} needs {need} GiB, node memory {num(node_mem)} GiB",
                       "continuing as asked; its pods may stay Pending", "sizing")
            doc = fits.get(hf)
            if doc and not doc.get("fits"):
                p.warn(f"--model {name}: {doc.get('summary', 'k3d-gpu fit says it does not fit')}",
                       "continuing as asked; VLLM_CPU_KVCACHE_SPACE as shipped", "sizing")
    elif layer == "agent-toolkit":
        reasons, picked = [f"node memory {num(node_mem)} GiB"], None
        for name, hf, mem, catalog_kv, src in CANDIDATES:
            need = mem + overhead
            doc = fits.get(hf)
            if need > node_mem:
                reasons.append(f"{name} needs {need} GiB")
            elif not doc:
                reasons.append(f"{name}: k3d-gpu fit gave no result")
            elif not doc.get("fits"):
                reasons.append(f"{name}: {doc.get('summary', 'k3d-gpu fit says it does not fit')}")
            else:
                reasons.append(f"{name} needs {need} GiB and k3d-gpu fit says it fits")
                picked = (name, hf, mem, catalog_kv, src)
                break
        if picked:
            p.choose("; ".join(reasons), f"llm_services_autodeploy_models: [{picked[0]}]", "sizing")
            p.set("llm_services_autodeploy_models", [picked[0]])
            kv_choice(*picked)
        else:
            p.choose("; ".join(reasons), "llm_services_autodeploy_models: [] (deploy one later with "
                     "./model-manager deploy <name>)", "autodeploy")
            p.set("llm_services_autodeploy_models", [])

    if layer == "agent-toolkit":
        p.choose("EAT default", f"guardrails_enabled: on (Qwen3Guard, {GUARD_GIB} GiB of the budget)", "guardrails")
        p.choose("EAT default", f"hybrid_router_enabled: on ({ROUTER_GIB} GiB of the budget)", "hybrid_router")

    for name in LAYER_REPOS[layer]:
        row = pins[name]
        manifest = f"repos.{layer}.yaml {REPO_LAYER[name]} rev: {row['sha']}"
        if row["source"].startswith("--"):
            p.choose(f"{row['source']} {row['sha']}", manifest, "as given")
        else:
            p.choose(f"pin {name} {row['ref']} (profile {row['profile']})", manifest, row["source"])

    cores = max(cpus((n.get("allocatable") or {}).get("cpu", "0")) for n in nodes)
    if cores < EAT_MIN_CORES:
        p.warn(f"node has {num(cores)} CPUs, under EAT's {EAT_MIN_CORES}-core minimum", "continuing", "cores")
    host = facts.get("host") or {}
    low = [(k, host.get("inotify_" + k), want) for k, want in INOTIFY_MIN
           if host.get("inotify_" + k) is not None and host["inotify_" + k] < want]
    if low:
        p.warn(", ".join(f"fs.inotify.{k} {have} < {want}" for k, have, want in low),
               "raise on the host: sudo sysctl -w " + " ".join(f"fs.inotify.{k}={want}" for k, _, want in low),
               "inotify")
    return p


def outputs(p, layer, profile, pins):
    files = {}
    files["k3d-gpu-overrides.yaml"] = (
        "# k3d-gpu eas-config: ansible extra vars for es_auto_installer.sh install, passed\n"
        "# last (-- -e @k3d-gpu-overrides.yaml) so each value wins; why: choices.txt\n"
        + "".join(f"{k}: {yaml_scalar(v)}\n" for k, v in p.overrides))
    files["global_config.edit.yaml"] = (
        f"# k3d-gpu eas-config: keys to set in env/{ENV_NAME}/global_config.yaml, where\n"
        "# _resolve_kubeconfig reads existing_kubernetes (not from extra vars)\n"
        f"existing_kubernetes: {yaml_scalar(KUBE_MOUNT)}\n")
    models = (f"# k3d-gpu eas-config: edits to env/{ENV_NAME}/models.yaml, the file model-manager reads\n"
              "# (extra vars do not reach it); defaults merge, models match an entry by name\n"
              f"defaults:\n  require_amx: {yaml_scalar(p.require_amx)}\n")
    if p.model_env:
        models += "models:\n"
        for name in dict.fromkeys(m for m, _, _ in p.model_env):
            models += f"  - name: {yaml_scalar(name)}\n    env:\n"
            models += "".join(f"      {k}: {yaml_scalar(v)}\n" for m, k, v in p.model_env if m == name)
            models += "".join(f"    args_append: {yaml_scalar(a)}\n" for m, a in p.model_args if m == name)
    else:
        models += "models: []\n"
    files["models.edit.yaml"] = models
    repos = (f"# k3d-gpu eas-config: configs/repos/repos.{layer}.yaml revs as commit SHAs\n"
             f"# (profile {profile}); each row sets rev on the manifest entry with its layer and url\n"
             "repos:\n")
    for name in LAYER_REPOS[layer]:
        row = pins[name]
        repos += (f"  - layer: {yaml_scalar(REPO_LAYER[name])}\n"
                  f"    url: {yaml_scalar('https://github.com/' + row['repo'])}\n"
                  f"    rev: {yaml_scalar(row['sha'])}\n")
    files[f"repos.{layer}.yaml"] = repos
    return files


def header(facts, layer, profile, pins):
    h = [f"# k3d-gpu eas-config: profile {profile}, layer {layer}, cluster {facts['cluster']}"]
    for name in ["eas"] + LAYER_REPOS.get(layer, []):
        h.append(f"# {name} {pins[name]['repo']} {pins[name]['sha']}")
    h.append("# each line: <fact> -> <choice> [<source>]; --explain prints each source as a permalink")
    return h


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--facts")
    ap.add_argument("--out")
    ap.add_argument("--pins", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "pins.json"))
    ap.add_argument("--layer", choices=["inference", "agent-toolkit", "erag"], default="inference")
    ap.add_argument("--eas-ref")
    ap.add_argument("--ei-ref")
    ap.add_argument("--eat-ref")
    ap.add_argument("--model")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--list-sources", action="store_true")
    a = ap.parse_args(argv)
    if a.list_sources:
        for k, url in SOURCES.items():
            print(f"{k} {url}")
        return 0
    if not a.facts or not a.out:
        ap.error("--facts and --out are required")
    try:
        profile, pins = resolve_pins(load_json(a.pins, "pins"), a.eas_ref, a.ei_ref, a.eat_ref)
        facts = load_json(a.facts, "facts")
        if facts.get("schema") != FACTS_SCHEMA:
            raise InputError(f"facts schema is {facts.get('schema')!r}, want {FACTS_SCHEMA}")
        if not facts.get("cluster") or not facts.get("nodes"):
            raise InputError("facts name no cluster or list no nodes")
        p = plan(facts, a.layer, pins, a.model)
    except (InputError, KeyError, TypeError) as e:
        print(f"eas-config: {e}", file=sys.stderr)
        return 2
    if p.refusals:
        clear_outputs(a.out)
        for line in render(p.refusals, a.explain):
            print(line, file=sys.stderr)
        return 3
    head = header(facts, a.layer, profile, pins)
    files = outputs(p, a.layer, profile, pins)
    files["choices.txt"] = "\n".join(head + render(p.lines + p.warns, False)) + "\n"
    os.makedirs(a.out, mode=0o700, exist_ok=True)
    clear_outputs(a.out)
    for name, text in files.items():
        with open(os.path.join(a.out, name), "w") as fh:
            fh.write(text)
    if a.explain:
        print("\n".join(head + render(p.lines + p.warns, True)))
    else:
        sys.stdout.write(files["choices.txt"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

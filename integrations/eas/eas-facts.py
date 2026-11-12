#!/usr/bin/env python3
"""eas-facts: the preflight of eas-up.sh, run on the host before anything is cloned
or installed. It reads the cluster with kubectl, each node's containerd config with
`docker exec`, and the model's memory fit with `k3d-gpu fit`, then either refuses
with a named cause or writes the facts file that `eas-up.sh render` reads.

Checks, in order:
  1. the cluster answers through the kubeconfig
  2. agent-sandbox ownership: EAT 036b662 applies agent-sandbox v0.5.0 into
     agent-sandbox-system with state: present (roles/agent_sandbox/tasks/install.yaml:55-63),
     which would patch any other controller there (k3d-gpu `sandbox up` installs v1.0.8)
  3. the Istio platform, from containerd's CNI bin_dirs on every node:
     ["/usr/bin"] is the k3d-gpu node image (k3d), ["/var/lib/rancher/k3s/data/cni"]
     is stock k3s (docs/intel/upstream-eat.md, Gate 2 probes 1-2); anything else is refused
  4. AMX: require_amx is true only when every schedulable node carries
     feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true (EI 170eb3a renderer.py:90-125
     puts a required nodeAffinity on that label on every model pod when it is true)
  5. the pod CIDR: every node's podCIDR inside the cluster CIDR
  6. model fit: the model and the qwen3guard guard, with the requests already on the
     node, within its allocatable cpu and memory; the model's KV cache and memory
     come from `k3d-gpu fit` (the mechanism integrations/eat/eat-up.sh uses)

Usage: eas-facts.py --kubeconfig FILE --images FILE --out FILE [--env NAME]

Overrides (environment; unset = detected):
  EAS_REQUIRE_AMX     true | false
  EAS_ISTIO_PLATFORM  k3d | k3s (skips reading containerd's config)
  EAS_CLUSTER_CIDR    the cluster's pod CIDR (default: k3s's 10.42.0.0/16)
  EAS_MODEL           EI catalog name of the model to deploy (default: qwen3-1-7b)
  EAS_MODEL_ID        its Hugging Face id, for k3d-gpu fit (needed with EAS_MODEL)
  EAS_MODEL_CPU       its CPU request (default: 8, at most what the node has free)
  K3D_GPU             the k3d-gpu command (default: k3d-gpu on PATH, else this repo's)

Exit 0 with the facts file written, 1 on a refusal (nothing written), 2 on bad usage.
"""

import argparse
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "k3d-gpu.eas-facts/v1"
# Where es_auto_installer.sh finds the kubeconfig inside the bastion (eas-up.sh mounts it there).
BASTION_KUBECONFIG = "/kube/config"
SANDBOX_NS = "agent-sandbox-system"
SANDBOX_DEPLOY = "agent-sandbox-controller"
# EAT 036b662 roles/agent_sandbox/defaults/main.yaml:22 (asserted again by `eas-up.sh render`).
EAT_SANDBOX_VERSION = "v0.5.0"
CONTAINERD_CONFIG = "/var/lib/rancher/k3s/agent/etc/containerd/config.toml"
PLATFORMS = {("/usr/bin",): "k3d", ("/var/lib/rancher/k3s/data/cni",): "k3s"}
AMX_LABEL = "feature.node.kubernetes.io/cpu-cpuid.AMXBF16"
K3S_CLUSTER_CIDR = "10.42.0.0/16"
DEFAULT_MODEL, DEFAULT_MODEL_ID = "qwen3-1-7b", "Qwen/Qwen3-1.7B"
# EI 170eb3a model_manager/models.yaml: the guard's requests (qwen3guard, lines 417-418)
# and the llm category's default cpu, which `cpu: auto` takes outside nri-balloons
# (runtimes.vllm.categories.llm.defaults; core/policy.py resolve_auto_cpu). `eas-up.sh
# render` asserts both against the checkout before it writes anything.
GUARD = {"name": "qwen3guard", "cpu": 8, "memory": "16Gi"}
MODEL_CPU_DEFAULT = 8
MODEL_CPU_MIN = 2

MEM_UNITS = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
             "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60, "m": 1e-3}


class Refusal(Exception):
    pass


def cpu_cores(q):
    """A Kubernetes CPU quantity ("8", "500m", "1.5") in cores."""
    q = str(q).strip()
    if q.endswith("m"):
        return float(q[:-1]) / 1000
    return float(q)


def mem_bytes(q):
    """A Kubernetes memory quantity ("16Gi", "131521428Ki", "512M", "1e9") in bytes."""
    m = re.fullmatch(r"([0-9.]+(?:[eE][0-9]+)?)([a-zA-Z]*)", str(q).strip())
    if not m or m.group(2) not in MEM_UNITS:
        raise Refusal("cannot read the memory quantity %r" % q)
    return float(m.group(1)) * MEM_UNITS[m.group(2)]


def gib(b):
    return "%.1f GiB" % (b / 2**30)


def cores(c):
    return ("%.2f" % c).rstrip("0").rstrip(".")


def run(argv, env=None, check=True):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, env=env)
    except FileNotFoundError:
        raise Refusal("%s is not installed" % argv[0])
    if check and r.returncode != 0:
        last = (r.stderr.strip().splitlines() or [""])[-1]
        raise Refusal("`%s` failed (rc %d): %s" % (" ".join(argv[:4]), r.returncode, last))
    return r


def image_tag(image):
    """The tag of name[:tag][@digest] (None when there is none)."""
    ref = image.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def bin_dirs(config):
    """containerd's CNI bin_dirs (bin_dir on config v2) from a config.toml text, as a tuple."""
    found = set()
    for m in re.finditer(r"^[ \t]*bin_dirs?[ \t]*=[ \t]*(.+?)[ \t]*$", config, re.M):
        value = m.group(1)
        dirs = tuple(a or b for a, b in re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', value))
        found.add(dirs)
    return found


def schedulable(node):
    if node.get("spec", {}).get("unschedulable"):
        return False
    return not any(t.get("effect") in ("NoSchedule", "NoExecute") for t in node.get("spec", {}).get("taints") or [])


def pod_requests(pod):
    """(cpu cores, memory bytes) the scheduler counts for a pod."""
    def total(containers, fn, key):
        return [fn(((c.get("resources") or {}).get("requests") or {}).get(key, 0)) for c in containers or []]
    spec, out = pod.get("spec", {}), []
    for key, fn in (("cpu", cpu_cores), ("memory", mem_bytes)):
        main = sum(total(spec.get("containers"), fn, key))
        init = max(total(spec.get("initContainers"), fn, key) or [0])
        overhead = fn((spec.get("overhead") or {}).get(key, 0))
        out.append(max(main, init) + overhead)
    return tuple(out)


def k3d_gpu():
    cmd = os.environ.get("K3D_GPU") or shutil.which("k3d-gpu")
    if cmd:
        return cmd
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "k3d-gpu")
    if os.path.isfile(repo):
        return repo
    raise Refusal("k3d-gpu is not on PATH (it sizes the model with `k3d-gpu fit`); set K3D_GPU")


def facts(kubeconfig, images, env_name):
    say = lambda msg: print("==> " + msg, flush=True)  # noqa: E731
    if not os.path.isfile(kubeconfig):
        raise Refusal("kubeconfig %s not found (set EAS_KUBECONFIG)" % kubeconfig)
    kenv = dict(os.environ, KUBECONFIG=kubeconfig)

    # 1. The cluster.
    try:
        nodes = json.loads(run(["kubectl", "get", "nodes", "-o", "json"], kenv).stdout)["items"]
    except Refusal as e:
        raise Refusal("the cluster in %s does not answer: %s" % (kubeconfig, e))
    if not nodes:
        raise Refusal("the cluster has no nodes")
    sched = [n for n in nodes if schedulable(n)]
    if not sched:
        raise Refusal("no schedulable node (every node is cordoned or tainted NoSchedule/NoExecute)")
    say("cluster: %d node(s), %d schedulable" % (len(nodes), len(sched)))

    # 2. Agent-sandbox ownership.
    out = run(["kubectl", "-n", SANDBOX_NS, "get", "deployment", SANDBOX_DEPLOY, "--ignore-not-found",
               "-o", "jsonpath={.spec.template.spec.containers[*].image}"], kenv).stdout.split()
    controller = [i for i in out if "agent-sandbox-controller" in i] or out
    for image in controller:
        if image_tag(image) != EAT_SANDBOX_VERSION:
            raise Refusal(
                "%s/%s runs %s, but Intel's agent toolkit (EAT 036b662) applies agent-sandbox %s over it "
                "(roles/agent_sandbox/tasks/install.yaml:55-63). Run `k3d-gpu sandbox down` first, or use a "
                "separate cluster." % (SANDBOX_NS, SANDBOX_DEPLOY, image, EAT_SANDBOX_VERSION))
    say("agent-sandbox: %s" % ("%s present (%s), EAT's own version" % (SANDBOX_DEPLOY, controller[0])
                               if controller else "no controller yet; EAT installs %s" % EAT_SANDBOX_VERSION))

    # 3. Istio platform.
    override = os.environ.get("EAS_ISTIO_PLATFORM", "")
    if override:
        if override not in ("k3d", "k3s"):
            raise Refusal("EAS_ISTIO_PLATFORM must be k3d or k3s, got %r" % override)
        platform, platform_from = override, "EAS_ISTIO_PLATFORM"
    else:
        seen = {}
        for n in nodes:
            name = n["metadata"]["name"]
            r = run(["docker", "exec", name, "cat", CONTAINERD_CONFIG], check=False)
            if r.returncode != 0:
                raise Refusal("cannot read %s on node %s with `docker exec` (is it a k3d node container?): %s. "
                              "Set EAS_ISTIO_PLATFORM=k3d or k3s if you know the CNI layout."
                              % (CONTAINERD_CONFIG, name, (r.stderr.strip().splitlines() or ["no output"])[-1]))
            dirs = bin_dirs(r.stdout)
            if len(dirs) != 1 or next(iter(dirs)) not in PLATFORMS:
                shown = ", ".join(json.dumps(list(d)) for d in sorted(dirs)) or "none"
                raise Refusal("node %s: containerd CNI bin_dirs %s is neither k3d's [\"/usr/bin\"] nor k3s's "
                              "[\"/var/lib/rancher/k3s/data/cni\"]; Istio's istio-cni would install its plugin "
                              "where containerd does not look" % (name, shown))
            seen[name] = PLATFORMS[next(iter(dirs))]
        if len(set(seen.values())) != 1:
            raise Refusal("nodes disagree on the CNI layout: %s" % seen)
        platform = next(iter(seen.values()))
        platform_from = "containerd CNI bin_dirs %s on %s" % (
            json.dumps(list(next(k for k, v in PLATFORMS.items() if v == platform))), ", ".join(sorted(seen)))
    say("Istio platform: %s (%s)" % (platform, platform_from))

    # 4. AMX.
    amx = [n["metadata"]["name"] for n in sched if (n["metadata"].get("labels") or {}).get(AMX_LABEL) == "true"]
    detected = len(amx) == len(sched)
    amx_from = "%d of %d schedulable node(s) carry %s=true" % (len(amx), len(sched), AMX_LABEL)
    req = os.environ.get("EAS_REQUIRE_AMX", "")
    if req:
        if req not in ("true", "false"):
            raise Refusal("EAS_REQUIRE_AMX must be true or false, got %r" % req)
        require_amx = req == "true"
        amx_from = "EAS_REQUIRE_AMX=%s (%s)" % (req, amx_from)
        if require_amx and not amx:
            say("WARNING: EAS_REQUIRE_AMX=true but no schedulable node carries %s=true: every model pod, "
                "qwen3guard included, will stay Pending and the fail-closed guardrail will refuse every request"
                % AMX_LABEL)
    else:
        require_amx = detected
    say("require_amx: %s (%s)" % (str(require_amx).lower(), amx_from))

    # 5. Pod CIDR.
    cidr_s = os.environ.get("EAS_CLUSTER_CIDR") or K3S_CLUSTER_CIDR
    try:
        cidr = ipaddress.ip_network(cidr_s)
    except ValueError:
        raise Refusal("EAS_CLUSTER_CIDR %r is not a network" % cidr_s)
    for n in nodes:
        pc = n.get("spec", {}).get("podCIDR")
        if pc and not ipaddress.ip_network(pc).subnet_of(cidr):
            raise Refusal("node %s has podCIDR %s, outside %s; set EAS_CLUSTER_CIDR to the cluster's pod CIDR"
                          % (n["metadata"]["name"], pc, cidr_s))
    say("cluster CIDR: %s" % cidr_s)

    # 6. Model fit.
    model = os.environ.get("EAS_MODEL") or DEFAULT_MODEL
    model_id = os.environ.get("EAS_MODEL_ID") or (DEFAULT_MODEL_ID if model == DEFAULT_MODEL else "")
    if not model_id:
        raise Refusal("EAS_MODEL=%s needs EAS_MODEL_ID, its Hugging Face id, for k3d-gpu fit" % model)
    r = run([k3d_gpu(), "fit", "--model", model_id, "--engine", "vllm", "--device", "cpu", "--json"], check=False)
    try:
        fit = json.loads(r.stdout)
    except ValueError:
        raise Refusal("k3d-gpu fit could not size %s (rc %d): %s"
                      % (model_id, r.returncode, (r.stderr.strip().splitlines() or ["no output"])[-1]))
    if not fit.get("fits"):
        raise Refusal("%s does not fit this host: %s ('k3d-gpu fit --model %s --engine vllm --device cpu --explain')"
                      % (model_id, fit.get("summary", "k3d-gpu fit says no"), model_id))
    kv = next((f.split("=", 1)[1] for f in fit.get("flags", []) if f.startswith("VLLM_CPU_KVCACHE_SPACE=")), "")
    model_mem = (fit.get("k8s") or {}).get("memory_request", "")
    if not kv or not model_mem:
        raise Refusal("k3d-gpu fit gave no VLLM_CPU_KVCACHE_SPACE or k8s memory_request for %s" % model_id)
    say("k3d-gpu fit: %s" % fit.get("summary", ""))

    pods = json.loads(run(["kubectl", "get", "pods", "-A", "-o", "json"], kenv).stdout)["items"]
    eligible = [n for n in sched if n["metadata"]["name"] in amx] if (require_amx and amx) else sched
    budgets = []
    for n in eligible:
        name, alloc = n["metadata"]["name"], n.get("status", {}).get("allocatable", {})
        used_cpu = used_mem = 0.0
        for p in pods:
            if p.get("spec", {}).get("nodeName") == name and p.get("status", {}).get("phase") not in ("Succeeded", "Failed"):
                c, m = pod_requests(p)
                used_cpu, used_mem = used_cpu + c, used_mem + m
        budgets.append({"node": name, "cpu": cpu_cores(alloc.get("cpu", 0)), "memory": mem_bytes(alloc.get("memory", 0)),
                        "used_cpu": used_cpu, "used_memory": used_mem})
    b = max(budgets, key=lambda x: (x["cpu"] - x["used_cpu"], x["memory"] - x["used_memory"]))
    free_cpu = b["cpu"] - b["used_cpu"] - GUARD["cpu"]
    want = os.environ.get("EAS_MODEL_CPU", "")
    if want:
        if not re.fullmatch(r"[1-9][0-9]*", want):
            raise Refusal("EAS_MODEL_CPU must be a whole number of CPUs, got %r" % want)
        model_cpu = shown = int(want)
    else:
        model_cpu = min(MODEL_CPU_DEFAULT, math.floor(free_cpu + 1e-9))
        shown = model_cpu
        if model_cpu < MODEL_CPU_MIN:
            model_cpu, shown = MODEL_CPU_MIN, ">= %d" % MODEL_CPU_MIN
    need_cpu = b["used_cpu"] + GUARD["cpu"] + model_cpu
    if need_cpu > b["cpu"] + 1e-9:
        raise Refusal("%s does not fit on node %s: cpu allocatable %s, already requested %s, %s %d, %s %s "
                      "(needs %s)" % (model, b["node"], cores(b["cpu"]), cores(b["used_cpu"]), GUARD["name"],
                                      GUARD["cpu"], model, shown, cores(need_cpu)))
    need_mem = b["used_memory"] + mem_bytes(GUARD["memory"]) + mem_bytes(model_mem)
    if need_mem > b["memory"]:
        raise Refusal("%s does not fit on node %s: memory allocatable %s, already requested %s, %s %s, %s %s "
                      "from k3d-gpu fit (needs %s)" % (model, b["node"], gib(b["memory"]), gib(b["used_memory"]),
                                                       GUARD["name"], GUARD["memory"], model, model_mem, gib(need_mem)))
    say("fit on %s: cpu %s + %s %d + %s %d of %s; memory %s + %s %s + %s %s of %s" % (
        b["node"], cores(b["used_cpu"]), GUARD["name"], GUARD["cpu"], model, model_cpu, cores(b["cpu"]),
        gib(b["used_memory"]), GUARD["name"], GUARD["memory"], model, model_mem, gib(b["memory"])))

    with open(images, encoding="utf-8") as f:
        text = f.read()
    repo = re.search(r"^\s+repository:\s*\"?([^\s\"#]+)", text, re.M)
    tag = re.search(r"^\s+tag:\s*\"?([^\s\"#]+)", text, re.M)
    if not (repo and tag and re.search(r"@sha256:[0-9a-f]{64}$", tag.group(1))):
        raise Refusal("%s has no digest-pinned agent_sandbox_router repository/tag" % images)

    return {
        "schema": SCHEMA,
        "env": env_name,
        "existing_kubernetes": BASTION_KUBECONFIG,
        "istio_platform": platform,
        "istio_platform_from": platform_from,
        "cluster_cidr": cidr_s,
        "require_amx": require_amx,
        "require_amx_from": amx_from,
        "model": {"name": model, "model_id": model_id, "cpu": model_cpu, "memory": model_mem,
                  "kv_cache_gib": kv, "fit": fit.get("summary", "")},
        "guard": GUARD,
        "node": {"name": b["node"], "allocatable_cpu": cores(b["cpu"]), "requested_cpu": cores(b["used_cpu"]),
                 "allocatable_memory": gib(b["memory"]), "requested_memory": gib(b["used_memory"])},
        "agent_sandbox_router_image": "%s:%s" % (repo.group(1), tag.group(1)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kubeconfig", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env", default="local")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.env):
        print("ERROR: EAS_ENV must be a plain directory name, got %r" % a.env, file=sys.stderr)
        return 2
    try:
        doc = facts(a.kubeconfig, a.images, a.env)
    except Refusal as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

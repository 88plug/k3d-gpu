#!/usr/bin/env python3
"""Is the EAS agent stack ready to serve, and if not, why: the check `k3d-gpu eas check`
runs (through eat-check.sh EAT_MODE=eas) before it sends its first request.

  kubectl get pods,daemonsets -A -o json | eas-ready.py

Reads the List on stdin and grades the namespaces a chat through LiteLLM and a sandbox
run depend on. Every pod there (a Job's excepted: model downloads finish and stay
behind) must be Ready, and every DaemonSet there must schedule a pod and have all of
them Ready. Then: at least one Ready envoy proxy pod for eg-gateway, at least one
model pod, at most one NRI resource policy and at most one NFD worker cluster-wide.

Each problem prints with its cause where the pod status names one:
  - Pending on the AMXBF16 nodeAffinity: EI's models.yaml defaults.require_amx is true
    (model_manager/core/renderer.py:90-95 at EI 3e8a244) on a cluster with no AMX node
  - Pending on Insufficient cpu or memory: the pod's requests do not fit
  - a DaemonSet that schedules no pod: its nodeSelector matches no node
  - a pod in istio-system not Ready: istio-cni and ztunnel need the node's CNI paths
The guard model is fail-closed (EI roles/litellm/templates/qwen3guard_gen.py.j2):
while it is not Ready, LiteLLM refuses every chat request.

Exit 0 when the stack is ready, 1 with a problem, 2 on input that is not a List.
Python standard library only.
"""

import json
import sys

AMX_LABEL = "feature.node.kubernetes.io/cpu-cpuid.AMXBF16"
# What each namespace serves, for the report. EAS 97f6157 and EI 3e8a244 defaults:
# istio_namespace, the envoy gateway, the litellm role, models.yaml defaults.namespace,
# and EAT 036b662's agent sandbox.
STACK = {
    "istio-system": "Istio ambient",
    "envoy-gateway-system": "Envoy Gateway",
    "litellm": "LiteLLM",
    "llm-inference": "the models",
    "agent-sandbox-system": "the agent sandbox",
}
EG_LABEL = "gateway.envoyproxy.io/owning-gateway-name"
GUARD_PREFIX = "qwen3guard"  # EI litellm role default guardrail_model


def required_affinity_keys(spec):
    terms = (((spec.get("affinity") or {}).get("nodeAffinity") or {})
             .get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms") or []
    return {e.get("key") for t in terms for e in t.get("matchExpressions") or []}


def requests(spec):
    got = {}
    for c in spec.get("containers") or []:
        for k, v in ((c.get("resources") or {}).get("requests") or {}).items():
            got.setdefault(k, v)
    return ", ".join(f"{k} {v}" for k, v in sorted(got.items()) if k in ("cpu", "memory")) or "none"


def condition(status, kind):
    for c in status.get("conditions") or []:
        if c.get("type") == kind:
            return c
    return {}


def is_ready(pod):
    st = pod.get("status") or {}
    if st.get("phase") != "Running":
        return False
    cs = st.get("containerStatuses") or []
    return bool(cs) and all(c.get("ready") for c in cs) and condition(st, "Ready").get("status", "True") == "True"


def cause(pod):
    """(state, cause) for a pod that is not Ready."""
    ns, name = pod["metadata"]["namespace"], pod["metadata"]["name"]
    spec, st = pod.get("spec") or {}, pod.get("status") or {}
    sched = condition(st, "PodScheduled")
    if sched.get("status") == "False":
        msg = sched.get("message") or sched.get("reason") or "unschedulable"
        if AMX_LABEL in required_affinity_keys(spec) and "affinity" in msg:
            return (f"Pending: {msg}",
                    f"it needs a node labelled {AMX_LABEL}=true and none is: env/k3d-gpu/models.yaml "
                    "defaults.require_amx is true on a cluster without AMX. k3d-gpu eas up writes it from the "
                    "node labels; re-run it, or set defaults.require_amx: false there")
        short = [r for r in ("cpu", "memory") if f"Insufficient {r}" in msg]
        if short:
            return (f"Pending: {msg}",
                    f"the node lacks free {' and '.join(short)} for its requests ({requests(spec)}): "
                    "a smaller model (k3d-gpu eas up --agent-toolkit --model NAME), or free the node")
        if "selector" in msg or "affinity" in msg:
            return (f"Pending: {msg}", f"nodeSelector {json.dumps(spec.get('nodeSelector') or {})} and its "
                    "node affinity match no node")
        return (f"Pending: {msg}", "")
    for c in st.get("containerStatuses") or []:
        w = (c.get("state") or {}).get("waiting")
        if w:
            state = f"{w.get('reason', 'waiting')}: {w.get('message', '')}".rstrip(": ")
            if ns == "istio-system":
                return (state, "istio-cni and ztunnel chain into the node's CNI paths, which istio_platform "
                        "picks (k3d: /bin, k3s: /var/lib/rancher/k3s/data/cni); see choices.txt and overlay 0002")
            return (state, "")
    if st.get("phase") == "Running":
        return ("Running, readiness probe not passed yet", "a model loads for minutes: wait and re-run")
    return (st.get("phase") or "unknown phase", "")


def check(items):
    problems = []
    pods = [i for i in items if i.get("kind") == "Pod"]
    sets = [i for i in items if i.get("kind") == "DaemonSet"]
    graded = 0
    for p in pods:
        ns, name = p["metadata"]["namespace"], p["metadata"]["name"]
        if ns not in STACK or (p.get("status") or {}).get("phase") == "Succeeded":
            continue
        if any(o.get("kind") == "Job" for o in p["metadata"].get("ownerReferences") or []):
            continue
        graded += 1
        if is_ready(p):
            continue
        state, why = cause(p)
        if ns == "llm-inference" and name.startswith(GUARD_PREFIX):
            why = ("the guardrail model; it is fail-closed, so LiteLLM refuses every chat while it is not "
                   "Ready" + (". " + why if why else ""))
        problems.append(f"{ns}/{name} ({STACK[ns]}): {state}" + (f" -> {why}" if why else ""))
    for d in sets:
        ns, name = d["metadata"]["namespace"], d["metadata"]["name"]
        if ns not in STACK:
            continue
        st = d.get("status") or {}
        want, ready = st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)
        if want == 0:
            sel = ((d.get("spec") or {}).get("template") or {}).get("spec", {}).get("nodeSelector") or {}
            hint = " (EAS's EnvoyProxy selects control-plane nodes by the value \"\", k3s stamps \"true\": " \
                   "overlay 0006)" if ns == "envoy-gateway-system" else ""
            problems.append(f"{ns}/{name}: DaemonSet schedules no pod -> nodeSelector {json.dumps(sel)} "
                            f"matches no node{hint}")
        elif ready < want:
            problems.append(f"{ns}/{name}: {ready}/{want} Ready")
    if not any(p["metadata"]["namespace"] == "envoy-gateway-system"
               and (p["metadata"].get("labels") or {}).get(EG_LABEL) == "eg-gateway" and is_ready(p) for p in pods):
        problems.append("envoy-gateway-system: no Ready envoy proxy pod for eg-gateway -> the gateway has no "
                        "data plane, so nothing reaches LiteLLM")
    models = [p for p in pods if p["metadata"]["namespace"] == "llm-inference"
              and not p["metadata"]["name"].startswith(GUARD_PREFIX)
              and not any(o.get("kind") == "Job" for o in p["metadata"].get("ownerReferences") or [])]
    if not models:
        problems.append("no model pod in llm-inference -> llm_services_autodeploy_models may be [] (k3d-gpu fit "
                        "found no model that fits; see choices.txt), or the install stopped before model-manager")
    nri = sorted(f"{d['metadata']['namespace']}/{d['metadata']['name']}" for d in sets
                 if d["metadata"]["name"].startswith("nri-resource-policy-"))
    if len(nri) > 1:
        problems.append(f"{len(nri)} NRI resource policies ({', '.join(nri)}) -> one per node only: "
                        "kubernetes_cpu_policy must be best-effort beside k3d-gpu's balloons policy")
    nfd = sorted(f"{d['metadata']['namespace']}/{d['metadata']['name']}" for d in sets
                 if d["metadata"]["name"] in ("nfd-worker", "node-feature-discovery-worker"))
    if len(nfd) > 1:
        problems.append(f"{len(nfd)} NFD workers ({', '.join(nfd)}) -> nfd_enabled must be false beside "
                        "k3d-gpu's NFD")
    return graded, problems


def main():
    try:
        doc = json.load(sys.stdin)
    except ValueError as e:
        print(f"eas-ready: stdin is not JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(doc, dict) or doc.get("kind") != "List" or not isinstance(doc.get("items"), list):
        print("eas-ready: stdin is not a kubectl List (kubectl get pods,daemonsets -A -o json)", file=sys.stderr)
        return 2
    graded, problems = check(doc["items"])
    if problems:
        print(f"stack not ready ({len(problems)}):")
        for line in problems:
            print("  " + line)
        return 1
    print(f"stack ready: {graded} pods Ready in {', '.join(STACK)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

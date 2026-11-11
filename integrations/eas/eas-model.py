#!/usr/bin/env python3
"""eas-model.py: the pieces of `k3d-gpu eas model add|list|remove` that are data, not I/O.

enterprise-inference v1.1.0 (d035b1e), EAS v1.2.0's inference layer, deploys every model on
the CPU: model-manager's pod templates request CPU and memory and no device
(model_manager/templates/llm-inference-service.yaml#L26-L33), and the runtime's `device:`
plumbing key is read by no code. So a model on an Intel Arc B60/B70 is k3d-gpu's: vLLM for
Intel GPUs (the image and pod spec `k3d-gpu agent up` serves with on a B70), one card per
model, registered with EAS's LiteLLM the way model-manager registers its own models
(model_manager/core/routing.py litellm_register: POST /model/new, then GET /model/info).

  eas-model.py name <hf-id>
      The served name EI's model-manager derives from an id (core/config.py resolve: the
      repo part, non-alphanumerics to '-', lower case), as a DNS-1035 label. Exit 3 when it
      is none (pass --name).
  eas-model.py cards --host H --plugin P --nodes N --pods P --out F
      Which card the model is sized for. H is fit.py's host/v1 probe, P the Intel GPU
      plugin's DaemonSet, N `kubectl get nodes -o json`, P `kubectl get pods -A -o json`.
      Prints one `fact -> choice` line per decision; F gets H with only that card. Exit 3:
      no card can take the model, the reason on stderr.
  eas-model.py parsers --config C --model-opt M
      "<tool parser> <reasoning parser>" ('-' for none) vLLM gets for the model family of
      config.json C, from console/model_opt.py M's FAMILY_PARSERS.
  eas-model.py manifest [options]
      The Namespace, PersistentVolumeClaim, Deployment and Service, as one JSON List for
      kubectl's stdin; with $HF_TOKEN set (gated models), a Secret holding it before them.
      --wait S: the Deployment's progress deadline, the seconds add waits for the rollout.
  eas-model.py payload --name N --api-base U
      The POST /model/new body, model-manager's shape.
  eas-model.py match --name N --api-base U   (stdin: GET /model/info)
      One line per LiteLLM entry named N: "ours <id>" when its api_base is U, else
      "foreign <api_base>".
  eas-model.py list --namespace NS --deployments D   (stdin: GET /model/info)
      The models LiteLLM serves, the ones `eas model add` made marked, and its Deployments
      in NS (D: `kubectl get deployments -o json`) LiteLLM does not list.
"""
import argparse
import json
import math
import os
import re
import sys

GIB = 1024 ** 3
NAMESPACE = "k3d-gpu-eas-models"
MANAGED_BY = "k3d-gpu-eas-model"
RESOURCE = "gpu.intel.com/xe"
# fit.py's busy rule (BUSY_CARD_PCT): others hold half the card's VRAM or more.
BUSY_PCT = 0.50
DNS_1035 = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")


class Refusal(Exception):
    pass


def gib(n):
    return "%.1f GiB" % (n / GIB)


def served_name(model_id):
    """EI model-manager's name for --id (core/config.py resolve), refused when it is no DNS-1035 label."""
    name = re.sub(r"[^a-zA-Z0-9]", "-", model_id.rsplit("/", 1)[-1]).lower().strip("-")
    if not DNS_1035.match(name):
        raise Refusal("%s gives the served name '%s', which is no DNS-1035 label (a letter first, then letters, "
                      "digits and '-', at most 63): pass --name" % (model_id, name))
    return name


def quantity(v):
    """A whole-number extended-resource quantity ("1", "2"), else 0."""
    return int(v) if isinstance(v, (int, str)) and str(v).isdigit() else 0


def plugin_cards(ds):
    """(cards the Intel GPU plugin advertises, or None for all of /dev/dri; its -shared-dev-num)."""
    spec = ds["spec"]["template"]["spec"]
    shared = 1
    for c in spec.get("containers") or []:
        for a in c.get("args") or []:
            m = re.match(r"^-?-shared-dev-num=([0-9]+)$", a)
            if m:
                shared = int(m.group(1))
    cards = set()
    for v in spec.get("volumes") or []:
        p = (v.get("hostPath") or {}).get("path") or ""
        if p.rstrip("/") == "/dev/dri":
            return None, shared
        if re.match(r"^/dev/dri/card[0-9]+$", p):
            cards.add(p.rsplit("/", 1)[1])
    return cards, shared


def cards(host, ds, nodes, pods):
    """(lines, the host/v1 document with only the card to size for). Raises Refusal."""
    lines = []
    alloc = sum(quantity((n.get("status") or {}).get("allocatable", {}).get(RESOURCE)) for n in nodes.get("items") or [])
    if alloc == 0:
        raise Refusal("the cluster advertises no %s: eas model add serves on an Intel Arc card bound to xe "
                      "(B60, B70) through the Intel GPU device plugin (k3d-gpu detect; with K3D_GPU_DRA=1 "
                      "recreate the cluster with K3D_GPU_DRA=0)" % RESOURCE)
    if ds is None:
        raise Refusal("no Intel GPU device plugin (DaemonSet kube-system/intel-gpu-plugin): eas model add asks it for %s" % RESOURCE)
    advertised, shared = plugin_cards(ds)
    if shared > 1:
        raise Refusal("the Intel GPU plugin shares each card among %d pods (-shared-dev-num=%d, K3D_GPU_INTEL_SHARED): "
                      "a model needs a whole card; recreate the cluster with K3D_GPU_INTEL_SHARED=1" % (shared, shared))
    # The pods that hold an xe device, and the cards their status names: kubelet reports
    # each container's device-plugin devices (containerStatuses[].allocatedResourcesStatus,
    # ResourceHealthStatus, beta and on by default since Kubernetes 1.36), the Intel GPU
    # plugin's device ids being cardN-<i>. When every holder names its cards, the cards the
    # plugin can hand out are exactly the rest; otherwise only the count is known.
    held, named, exact = [], {}, True
    for p in pods.get("items") or []:
        st = p.get("status") or {}
        # A pod no node took yet (Pending: Insufficient memory, an unbound claim) was handed
        # no card: kubelet allocates devices when it admits the pod.
        if st.get("phase") in ("Succeeded", "Failed") or not (p.get("spec") or {}).get("nodeName"):
            continue
        k = 0
        for c in (p.get("spec") or {}).get("containers") or []:
            r = c.get("resources") or {}
            k += quantity((r.get("limits") or {}).get(RESOURCE) or (r.get("requests") or {}).get(RESOURCE))
        if not k:
            continue
        pod = "%s/%s" % (p["metadata"]["namespace"], p["metadata"]["name"])
        held.append((pod, k))
        ids = [x.get("resourceID") or "" for cs in st.get("containerStatuses") or []
               for r in cs.get("allocatedResourcesStatus") or [] if r.get("name") == RESOURCE
               for x in r.get("resources") or []]
        mine = {m.group(1) for m in (re.match(r"^(card[0-9]+)-[0-9]+$", i) for i in ids) if m}
        # The plugin shares no card here (refused above), so k devices are k cards.
        if len(mine) != k:
            exact = False
        for c in mine:
            named[c] = pod
    taken = sum(k for _, k in held)
    who = ", ".join("%s (%d)" % h for h in held) or "none"
    lines.append("%s: %d advertised, %d held by pods (%s) -> %d free" % (RESOURCE, alloc, taken, who, alloc - taken))
    if alloc - taken < 1:
        raise Refusal("every %s the cluster advertises is held by a pod: %s; remove one (k3d-gpu eas model "
                      "remove NAME, or what else holds it) first" % (RESOURCE, who))
    xe = [g for g in host.get("gpus") or [] if g.get("driver") == "xe"]
    seen = [g for g in xe if advertised is None or g["card"] in advertised]
    lines.append("cards the plugin advertises -> %s" % (
        "all of /dev/dri" if advertised is None else " ".join(sorted(advertised)) + " (k3d-gpu up left the others out: busy then)"))
    if not seen:
        raise Refusal("this host's probe sees no xe card with device memory that the plugin advertises (probed: %s): "
                      "eas model add runs on the host the cluster's cards are in" % (" ".join(g["card"] for g in xe) or "none"))
    # Each advertised device must be a card the fit can size: an integrated GPU on xe, a card
    # whose VRAM the probe cannot read, or a card advertised by two nodes could be handed out.
    if alloc > len(seen):
        raise Refusal("the cluster advertises %d %s and this host's probe sizes %d of those cards (%s): the plugin "
                      "could hand this model one the fit cannot size (an integrated GPU on xe, a card whose VRAM the "
                      "probe cannot read: k3d-gpu fit --explain lists the cards it reads, or a card two nodes advertise)"
                      % (alloc, RESOURCE, len(seen), " ".join(g["card"] for g in seen)))
    busy, idle = [], []
    for g in seen:
        users = ", ".join("%s[%d] %s" % (u["comm"], u["pid"], gib(u["bytes"])) for u in (g.get("users") or [])[:3])
        state = "busy" if g["vram_used"] >= g["vram_total"] * BUSY_PCT else "idle"
        (busy if state == "busy" else idle).append(g)
        if exact and g["card"] in named:
            state = "held by " + named[g["card"]]
        lines.append("%s (%s): VRAM %s, in use %s%s -> %s" % (g["card"], g["bdf"], gib(g["vram_total"]), gib(g["vram_used"]),
                                                              " by " + users if users else "", state))
    remedy = ("Stop what holds it, or recreate the cluster (k3d-gpu down && k3d-gpu up leaves a busy card out of "
              "the plugin)")
    if exact:
        free = [g for g in seen if g["card"] not in named]
        lines.append("cards no pod holds -> %s (from the pods' status)" % (" ".join(g["card"] for g in free) or "none"))
        out_busy = [g for g in free if g in busy]
        if out_busy:
            one = len(out_busy) == 1
            raise Refusal("%s %s busy and no pod holds %s: %s held outside the cluster, and the device plugin could "
                          "hand %s to this model. %s"
                          % (", ".join("%s (%s)" % (g["card"], g["bdf"]) for g in out_busy), "is" if one else "are",
                             "it" if one else "them", "it is" if one else "they are", "it" if one else "one", remedy))
        if not free:
            raise Refusal("every advertised card is held by a pod: %s" % who)
        pick = min(free, key=lambda g: (g["vram_total"] - g["vram_used"], g["card"]))
        lines.append("fit -> sized for %s, the card no pod holds with the least free VRAM (%s): the plugin may hand "
                     "this model any of them" % (pick["card"], gib(pick["vram_total"] - pick["vram_used"])))
    else:
        # A pod holding a card is assumed to use it, so the busy cards past the held count are
        # held by something else, and the plugin could hand one to this model. A pod that has
        # not filled half its card yet hides one busy card from this count.
        lines.append("not every pod's status names its card (Kubernetes before 1.36, or a pod kubelet has not reported yet): "
                     "the count decides")
        if len(busy) > taken:
            raise Refusal("%d advertised card(s) are busy (%s) and pods hold %d: at least one is held outside the "
                          "cluster, and the device plugin could hand it to this model. %s"
                          % (len(busy), ", ".join("%s (%s)" % (g["card"], g["bdf"]) for g in busy), taken, remedy))
        if not idle:
            raise Refusal("no advertised card is idle")
        pick = min(idle, key=lambda g: (g["vram_total"] - g["vram_used"], g["card"]))
        lines.append("fit -> sized for %s, the idle card with the least free VRAM (%s): the plugin may hand this "
                     "model any idle card" % (pick["card"], gib(pick["vram_total"] - pick["vram_used"])))
    out = dict(host)
    out["gpus"] = [pick]
    return lines, out


def parsers(config, family_parsers):
    mt = config.get("model_type") or (config.get("text_config") or {}).get("model_type") or ""
    tool, reasoning = (family_parsers.get(mt) or {}).get("vllm") or (None, None)
    return tool or "-", reasoning or "-"


def manifest(a):
    labels = {"app.kubernetes.io/name": a.name, "app.kubernetes.io/managed-by": MANAGED_BY}
    args = [a.model, "--revision=" + a.revision, "--served-model-name=" + a.name, "--port=8000",
            # The flags `k3d-gpu agent up` serves vLLM with on an Intel GPU (vllm_manifest), from
            # Intel's Arc runtime: float16, 64-token blocks, eager mode, no prefix caching.
            "--dtype=float16", "--block-size=64", "--max-model-len=%d" % a.ctx, "--max-num-batched-tokens=2048",
            "--gpu-memory-utilization=" + a.util, "--enforce-eager", "--no-enable-prefix-caching",
            "--max-num-seqs=%d" % a.seqs]
    if a.kv_dtype:
        args.append("--kv-cache-dtype=" + a.kv_dtype)
    args.append("--tensor-parallel-size=1")
    if a.tool_parser != "-":
        args += ["--enable-auto-tool-choice", "--tool-call-parser=" + a.tool_parser]
    if a.reasoning_parser != "-":
        args.append("--reasoning-parser=" + a.reasoning_parser)
    env = [{"name": k, "value": v} for k, v in (e.split("=", 1) for e in a.env)]
    # Gated models: the token from Secret hf-token, when HF_TOKEN made one.
    env.append({"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {"name": "hf-token", "key": "token", "optional": True}}})
    pod = {
        # No Service environment variables: a Service named like vLLM's own variables
        # (VLLM_PORT) stops its engine core (k3d-gpu #339).
        "enableServiceLinks": False,
        "containers": [{
            "name": "vllm", "image": a.image, "imagePullPolicy": "IfNotPresent", "args": args, "env": env,
            "ports": [{"name": "http", "containerPort": 8000}],
            # No liveness probe: the first start downloads the weights and compiles kernels.
            "readinessProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 5},
            "resources": {"requests": {"cpu": a.cpus, "memory": a.memory},
                          "limits": {"memory": a.memory, RESOURCE: "1"}},
            "volumeMounts": [{"name": "models", "mountPath": "/models"}, {"name": "shm", "mountPath": "/dev/shm"}]}],
        "volumes": [{"name": "models", "persistentVolumeClaim": {"claimName": a.name + "-models"}},
                    {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}}]}
    if a.gid:
        pod["securityContext"] = {"supplementalGroups": [int(a.gid)]}
    ann = {"k3d-gpu.io/model": a.model, "k3d-gpu.io/revision": a.revision, "k3d-gpu.io/agent-fit": a.fit}
    meta = {"name": a.name, "namespace": NAMESPACE, "labels": labels}
    storage = max(20, math.ceil(a.weight_bytes / GIB) + 10)
    items = [{"apiVersion": "v1", "kind": "Namespace",
              "metadata": {"name": NAMESPACE, "labels": {"app.kubernetes.io/managed-by": MANAGED_BY}}}]
    token = os.environ.get("HF_TOKEN") or ""
    if token:
        items.append({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                      "metadata": {"name": "hf-token", "namespace": NAMESPACE,
                                   "labels": {"app.kubernetes.io/managed-by": MANAGED_BY}},
                      "stringData": {"token": token}})
    items += [
        {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": dict(meta, name=a.name + "-models"),
         "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "%dGi" % storage}}}},
        # kubectl rollout status stops at the progress deadline (600 s unless set) whatever
        # its --timeout, and a first start pulls the image, downloads the weights and
        # compiles kernels: the deadline is the wait add allows.
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": dict(meta, annotations=ann),
         "spec": {"replicas": 1, "progressDeadlineSeconds": a.wait, "strategy": {"type": "Recreate"},
                  "selector": {"matchLabels": labels},
                  "template": {"metadata": {"labels": labels, "annotations": ann}, "spec": pod}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": meta,
         "spec": {"selector": labels, "ports": [{"name": "http", "port": 8000, "targetPort": "http"}]}},
    ]
    return {"apiVersion": "v1", "kind": "List", "items": items}


def model_info(text):
    """GET /model/info's entries. LiteLLM v1.84.0 answers 500 'LLM Model List not loaded in'
    when it has none."""
    d = json.loads(text)
    if isinstance(d, dict) and "data" in d:
        return d["data"] or []
    if "Model List not loaded" in json.dumps(d):
        return []
    raise ValueError("GET /model/info: no data in %s" % text[:200])


def api_base(entry):
    return (entry.get("litellm_params") or {}).get("api_base") or ""


def main(argv):
    ap = argparse.ArgumentParser(prog="eas-model.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("name")
    n.add_argument("model")
    c = sub.add_parser("cards")
    for k in ("--host", "--plugin", "--nodes", "--pods", "--out"):
        c.add_argument(k, required=True)
    p = sub.add_parser("parsers")
    p.add_argument("--config", required=True)
    p.add_argument("--model-opt", required=True)
    m = sub.add_parser("manifest")
    for k in ("--name", "--model", "--revision", "--image", "--util", "--memory", "--cpus", "--fit"):
        m.add_argument(k, required=True)
    m.add_argument("--ctx", type=int, required=True)
    m.add_argument("--seqs", type=int, required=True)
    m.add_argument("--wait", type=int, required=True)
    m.add_argument("--weight-bytes", type=int, default=0)
    m.add_argument("--kv-dtype", default="")
    m.add_argument("--gid", default="")
    m.add_argument("--tool-parser", default="-")
    m.add_argument("--reasoning-parser", default="-")
    m.add_argument("--env", action="append", default=[])
    pl = sub.add_parser("payload")
    pl.add_argument("--name", required=True)
    pl.add_argument("--api-base", required=True)
    mt = sub.add_parser("match")
    mt.add_argument("--name", required=True)
    mt.add_argument("--api-base", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--namespace", default=NAMESPACE)
    ls.add_argument("--deployments", required=True)
    a = ap.parse_args(argv[1:])
    try:
        if a.cmd == "name":
            print(served_name(a.model))
        elif a.cmd == "cards":
            docs = []
            for f in (a.host, a.plugin, a.nodes, a.pods):
                with open(f, encoding="utf-8") as fh:
                    text = fh.read()
                docs.append(json.loads(text) if text.strip() else None)
            lines, out = cards(docs[0], docs[1], docs[2] or {}, docs[3] or {})
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, sort_keys=True)
            print("\n".join(lines))
        elif a.cmd == "parsers":
            import importlib.util
            spec = importlib.util.spec_from_file_location("model_opt", a.model_opt)
            mo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mo)
            with open(a.config, encoding="utf-8") as fh:
                print("%s %s" % parsers(json.load(fh), mo.FAMILY_PARSERS))
        elif a.cmd == "manifest":
            for v in [a.name, a.model, a.revision, a.image, a.util, a.memory, a.cpus, a.kv_dtype, a.gid,
                      a.tool_parser, a.reasoning_parser] + a.env:
                if re.search(r"[\s\"'\\]", v):
                    raise Refusal("manifest value %r has a space, quote or backslash" % v)
            if not DNS_1035.match(a.name):
                raise Refusal("'%s' is no DNS-1035 label" % a.name)
            if a.wait < 1:
                raise Refusal("--wait must be a positive number of seconds, got %d" % a.wait)
            if "@sha256:" not in a.image:
                raise Refusal("image %s is not pinned by digest" % a.image)
            json.dump(manifest(a), sys.stdout, indent=2)
            print()
        elif a.cmd == "payload":
            # model_manager/core/routing.py litellm_register, for a chat model.
            print(json.dumps({"model_name": a.name, "litellm_params": {
                "model": "openai/" + a.name, "api_base": a.api_base, "api_key": "unused"}}))
        elif a.cmd == "match":
            for e in model_info(sys.stdin.read()):
                if e.get("model_name") != a.name:
                    continue
                if api_base(e) == a.api_base:
                    print("ours %s" % ((e.get("model_info") or {}).get("id") or ""))
                else:
                    print("foreign %s" % (api_base(e) or "(no api_base)"))
        elif a.cmd == "list":
            entries = model_info(sys.stdin.read())
            with open(a.deployments, encoding="utf-8") as fh:
                deps = {d["metadata"]["name"]: d for d in (json.load(fh).get("items") or [])}
            rows, listed = [], set()
            for e in sorted(entries, key=lambda e: e.get("model_name") or ""):
                name, base = e.get("model_name") or "?", api_base(e)
                ours = name in deps and base == "http://%s.%s.svc.cluster.local:8000/v1" % (name, a.namespace)
                if ours:
                    listed.add(name)
                    d = deps[name]
                    ann = d["metadata"].get("annotations") or {}
                    ready = (d.get("status") or {}).get("readyReplicas") or 0
                    rows.append((name, "k3d-gpu", "%s@%s" % (ann.get("k3d-gpu.io/model", "?"), ann.get("k3d-gpu.io/revision", "?")[:12]),
                                 "ready" if ready else "not ready", ann.get("k3d-gpu.io/agent-fit", "")))
                else:
                    rows.append((name, "eas", base or "-", "", ""))
            for name in sorted(set(deps) - listed):
                ann = deps[name]["metadata"].get("annotations") or {}
                rows.append((name, "k3d-gpu", "%s@%s" % (ann.get("k3d-gpu.io/model", "?"), ann.get("k3d-gpu.io/revision", "?")[:12]),
                             "NOT REGISTERED in LiteLLM", ""))
            print("%-28s %-8s %-56s %s" % ("NAME", "BY", "MODEL / API_BASE", "STATE"))
            for r in rows:
                print(("%-28s %-8s %-56s %s %s" % r).rstrip())
    except Refusal as e:
        sys.stderr.write("%s\n" % e)
        return 3
    except (OSError, ValueError, KeyError, TypeError) as e:
        sys.stderr.write("eas-model.py %s: %s: %s\n" % (a.cmd, type(e).__name__, e))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

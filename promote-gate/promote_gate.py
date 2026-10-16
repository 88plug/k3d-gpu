#!/usr/bin/env python3
"""promote-gate: check a release tested on a k3d-gpu laptop against a prod Xeon twin.

A twin is a directory holding the sysfs topology files listed in capture-globs.txt,
a twin.yaml with the node's allocatable, and optionally the prod BalloonsPolicy CR.

Subcommands
  capture  <sysroot> <out>              record a twin from a host root (/ or a sysfs tree)
  meta     <twin>                       write twin.yaml for a tree fetched by twin-capture.yml
  project  --map M --prod-twin P --dev-twin D <prod.yaml>
                                        print the dev release: the prod release with
                                        resources scaled by the twins' allocatable ratio
                                        (clamped at per-component memory floors) and
                                        balloon labels mapped through the map's table
  certify  --map M --prod-twin P --dev-twin D <prod.yaml> <dev.yaml>
                                        recompute the projection from prod.yaml and require
                                        dev.yaml to equal it (quantities compared by value)
  replay   --twin P [--policy B] [--orders N] <prod.yaml>
                                        replay pod creation against the twin with a reduced
                                        model of the nri-plugins balloons policy; predict
                                        balloon cpusets and NUMA spans; report findings
  check    (certify + replay; exit 1 on any violation or finding)

The replay is a model of the balloons policy, not the upstream engine. What it
models and what it leaves out is listed in model_assumptions().
"""
import argparse
import copy
import glob
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from fractions import Fraction

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "Pod")

# --------------------------------------------------------------------------- quantities
_SUFFIX = {
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60,
    "n": Fraction(1, 10**9), "u": Fraction(1, 10**6), "m": Fraction(1, 1000), "": 1,
    "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15, "E": 10**18,
}
_QTY = re.compile(r"^([+-]?[0-9]*\.?[0-9]+)(?:[eE]([+-]?[0-9]+))?([a-zA-Z]*)$")


def qty(v):
    """Kubernetes quantity -> exact Fraction of the base unit (cores or bytes)."""
    m = _QTY.match(str(v).strip())
    if not m or m.group(3) not in _SUFFIX:
        raise ValueError(f"bad quantity {v!r}")
    n = Fraction(m.group(1))
    if m.group(2):
        n *= Fraction(10) ** int(m.group(2))
    return n * _SUFFIX[m.group(3)]


def fmt_cpu(f):
    return f"{max(1, math.ceil(f * 1000))}m"


def fmt_mem(f):
    return f"{math.ceil(f / 2**20)}Mi"


# --------------------------------------------------------------------------- cpu lists
def parse_list(s):
    out = []
    for part in s.strip().split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def fmt_list(cpus):
    cpus = sorted(cpus)
    runs, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        runs.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(runs)


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


# --------------------------------------------------------------------------- twin capture
def capture_globs():
    with open(os.path.join(HERE, "capture-globs.txt")) as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def capture(sysroot, out, allocatable=None):
    """Copy the capture-globs files from sysroot into out and write twin.yaml."""
    n = 0
    for pattern in capture_globs():
        for src in sorted(glob.glob(os.path.join(sysroot, pattern))):
            rel = os.path.relpath(src, sysroot)
            dst = os.path.join(out, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            text = _read(src) or ""
            if rel.endswith("/meminfo"):  # keep the size, drop the moving counters
                text = "\n".join(ln for ln in text.splitlines() if "MemTotal:" in ln)
            with open(dst, "w") as f:
                f.write(text + "\n")
            n += 1
    return write_meta(out, allocatable, n)


def write_meta(out, allocatable=None, files=None):
    """twin.yaml for a twin tree; allocatable from kubectl if given, else from sysfs."""
    topo = Topology(out, need_meta=False)
    meta = {
        "allocatable": allocatable or {
            "cpu": str(len(topo.cpus)),
            "memory": f"{sum(nd['mem'] for nd in topo.nodes.values()) // 2**20}Mi",
        },
        "allocatableSource": "kubectl" if allocatable else "derived-from-sysfs",
    }
    if files is not None:
        meta["files"] = files
    with open(os.path.join(out, "twin.yaml"), "w") as f:
        yaml.safe_dump(meta, f, sort_keys=True)
    return meta


def file_hash(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree_hash(d):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(d)):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            h.update(os.path.relpath(p, d).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


# --------------------------------------------------------------------------- topology
class Topology:
    """Read-only view of a twin directory. Everything comes from files on disk."""

    def __init__(self, twin, need_meta=True):
        self.dir = twin
        sysd = os.path.join(twin, "sys/devices/system")
        self.cpus = {}
        for c in parse_list(_read(os.path.join(sysd, "cpu/online"), "")):
            t = os.path.join(sysd, f"cpu/cpu{c}/topology")
            self.cpus[c] = {
                "pkg": int(_read(f"{t}/physical_package_id", "0")),
                "die": int(_read(f"{t}/die_id", "0")),
                "core": frozenset(parse_list(_read(f"{t}/thread_siblings_list", str(c)))),
                "node": 0,
                "type": None,
            }
        node_ids = sorted(int(os.path.basename(p)[4:])
                          for p in glob.glob(os.path.join(sysd, "node/node[0-9]*")))
        if not node_ids:
            node_ids = [0]
        self.nodes = {}
        for i, nid in enumerate(node_ids):
            nd = os.path.join(sysd, f"node/node{nid}")
            cl = _read(f"{nd}/cpulist")
            cpus = set(parse_list(cl)) if cl is not None else set(self.cpus)
            dist = [int(x) for x in (_read(f"{nd}/distance") or "10").split()]
            mem = 0
            for line in (_read(f"{nd}/meminfo") or "").splitlines():
                if "MemTotal:" in line:
                    mem = int(line.split()[-2]) * 1024
            self.nodes[nid] = {"cpus": cpus & set(self.cpus), "mem": mem,
                               "dist": dict(zip(node_ids, dist))}
            for c in cpus & set(self.cpus):
                self.cpus[c]["node"] = nid
        for kind, path in (("performance", "cpu_core"), ("efficient", "cpu_atom")):
            for c in parse_list(_read(os.path.join(twin, f"sys/devices/{path}/cpus"), "")):
                if c in self.cpus:
                    self.cpus[c]["type"] = kind
        self.hybrid = any(v["type"] for v in self.cpus.values())
        self.meta = {}
        if need_meta:
            with open(os.path.join(twin, "twin.yaml")) as f:
                self.meta = yaml.safe_load(f)

    def allocatable(self):
        a = self.meta["allocatable"]
        return qty(a["cpu"]), qty(a["memory"])

    def distance(self, a, b):
        return self.nodes[a]["dist"].get(b, 10 if a == b else 20)


# --------------------------------------------------------------------------- releases
def load_docs(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def _pod_spec(doc):
    return doc["spec"] if doc["kind"] == "Pod" else doc["spec"]["template"]["spec"]


def _pod_meta(doc):
    return doc["metadata"] if doc["kind"] == "Pod" else doc["spec"]["template"].setdefault("metadata", {})


def workloads(docs):
    return [d for d in docs if d.get("kind") in WORKLOAD_KINDS]


def expand_pods(docs):
    """Pods in apply order: documents in file order, replicas in index order."""
    pods = []
    for d in workloads(docs):
        reps = 1 if d["kind"] == "Pod" else int(d["spec"].get("replicas", 1))
        cpu = mem = Fraction(0)
        for c in _pod_spec(d).get("containers", []):
            req = (c.get("resources") or {}).get("requests") or {}
            cpu += qty(req.get("cpu", 0))
            mem += qty(req.get("memory", 0))
        for i in range(reps):
            pods.append({
                "name": f"{d['metadata']['name']}-{i}",
                "workload": d["metadata"]["name"],
                "namespace": d["metadata"].get("namespace", "default"),
                "labels": dict(_pod_meta(d).get("labels") or {}),
                "cpu": cpu, "mem": mem,
            })
    return pods


# --------------------------------------------------------------------------- projection
def load_map(path):
    with open(path) as f:
        return yaml.safe_load(f)


def ratios(pmap, prod, dev):
    pc, pm = prod.allocatable()
    dc, dm = dev.allocatable()
    r = pmap.get("ratio") or {}
    cpu = Fraction(str(r["cpu"])) if r.get("cpu", "auto") != "auto" else min(Fraction(1), dc / pc)
    mem = Fraction(str(r["memory"])) if r.get("memory", "auto") != "auto" else min(Fraction(1), dm / pm)
    return cpu, mem


def project(prod_docs, pmap, cpu_r, mem_r):
    """Dev release = prod release with resources scaled and balloon labels mapped.
    Returns (dev_docs, errors). Touches only the paths named in allowlist()."""
    docs, errors = copy.deepcopy(prod_docs), []
    floors = pmap.get("floors") or {}
    table = pmap.get("labelTable") or {}
    for d in workloads(docs):
        name = d["metadata"]["name"]
        floor = floors.get(name, {})
        for c in _pod_spec(d).get("containers", []):
            res = c.get("resources") or {}
            for kind in ("requests", "limits"):
                q = res.get(kind) or {}
                if "cpu" in q:
                    q["cpu"] = fmt_cpu(qty(q["cpu"]) * cpu_r)
                if "memory" in q:
                    want = qty(q["memory"]) * mem_r
                    if "memory" in floor:
                        f = qty(floor["memory"])
                        if f > qty(q["memory"]):
                            errors.append(f"{name}/{c['name']}: memory floor {floor['memory']} "
                                          f"above prod {kind} {q['memory']}")
                        want = max(want, f)
                    q["memory"] = fmt_mem(want)
        labels = _pod_meta(d).setdefault("labels", {})
        if table and table["fromKey"] in labels:
            v = labels.pop(table["fromKey"])
            if v not in table["map"]:
                errors.append(f"{name}: balloon label {table['fromKey']}={v} has no entry in the label table")
            elif table["map"][v] is not None:
                labels[table["toKey"]] = table["map"][v]
    return docs, errors


def allowlist(pmap):
    table = pmap.get("labelTable") or {}
    pats = [r"^(spec\.template\.)?spec\.containers\[[^]]+\]\.resources\.(requests|limits)\.(cpu|memory)$"]
    for k in (table.get("fromKey"), table.get("toKey")):
        if k:
            pats.append(r"^(spec\.template\.)?metadata\.labels\." + re.escape(k) + "$")
    return [re.compile(p) for p in pats]


def _walk(a, b, path, out, canonical):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a or k not in b:
                out.append((p, a.get(k), b.get(k)))
            else:
                _walk(a[k], b[k], p, out, canonical)
    elif isinstance(a, list) and isinstance(b, list):
        named = all(isinstance(x, dict) and "name" in x for x in a + b)
        if named:
            _walk({x["name"]: x for x in a}, {x["name"]: x for x in b}, path, out, canonical)
            return
        if len(a) != len(b):
            out.append((path, a, b))
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]", out, canonical)
    else:
        if canonical and path.split(".")[-1] in ("cpu", "memory") and ".resources." in path:
            try:
                if qty(a) == qty(b):
                    return
            except (ValueError, TypeError):
                pass
        if a != b:
            out.append((path, a, b))


def _container_path(path):
    # containers are compared by name: spec.template.spec.containers.<name>.x -> containers[<name>].x
    return re.sub(r"\.containers\.([^.]+)", r".containers[\1]", path)


def _doc_key(d):
    m = d.get("metadata", {})
    return (d.get("kind"), m.get("namespace", ""), m.get("name"))


def diff_docs(a_docs, b_docs, canonical=True):
    """Structural diff of two releases, documents keyed by kind/namespace/name."""
    key = _doc_key
    A, B = {key(d): d for d in a_docs}, {key(d): d for d in b_docs}
    out = []
    for k in sorted(set(A) | set(B), key=str):
        if k not in A or k not in B:
            out.append(("/".join(map(str, k)), "<document>", A.get(k) is not None, B.get(k) is not None))
            continue
        diffs = []
        _walk(A[k], B[k], "", diffs, canonical)
        out.extend(("/".join(map(str, k)), _container_path(p), x, y) for p, x, y in diffs)
    return out


def certify(prod_docs, dev_docs, pmap, cpu_r, mem_r, canonical=True):
    """Violations of: dev == project(prod), and project() only touched allowlisted paths."""
    expected, errors = project(prod_docs, pmap, cpu_r, mem_r)
    violations = [{"kind": "projection-error", "detail": e} for e in errors]
    allow = allowlist(pmap)
    for doc, path, _, _ in diff_docs(prod_docs, expected):
        if not any(p.match(path) for p in allow):
            violations.append({"kind": "projection-outside-allowlist", "doc": doc, "path": path})
    for doc, path, want, got in diff_docs(expected, dev_docs, canonical):
        violations.append({"kind": "dev-differs-from-projection", "doc": doc, "path": path,
                           "expected": want, "got": got})
    return violations


def baseline_ignore_diff(prod_docs, dev_docs, pmap):
    """What a path-filtered diff (dyff/kubectl diff with an ignore list) reports:
    every difference outside the resource and balloon-label paths, values on those
    paths ignored."""
    allow = allowlist(pmap)
    return [d for d in diff_docs(prod_docs, dev_docs, canonical=False)
            if not any(p.match(d[1]) for p in allow)]


# --------------------------------------------------------------------------- balloons replay
def model_assumptions():
    return [
        "balloon type = first balloonType whose matchExpressions all match the pod "
        "(keys pod/labels/<k>, namespace, name; Equals NotEqual In NotIn Exists NotExist AlwaysTrue); "
        "otherwise the built-in default balloon",
        "pods in reservedPoolNamespaces run on the reserved CPUs and are not placed",
        "balloon size = max(minCPUs, ceil(sum of member pods' CPU requests)); a pod joins an "
        "existing balloon of its type unless preferNewBalloons, the add would pass maxCPUs, "
        "and maxBalloons still allows a new one",
        "a new balloon is homed on the NUMA node with the most free CPUs of its preferred core "
        "type (ties: lowest node id): allocatorTopologyBalancing",
        "growth takes free CPUs by (NUMA distance from home, completes a partly used core, "
        "whole free core, preferred core type, cpu id)",
        "reserved CPUs: reservedResources.cpu taken as whole cores from node 0 upward",
        "pod memory is charged to the balloon's home node; overflow spills to other nodes",
        "NOT modelled: pre-existing containers, balloon shrink on pod delete, idle-CPU sharing, "
        "kubelet CPU manager, CPU-allocator priority, cache/L3 grouping, SNC sub-NUMA via die_id "
        "only through NUMA nodes",
    ]


def _match_expr(e, pod):
    key, op, vals = e["key"], e["operator"], e.get("values") or []
    if op == "AlwaysTrue":
        return True
    if key.startswith("pod/labels/"):
        lk = key[len("pod/labels/"):]
        present, v = lk in pod["labels"], pod["labels"].get(lk)
    elif key == "namespace":
        present, v = True, pod["namespace"]
    elif key == "name":
        present, v = True, pod["name"]
    else:
        present, v = False, None
    return {
        "Equals": present and v == (vals[0] if vals else None),
        "NotEqual": not (present and v == (vals[0] if vals else None)),
        "In": present and v in vals,
        "NotIn": not (present and v in vals),
        "Exists": present,
        "NotExist": not present,
    }.get(op, False)


def match_type(policy, pod):
    for bt in policy.get("balloonTypes") or []:
        if bt.get("namespaces") and pod["namespace"] not in bt["namespaces"]:
            continue
        if all(_match_expr(e, pod) for e in bt.get("matchExpressions") or []):
            return bt
    return {"name": "default"}


def load_policy(path):
    for d in load_docs(path):
        for item in d.get("items", [d]) if d.get("kind") == "List" else [d]:
            if item.get("kind") == "BalloonsPolicy":
                return item["spec"]
    raise ValueError(f"{path}: no BalloonsPolicy document")


def _reserve(topo, n):
    """Whole cores from the lowest node and cpu ids until at least n CPUs are reserved."""
    got = set()
    for c in sorted(topo.cpus, key=lambda c: (topo.cpus[c]["node"], c)):
        if len(got) >= n:
            break
        got |= topo.cpus[c]["core"] & set(topo.cpus)
    return got


def replay_once(topo, policy, pods, gate):
    findings, alloc_cpu, alloc_mem = [], *topo.allocatable()
    used_cpu = used_mem = Fraction(0)
    resv = qty((policy.get("reservedResources") or {}).get("cpu", "0"))
    free = set(topo.cpus) - _reserve(topo, math.ceil(resv))
    node_mem = {n: v["mem"] for n, v in topo.nodes.items()}
    balloons, warned = [], set()
    strict = set(gate.get("strictNumaBalloonTypes") or [])
    label_key = gate.get("balloonLabelKey")

    for pod in pods:
        # scheduler-level feasibility: what a KWOK twin with the same allocatable reports
        if used_cpu + pod["cpu"] > alloc_cpu or used_mem + pod["mem"] > alloc_mem:
            findings.append({"kind": "UNSCHEDULABLE", "pod": pod["name"], "level": "scheduler"})
            continue
        used_cpu += pod["cpu"]
        used_mem += pod["mem"]
        if pod["namespace"] in (policy.get("reservedPoolNamespaces") or []):
            continue
        bt = match_type(policy, pod)
        if label_key and label_key in pod["labels"] and not any(
                label_key in (e.get("key") or "") for e in bt.get("matchExpressions") or []):
            findings.append({"kind": "NO_SUCH_BALLOON_TYPE", "pod": pod["name"],
                             "detail": f"{label_key}={pod['labels'][label_key]} landed in {bt['name']}"})
        ctype = bt.get("preferCoreType")
        if ctype and not any(v["type"] == ctype for v in topo.cpus.values()) and bt["name"] not in warned:
            warned.add(bt["name"])
            findings.append({"kind": "CORE_TYPE_UNSAT", "balloonType": bt["name"],
                             "detail": f"preferCoreType {ctype}: twin has no {ctype} cores"})
        mine = [b for b in balloons if b["type"] == bt["name"]]
        maxc = bt.get("maxCPUs") or 0
        cand = [b for b in mine
                if not bt.get("preferNewBalloons")
                and (not maxc or math.ceil(b["req"] + pod["cpu"]) <= maxc)]
        if cand:
            b = cand[0]
        elif not bt.get("maxBalloons") or len(mine) < bt["maxBalloons"]:
            def free_on(n):
                return sum(1 for c in topo.nodes[n]["cpus"] & free
                           if not ctype or topo.cpus[c]["type"] in (ctype, None))
            home = max(sorted(topo.nodes), key=lambda n: (free_on(n), -n))
            b = {"type": bt["name"], "id": f"{bt['name']}[{len(mine)}]", "home": home,
                 "cpus": set(), "req": Fraction(0), "pods": [], "mem": {}}
            balloons.append(b)
        else:
            b = mine[0]
        b["req"] += pod["cpu"]
        b["pods"].append(pod["name"])
        need = max(int(bt.get("minCPUs") or 0), math.ceil(b["req"]))
        if maxc and need > maxc:
            findings.append({"kind": "MAX_CPUS_EXCEEDED", "balloon": b["id"],
                             "detail": f"needs {need} > maxCPUs {maxc}"})
        while len(b["cpus"]) < need:
            if not free:
                findings.append({"kind": "BALLOON_CPU_EXHAUSTED", "balloon": b["id"], "pod": pod["name"],
                                 "detail": f"needs {need}, has {len(b['cpus'])}, no free CPUs"})
                break
            c = min(free, key=lambda c: (
                topo.distance(b["home"], topo.cpus[c]["node"]),
                0 if topo.cpus[c]["core"] & b["cpus"] else 1,
                0 if topo.cpus[c]["core"] <= free else 1,
                0 if not ctype or topo.cpus[c]["type"] == ctype else 1,
                c))
            free.discard(c)
            b["cpus"].add(c)
        # memory: home node first, then nearest nodes
        left = pod["mem"]
        for n in sorted(topo.nodes, key=lambda n: (topo.distance(b["home"], n), n)):
            take = min(left, Fraction(node_mem[n]))
            if take > 0:
                node_mem[n] -= take
                b["mem"][n] = b["mem"].get(n, 0) + take
                left -= take
            if left <= 0:
                break

    for b in balloons:
        nodes = sorted({topo.cpus[c]["node"] for c in b["cpus"]})
        b["numaNodes"] = nodes
        b["memNodes"] = sorted(b["mem"])
        if b["type"] in strict and len(nodes) > 1:
            findings.append({"kind": "CROSS_NUMA_CPU", "balloon": b["id"], "detail": f"cpus on nodes {nodes}"})
        if b["type"] in strict and len(b["memNodes"]) > 1:
            findings.append({"kind": "CROSS_NUMA_MEM", "balloon": b["id"],
                             "detail": f"memory on nodes {b['memNodes']}"})
    return findings, balloons


def replay(topo, policy, pods, gate, orders=0, seed=0):
    """Replay in apply order, then in `orders` seeded random orders. A finding that shows up
    in some orders and not others is reported once more as ORDER_SENSITIVE."""
    findings, balloons = replay_once(topo, policy, pods, gate)
    first = {f["kind"] for f in findings}
    rng, extra = random.Random(seed), set()
    for _ in range(orders):
        perm = pods[:]
        rng.shuffle(perm)
        fs, _ = replay_once(topo, policy, perm, gate)
        extra |= {f["kind"] for f in fs} - first
    for k in sorted(extra):
        findings.append({"kind": "ORDER_SENSITIVE", "detail": f"{k} appears under another apply order"})
    return findings, balloons


# --------------------------------------------------------------------------- cli
def _dump(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promote-gate", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("sysroot")
    c.add_argument("out")
    m = sub.add_parser("meta")
    m.add_argument("twin")
    for name in ("project", "certify", "check"):
        p = sub.add_parser(name)
        p.add_argument("--map", required=True)
        p.add_argument("--prod-twin", required=True)
        p.add_argument("--dev-twin", required=True)
        p.add_argument("prod")
        if name != "project":
            p.add_argument("dev")
        if name == "check":
            p.add_argument("--policy")
            p.add_argument("--orders", type=int, default=20)
    r = sub.add_parser("replay")
    r.add_argument("--twin", required=True)
    r.add_argument("--policy")
    r.add_argument("--map")
    r.add_argument("--orders", type=int, default=20)
    r.add_argument("prod")
    a = ap.parse_args(argv)

    if a.cmd == "capture":
        _dump(capture(a.sysroot, a.out))
        return 0
    if a.cmd == "meta":
        _dump(write_meta(a.twin))
        return 0
    t0 = time.monotonic()
    report, failed = {}, False
    if a.cmd in ("project", "certify", "check"):
        pmap = load_map(a.map)
        prod_t, dev_t = Topology(a.prod_twin), Topology(a.dev_twin)
        cpu_r, mem_r = ratios(pmap, prod_t, dev_t)
        prod = load_docs(a.prod)
        if a.cmd == "project":
            docs, errors = project(prod, pmap, cpu_r, mem_r)
            for e in errors:
                print(f"promote-gate: {e}", file=sys.stderr)
            yaml.safe_dump_all(docs, sys.stdout, sort_keys=False)
            return 1 if errors else 0
        dev = load_docs(a.dev)
        v = certify(prod, dev, pmap, cpu_r, mem_r)
        report["certificate"] = {
            "prodSha256": file_hash(a.prod),
            "devSha256": file_hash(a.dev),
            "mapSha256": file_hash(a.map),
            "prodTwinSha256": tree_hash(a.prod_twin), "devTwinSha256": tree_hash(a.dev_twin),
            "ratio": {"cpu": str(cpu_r), "memory": str(mem_r)},
            "violations": v, "ok": not v,
        }
        failed |= bool(v)
    if a.cmd in ("replay", "check"):
        twin = a.twin if a.cmd == "replay" else a.prod_twin
        topo = Topology(twin)
        policy = load_policy(a.policy or os.path.join(twin, "balloonspolicy.yaml"))
        gate = (load_map(a.map).get("gate") if getattr(a, "map", None) else None) or {}
        f, balloons = replay(topo, policy, expand_pods(load_docs(a.prod)), gate, orders=a.orders)
        report["replay"] = {
            "findings": f, "ok": not f, "assumptions": model_assumptions(),
            "balloons": [{"id": b["id"], "cpus": fmt_list(b["cpus"]), "numaNodes": b["numaNodes"],
                          "memNodes": b["memNodes"], "pods": b["pods"]} for b in balloons],
        }
        failed |= bool(f)
    report["seconds"] = round(time.monotonic() - t0, 3)
    _dump(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

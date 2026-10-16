"""DRAM bandwidth as a schedulable UMA resource: the mechanism, stdlib only.

Pieces:
  bytes_per_token / greedy_demand_gbps  roofline demand from model metadata
  node_capacity_patch                    advertise k3d-gpu.io/mem-bw-gbps on the node
  mutate                                 mutating-webhook handler (AdmissionReview v1)
  Guard                                  AIMD cpu.max controller driven by decode ITL
  read_cgroup_signals / cpu_max_string   cgroup v2 read/write helpers for the guard
"""

import base64
import json
import math
import os

RESOURCE = "k3d-gpu.io/mem-bw-gbps"
ANN = "k3d-gpu.io/"  # annotation prefix on inference pods
CAPACITY_FRACTION = 0.85


# --- roofline demand ---------------------------------------------------------

def weight_bytes(params_b, weight_bits):
    """Weight bytes read per decode step: every weight once."""
    return params_b * 1e9 * weight_bits / 8


def kv_bytes_per_token(layers, kv_heads, head_dim, kv_bits=16):
    """KV-cache bytes stored per context token (K and V)."""
    return 2 * layers * kv_heads * head_dim * kv_bits / 8


def bytes_per_token(params_b, weight_bits, layers, kv_heads, head_dim, ctx_tokens, kv_bits=16):
    """DRAM bytes per decoded token: all weights plus the whole KV cache, since
    each decode step attends over every cached token."""
    return weight_bytes(params_b, weight_bits) + ctx_tokens * kv_bytes_per_token(
        layers, kv_heads, head_dim, kv_bits)


def greedy_demand_gbps(bpt, device_reach_gbps, compute_tok_s):
    """Bandwidth a decoder draws when nothing caps it: it runs at the lower of
    its compute ceiling and its device's reachable bandwidth. This, not a
    declared target tok/s, is what a bandwidth-bound decoder consumes."""
    return min(device_reach_gbps, compute_tok_s * bpt / 1e9)


def target_demand_gbps(bpt, target_tok_s):
    """Demand as the original idea stated it: bytes/token x target tok/s."""
    return bpt * target_tok_s / 1e9


# --- node advertisement ------------------------------------------------------

def node_capacity(stream_triad_gbps, fraction=CAPACITY_FRACTION):
    """Extended resources are integers: floor so the node never over-advertises."""
    return int(math.floor(stream_triad_gbps * fraction))


def node_capacity_patch(stream_triad_gbps, fraction=CAPACITY_FRACTION):
    """JSON for `kubectl patch node <n> --subresource=status --type=json -p ...`."""
    key = RESOURCE.replace("~", "~0").replace("/", "~1")
    val = str(node_capacity(stream_triad_gbps, fraction))
    return [{"op": "add", "path": f"/status/capacity/{key}", "value": val}]


# --- mutating webhook --------------------------------------------------------

# Per-device reachable DRAM bandwidth (GB/s) and decode compute ceiling scale.
# Placeholders until STREAM/OVMS solo runs on the host replace them (live probe L2).
DEVICE_REACH_GBPS = {"cpu": 60.0, "igpu": 25.0}


def pod_demand_gbps(annotations, reach=DEVICE_REACH_GBPS):
    """Demand from pod annotations, or None when the pod is not an annotated
    inference pod. Annotations (all under k3d-gpu.io/):
      model-params-b, weight-bits, layers, kv-heads, head-dim, ctx-tokens,
      device (cpu|igpu), compute-tok-s; optional kv-bits."""
    a = {k[len(ANN):]: v for k, v in (annotations or {}).items() if k.startswith(ANN)}
    need = ("model-params-b", "weight-bits", "layers", "kv-heads", "head-dim",
            "ctx-tokens", "device", "compute-tok-s")
    if not all(k in a for k in need):
        return None
    bpt = bytes_per_token(float(a["model-params-b"]), float(a["weight-bits"]),
                          int(a["layers"]), int(a["kv-heads"]), int(a["head-dim"]),
                          int(a["ctx-tokens"]), float(a.get("kv-bits", 16)))
    return greedy_demand_gbps(bpt, reach[a["device"]], float(a["compute-tok-s"]))


def mutate(review, reach=DEVICE_REACH_GBPS):
    """AdmissionReview v1 in, AdmissionReview v1 out. Adds the bandwidth request
    and limit (equal: extended resources cannot overcommit) to the first container."""
    req = review["request"]
    resp = {"uid": req["uid"], "allowed": True}
    pod = req["object"]
    demand = pod_demand_gbps(pod.get("metadata", {}).get("annotations"), reach)
    if demand is not None:
        qty = str(int(math.ceil(demand)))
        key = RESOURCE.replace("~", "~0").replace("/", "~1")
        res = pod["spec"]["containers"][0].get("resources", {})
        ops = []
        if "resources" not in pod["spec"]["containers"][0]:
            ops.append({"op": "add", "path": "/spec/containers/0/resources", "value": {}})
        for kind in ("requests", "limits"):
            if kind not in res:
                ops.append({"op": "add", "path": f"/spec/containers/0/resources/{kind}", "value": {}})
            ops.append({"op": "add", "path": f"/spec/containers/0/resources/{kind}/{key}", "value": qty})
        resp["patchType"] = "JSONPatch"
        resp["patch"] = base64.b64encode(json.dumps(ops).encode()).decode()
    return {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": resp}


def admit(demands, capacity):
    """First-fit admission the way the default scheduler bin-packs an extended
    resource: returns (admitted indices, overflow indices)."""
    used, admitted, overflow = 0.0, [], []
    for i, d in enumerate(demands):
        q = math.ceil(d)
        if used + q <= capacity:
            used += q
            admitted.append(i)
        else:
            overflow.append(i)
    return admitted, overflow


# --- runtime guard -----------------------------------------------------------

class Guard:
    """AIMD controller on sandbox cpu.max quotas (fractions of the sandbox's CPUs).

    Input per tick: the victim decode's recent ITL samples (seconds; empty when
    the victim is idle) and one ranking score per sandbox. The score is
    pluggable: PSI memory / refaults (read_cgroup_signals) as the idea proposed,
    or a per-cgroup LLC-miss rate (perf -G), which tracks DRAM traffic; PSI
    memory and refaults measure capacity stalls, not bandwidth. It never sees
    bandwidth, allocations or the true quota->bandwidth curve."""

    def __init__(self, solo_itl, n_sandboxes, slack=0.08, cut=0.6, relax=1.25,
                 floor=0.05, rank="pressure", rng=None):
        self.solo_itl = solo_itl
        self.q = [1.0] * n_sandboxes
        self.slack, self.cut, self.relax, self.floor = slack, cut, relax, floor
        self.rank, self.rng = rank, rng

    def _victim_order(self, pressure):
        idx = list(range(len(self.q)))
        if self.rank == "random":
            self.rng.shuffle(idx)
            return idx
        return sorted(idx, key=lambda i: -pressure[i])

    def step(self, itl_samples, pressure):
        if itl_samples:
            s = sorted(itl_samples)
            p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
            if p95 > self.solo_itl * (1 + self.slack):
                for i in self._victim_order(pressure):
                    if self.q[i] > self.floor:
                        self.q[i] = max(self.floor, self.q[i] * self.cut)
                        break
                return self.q
        # Victim idle or within slack: relax every throttled sandbox.
        self.q = [min(1.0, x * self.relax) for x in self.q]
        return self.q


def cpu_max_string(fraction, ncpus, period_us=100000):
    """cgroup v2 cpu.max value for a quota of fraction x ncpus CPUs."""
    if fraction >= 1.0:
        return f"max {period_us}"
    return f"{max(1000, int(fraction * ncpus * period_us))} {period_us}"


def read_cgroup_signals(cgroup_dir):
    """(PSI memory some avg10, workingset refaults total) for one cgroup v2 dir."""
    avg10 = 0.0
    with open(os.path.join(cgroup_dir, "memory.pressure")) as f:
        for line in f:
            if line.startswith("some "):
                avg10 = float(dict(kv.split("=") for kv in line.split()[1:])["avg10"])
    refaults = 0
    with open(os.path.join(cgroup_dir, "memory.stat")) as f:
        for line in f:
            k, v = line.split()
            if k.startswith("workingset_refault"):
                refaults += int(v)
    return avg10, refaults


def write_cpu_max(cgroup_dir, value):
    with open(os.path.join(cgroup_dir, "cpu.max"), "w") as f:
        f.write(value)

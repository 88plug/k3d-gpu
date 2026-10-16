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

    Inputs per tick, all observable on a real node:
      itl_samples  the victim decode's recent inter-token latencies (seconds),
                   timed from its token stream (OVMS exports no per-token metric);
      pressure     one ranking score per sandbox: PSI memory / refaults
                   (read_cgroup_signals) as first proposed, or a per-cgroup
                   LLC-miss rate (perf -G), which tracks DRAM traffic;
      active       victim has requests in flight (OVMS ovms_current_requests /
                   ovms_current_graphs > 0). None = unknown, inferred from samples.
    It never sees bandwidth, allocations or the true quota->bandwidth curve.

    sensor="geomean": trigger when the window's geometric-mean ITL exceeds the
      solo geometric mean by > slack AND by > z standard errors of the solo
      log-ITL spread (a few-sample window p95 is biased high and fires on noise).
    sensor="p95": v1 trigger, window p95 vs solo p95.
    memory=True: remember the quotas that last held the SLO while the victim was
      active, and re-apply them the tick a new burst starts, instead of
      re-discovering them by cutting from 1.0 at every burst onset."""

    def __init__(self, solo_itl, n_sandboxes, slack=0.08, cut=0.6, relax=1.25,
                 floor=0.05, rank="pressure", rng=None, sensor="p95", memory=False,
                 solo_log_sd=0.0, z=2.0, min_samples=3, active_relax=1.02,
                 window=3.0):
        self.solo_itl = solo_itl
        self.q = [1.0] * n_sandboxes
        self.slack, self.cut, self.relax, self.floor = slack, cut, relax, floor
        self.rank, self.rng = rank, rng
        self.sensor, self.memory = sensor, memory
        self.solo_log_sd, self.z, self.min_samples = solo_log_sd, z, min_samples
        self.active_relax = active_relax
        self.window = window
        self.hold = None
        self.was_active = False
        self.last_cut = float("-inf")

    def _victim_order(self, pressure):
        idx = list(range(len(self.q)))
        if self.rank == "random":
            self.rng.shuffle(idx)
            return idx
        return sorted(idx, key=lambda i: -pressure[i])

    def _slow(self, s):
        if self.sensor == "p95":
            s = sorted(s)
            return s[min(len(s) - 1, int(0.95 * len(s)))] > self.solo_itl * (1 + self.slack)
        if len(s) < self.min_samples:
            return None
        mlog = sum(math.log(x) for x in s) / len(s)
        excess = mlog - math.log(self.solo_itl)
        return (excess > math.log1p(self.slack)
                and excess > self.z * self.solo_log_sd / math.sqrt(len(s)))

    def step(self, itl_samples, pressure, active=None, now=None):
        """itl_samples: floats (sensor="p95") or (timestamp, itl) pairs (sensor="geomean")."""
        if active is None:
            active = bool(itl_samples)
        if self.memory and active and not self.was_active and self.hold:
            self.q = [min(a, b) for a, b in zip(self.q, self.hold)]
        self.was_active = active
        if self.sensor == "geomean":
            # Only tokens produced after the last cut reflect the current quotas.
            itl_samples = [x for t, x in itl_samples
                           if t > self.last_cut and (now is None or t > now - self.window)]
        slow = self._slow(itl_samples) if itl_samples else None
        if slow:
            for i in self._victim_order(pressure):
                if self.q[i] > self.floor:
                    self.q[i] = max(self.floor, self.q[i] * self.cut)
                    break
            self.last_cut = now if now is not None else self.last_cut
        elif slow is None and active and self.sensor == "geomean":
            pass                   # running but too few fresh tokens to judge: hold
        else:
            # Within SLO: probe upward slowly while the victim runs, quickly when idle.
            r = self.active_relax if (self.memory and active) else self.relax
            self.q = [min(1.0, x * r) for x in self.q]
        if self.memory and active:
            self.hold = list(self.q)   # the next burst resumes from here
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


# --- webhook server ----------------------------------------------------------

def handle_body(body):
    """Raw AdmissionReview request bytes -> response bytes. Malformed input is
    allowed through unchanged (the MutatingWebhookConfiguration also sets
    failurePolicy: Ignore), so a webhook bug never blocks pod creation."""
    try:
        out = mutate(json.loads(body))
    except (ValueError, KeyError, TypeError, IndexError):
        uid = ""
        try:
            uid = json.loads(body)["request"]["uid"]
        except (ValueError, KeyError, TypeError):
            pass
        out = {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview",
               "response": {"uid": uid, "allowed": True}}
    return json.dumps(out).encode()


def serve(port=8443, cert="/tls/tls.crt", key="/tls/tls.key"):
    import http.server
    import ssl

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            out = handle_body(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = http.server.ThreadingHTTPServer(("", port), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()


if __name__ == "__main__":
    serve()

"""Operating points and the lever plan a presence flip produces.

A lever is LIVE when it can be changed on a running node without dropping model
state (KV cache, compiled graph). Presence flips may only touch LIVE levers.
RESTART-BOUND levers (OVMS graph node_options, llama.cpp startup flags, a
balloons re-pin of a running container) are fixed for both operating points.
"""

LIVE, RESTART = "live", "restart"

# name -> (tier, estimated apply latency in s). Latencies are estimates for the
# live probe to confirm; they are not measured on this host yet.
LEVERS = {
    "epp_p": (LIVE, 0.05),            # sysfs write, one per P-core CPU
    "epp_e": (LIVE, 0.05),            # sysfs write, one per E-core CPU
    "gpu_power_w": (LIVE, 1.0),       # nvidia-smi -i N -pl W, per GPU, run in parallel
    "gw_concurrency": (LIVE, 0.0),    # gateway: interactive requests admitted at once
    "gw_bg_release": (LIVE, 0.0),     # gateway: queued background steps released per batch
    "gw_chunk_tokens": (LIVE, 0.0),   # gateway: prefix-fed prefill chunk size (iGPU model)
    "gw_chunk_gap_ms": (LIVE, 0.0),   # gateway: idle gap between prefill chunks
    "label": (LIVE, 1.0),             # kubectl label node (off the critical path)
    "ovms_max_num_batched_tokens": (RESTART, None),  # graph node_options -> config reload
    "ovms_enable_prefix_caching": (RESTART, None),
    "llamacpp_parallel": (RESTART, None),            # --parallel startup flag
    "llamacpp_batch": (RESTART, None),               # -b startup flag
    "balloon": (RESTART, None),                      # cpuset chosen at container create
}

# Critical path of a switch-back: everything but the label, applied in parallel.
OFF_CRITICAL_PATH = {"label"}


class TieringError(ValueError):
    pass


def validate(points):
    """Every operating point must agree on every restart-bound lever."""
    for name, (tier, _) in LEVERS.items():
        if tier != RESTART:
            continue
        values = {repr(p.get(name)) for p in points.values()}
        if len(values) > 1:
            raise TieringError(f"{name} is restart-bound but differs across operating points: {sorted(values)}")
    for p in points.values():
        unknown = set(p) - set(LEVERS)
        if unknown:
            raise TieringError(f"unknown levers: {sorted(unknown)}")


def plan(old, new):
    """Live lever changes going from operating point `old` to `new`."""
    changes = []
    for name in sorted(set(old) | set(new)):
        if old.get(name) == new.get(name):
            continue
        tier, _ = LEVERS[name]
        if tier != LIVE:
            raise TieringError(f"presence flip would change restart-bound lever {name}")
        changes.append((name, new.get(name)))
    return changes


def critical_latency(changes):
    """Estimated wall time of a plan: live levers apply in parallel."""
    lat = [LEVERS[n][1] for n, _ in changes if n not in OFF_CRITICAL_PATH]
    return max(lat, default=0.0)


def commands(changes, *, p_cpus, e_cpus, gpus, node, state):
    """Render a plan into the concrete writes. Returned, never executed here."""
    out = []
    for name, value in changes:
        if name in ("epp_p", "epp_e"):
            for cpu in (p_cpus if name == "epp_p" else e_cpus):
                out.append(("write", f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/energy_performance_preference", value))
        elif name == "gpu_power_w":
            for idx, watts in enumerate(value):
                lo, hi = gpus[idx]
                out.append(("exec", ["nvidia-smi", "-i", str(idx), "-pl", str(max(lo, min(hi, watts)))]))
        elif name == "label":
            out.append(("exec", ["kubectl", "label", "node", node, f"k3d-gpu.io/attention={state}", "--overwrite"]))
        else:
            out.append(("gateway", name, value))
    return out


def default_points(gpus):
    """Present/away operating points for a node. `gpus` = [(min_w, max_w), ...]."""
    shared = {"ovms_max_num_batched_tokens": 2048, "ovms_enable_prefix_caching": True,
              "llamacpp_parallel": 4, "llamacpp_batch": 2048, "balloon": "by-label"}
    present = dict(shared, epp_p="performance", epp_e="balance_performance",
                   gpu_power_w=[hi for _, hi in gpus], gw_concurrency=1, gw_bg_release=1,
                   gw_chunk_tokens=None, gw_chunk_gap_ms=None, label="present")
    away = dict(shared, epp_p="balance_power", epp_e="power",
                gpu_power_w=[lo for lo, _ in gpus], gw_concurrency=4, gw_bg_release=4,
                gw_chunk_tokens=2048, gw_chunk_gap_ms=0.0, label="away")
    return {"present": present, "away": away}

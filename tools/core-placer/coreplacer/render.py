"""Render the placer's state as a BalloonsPolicy spec and per-pod balloon choices.

The balloons policy assigns a container to a balloon when the container starts,
so a pod changes balloon only by being re-created with a new label (or the
balloon.balloons.resource-policy.nri.io annotation). The placer therefore emits
label changes; the move cost of a restart is charged in the simulation.

HFI can only narrow `availableResources.cpu` (drop CPUs with perf_cap 0).
preferCoreType is still the static CPUID core type, so the live P pool feeds the
planner capacity, not balloon membership.
"""
from .perf import format_cpulist

LABEL = "k3d-gpu.io/cores"


def _match(value):
    return [{"key": f"pod/labels/{LABEL}", "operator": "Equals", "values": [value]}]


def balloons_spec(available_cpus, amx=False):
    types = [
        {"name": "performance", "preferCoreType": "performance", "matchExpressions": _match("performance")},
        {"name": "efficient", "preferCoreType": "efficient", "matchExpressions": _match("efficient")},
    ]
    spec = {
        "availableResources": {"cpu": "cpuset:" + format_cpulist(available_cpus)},
        "reservedResources": {"cpu": "1000m"},
        "reservedPoolNamespaces": ["kube-system"],
        "allocatorTopologyBalancing": True,
        "balloonTypes": types,
    }
    if amx:
        # Whole physical cores, sibling hyperthread hidden, and no two AMX
        # balloons on one core.
        types.append({"name": "amx", "hideHyperthreads": True, "preferSpreadOnPhysicalCores": False,
                      "loads": ["amx-core"], "matchExpressions": _match("amx")})
        spec["loadClasses"] = [{"name": "amx-core", "level": "core", "overloadsLevelInBalloon": True}]
    types.append({"name": "shared", "shareIdleCPUsInSame": "system", "maxBalloons": 1,
                  "matchExpressions": [{"key": "name", "operator": "AlwaysTrue"}]})
    return spec


def balloons_policy(available_cpus, amx=False):
    return {"apiVersion": "config.nri/v1alpha1", "kind": "BalloonsPolicy",
            "metadata": {"name": "default", "namespace": "kube-system"},
            "spec": balloons_spec(available_cpus, amx)}

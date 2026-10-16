"""Split pods between the shared balloon (asym packing hands it idle P-cores first)
and the efficient balloon (E-cores only).

Model the placer optimises, built only from its own estimates: shared pods
share the P-cores pro rata, phi = min(1, P / D_shared); a pod's normalised
progress (rate / its all-P rate) is 1 - (1 - phi)(1 - 1/S) in shared and 1/S
on E. The objective is the sum (weighted speedup). Pods without an estimate
are kept in shared, where they get measured.
"""
import itertools


def progress(S, phi):
    return 1.0 - (1.0 - phi) * (1.0 - 1.0 / S)


def value(pods, shared, p_cpus):
    """pods: {pod: (demand, S)}; shared: set of pods."""
    D = sum(pods[p][0] for p in shared)
    phi = 1.0 if D <= p_cpus else p_cpus / D
    return sum(progress(S, phi) if p in shared else 1.0 / S for p, (_d, S) in pods.items())


def best_split(pods, p_cpus, forced_shared=(), exhaustive_max=12):
    """Returns the shared set. Exact enumeration up to `exhaustive_max` free pods,
    otherwise the best prefix of pods ranked by (1 - 1/S) / demand."""
    forced = set(forced_shared)
    free = sorted((p for p in pods if p not in forced),
                  key=lambda p: -(1 - 1 / pods[p][1]) / max(pods[p][0], 1e-3))
    if len(free) <= exhaustive_max:
        cands = (forced | set(c) for k in range(len(free) + 1) for c in itertools.combinations(free, k))
    else:
        cands = (forced | set(free[:k]) for k in range(len(free) + 1))
    return max(cands, key=lambda s: (value(pods, s, p_cpus), len(s)))

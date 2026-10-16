"""Claim (2) simulation: iGPU prefill chunking vs compositor frame time.

Physics (truth) and controller (estimates) are separate objects. The controller
computes its chunk from `Calib` only; the GPU timeline runs on `Physics`.

GPU model: one queue. Prefill work is a sequence of non-preemptible slices; the
compositor arrives at each vblank (animating desktop), waits for the running
slice to end, then runs. A frame whose compositor work finishes after the next
vblank is shown late: frame time = ceil((end - vblank) / period) * period.

Preemption regimes (what bounds a non-preemptible slice):
  step      the whole prefill step (chunk) runs as one unit
  kernel    one kernel; the largest is `f_kernel` of the step's compute
  timeslice a kernel, but cut at `ts_ms` (i915/GuC timeslice preemption works)
"""

import math
import random
from dataclasses import dataclass, replace

from .chunk import budget_chunk


@dataclass(frozen=True)
class Physics:
    t_tok_ms: float = 3.75      # prefill compute per token (1.5B model ~ 3 GFLOP/tok at ~0.8 TFLOPS)
    w_ms: float = 15.0          # per-step fixed cost (weight streaming ~0.9 GB INT4 at ~60 GB/s)
    kernels: int = 200          # kernels per step
    f_kernel: float = 0.02      # largest kernel / step compute
    block: int = 16             # KV block size (recomputed once per prefix-fed chunk)
    req_ms: float = 20.0        # per prefix-fed request host overhead (GPU idle)
    c_ms: float = 3.0           # compositor work per frame (median)
    c_sigma: float = 0.3        # lognormal spread of compositor work
    refresh_hz: float = 60.0
    ts_ms: float = 1.0          # i915 timeslice_duration_ms (read on this host: 1)


@dataclass(frozen=True)
class Calib:
    """What the controller believes, from a calibration probe."""
    t_tok_ms: float
    f_kernel: float
    c_p99_ms: float
    refresh_hz: float
    block: int = 16
    margin_ms: float = 1.0


def calib_from(ph, err=0.0, rng=None, refresh_hz=None):
    """A calibration that misses truth by up to `err` (relative) per field."""
    rng = rng or random.Random(0)
    j = lambda: 1.0 + rng.uniform(-err, err)  # noqa: E731
    c_p99 = ph.c_ms * math.exp(2.326 * ph.c_sigma)
    return Calib(t_tok_ms=ph.t_tok_ms * j(), f_kernel=ph.f_kernel * j(),
                 c_p99_ms=c_p99 * j(), refresh_hz=refresh_hz or ph.refresh_hz,
                 block=ph.block)


def present_chunk(cal, regime):
    """The chunk the controller sends while the user is present."""
    # In `timeslice` the controller still sizes for kernels: it does not assume
    # preemption works.
    frac = 1.0 if regime == "step" else cal.f_kernel
    return budget_chunk(1.0 / cal.t_tok_ms, cal.refresh_hz, cal.c_p99_ms,
                        block=cal.block, margin_ms=cal.margin_ms, slice_frac=frac)


def _work(ph, n_tokens, chunk, regime, fed, rng):
    """Yield ('gpu', ms) slices and ('idle', ms) host gaps for one prompt."""
    done = 0
    while done < n_tokens:
        n = min(chunk, n_tokens - done)
        computed = n + (ph.block if fed and done else 0)  # tail block re-prefilled
        compute = computed * ph.t_tok_ms * rng.uniform(0.95, 1.05)
        if regime == "step":
            slices = [ph.w_ms + compute]
        else:
            k = max(1, round(1.0 / ph.f_kernel))
            per = compute / k + ph.w_ms / k
            slices = [per] * k
            if regime == "timeslice":
                slices = [s for p in slices for s in _cut(p, ph.ts_ms)]
        for s in slices:
            yield ("gpu", s)
        done += n
        if fed and done < n_tokens:
            yield ("idle", ph.req_ms)


def _cut(d, ts):
    q, r = divmod(d, ts)
    return [ts] * int(q) + ([r] if r > 1e-9 else [])


def run(ph, chunk, regime, *, fed, animate, n_tokens=8192, seconds=None, seed=1):
    """Simulate prefill of `n_tokens` prompts back-to-back.
    Returns (frame_p99 / period, tokens per second)."""
    rng = random.Random(seed)
    period = 1000.0 / ph.refresh_hz
    t = 0.0                 # GPU free-at
    next_vb = 0.0
    frames = []
    tokens = 0
    horizon = (seconds or 0) * 1000.0

    def compositor(upto):
        nonlocal t, next_vb
        while next_vb <= upto:
            start = max(next_vb, t)
            c = ph.c_ms * math.exp(rng.gauss(0.0, ph.c_sigma))
            end = start + c
            frames.append(max(1, math.ceil((end - next_vb) / period - 1e-9)) * period)
            t = end
            next_vb += period

    while True:
        for kind, d in _work(ph, n_tokens, chunk, regime, fed, rng):
            if kind == "idle":
                # host gap: wall time passes, the GPU is free for the compositor
                t_end = t + d
                if animate:
                    compositor(t_end)
                t = max(t, t_end)
                continue
            if animate:
                compositor(t)  # vblanks that arrived while the GPU was free/busy
            t += d
        tokens += n_tokens
        if t >= horizon:
            break
    if animate:
        compositor(t)
    p99 = sorted(frames)[int(0.99 * (len(frames) - 1))] / period if frames else 1.0
    return p99, tokens / (t / 1000.0)


def idle_desktop_p99(ph, seconds=30.0, seed=1):
    rng = random.Random(seed)
    period = 1000.0 / ph.refresh_hz
    frames = []
    for _ in range(int(seconds * ph.refresh_hz)):
        c = ph.c_ms * math.exp(rng.gauss(0.0, ph.c_sigma))
        frames.append(max(1, math.ceil(c / period - 1e-9)) * period)
    return sorted(frames)[int(0.99 * (len(frames) - 1))] / period


def away_chunk(cal, switch_back_ms=2000.0, tp_chunk=2048):
    """Away mode still prefix-feeds: the step in flight when the user returns
    cannot be resized, so one away step must fit the switch-back budget."""
    n = int(0.85 * switch_back_ms / cal.t_tok_ms) // cal.block * cal.block
    return max(cal.block, min(tp_chunk, n))


def evaluate(ph, regime, *, cal=None, tp_chunk=2048, seconds=120.0, seed=1,
             reload_s=None):
    """All claim-(2) numbers for one physics + regime.
    `reload_s`: ablation where the chunk is changed server-side through an OVMS
    config reload (graph rebuild) instead of gateway prefix feeding."""
    cal = cal or calib_from(ph)
    n_present = present_chunk(cal, regime)
    n_away = away_chunk(cal, tp_chunk=tp_chunk)
    idle = idle_desktop_p99(ph, seed=seed)
    kw = dict(seconds=seconds, seed=seed)
    gated_present_p99, gated_present_tps = run(ph, n_present, regime, fed=True, animate=True, **kw)
    smooth_p99, _ = run(ph, n_present, regime, fed=False, animate=True, **kw)
    tp_p99, _ = run(ph, tp_chunk, regime, fed=False, animate=True, **kw)
    _, tp_tps = run(ph, tp_chunk, regime, fed=False, animate=False, **kw)
    # smoothness-tuned static: the server chunk itself set to the present budget
    # (no prefix-feeding overhead: the strongest static smooth baseline).
    _, smooth_tps = run(ph, n_present, regime, fed=False, animate=False, **kw)
    if reload_s is None:
        _, gated_away_tps = run(ph, n_away, regime, fed=True, animate=False, **kw)
        switch_back_s = (ph.w_ms + n_away * ph.t_tok_ms * 1.05 + ph.req_ms) / 1000.0
    else:
        gated_away_tps = tp_tps
        switch_back_s = reload_s
    r = {
        "regime": regime, "present_chunk": n_present, "away_chunk": n_away,
        "idle_p99": idle, "gated_present_p99": gated_present_p99,
        "smooth_static_p99": smooth_p99, "tp_static_p99": tp_p99,
        "gated_present_tps": gated_present_tps, "gated_away_tps": gated_away_tps,
        "tp_static_tps": tp_tps, "smooth_static_tps": smooth_tps,
        "switch_back_s": switch_back_s,
    }
    r["smooth_ok"] = gated_present_p99 <= 1.25 * idle
    r["kill"] = tp_p99 <= 1.25 * idle
    r["away_vs_tp"] = gated_away_tps / tp_tps
    r["away_vs_smooth"] = gated_away_tps / smooth_tps - 1.0
    r["switch_ok"] = switch_back_s <= 2.0
    r["holds"] = (r["smooth_ok"] and not r["kill"] and r["switch_ok"]
                  and r["away_vs_tp"] >= 0.97 and r["away_vs_smooth"] >= 0.15)
    return r


def perturb(ph, rng, rel=0.3):
    f = lambda: 1.0 + rng.uniform(-rel, rel)  # noqa: E731
    return replace(ph, t_tok_ms=ph.t_tok_ms * f(), w_ms=ph.w_ms * f(),
                   f_kernel=ph.f_kernel * f(), req_ms=ph.req_ms * f(),
                   c_ms=ph.c_ms * f())

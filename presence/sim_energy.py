"""Claim (1) simulation: node J/task, presence-gated vs best static config.

Truth and controller are separate:
- `Human` generates true presence intervals and the input events the human
  makes while present (with reading pauses). The controller only receives the
  input-event timestamps, through `attention.replay`.
- `Node` physics (power-speed exponents, rates) is truth. The controller picks
  its away operating point from `NodeCalib`, a copy perturbed by up to ±15%.

Fluid model, dt = 0.25 s. C agents run tasks of S steps; a step is prefill ->
decode -> CPU tool work. Agents are split across the GPUs. On a GPU, prefill
runs before decode (chunked-prefill priority). Decode of b streams batches with
aggregate rate tps1 * b / (1 + beta * (b - 1)).

GPU power under cap: a phase demands P_dem watts; the drawn power is
min(cap, P_dem) and speed is (min(cap, P_dem) / P_dem) ** alpha_phase.
Prefill is compute-bound (alpha_prefill ~ 0.8). Decode is memory-bound;
alpha_decode is swept because the claim lives or dies on it.

Present-mode TTFT counts only steps that start while the human is truly present.
"""

import bisect
import random
from dataclasses import dataclass, replace

from .attention import AWAY, replay


@dataclass(frozen=True)
class Node:
    gpu_max_w: tuple = (70.0, 50.0)     # nvidia-smi power.max_limit on this host
    gpu_min_w: tuple = (30.0, 30.0)     # nvidia-smi power.min_limit on this host
    gpu_idle_w: tuple = (10.0, 7.0)     # assumption (not measured: benchmarks running)
    decode_dem: float = 0.7             # decode power demand / max (assumption)
    alpha_prefill: float = 0.8
    alpha_decode: float = 0.3
    prefill_tps: tuple = (3000.0, 2000.0)
    decode_tps: tuple = (45.0, 30.0)
    beta: float = 0.15                  # batch decode efficiency loss
    cpu_idle_w: float = 8.0
    cpu_tool_w: dict = None             # EPP -> package watts while a tool runs
    cpu_tool_speed: dict = None         # EPP -> tool speed (perf = 1)

    def __post_init__(self):
        if self.cpu_tool_w is None:
            object.__setattr__(self, "cpu_tool_w", {"performance": 45.0, "power": 22.0})
        if self.cpu_tool_speed is None:
            object.__setattr__(self, "cpu_tool_speed", {"performance": 1.0, "power": 0.7})


@dataclass(frozen=True)
class Work:
    agents: int = 4
    tasks: int = 50
    steps: int = 12
    prompt_tokens: int = 3000           # new (uncached) prompt tokens per step
    out_tokens: int = 300
    tool_s: float = 6.0


@dataclass(frozen=True)
class OpPoint:
    cap: float = 1.0          # fraction of each GPU's max power limit
    epp: str = "performance"
    hold: int = 1             # gateway: release steps in batches of this many
    hold_timeout_s: float = 30.0


class Human:
    """Alternating present/away intervals. While present the human types in
    bursts separated by reading pauses; long pauses look like absence."""

    def __init__(self, away_frac=0.5, mean_cycle_s=3600.0, pause_mean_s=40.0, seed=0):
        self.away_frac, self.cycle, self.pause_mean = away_frac, mean_cycle_s, pause_mean_s
        self.rng = random.Random(seed)

    def timeline(self, horizon):
        rng, t, present, inputs = self.rng, 0.0, [], []
        while t < horizon:
            p_len = rng.expovariate(1.0 / (self.cycle * (1 - self.away_frac) + 1e-9))
            a_len = rng.expovariate(1.0 / (self.cycle * self.away_frac + 1e-9))
            end = t + p_len
            present.append((t, end))
            u = t
            while u < end:
                inputs.append(u)
                u += rng.expovariate(1.0 / self.pause_mean)
            t = end + a_len
        return present, inputs


def is_in(intervals, t):
    i = bisect.bisect_right(intervals, (t, float("inf"))) - 1
    return i >= 0 and intervals[i][0] <= t < intervals[i][1]


def mode_fn(transitions):
    """Controller mode at time t from attention transitions [(t, state)]."""
    times = [tt for tt, _ in transitions]

    def f(t):
        return transitions[max(0, bisect.bisect_right(times, t) - 1)][1]
    return f


def simulate(node, work, mode_at, ops, present_iv, dt=0.25, switch_s=1.0, limit_s=4e5):
    """Run the replay. `mode_at(t)` -> 'present'|'away' is what the controller
    believes; `ops` maps that to an OpPoint. Lever changes take `switch_s`."""
    n_gpu = len(node.gpu_max_w)
    tasks_left = [work.tasks // work.agents + (1 if i < work.tasks % work.agents else 0)
                  for i in range(work.agents)]
    # agent state: phase in {ready, held, prefill, decode, tool, done}, remaining amount
    ag = [{"phase": "ready", "left": 0.0, "step": 0, "t0": 0.0} for _ in range(work.agents)]
    gpu_of = [i % n_gpu for i in range(work.agents)]
    t, energy, ttft_present, applied, pending = 0.0, 0.0, [], ops["present"], None
    while t < limit_s:
        want = ops[mode_at(t)]
        if want != applied and pending is None:
            pending = (t + switch_s, want)
        if pending and t >= pending[0]:
            applied, pending = pending[1], None
            # a change already in flight lands; re-check next tick
        op = applied
        # gateway release: held steps go out when `hold` are waiting or the mode is present
        held = [a for a in ag if a["phase"] == "held"]
        live = sum(a["phase"] != "done" for a in ag)
        if held and (op.hold == 1 or len(held) >= min(op.hold, live)
                     or t - min(a["t0"] for a in held) >= op.hold_timeout_s):
            for a in held:
                a["phase"], a["left"] = "prefill", float(work.prompt_tokens)
        for i, a in enumerate(ag):
            if a["phase"] == "ready":
                if tasks_left[i] == 0:
                    a["phase"] = "done"
                    continue
                a["phase"], a["t0"] = "held", t
        p = 0.0
        for g in range(n_gpu):
            cap_w = node.gpu_max_w[g] * op.cap
            mine = [a for i, a in enumerate(ag) if gpu_of[i] == g]
            pre = [a for a in mine if a["phase"] == "prefill"]
            dec = [a for a in mine if a["phase"] == "decode"]
            if pre:
                dem = node.gpu_max_w[g]
                draw = min(cap_w, dem)
                rate = node.prefill_tps[g] * (draw / dem) ** node.alpha_prefill
                a = pre[0]
                a["left"] -= rate * dt
                if a["left"] <= 0:
                    if is_in(present_iv, a["t0"]):  # t0 = step ready (incl. gateway hold)
                        ttft_present.append(t + dt + a["left"] / rate - a["t0"])
                    a["phase"], a["left"] = "decode", float(work.out_tokens)
                p += draw
            elif dec:
                b = len(dec)
                dem = min(node.gpu_max_w[g], node.gpu_max_w[g] * node.decode_dem * (1 + 0.1 * (b - 1)))
                draw = min(cap_w, dem)
                agg = node.decode_tps[g] * b / (1 + node.beta * (b - 1)) * (draw / dem) ** node.alpha_decode
                for a in dec:
                    a["left"] -= agg / b * dt
                    if a["left"] <= 0:
                        a["phase"], a["left"] = "tool", work.tool_s
                p += draw
            else:
                p += node.gpu_idle_w[g]
        tools = [a for a in ag if a["phase"] == "tool"]
        p += node.cpu_idle_w + (node.cpu_tool_w[op.epp] - node.cpu_idle_w) * min(1.0, len(tools) / 2)
        for i, a in enumerate(ag):
            if a["phase"] == "tool":
                a["left"] -= node.cpu_tool_speed[op.epp] * dt
                if a["left"] <= 0:
                    a["step"] += 1
                    if a["step"] == work.steps:
                        a["step"] = 0
                        tasks_left[i] -= 1
                    a["phase"] = "ready"
        energy += p * dt
        t += dt
        if all(a["phase"] == "done" for a in ag):
            break
    ttft_present.sort()
    p95 = ttft_present[int(0.95 * (len(ttft_present) - 1))] if ttft_present else 0.0
    return {"j_per_task": energy / work.tasks, "wall_s": t, "p95_ttft": p95,
            "n_present_steps": len(ttft_present)}


CAPS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.43)
EPPS = ("performance", "power")


def calib_of(node, err=0.15, seed=0):
    rng = random.Random(seed)
    j = lambda: 1.0 + rng.uniform(-err, err)  # noqa: E731
    return replace(node, alpha_decode=node.alpha_decode * j(), alpha_prefill=node.alpha_prefill * j(),
                   decode_dem=min(1.0, node.decode_dem * j()))


def predict_step(cal, work, cap, epp):
    """Controller's model of one step on each GPU: (seconds, joules)."""
    secs = joules = 0.0
    for g in range(len(cal.gpu_max_w)):
        mx = cal.gpu_max_w[g]
        pre_draw = min(cap, 1.0) * mx
        dec_dem = cal.decode_dem * mx
        dec_draw = min(cap * mx, dec_dem)
        t_pre = work.prompt_tokens / (cal.prefill_tps[g] * (pre_draw / mx) ** cal.alpha_prefill)
        t_dec = work.out_tokens / (cal.decode_tps[g] * (dec_draw / dec_dem) ** cal.alpha_decode)
        t_tool = work.tool_s / cal.cpu_tool_speed[epp]
        secs += t_pre + t_dec + t_tool
        joules += pre_draw * t_pre + dec_draw * t_dec + cal.gpu_idle_w[g] * t_tool + cal.cpu_tool_w[epp] * t_tool
    return secs, joules


def choose_away(cal, work, present, max_slowdown=1.12, hold=None):
    """Controller-side choice from its calibration only: the (cap, EPP) with the
    lowest predicted J/step whose predicted step time stays under `max_slowdown`
    of the latency point, or of the present point if that is already slower.
    Gateway hold = number of agents."""
    lat, _ = predict_step(cal, work, 1.0, "performance")
    cur, cur_j = predict_step(cal, work, present.cap, present.epp)
    limit = max(max_slowdown * lat, cur)
    best = (cur_j, present.cap, present.epp)
    for cap in CAPS:
        for epp in EPPS:
            secs, joules = predict_step(cal, work, cap, epp)
            if secs <= limit and joules < best[0]:
                best = (joules, cap, epp)
    return OpPoint(cap=best[1], epp=best[2], hold=work.agents if hold is None else hold)


def evaluate(node, work, human, *, horizon=None, cal_seed=0, shuffle=False, dt=0.25,
             away_hold=None):
    """Gated vs best static. Returns the numbers the pre-registered AC5 reads.

    Both sides get the same offline tuning: the static grid is run once on the
    replay, and the gated present point is the cheapest static point whose
    present p95 TTFT is within 5% of latency-tuned. The away point comes from
    the controller's perturbed calibration only."""
    lat = OpPoint()
    probe = simulate(node, work, lambda t: "present", {"present": lat}, [(0, 1e9)], dt=dt)
    horizon = horizon or probe["wall_s"] * 3
    present_iv, inputs = human.timeline(horizon)
    run = lambda mode_at, ops: simulate(node, work, mode_at, ops, present_iv, dt=dt)  # noqa: E731
    ref = run(lambda t: "present", {"present": lat})
    statics = []
    for cap in CAPS:
        for epp in EPPS:
            for hold in sorted({1, work.agents}):
                op = OpPoint(cap, epp, hold)
                r = run(lambda t: "present", {"present": op})
                r["op"] = op
                statics.append(r)
    best = min((r for r in statics if r["p95_ttft"] <= 1.10 * ref["p95_ttft"]),
               key=lambda r: r["j_per_task"])
    present_op = min((r for r in statics if r["p95_ttft"] <= 1.05 * ref["p95_ttft"]),
                     key=lambda r: r["j_per_task"])["op"]
    if shuffle:  # an independent human with the same statistics: no information
        _, inputs = Human(human.away_frac, human.cycle, human.pause_mean,
                          seed=human.rng.randint(1, 10**9)).timeline(horizon)
    tr = replay(inputs, horizon)
    cal = calib_of(node, seed=cal_seed)
    away = choose_away(cal, work, present_op, hold=away_hold)
    gated = run(mode_fn(tr), {"present": present_op, "away": away})
    end = gated["wall_s"]
    away_time = sum(max(0.0, min(b, end) - a) for a, b in _away_intervals(tr, end))
    true_away = end - sum(max(0.0, min(b, end) - a) for a, b in present_iv if a < end)
    return {
        "ref": ref, "best_static": best, "gated": gated,
        "present_op": present_op, "away_op": away,
        "saving_vs_static": 1 - gated["j_per_task"] / best["j_per_task"],
        "saving_vs_ref": 1 - gated["j_per_task"] / ref["j_per_task"],
        "wall_vs_ref": gated["wall_s"] / ref["wall_s"],
        "ttft_vs_ref": gated["p95_ttft"] / ref["p95_ttft"],
        "controller_away_frac": away_time / end,
        "true_away_frac": true_away / end,
    }


def _away_intervals(tr, end):
    out, start = [], None
    for t, s in tr:
        if s == AWAY and start is None:
            start = t
        elif s != AWAY and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, end))
    return out

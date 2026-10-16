"""Fluid simulation of DRAM-bandwidth contention on a client UMA SoC.

The world (DRAM capacity, arbitration weights, loaded-latency curve, power
limit, cpu.max->bandwidth curve) is hidden state of World. Policies and the
Guard see only what a real node would: pod annotations with perturbed device
reach estimates, a noisy STREAM measurement, noisy ITL samples, and noisy
per-sandbox ranking scores. Numbers marked ILLUSTRATIVE are placeholders, not
measurements of this host; S5 sweeps them.

Usage: python3 sim.py [--quick]
"""

import itertools
import math
import random
import sys

import membw

DT = 0.02


# --- world -------------------------------------------------------------------

def waterfill(cap, demands, weights):
    """Weighted max-min fair split of cap, each client capped at its demand."""
    alloc = [0.0] * len(demands)
    active = {i for i, d in enumerate(demands) if d > 0}
    remaining = cap
    while active and remaining > 1e-12:
        wsum = sum(weights[i] for i in active)
        full = [i for i in active if alloc[i] + remaining * weights[i] / wsum >= demands[i]]
        if not full:
            for i in active:
                alloc[i] += remaining * weights[i] / wsum
            break
        for i in full:
            remaining -= demands[i] - alloc[i]
            alloc[i] = demands[i]
            active.discard(i)
    return alloc


class Client:
    """A DRAM client. Decoders: bpt > 0, rate = delivered GB/s / bpt.
    Sandboxes: bpt == 0, progress = delivered GB/s; cpu.max fraction q scales
    their reach by q**gamma."""

    def __init__(self, name, reach, compute_tok_s=math.inf, bpt=0.0, weight=1.0,
                 lat_sens=0.2, watts=10.0, psi=0.0, est_reach=None):
        self.name, self.reach, self.compute, self.bpt = name, reach, compute_tok_s, bpt
        self.weight, self.lat_sens, self.watts, self.psi = weight, lat_sens, watts, psi
        self.est_reach = reach if est_reach is None else est_reach
        self.q = 1.0


class World:
    def __init__(self, cap_gbps, alpha=0.5, knee=0.6, gamma=1.0, pl1=117.0,
                 p_base=8.0, ff_exp=0.5):
        self.cap, self.alpha, self.knee, self.gamma = cap_gbps, alpha, knee, gamma
        self.pl1, self.p_base, self.ff_exp = pl1, p_base, ff_exp

    def freq_factor(self, running):
        p = self.p_base + sum(c.watts * (c.q if c.bpt == 0 else 1.0) for c in running)
        if p <= self.pl1:
            return 1.0
        return ((self.pl1 - self.p_base) / (p - self.p_base)) ** self.ff_exp

    def latency(self, u):
        return 1.0 + self.alpha * max(0.0, u - self.knee) / (1.0 - self.knee)

    def deliver(self, running):
        """Delivered GB/s per running client (fixed point of demand under loaded latency)."""
        ff = self.freq_factor(running)
        base = []
        for c in running:
            if c.bpt > 0:
                base.append(min(c.reach * ff, c.compute * ff * c.bpt / 1e9))
            else:
                base.append(c.reach * ff * c.q ** self.gamma)
        w = [c.weight for c in running]
        alloc = waterfill(self.cap, base, w)
        for _ in range(12):
            u = min(0.999, sum(alloc) / self.cap)
            lat = self.latency(u)
            eff = [b / lat ** c.lat_sens for b, c in zip(base, running)]
            new = waterfill(self.cap, eff, w)
            alloc = [0.5 * a + 0.5 * n for a, n in zip(alloc, new)]
        return alloc


def p95(xs):
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))] if s else float("nan")


# --- S1/S2: admission ---------------------------------------------------------

def run_jobs(world, schedule, jobs, rng, sigma, horizon=400.0):
    """schedule(running, queued) -> list of jobs to start now. Each job is
    (client, tokens). Returns per-client ITL samples and makespan."""
    remaining = {c.name: n for c, n in jobs}
    carry = {c.name: 0.0 for c, _ in jobs}
    itl = {c.name: [] for c, _ in jobs}
    running, queued = [], [c for c, _ in jobs]
    t = 0.0
    while (running or queued) and t < horizon:
        for c in schedule(running, queued):
            queued.remove(c)
            running.append(c)
        if not running:
            break
        for c, gbps in zip(running, world.deliver(running)):
            rate = gbps * 1e9 / c.bpt
            carry[c.name] += rate * DT
            while carry[c.name] >= 1 and remaining[c.name] > 0:
                carry[c.name] -= 1
                remaining[c.name] -= 1
                itl[c.name].append(rng.lognormvariate(0, sigma) / rate)
        running = [c for c in running if remaining[c.name] > 0]
        t += DT
    return itl, t


def everything(running, queued):
    return list(queued)


def budget_policy(capacity, demand_of):
    def sched(running, queued):
        used = sum(math.ceil(demand_of(c)) for c in running)
        start = []
        for c in queued:
            q = math.ceil(demand_of(c))
            if used + q <= capacity or (not running and not start):
                used += q
                start.append(c)
        return start
    return sched


def admission_pods(r_igpu, est_err, rng):
    """ILLUSTRATIVE pod mix. bpt from real model shapes; reach/compute are placeholders."""
    llama8 = membw.bytes_per_token(8.03, 4.5, 32, 8, 128, 4096)    # Llama-3.1-8B int4, 4k ctx
    llama3 = membw.bytes_per_token(3.21, 4.5, 28, 8, 128, 4096)    # Llama-3.2-3B Q4, 4k ctx
    qwen15 = membw.bytes_per_token(1.54, 4.5, 28, 2, 128, 4096)    # Qwen2.5-1.5B Q4, 4k ctx
    embed = 0.22e9                                                 # bge-base fp16 weights per batch step
    def e():
        return rng.uniform(1 - est_err, 1 + est_err)
    return [
        (Client("igpu-ovms-8b", r_igpu, 6.0, llama8, lat_sens=1.0, watts=12, est_reach=r_igpu * e()), 300),
        (Client("cpu-llama-3b", 55.0, 30.0, llama3, lat_sens=0.3, watts=35, est_reach=55.0 * e()), 1500),
        (Client("ecore-qwen-1.5b", 22.0, 25.0, qwen15, lat_sens=0.3, watts=20, est_reach=22.0 * e()), 1500),
        (Client("embedder", 12.0, 60.0, embed, lat_sens=0.5, watts=10, est_reach=12.0 * e()), 3000),
    ]


TARGET_TOK_S = {"igpu-ovms-8b": 3.0, "cpu-llama-3b": 10.0, "ecore-qwen-1.5b": 10.0, "embedder": 20.0}


def s1(cap=65.0, r_igpu=25.0, w_igpu=1.0, alpha=0.5, knee=0.6, sigma=0.1, seed=1, est_err=0.2):
    rng = random.Random(seed)
    world = World(cap, alpha=alpha, knee=knee)
    jobs = admission_pods(r_igpu, est_err, rng)
    jobs[0][0].weight = w_igpu
    stream = cap * rng.gauss(1.0, 0.03)                 # node's own STREAM measurement
    capacity = membw.node_capacity(stream)

    def greedy(c):
        return membw.greedy_demand_gbps(c.bpt, c.est_reach, c.compute)

    def target(c):
        return membw.target_demand_gbps(c.bpt, TARGET_TOK_S[c.name])

    solo = {}
    for c, n in jobs:
        itl, _ = run_jobs(world, everything, [(c, n)], random.Random(seed), sigma)
        solo[c.name] = p95(itl[c.name])
    out = {}
    for name, sched in (("default", everything),
                        ("bw-target", budget_policy(capacity, target)),
                        ("bw-greedy", budget_policy(capacity, greedy))):
        itl, span = run_jobs(world, sched, jobs, random.Random(seed), sigma)
        deg = {k: p95(v) / solo[k] - 1 for k, v in itl.items()}
        out[name] = {"worst_deg": max(deg.values()), "deg": deg,
                     "agg_tok_s": sum(n for _, n in jobs) / span}
    return out


# --- S3/S4: guard -------------------------------------------------------------

def burst_trace(duty, horizon, rng, mean_on=3.0):
    """List of (start, end) victim-active intervals."""
    if duty >= 1.0:
        return [(0.0, horizon)]
    mean_off = mean_on * (1 - duty) / duty
    t, out = rng.expovariate(1 / mean_off), []
    while t < horizon:
        on = rng.expovariate(1 / mean_on)
        out.append((t, min(horizon, t + on)))
        t += on + rng.expovariate(1 / mean_off)
    return out


def guard_world(cap, r_igpu, w_igpu, alpha, knee, gamma, pl1, stream_reach=None):
    world = World(cap, alpha=alpha, knee=knee, gamma=gamma, pl1=pl1)
    llama8 = membw.bytes_per_token(8.03, 4.5, 32, 8, 128, 4096)
    victim = Client("igpu-ovms-8b", r_igpu, 6.0, llama8, weight=w_igpu, lat_sens=1.0, watts=12)
    reach = 0.9 * cap if stream_reach is None else stream_reach
    stream = Client("stream-sandbox", reach, lat_sens=0.1, watts=30, psi=0.2)
    # A capacity-pressured, bandwidth-light job (e.g. a build under a memory limit):
    # high PSI/refaults, ~1 GB/s DRAM. ILLUSTRATIVE.
    decoy = Client("psi-heavy-build", 1.0, lat_sens=0.1, watts=15, psi=8.0)
    return world, victim, [stream, decoy]


def run_guard(world, victim, sandboxes, trace, horizon, rng, sigma, mode, static_q=1.0,
              rank_signal="llc", solo_itl=None, score_noise=0.3):
    """mode: none | static | guard | guard-random | guard-blind.
    Returns (victim active tok/s, per-sandbox GB.s progress, victim ITLs)."""
    # Static baseline is an oracle: it caps only the bandwidth hog (sandbox 0).
    for i, s in enumerate(sandboxes):
        s.q = static_q if mode == "static" and i == 0 else 1.0
    guard = None
    if mode.startswith("guard"):
        # solo_itl = (p95, geometric mean, log-sd) from the victim's solo profile.
        p95_solo, gm_solo, sd_solo = solo_itl
        noise = rng.gauss(1.0, 0.03)          # solo profile measured on another run
        if mode == "guard-v1":
            guard = membw.Guard(p95_solo * noise, len(sandboxes), rng=rng)
        else:
            guard = membw.Guard(gm_solo * noise, len(sandboxes), rng=rng, sensor="geomean",
                                memory=mode != "guard-nomem", solo_log_sd=sd_solo,
                                rank="random" if mode == "guard-random" else "pressure")
    tok, act_time, carry = 0.0, 0.0, 0.0
    prog = [0.0] * len(sandboxes)
    itls, window = [], []
    tick, ti, t = 0.1, 0, 0.0
    since_tick = 0.0
    while t < horizon:
        while ti < len(trace) and trace[ti][1] <= t:
            ti += 1
        active = ti < len(trace) and trace[ti][0] <= t < trace[ti][1]
        running = ([victim] if active else []) + sandboxes
        alloc = world.deliver(running)
        if active:
            rate = alloc[0] * 1e9 / victim.bpt
            tok += rate * DT
            act_time += DT
            carry += rate * DT
            while carry >= 1:
                carry -= 1
                x = rng.lognormvariate(0, sigma) / rate
                itls.append(x)
                window.append((t, x))
        for i, a in enumerate(alloc[1 if active else 0:]):
            prog[i] += a * DT
        since_tick += DT
        if guard and since_tick >= tick:
            since_tick = 0.0
            window = [(ts, x) for ts, x in window if ts > t - 3.0]
            if mode == "guard-blind":
                samples = []
            elif mode == "guard-v1":   # v1: plain ITLs from the last 1 s
                samples = [x for ts, x in window if ts > t - 1.0]
            else:
                samples = window
            if rank_signal == "llc":   # per-cgroup LLC misses ~ DRAM traffic
                score = [a * rng.lognormvariate(0, score_noise) for a in alloc[1 if active else 0:]]
            else:                      # PSI memory / refaults: capacity stalls
                score = [s.psi * rng.lognormvariate(0, score_noise) for s in sandboxes]
            for s, q in zip(sandboxes, guard.step(samples, score, active=active, now=t)):
                s.q = q
        t += DT
    return (tok / act_time if act_time else float("nan")), prog, itls


def s3(cap=65.0, r_igpu=25.0, w_igpu=1.0, alpha=0.5, knee=0.6, gamma=1.0, pl1=117.0,
       duty=0.4, sigma=0.1, seed=7, horizon=120.0, rank_signal="llc", stream_reach=None):
    tr = burst_trace(duty, horizon, random.Random(seed))
    world, victim, sbx = guard_world(cap, r_igpu, w_igpu, alpha, knee, gamma, pl1, stream_reach)
    solo_rate, _, solo_itls = run_guard(world, victim, [], tr, horizon, random.Random(seed), sigma, "none")
    _, sbx_solo, _ = run_guard(world, victim, sbx, [], horizon, random.Random(seed), sigma, "none")
    logs = [math.log(x) for x in solo_itls]
    mu = sum(logs) / len(logs)
    sd = math.sqrt(sum((x - mu) ** 2 for x in logs) / len(logs))
    solo_itl = (p95(solo_itls), math.exp(mu), sd)

    def measure(mode, q=1.0):
        rate, prog, _ = run_guard(world, victim, sbx, tr, horizon, random.Random(seed), sigma, mode,
                                  static_q=q, rank_signal=rank_signal, solo_itl=solo_itl)
        slow = [1 - p / s for p, s in zip(prog, sbx_solo)]
        return {"victim": rate / solo_rate, "stream_slow": slow[0], "decoy_slow": slow[1],
                "sbx_slow": 1 - sum(prog) / sum(sbx_solo)}

    naive = measure("none")
    # Oracle static cpu.max: the highest quota on the bandwidth hog that holds
    # the victim >= 90%. Victim throughput falls monotonically in q, so bisect.
    lo, hi = 0.0, 1.0
    if naive["victim"] >= 0.9:
        lo = 1.0
    else:
        for _ in range(9):
            mid = (lo + hi) / 2
            if measure("static", mid)["victim"] >= 0.9:
                lo = mid
            else:
                hi = mid
    static = measure("static", lo) if lo > 0 else None
    return {"naive": naive, "static_q": lo, "static": static, "guard": measure("guard"),
            "guard_random": measure("guard-random"), "guard_blind": measure("guard-blind"),
            "guard_nomem": measure("guard-nomem"), "guard_v1": measure("guard-v1")}


def s3_verdict(r):
    g, st = r["guard"], r["static"]
    holds = g["victim"] >= 0.9
    if st is None:      # no static quota reaches 90%
        return holds, float("inf") if holds else float("nan")
    adv = 1 - g["stream_slow"] / st["stream_slow"] if st["stream_slow"] > 0 else 0.0
    return holds, adv


# --- report -------------------------------------------------------------------

def fmt(d):
    return " ".join(f"{k}={v:+.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in d.items())


def main(quick=False):
    print("== S1/S2 admission, default point (C=65 GB/s, iGPU reach 25, alpha=0.5) ==")
    r = s1()
    for k, v in r.items():
        print(f"  {k:10s} worst_p95_deg={v['worst_deg']:+.2f} agg_tok_s={v['agg_tok_s']:.1f}  "
              + fmt({n: x for n, x in v['deg'].items()}))
    agg = r["bw-greedy"]["agg_tok_s"] / r["default"]["agg_tok_s"] - 1
    print(f"  S1  pass={r['bw-greedy']['worst_deg'] <= 0.15 and r['default']['worst_deg'] >= 0.40}"
          f"  S1a (target demand protects?)={r['bw-target']['worst_deg'] <= 0.15}  S2 agg delta={agg:+.3f}")

    print("== S3 guard, default point (duty 0.4, LLC-miss ranking) ==")
    for sig in ("llc", "psi"):
        r = s3(rank_signal=sig)
        print(f"  rank={sig}: static_q={r['static_q']:.3f}")
        for k in ("naive", "static", "guard", "guard_nomem", "guard_v1", "guard_random", "guard_blind"):
            if r[k]:
                print(f"    {k:13s} " + fmt(r[k]))
        print(f"    S3 holds90,advantage = {s3_verdict(r)}")
    r = s3(duty=1.0)
    print(f"  S3a duty=1.0: {s3_verdict(r)}  static_q={r['static_q']:.3f}")

    print("== S4 attribution: infinite DRAM, power limit only ==")
    for pl1 in (117.0, 35.0):
        # Sandbox keeps its physical reach (0.9 x 65 GB/s); only DRAM capacity is removed.
        r = s3(cap=1e6, pl1=pl1, horizon=60.0, stream_reach=58.5)
        print(f"  PL1={pl1:.0f} W naive victim={r['naive']['victim']:.3f}")

    print("== S5 robustness grid ==")
    caps, reaches, ws, alphas, gammas = (50.0, 65.0, 80.0), (12.0, 25.0, 40.0), (0.5, 1.0, 1.5), \
        (0.0, 0.5, 1.5), (0.5, 1.0, 2.0)
    if quick:
        caps, reaches, ws, alphas, gammas = (65.0,), (12.0, 25.0), (0.5, 1.0), (0.0, 1.5), (1.0,)
    # Grid seeds differ from the seeds the guard constants were picked on (s1 seed 1, s3 seed 7).
    tally = dict.fromkeys(("s1", "s1n", "s3", "s3n", "cont", "s3c_ok", "nomem", "v1", "rand_worse",
                           "blind_fail", "s3a", "s3an"), 0)
    kill = {}
    rows = ["cap\treach\tw\talpha\tgamma\tnaive\tstatic_q\tstatic_slow\tguard_v\tguard_slow"
            "\tnomem_v\tnomem_slow\tv1_v\tv1_slow\trand_v\trand_sbx\tguard_sbx"]
    for cap, ri, w, a in itertools.product(caps, reaches, ws, alphas):
        r = s1(cap=cap, r_igpu=ri, w_igpu=w, alpha=a, sigma=0.2, seed=101)
        tally["s1n"] += 1
        tally["s1"] += r["bw-greedy"]["worst_deg"] <= 0.15 and r["default"]["worst_deg"] >= 0.40
        for g in gammas:
            r3 = s3(cap=cap, r_igpu=ri, w_igpu=w, alpha=a, gamma=g, sigma=0.2, horizon=60.0, seed=107)
            tally["s3n"] += 1
            holds, adv = s3_verdict(r3)
            ok = holds and adv >= 0.25
            tally["s3"] += ok
            contended = r3["naive"]["victim"] < 0.9
            if contended:
                tally["cont"] += 1
                tally["s3c_ok"] += ok
                for k in ("nomem", "v1"):
                    h, ad = s3_verdict(dict(r3, guard=r3["guard_" + k]))
                    tally[k] += h and ad >= 0.25
                gr, gd = r3["guard_random"], r3["guard"]
                tally["rand_worse"] += gr["victim"] < 0.9 or gr["sbx_slow"] > gd["sbx_slow"]
                tally["blind_fail"] += r3["guard_blind"]["victim"] < 0.9
            kill.setdefault(ri, []).append(r3["naive"]["victim"] > 1 / 1.15)
            st = r3["static"] or {"stream_slow": float("nan")}
            rows.append("\t".join(f"{x:.3f}" for x in (
                cap, ri, w, a, g, r3["naive"]["victim"], r3["static_q"], st["stream_slow"],
                r3["guard"]["victim"], r3["guard"]["stream_slow"],
                r3["guard_nomem"]["victim"], r3["guard_nomem"]["stream_slow"],
                r3["guard_v1"]["victim"], r3["guard_v1"]["stream_slow"],
                r3["guard_random"]["victim"], r3["guard_random"]["sbx_slow"], r3["guard"]["sbx_slow"])))
        if not quick:
            r3a = s3(cap=cap, r_igpu=ri, w_igpu=w, alpha=a, duty=1.0, sigma=0.2, horizon=60.0, seed=107)
            if r3a["naive"]["victim"] < 0.9:
                tally["s3an"] += 1
                h, adv = s3_verdict(r3a)
                tally["s3a"] += not (h and adv >= 0.25)
    t = tally
    print(f"  S1 pass {t['s1']}/{t['s1n']}   S3 pass {t['s3']}/{t['s3n']} all points, "
          f"{t['s3c_ok']}/{t['cont']} contended (naive victim < 0.9)")
    print(f"  contended ablations: no-memory pass {t['nomem']}/{t['cont']}, v1 pass {t['v1']}/{t['cont']}, "
          f"S3b random-rank worse {t['rand_worse']}/{t['cont']}, S3c blind misses 90% {t['blind_fail']}/{t['cont']}")
    if not quick:
        print(f"  S3a (duty=1, contended) advantage vanishes {t['s3a']}/{t['s3an']}")
    for ri, v in sorted(kill.items()):
        print(f"  kill region: iGPU reach {ri:>4.0f} GB/s -> naive degradation <15% in {sum(v)}/{len(v)} grid points")
    with open("s5_grid.tsv" if not quick else "s5_grid_quick.tsv", "w") as f:
        f.write("\n".join(rows) + "\n")

if __name__ == "__main__":
    main(quick="--quick" in sys.argv)

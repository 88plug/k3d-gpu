#!/usr/bin/env python3
"""Discrete-event simulation of an agent loop split across the engines of one
Intel client package (P-cores, E-cores, iGPU render, iGPU video, NPU).

Fluid model: between two events every running job progresses at a constant
rate; an event is a job finishing, which moves its agent to the next stage. At
each event the rates are recomputed in three steps:

  1. engine share   - each engine is time-shared CFS-style: an entity (an LLM
                      server thread group, or a single job) asking for `cap`
                      units gets cap * min(1, capacity / sum(caps)). Thread
                      groups squeezed below their thread count lose extra
                      throughput to spin-waiting (spin_penalty).
  2. package power  - busy units draw watts_per_unit * phi**dvfs_exponent on
                      DVFS engines (P, E, iGPU) and a fixed draw on fixed-
                      function engines (vcs, NPU). If the total exceeds PL1 one
                      common frequency factor phi is solved exactly.
  3. DRAM bandwidth - each job wants rate * bytes_per_unit; when the sum exceeds
                      dram_gbps the bandwidth is water-filled (max-min fair) and
                      a job runs at min(compute rate, bandwidth / bytes).
                      Concurrent decodes in one LLM server share one weight read
                      per step (continuous batching).

Service times are parameters (agent/params.json), not measurements.

Usage: sim.py [--params FILE] [--set key=value ...] [--json]
"""
import argparse
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CPU_ENGINES = ("P", "E")

PIPELINE = ("gateway", "embed_query", "vdb_search", "rerank", "llm_prefill",
            "llm_decode", "gateway", "tool", "media", "embed_ingest")

ROLE_OF = {"gateway": "gateway", "embed_query": "embed", "embed_ingest": "embed",
           "vdb_search": "vdb", "rerank": "rerank", "llm_prefill": "llm",
           "llm_decode": "llm", "tool": "tool", "media": "media"}


def load_params(path=None):
    with open(path or os.path.join(HERE, "params.json")) as f:
        return json.load(f)


def set_param(params, dotted, value):
    """--set package.pl1_w=35 style override."""
    node = params
    keys = dotted.split(".")
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = json.loads(value) if value[:1] in "[{0123456789-tf" else value


def topology(name, has_npu=False):
    """Role -> engine placement for the named topology."""
    accel = "npu" if has_npu else "igpu"
    if name == "BASE":
        return dict.fromkeys(ROLE_OF.values(), "P")
    if name == "CPU_SPLIT":
        return {"llm": "P", "embed": "E", "rerank": "E", "vdb": "E",
                "gateway": "E", "tool": "E", "media": "E"}
    if name == "SPLIT_P":
        return {"llm": "P", "embed": accel, "rerank": accel, "vdb": "E",
                "gateway": "E", "tool": "E", "media": "vcs"}
    if name == "SPLIT_G":
        side = "npu" if has_npu else "P"
        return {"llm": "igpu", "embed": side, "rerank": side, "vdb": "E",
                "gateway": "E", "tool": "E", "media": "vcs"}
    raise ValueError(f"unknown topology {name}")


TOPOLOGIES = ("BASE", "CPU_SPLIT", "SPLIT_P", "SPLIT_G")


class Job:
    __slots__ = ("agent", "stage", "engine", "group", "cap", "remaining",
                 "compute", "bytes", "shared_bytes", "rate")

    def __init__(self, agent, stage, engine, group, cap, units, compute, nbytes, shared):
        self.agent, self.stage, self.engine, self.group = agent, stage, engine, group
        self.cap, self.remaining = cap, units
        self.compute, self.bytes, self.shared_bytes = compute, nbytes, shared
        self.rate = 0.0


def water_fill(capacity, demands):
    """Max-min fair split of `capacity` over `demands`; returns allocations."""
    alloc = [0.0] * len(demands)
    left = sorted(range(len(demands)), key=lambda i: demands[i])
    cap = capacity
    while left:
        share = cap / len(left)
        i = left[0]
        if demands[i] <= share:
            alloc[i] = demands[i]
            cap -= demands[i]
            left.pop(0)
        else:
            for j in left:
                alloc[j] = share
            break
    return alloc


class Sim:
    def __init__(self, params, placement, agents, llm_threads, seed=1, side_threads=None):
        self.p = params
        self.place = placement
        self.n = agents
        self.llm_threads = llm_threads
        self.side_threads = side_threads   # thread cap for every non-LLM CPU stage
        self.rng = random.Random(seed)
        self.engines = params["engines"]
        for role, eng in placement.items():
            if self.engines[eng]["capacity"] <= 0:
                raise ValueError(f"role {role} placed on absent engine {eng}")
        self.t = 0.0
        self.jobs = []
        self.turn_start = [0.0] * agents
        self.stage_idx = [0] * agents
        self.latencies = []      # (finish_time, latency, energy_so_far)
        self.phi = 1.0
        self.power_w = 0.0
        self.energy_j = 0.0
        self.phi_time = 0.0      # integral of phi dt
        self.bw_time = 0.0       # integral of DRAM GB/s dt
        self.busy_time = {e: 0.0 for e in self.engines}

    # ---- workload ------------------------------------------------------
    def _resolve(self, v):
        if isinstance(v, str):
            return self.llm_threads if v == "llm_threads" else self.p["workload"][v]
        return v

    def _make_job(self, agent, stage):
        spec = self.p["stages"][stage]
        role = ROLE_OF[stage]
        eng = self.place[role]
        prob = spec.get("probability")
        if prob is not None and self.rng.random() >= self._resolve(prob):
            return None
        compute = spec["compute"].get(eng)
        if compute is None and eng == "E" and "P" in spec["compute"]:
            compute = spec["compute"]["P"] / self.p["e_core_perf"]
        if compute is None:
            raise ValueError(f"stage {stage} has no service time on engine {eng}")
        units = self._resolve(spec["units"])
        sigma = self.p["workload"]["lognormal_sigma"]
        units *= self.rng.lognormvariate(-sigma * sigma / 2, sigma)  # mean 1
        if eng in CPU_ENGINES:
            cap = min(self._resolve(spec["par"]), self.engines[eng]["capacity"])
            if role != "llm" and self.side_threads:
                cap = min(cap, self.side_threads)
        else:
            cap = 1
        group = "llm" if role == "llm" else None
        return Job(agent, stage, eng, group, cap, units, compute,
                   spec["bytes"], spec.get("shared_bytes", 0.0))

    def _advance_agent(self, agent):
        """Start the next stage with work for `agent`, closing turns as needed."""
        while True:
            i = self.stage_idx[agent]
            if i == len(PIPELINE):
                self.latencies.append((self.t, self.t - self.turn_start[agent], self.energy_j))
                self.turn_start[agent] = self.t
                self.stage_idx[agent] = i = 0
            self.stage_idx[agent] = i + 1
            job = self._make_job(agent, PIPELINE[i])
            if job is not None:
                self.jobs.append(job)
                return

    # ---- rates ---------------------------------------------------------
    def _rates(self):
        pk = self.p["package"]
        busy = {e: 0.0 for e in self.engines}
        share = {}
        for eng in self.engines:
            members = [j for j in self.jobs if j.engine == eng]
            if not members:
                continue
            entities = {}
            for j in members:
                key = j.group or id(j)
                entities.setdefault(key, []).append(j)
            caps = {k: v[0].cap for k, v in entities.items()}
            cap_sum = sum(caps.values())
            scale = min(1.0, self.engines[eng]["capacity"] / cap_sum)
            for k, js in entities.items():
                alloc = caps[k] * scale
                busy[eng] += alloc
                eff = alloc
                if eng in CPU_ENGINES and caps[k] > 1 and scale < 1.0:
                    eff = alloc * scale ** self.p["spin_penalty"]
                for j in js:
                    share[id(j)] = eff / len(js)

        dyn = sum(busy[e] * self.engines[e]["watts_per_unit"] for e in busy if self.engines[e]["dvfs"])
        fixed = sum(busy[e] * self.engines[e]["watts_per_unit"] for e in busy if not self.engines[e]["dvfs"])
        head = pk["pl1_w"] - pk["base_w"] - fixed
        if dyn <= 0 or pk["base_w"] + fixed + dyn <= pk["pl1_w"]:
            phi = 1.0
        elif head <= 0:
            phi = pk["phi_min"]
        else:
            phi = max(pk["phi_min"], (head / dyn) ** (1.0 / pk["dvfs_exponent"]))
        self.phi = phi
        self.busy = busy
        self.power_w = pk["base_w"] + fixed + dyn * phi ** pk["dvfs_exponent"]

        decoders = {}
        for j in self.jobs:
            if j.shared_bytes:
                decoders[j.group] = decoders.get(j.group, 0) + 1
        compute_rate, bpu, demand = [], [], []
        for j in self.jobs:
            f = phi if self.engines[j.engine]["dvfs"] else 1.0
            rc = share[id(j)] * f / j.compute
            b = j.bytes + (j.shared_bytes / decoders[j.group] if j.shared_bytes else 0.0)
            compute_rate.append(rc)
            bpu.append(b)
            demand.append(rc * b)
        bw = water_fill(pk["dram_gbps"], demand)
        for j, rc, b, a in zip(self.jobs, compute_rate, bpu, bw):
            j.rate = rc if b <= 0 else min(rc, a / b)
        self.bw_used = sum(bw)

    # ---- main loop -----------------------------------------------------
    def run(self, turns=400, warmup=40):
        for a in range(self.n):
            self._advance_agent(a)
        target = turns + warmup
        while len(self.latencies) < target:
            self._rates()
            dt = min(j.remaining / j.rate for j in self.jobs)
            self.t += dt
            self.energy_j += self.power_w * dt
            self.phi_time += self.phi * dt
            self.bw_time += self.bw_used * dt
            for e, u in self.busy.items():
                self.busy_time[e] += u * dt
            done = []
            for j in self.jobs:
                j.remaining -= j.rate * dt
                if j.remaining <= 1e-9 * max(1.0, j.rate):
                    done.append(j)
            for j in done:
                self.jobs.remove(j)
                self._advance_agent(j.agent)
        kept = self.latencies[warmup:target]
        t0, _, e0 = self.latencies[warmup - 1] if warmup else (0.0, 0.0, 0.0)
        span = kept[-1][0] - t0
        lat = sorted(x for _, x, _ in kept)
        return {
            "throughput": len(kept) / span,
            "mean_latency": sum(lat) / len(lat),
            "p95_latency": lat[min(len(lat) - 1, int(math.ceil(0.95 * len(lat))) - 1)],
            "joules_per_turn": (kept[-1][2] - e0) / len(kept),
            "mean_phi": self.phi_time / self.t,
            "mean_dram_gbps": self.bw_time / self.t,
            "util": {e: self.busy_time[e] / self.t / c["capacity"]
                     for e, c in self.engines.items() if c["capacity"] > 0},
        }


def evaluate(params, topo, agents, threads=(2, 4, 6, 8), side=(1, 2, 4, 8),
             seeds=(1, 2, 3), turns=400):
    """Tune this topology: best (LLM threads, side-stage thread cap) by mean
    throughput over seeds. Every topology gets the same grid."""
    has_npu = params["engines"]["npu"]["capacity"] > 0
    place = topology(topo, has_npu)
    if place["llm"] not in CPU_ENGINES:
        threads = (1,)
    best = None
    for k in threads:
        for c in side:
            runs = [Sim(params, place, agents, k, seed=s, side_threads=c).run(turns=turns)
                    for s in seeds]
            r = {m: sum(x[m] for x in runs) / len(runs) for m in runs[0] if m != "util"}
            r["util"] = {e: sum(x["util"][e] for x in runs) / len(runs) for e in runs[0]["util"]}
            r["llm_threads"], r["side_threads"] = k, c
            if best is None or r["throughput"] > best["throughput"]:
                best = r
    return best


def compare(params, agents, turns=400):
    rows = {t: evaluate(params, t, agents, turns=turns) for t in TOPOLOGIES}
    base = rows["BASE"]
    for t, r in rows.items():
        r["gain"] = r["throughput"] / base["throughput"]
        r["p95_vs_base"] = r["p95_latency"] / base["p95_latency"]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--params")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--agents", type=int, nargs="+", default=[1, 2, 4, 8])
    ap.add_argument("--turns", type=int, default=400)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    params = load_params(a.params)
    for kv in a.set:
        k, v = kv.split("=", 1)
        set_param(params, k, v)
    out = {n: compare(params, n, a.turns) for n in a.agents}
    if a.json:
        json.dump(out, sys.stdout, indent=1)
        print()
        return
    print(f"PL1={params['package']['pl1_w']} W  DRAM={params['package']['dram_gbps']} GB/s")
    print(f"{'N':>2} {'topology':<10} {'k':>2} {'s':>2} {'turns/s':>8} {'gain':>6} {'mean s':>7} {'p95 s':>7} {'p95/base':>8} {'J/turn':>7} {'phi':>5} {'GB/s':>5}  util P/E/igpu/vcs")
    for n, rows in out.items():
        for t, r in rows.items():
            print(f"{n:>2} {t:<10} {r['llm_threads']:>2} {r['side_threads']:>2} {r['throughput']:>8.3f} {r['gain']:>6.2f} "
                  f"{r['mean_latency']:>7.2f} {r['p95_latency']:>7.2f} {r['p95_vs_base']:>8.2f} {r['joules_per_turn']:>7.0f} "
                  f"{r['mean_phi']:>5.2f} {r['mean_dram_gbps']:>5.1f}  "
                  + "/".join(f"{r['util'].get(e, 0):.2f}" for e in ("P", "E", "igpu", "vcs")))


if __name__ == "__main__":
    main()

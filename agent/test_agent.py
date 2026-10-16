#!/usr/bin/env python3
"""Unit tests for agent/sim.py (pre-registered criterion S3) and agent/agent_profile.py.

Usage: python3 -m unittest discover -s agent -p 'test_*.py'
"""
import copy
import math
import unittest

import agent_profile
import sim

PARAMS = sim.load_params()


def params(**overrides):
    p = copy.deepcopy(PARAMS)
    for k, v in overrides.items():
        sim.set_param(p, k.replace("__", "."), str(v))
    return p


def bare_sim(p=None, place=None):
    p = p or params()
    return sim.Sim(p, place or sim.topology("BASE"), agents=1, llm_threads=8)


def job(engine="P", cap=1, units=1.0, compute=1.0, nbytes=0.0, group=None, shared=0.0, agent=0):
    return sim.Job(agent, "tool", engine, group, cap, units, compute, nbytes, shared)


class WaterFill(unittest.TestCase):
    def test_under_capacity_gets_demand(self):
        self.assertEqual(sim.water_fill(10, [1, 2, 3]), [1, 2, 3])

    def test_max_min_fair(self):
        self.assertEqual(sim.water_fill(9, [1, 10, 10]), [1, 4, 4])

    def test_conserves_capacity(self):
        self.assertAlmostEqual(sum(sim.water_fill(5, [3, 3, 3])), 5)


class Rates(unittest.TestCase):
    def test_single_job_runs_at_capacity_over_work(self):
        s = bare_sim()
        j = job(cap=4, compute=2.0)
        s.jobs = [j]
        s._rates()
        self.assertAlmostEqual(j.rate, 4 / 2.0)  # 4 cores, 2 core-s per unit

    def test_processor_sharing_two_equal_jobs(self):
        s = bare_sim()
        a, b = job(engine="igpu"), job(engine="igpu")
        s.jobs = [a, b]
        s._rates()
        self.assertAlmostEqual(a.rate, 0.5)
        self.assertAlmostEqual(b.rate, 0.5)

    def test_cfs_proportional_share_and_spin_penalty(self):
        s = bare_sim(params(spin_penalty=1.0))
        llm = job(cap=8, group="llm")
        tool = job(cap=1)
        s.jobs = [llm, tool]
        s._rates()
        scale = 8 / 9
        self.assertAlmostEqual(tool.rate, 1 * scale)            # one thread: no spin penalty
        self.assertAlmostEqual(llm.rate, 8 * scale * scale)      # squeezed thread group pays s^1

    def test_power_cap_solved_exactly(self):
        p = params(package__pl1_w=50)
        s = bare_sim(p)
        j = job(cap=8, compute=1.0)
        s.jobs = [j]
        s._rates()
        dyn = 8 * p["engines"]["P"]["watts_per_unit"]
        phi = ((50 - p["package"]["base_w"]) / dyn) ** (1 / 3)
        self.assertAlmostEqual(s.phi, phi)
        self.assertAlmostEqual(s.power_w, 50)
        self.assertAlmostEqual(j.rate, 8 * phi)

    def test_fixed_function_engine_not_throttled(self):
        s = bare_sim(params(package__pl1_w=20))
        p_job = job(cap=8)
        v_job = job(engine="vcs", compute=0.5)
        s.jobs = [p_job, v_job]
        s._rates()
        self.assertLess(s.phi, 1.0)
        self.assertAlmostEqual(v_job.rate, 1 / 0.5)

    def test_phi_floor_when_fixed_draw_exceeds_limit(self):
        s = bare_sim(params(package__pl1_w=5))
        s.jobs = [job(cap=8)]
        s._rates()
        self.assertEqual(s.phi, PARAMS["package"]["phi_min"])

    def test_bandwidth_cap(self):
        s = bare_sim(params(package__dram_gbps=10))
        j = job(cap=8, compute=0.001, nbytes=2.0)  # compute alone would allow 8000 units/s
        s.jobs = [j]
        s._rates()
        self.assertAlmostEqual(j.rate, 10 / 2.0)

    def test_batched_decode_shares_weight_read(self):
        s = bare_sim(params(package__dram_gbps=10))
        a = job(cap=8, group="llm", compute=1e-6, nbytes=0.0, shared=2.0)
        b = job(cap=8, group="llm", compute=1e-6, nbytes=0.0, shared=2.0)
        s.jobs = [a, b]
        s._rates()
        # one 2 GB weight read per step serves both: 1 GB per token each, 5 GB/s each
        self.assertAlmostEqual(a.rate, 5.0)
        self.assertAlmostEqual(b.rate, 5.0)


class EndToEnd(unittest.TestCase):
    def test_single_agent_turn_matches_analytic_sum(self):
        p = params(workload__lognormal_sigma=0, workload__media_probability=0,
                   package__dram_gbps=1e9, package__pl1_w=1e9)
        r = sim.Sim(p, sim.topology("BASE"), agents=1, llm_threads=8).run(turns=5, warmup=1)
        expect = 0.0
        for stage in sim.PIPELINE:
            spec = p["stages"][stage]
            if stage == "media":
                continue
            units = spec["units"] if not isinstance(spec["units"], str) else p["workload"][spec["units"]]
            par = 8 if spec["par"] == "llm_threads" else spec["par"]
            expect += units * spec["compute"]["P"] / par
        self.assertAlmostEqual(r["mean_latency"], expect, places=6)
        self.assertAlmostEqual(r["throughput"], 1 / expect, places=6)

    def test_deterministic_per_seed(self):
        place = sim.topology("SPLIT_P")
        a = sim.Sim(PARAMS, place, 3, 8, seed=7).run(turns=30, warmup=5)
        b = sim.Sim(PARAMS, place, 3, 8, seed=7).run(turns=30, warmup=5)
        c = sim.Sim(PARAMS, place, 3, 8, seed=8).run(turns=30, warmup=5)
        self.assertEqual(a, b)
        self.assertNotEqual(a["mean_latency"], c["mean_latency"])

    def test_absent_engine_rejected(self):
        with self.assertRaises(ValueError):
            sim.Sim(PARAMS, sim.topology("SPLIT_P", has_npu=True), 1, 8)

    def test_split_without_side_work_gains_nothing(self):
        p = params(workload__rerank_docs=0, workload__media_probability=0,
                   workload__ingest_chunks=0, stages__tool__compute__P=0.0001,
                   stages__embed_query__compute__P=0.0001, stages__vdb_search__compute__P=0.0001)
        rows = sim.compare(p, 4, turns=60)
        self.assertTrue(math.isclose(rows["CPU_SPLIT"]["gain"], 1.0, abs_tol=0.03))


class Profile(unittest.TestCase):
    HOST = "nvidia nvidia.com/gpu\nintel gpu.intel.com/i915\nhybrid k3d-gpu.io/hybrid-cpu=true\n"

    def test_this_host_renders_split_p(self):
        out = agent_profile.render(agent_profile.parse_detect(self.HOST))
        self.assertEqual(out["topology"], "SPLIT_P")
        roles = out["roles"]
        self.assertEqual(roles["llm"]["labels"], {"k3d-gpu.io/cores": "performance"})
        self.assertEqual(roles["tool"]["labels"], {"k3d-gpu.io/cores": "efficient"})
        self.assertEqual(roles["rerank"]["limits"], {"gpu.intel.com/i915": 1})
        self.assertEqual(roles["media"]["limits"], {"gpu.intel.com/i915": 1})
        self.assertEqual(out["requires"]["intel-gpu-plugin"], "-shared-dev-num=3")

    def test_npu_takes_embeddings(self):
        caps = agent_profile.parse_detect(self.HOST + "npu npu.intel.com/accel\n")
        roles = agent_profile.render(caps)["roles"]
        self.assertEqual(roles["embed"]["limits"], {"npu.intel.com/accel": 1})
        self.assertEqual(roles["media"]["limits"], {"gpu.intel.com/i915": 1})

    def test_xe_resource_used_when_detected(self):
        caps = agent_profile.parse_detect("intel gpu.intel.com/xe\nhybrid k3d-gpu.io/hybrid-cpu=true\n")
        roles = agent_profile.render(caps)["roles"]
        self.assertEqual(roles["rerank"]["limits"], {"gpu.intel.com/xe": 1})

    def test_fallbacks(self):
        self.assertEqual(agent_profile.render({"hybrid": []})["topology"], "CPU_SPLIT")
        base = agent_profile.render({})
        self.assertEqual(base["topology"], "BASE")
        self.assertEqual(base["roles"]["llm"]["labels"], {})
        self.assertEqual(base["requires"], {})

    def test_unsupported_topology_rejected(self):
        with self.assertRaises(ValueError):
            agent_profile.render({"hybrid": []}, "SPLIT_P")
        with self.assertRaises(ValueError):
            agent_profile.render({"intel": ["gpu.intel.com/i915"]}, "CPU_SPLIT")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests and falsification sims for promote-gate (promote-gate/promote_gate.py).

No cluster, no hardware: helm renders the fixture chart, twins are sysfs trees on disk
(two recorded 2-socket SNC Xeons from containers/nri-plugins pkg/sysfs test data, and
the i9-13900T this was written on).

Acceptance criteria, registered before the sims were run:

  CERTIFICATE (dev release == projection of the prod release)
  C1  legit projections, 50 seeded releases with equivalent quantity spellings:
      0 violations.
  C2  seeded edits, 50 per class: non-resource (M1), off-ratio resources (M2),
      balloon label not per table (M3), memory floor dropped (M4): 200/200 flagged.
  C3  path-filtered diff baseline (what dyff / kubectl diff with an ignore list does)
      flags M1 and misses M2-M4. If the baseline caught M2-M4 too, the certificate
      adds nothing over it and the claim is refuted.
  C4  ablation, quantities compared as strings: > 0 false positives on C1's set.
      This shows value canonicalization is what keeps the false-positive rate at 0.

  REPLAY (reduced balloons model on the prod twin)
  R1  clean release: 0 findings on the 112-CPU twin over apply order + 500 orders.
  R2  8 seeded misconfiguration classes, sized from each twin's own shape:
      8/8 detected on both Xeon twins, and nothing on the clean base.
  R3  ablation, NUMA topology removed (same CPUs and memory, one node): the
      NUMA classes (cross-NUMA CPU, cross-NUMA memory, order fragmentation) are no
      longer detected and the other 5 still are. If they stayed detected, the
      topology would not be doing the work.
  R4  ablation, scheduler-level feasibility only (what a KWOK twin sees): only the
      over-allocatable class is detected.
  R5  ablation, apply order only (no reorder replay): the fragmentation class is
      missed.
  R6  perturbation, CPU and NUMA ids randomly relabelled: the same classes are
      detected and balloon NUMA-span counts are unchanged.
  R7  check time (certify + replay with 20 orders) < 10 s.

Not testable here (needs a Xeon running the balloons policy): exact cpuset agreement
with the cpusets nri-plugins exports through NodeResourceTopology.
"""
import copy
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from fractions import Fraction

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "promote-gate")
TD = os.path.join(GATE, "testdata")
sys.path.insert(0, GATE)
import promote_gate as pg  # noqa: E402

XEON = os.path.join(TD, "twins/xeon-2s-snc-112c")
XEON88 = os.path.join(TD, "twins/xeon-2s-snc-88c")
LAPTOP = os.path.join(TD, "twins/i9-13900t")
HELM = shutil.which("helm")
RESULTS = {}


def load_yaml(p):
    with open(p) as f:
        return yaml.safe_load(f)


def render(values, tmp):
    p = os.path.join(tmp, f"v{random.random()}.yaml")
    with open(p, "w") as f:
        yaml.safe_dump(values, f)
    out = subprocess.run([HELM, "template", "stack", os.path.join(TD, "chart"), "-f", p],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


# ----------------------------------------------------------------- certificate helpers
def spell_cpu(milli, rng):
    """Equivalent spellings of a CPU quantity given in millicores."""
    opts = [f"{milli}m", str(milli / 1000)]
    if milli % 1000 == 0:
        opts.append(str(milli // 1000))
    return rng.choice(opts)


def spell_mem(mib, rng):
    opts = [f"{mib}Mi", str(mib * 2**20), f"{mib * 1024}Ki"]
    if mib % 1024 == 0:
        opts.append(f"{mib // 1024}Gi")
    return rng.choice(opts)


def dev_values_by_hand(prod_values, pmap, cpu_r, mem_r, rng):
    """Independent re-statement of the projection rule, written as values the way a
    developer would, with randomly chosen equivalent quantity spellings."""
    v = copy.deepcopy(prod_values)
    for c in v["components"]:
        cpu_milli = -(-pg.qty(c["cpu"]) * cpu_r * 1000 // 1)  # ceil
        mem_mib = -(-pg.qty(c["memory"]) * mem_r // 2**20)
        floor = (pmap["floors"].get(c["name"]) or {}).get("memory")
        if floor:
            mem_mib = max(mem_mib, pg.qty(floor) / 2**20)
        c["cpu"] = spell_cpu(int(max(cpu_milli, 1)), rng)
        c["memory"] = spell_mem(int(mem_mib), rng)
        labels = c.pop("labels", None) or {}
        b = labels.pop("k3d-gpu.io/balloon", None)
        if b:
            labels["k3d-gpu.io/cores"] = pmap["labelTable"]["map"][b]
        if labels:
            c["labels"] = labels
    return v


def random_prod(base, rng):
    v = copy.deepcopy(base)
    for c in v["components"]:
        c["cpu"] = rng.choice([str(rng.randint(1, 24)), f"{rng.randint(1, 48) * 500}m"])
        c["memory"] = rng.choice([f"{rng.randint(1, 32)}Gi", f"{rng.randint(1, 64) * 512}Mi"])
        floor = {"ovms-llm": 10, "embedder": 2}.get(c["name"])
        if floor and pg.qty(c["memory"]) < floor * 2**30:
            c["memory"] = f"{floor + rng.randint(0, 20)}Gi"
    return v


# ----------------------------------------------------------------- replay helpers
def twin_shape(twin):
    t = pg.Topology(twin)
    cpu_nodes = [n for n in t.nodes.values() if n["cpus"]]
    return {"N": min(len(n["cpus"]) for n in cpu_nodes), "K": len(cpu_nodes),
            "M": min(n["mem"] for n in cpu_nodes), "alloc": int(t.allocatable()[0])}


def comp(values, name):
    return next(c for c in values["components"] if c["name"] == name)


def clean_values(shape):
    v = load_yaml(os.path.join(TD, "values-prod.yaml"))
    comp(v, "ovms-llm")["cpu"] = str(shape["N"] - 8)
    return v


def misconfigs(shape):
    """class -> (values, policy-mutator or None, finding kind that must appear)."""
    N, K, M, A, R = shape["N"], shape["K"], shape["M"], shape["alloc"], 2
    out = {}
    v = clean_values(shape)
    comp(v, "vectordb")["cpu"] = str(A + 1)
    out["over-allocatable"] = (v, None, "UNSCHEDULABLE")

    v = clean_values(shape)
    used = 2 * (N - 8) + 8 + 6 + 4
    e = comp(v, "embedder")
    e.update(cpu="600m", memory="1Gi", replicas=(A - R - used) + 10)
    out["balloon-rounding-exhaustion"] = (v, None, "BALLOON_CPU_EXHAUSTED")

    v = clean_values(shape)
    comp(v, "ovms-llm")["cpu"] = str(N + 4)
    out["inference-wider-than-numa"] = (v, None, "CROSS_NUMA_CPU")

    v = clean_values(shape)
    o = comp(v, "ovms-llm")
    o.update(cpu=str(N - R), memory="4Gi", replicas=K)
    db = comp(v, "vectordb")
    db.update(cpu=str(2 * R), memory="1Gi")
    v["components"] = [o, db]
    out["order-fragmentation"] = (v, None, "ORDER_SENSITIVE")

    v = clean_values(shape)
    comp(v, "ovms-llm")["memory"] = f"{int(M * 1.5) // 2**20}Mi"
    out["memory-wider-than-numa"] = (v, None, "CROSS_NUMA_MEM")

    def laptop_core_type(policy):
        policy["balloonTypes"][0]["preferCoreType"] = "performance"
    out["laptop-core-type-in-prod-policy"] = (clean_values(shape), laptop_core_type, "CORE_TYPE_UNSAT")

    v = clean_values(shape)
    comp(v, "embedder")["labels"] = {"k3d-gpu.io/balloon": "inferense"}
    out["balloon-label-typo"] = (v, None, "NO_SUCH_BALLOON_TYPE")

    v = clean_values(shape)
    comp(v, "agent")["replicas"] = 10
    out["agent-balloon-over-maxcpus"] = (v, None, "MAX_CPUS_EXCEEDED")
    return out


def run_replay(twin, docs, mutate_policy=None, orders=20, scheduler_only=False):
    policy = pg.load_policy(os.path.join(twin, "balloonspolicy.yaml"))
    if mutate_policy:
        policy = copy.deepcopy(policy)
        mutate_policy(policy)
    gate = load_yaml(os.path.join(TD, "projection-map.yaml"))["gate"]
    f, balloons = pg.replay(pg.Topology(twin), policy, pg.expand_pods(docs), gate, orders=orders)
    if scheduler_only:
        f = [x for x in f if x.get("level") == "scheduler"]
    return {x["kind"] for x in f}, balloons


def flatten(twin, out):
    """Same CPUs, cores and total memory; one NUMA node."""
    t = pg.Topology(twin)
    shutil.copytree(twin, out)
    nd = os.path.join(out, "sys/devices/system/node")
    shutil.rmtree(nd)
    os.makedirs(os.path.join(nd, "node0"))
    with open(os.path.join(nd, "node0/cpulist"), "w") as f:
        f.write(pg.fmt_list(t.cpus) + "\n")
    with open(os.path.join(nd, "node0/distance"), "w") as f:
        f.write("10\n")
    with open(os.path.join(nd, "node0/meminfo"), "w") as f:
        f.write(f"Node 0 MemTotal: {sum(n['mem'] for n in t.nodes.values()) // 1024} kB\n")
    with open(os.path.join(nd, "online"), "w") as f:
        f.write("0\n")
    return out


def relabel(twin, out, seed):
    """Copy of twin with CPU ids and NUMA node ids randomly permuted."""
    rng = random.Random(seed)
    t = pg.Topology(twin)
    cpus, nodes = sorted(t.cpus), sorted(t.nodes)
    pc = dict(zip(cpus, rng.sample(cpus, len(cpus))))
    pn = dict(zip(nodes, rng.sample(nodes, len(nodes))))
    inv = {v: k for k, v in pn.items()}
    os.makedirs(out)
    for fn in ("twin.yaml", "balloonspolicy.yaml"):
        shutil.copy(os.path.join(twin, fn), out)
    sysd = os.path.join(out, "sys/devices/system")

    def w(rel, text):
        p = os.path.join(sysd, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text + "\n")

    w("cpu/online", pg.fmt_list(pc.values()))
    for c, info in t.cpus.items():
        d = f"cpu/cpu{pc[c]}/topology"
        w(f"{d}/physical_package_id", str(info["pkg"]))
        w(f"{d}/die_id", str(info["die"]))
        w(f"{d}/core_id", str(min(pc[s] for s in info["core"])))
        w(f"{d}/thread_siblings_list", pg.fmt_list(pc[s] for s in info["core"]))
    w("node/online", pg.fmt_list(pn.values()))
    for n, info in t.nodes.items():
        d = f"node/node{pn[n]}"
        w(f"{d}/cpulist", pg.fmt_list(pc[c] for c in info["cpus"]))
        w(f"{d}/distance", " ".join(str(t.distance(n, inv[m])) for m in sorted(pn.values())))
        w(f"{d}/meminfo", f"Node {pn[n]} MemTotal: {info['mem'] // 1024} kB")
    return out


# ----------------------------------------------------------------- tests
@unittest.skipUnless(HELM, "helm not on PATH")
class Certificate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.pmap = load_yaml(os.path.join(TD, "projection-map.yaml"))
        cls.cpu_r, cls.mem_r = pg.ratios(cls.pmap, pg.Topology(XEON), pg.Topology(LAPTOP))
        cls.base = load_yaml(os.path.join(TD, "values-prod.yaml"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def cert(self, prod, dev, canonical=True):
        return pg.certify(prod, dev, self.pmap, self.cpu_r, self.mem_r, canonical)

    def test_hand_written_dev_values_certify(self):
        prod = render(self.base, self.tmp)
        dev = render(load_yaml(os.path.join(TD, "values-dev.yaml")), self.tmp)
        self.assertEqual(self.cert(prod, dev), [])

    def test_projection_errors(self):
        v = copy.deepcopy(self.base)
        comp(v, "ovms-llm")["memory"] = "8Gi"  # below the 10Gi floor
        comp(v, "agent")["labels"] = {"k3d-gpu.io/balloon": "batch"}  # not in the table
        _, errors = pg.project(render(v, self.tmp), self.pmap, self.cpu_r, self.mem_r)
        self.assertEqual(len(errors), 3, errors)  # floor > requests, > limits; label

    def test_mutation_suite(self):
        rng = random.Random(1)
        counts = {k: {"ours": 0, "baseline": 0, "strings": 0, "n": 0}
                  for k in ("legit", "M1-non-resource", "M2-off-ratio", "M3-label", "M4-floor")}

        def score(cls, prod, dev):
            c = counts[cls]
            c["n"] += 1
            c["ours"] += bool(self.cert(prod, dev))
            c["baseline"] += bool(pg.baseline_ignore_diff(prod, dev, self.pmap))
            c["strings"] += bool(self.cert(prod, dev, canonical=False))

        for _ in range(50):
            pv = random_prod(self.base, rng)
            prod = render(pv, self.tmp)
            dv = dev_values_by_hand(pv, self.pmap, self.cpu_r, self.mem_r, rng)
            score("legit", prod, render(dv, self.tmp))

            m = copy.deepcopy(dv)
            c = rng.choice(m["components"])
            edit = rng.randrange(6)
            if edit == 0:
                c["image"] += "-rc1"
            elif edit == 1:
                c.setdefault("env", {})["DEBUG"] = "1"
            elif edit == 2:
                c["replicas"] = c.get("replicas", 1) + 1
            elif edit == 3:
                c["port"] = 9090
            elif edit == 4:
                c["args"] = ["--log_level", "DEBUG"]
            else:
                c.setdefault("labels", {})["tier"] = "canary"
            score("M1-non-resource", prod, render(m, self.tmp))

            # M2: prod CPU changed after dev was tested, or dev CPU hand-edited
            factor = rng.choice([Fraction(1, 2), Fraction(3, 4), Fraction(3, 2), 2, 3])
            name = rng.choice([x["name"] for x in pv["components"]])
            if rng.random() < 0.5:
                p2 = copy.deepcopy(pv)
                comp(p2, name)["cpu"] = f"{int(pg.qty(comp(p2, name)['cpu']) * factor * 1000)}m"
                score("M2-off-ratio", render(p2, self.tmp), render(dv, self.tmp))
            else:
                d2 = copy.deepcopy(dv)
                comp(d2, name)["cpu"] = f"{int(pg.qty(comp(d2, name)['cpu']) * factor * 1000)}m"
                score("M2-off-ratio", prod, render(d2, self.tmp))

            d3 = copy.deepcopy(dv)
            target = rng.choice([x for x in d3["components"] if "labels" in x])
            cur = target["labels"]["k3d-gpu.io/cores"]
            if rng.random() < 0.5:
                target["labels"]["k3d-gpu.io/cores"] = "efficient" if cur == "performance" else "performance"
            else:
                del target["labels"]["k3d-gpu.io/cores"]
            score("M3-label", prod, render(d3, self.tmp))

            d4 = copy.deepcopy(dv)
            o = comp(d4, "ovms-llm")
            unfloored = -(-pg.qty(comp(pv, "ovms-llm")["memory"]) * self.mem_r // 2**20)
            if unfloored >= 10 * 1024:  # floor not binding for this release: force it
                comp(pv, "ovms-llm")["memory"] = "11Gi"
                prod = render(pv, self.tmp)
                unfloored = -(-pg.qty("11Gi") * self.mem_r // 2**20)
            o["memory"] = f"{int(unfloored)}Mi"
            score("M4-floor", prod, render(d4, self.tmp))

        RESULTS["certificate"] = counts
        self.assertEqual(counts["legit"]["ours"], 0, "C1: false positives")
        for k in ("M1-non-resource", "M2-off-ratio", "M3-label", "M4-floor"):
            self.assertEqual(counts[k]["ours"], 50, f"C2: {k} not all flagged")
        self.assertEqual(counts["M1-non-resource"]["baseline"], 50)
        for k in ("M2-off-ratio", "M3-label", "M4-floor"):
            self.assertEqual(counts[k]["baseline"], 0, f"C3: baseline caught {k}")
        self.assertEqual(counts["legit"]["baseline"], 0)
        self.assertGreater(counts["legit"]["strings"], 0, "C4: string compare had no false positives")


@unittest.skipUnless(HELM, "helm not on PATH")
class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def detect(self, twin, orders=20, scheduler_only=False, shape_of=None):
        # misconfigs are sized from the real twin, also when replaying on an ablated copy
        shape = twin_shape(shape_of or twin)
        clean, _ = run_replay(twin, render(clean_values(shape), self.tmp), orders=orders,
                              scheduler_only=scheduler_only)
        got = {}
        for cls, (values, mut, kind) in misconfigs(shape).items():
            kinds, _ = run_replay(twin, render(values, self.tmp), mut, orders=orders,
                                  scheduler_only=scheduler_only)
            got[cls] = kind in kinds and kind not in clean
        return clean, got

    def test_clean_release_no_findings(self):
        docs = render(load_yaml(os.path.join(TD, "values-prod.yaml")), self.tmp)
        kinds, balloons = run_replay(XEON, docs, orders=500)
        self.assertEqual(kinds, set(), "R1")
        self.assertTrue(all(len(b["numaNodes"]) == 1 for b in balloons if b["type"] == "inference"))

    def test_same_release_differs_by_twin(self):
        # values-prod.yaml is clean on the 28-CPU-per-node twin; on the 22-CPU-per-node
        # twin its 20-CPU LLM balloons fragment under some apply orders.
        docs = render(load_yaml(os.path.join(TD, "values-prod.yaml")), self.tmp)
        self.assertIn("ORDER_SENSITIVE", run_replay(XEON88, docs, orders=500)[0])

    def test_misconfig_classes_and_ablations(self):
        numa = {"inference-wider-than-numa", "memory-wider-than-numa", "order-fragmentation"}
        res = {}
        for name, twin in (("xeon-112c", XEON), ("xeon-88c", XEON88)):
            clean, got = self.detect(twin)
            self.assertEqual(clean, set(), f"{name}: clean base has findings")
            self.assertEqual(sum(got.values()), 8, f"R2 {name}: {got}")
            res[name] = got

            flat = flatten(twin, os.path.join(self.tmp, f"flat-{name}"))
            _, got_flat = self.detect(flat, shape_of=twin)
            self.assertEqual({k for k, v in got_flat.items() if not v}, numa, f"R3 {name}: {got_flat}")
            res[name + "/no-numa"] = got_flat

            _, got_sched = self.detect(twin, scheduler_only=True)
            self.assertEqual({k for k, v in got_sched.items() if v}, {"over-allocatable"}, f"R4 {name}")
            res[name + "/scheduler-only"] = got_sched

            _, got_order = self.detect(twin, orders=0)
            self.assertEqual({k for k, v in got_order.items() if not v}, {"order-fragmentation"}, f"R5 {name}")
            res[name + "/apply-order-only"] = got_order

            for seed in range(3):
                rt = relabel(twin, os.path.join(self.tmp, f"relabel-{name}-{seed}"), seed)
                _, got_rl = self.detect(rt, shape_of=twin)
                self.assertEqual(got_rl, got, f"R6 {name} seed {seed}")
                docs = render(clean_values(twin_shape(twin)), self.tmp)
                spans = sorted(len(b["numaNodes"]) for b in run_replay(twin, docs)[1])
                spans_rl = sorted(len(b["numaNodes"]) for b in run_replay(rt, docs)[1])
                self.assertEqual(spans, spans_rl, f"R6 spans {name} seed {seed}")
        RESULTS["replay"] = {k: f"{sum(v.values())}/8" for k, v in res.items()}

    def test_magnitude_sweep(self):
        # The NUMA findings track the twin's node size, not one hand-picked value.
        for twin in (XEON, XEON88):
            sh = twin_shape(twin)
            for d in (1, 2, 3):
                v = clean_values(sh)
                comp(v, "ovms-llm")["cpu"] = str(sh["N"] + d)
                self.assertIn("CROSS_NUMA_CPU", run_replay(twin, render(v, self.tmp))[0])
            for f in (1.02, 1.1):
                v = clean_values(sh)
                comp(v, "ovms-llm")["memory"] = f"{int(sh['M'] * f) // 2**20}Mi"
                self.assertIn("CROSS_NUMA_MEM", run_replay(twin, render(v, self.tmp))[0])
            v = clean_values(sh)
            comp(v, "ovms-llm")["cpu"] = str(sh["N"] - 2)
            self.assertEqual(run_replay(twin, render(v, self.tmp), orders=0)[0], set())
            self.assertIn("ORDER_SENSITIVE", run_replay(twin, render(v, self.tmp), orders=200)[0])

    def test_check_time(self):
        prod = os.path.join(self.tmp, "prod.yaml")
        dev = os.path.join(self.tmp, "dev.yaml")
        for path, vf in ((prod, "values-prod.yaml"), (dev, "values-dev.yaml")):
            with open(path, "w") as f:
                subprocess.run([HELM, "template", "stack", os.path.join(TD, "chart"),
                                "-f", os.path.join(TD, vf)], check=True, stdout=f)
        t0 = time.monotonic()
        with open(os.devnull, "w") as null:
            old, sys.stdout = sys.stdout, null
            try:
                rc = pg.main(["check", "--map", os.path.join(TD, "projection-map.yaml"),
                              "--prod-twin", XEON, "--dev-twin", LAPTOP, prod, dev])
            finally:
                sys.stdout = old
        dt = time.monotonic() - t0
        RESULTS["check_seconds"] = round(dt, 3)
        self.assertEqual(rc, 0)
        self.assertLess(dt, 10, "R7")


class Units(unittest.TestCase):
    def test_qty(self):
        self.assertEqual(pg.qty("500m"), pg.qty("0.5"))
        self.assertEqual(pg.qty("1Gi"), pg.qty("1024Mi"))
        self.assertEqual(pg.qty("1e3"), 1000)
        with self.assertRaises(ValueError):
            pg.qty("1GB")

    def test_cpulist(self):
        self.assertEqual(pg.parse_list("0-2,5"), [0, 1, 2, 5])
        self.assertEqual(pg.fmt_list([5, 0, 1, 2]), "0-2,5")

    def test_capture_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            meta = pg.capture(XEON, os.path.join(d, "t"))
            self.assertEqual(meta["allocatable"]["cpu"], "112")
            self.assertEqual(pg.tree_hash(os.path.join(XEON, "sys")),
                             pg.tree_hash(os.path.join(d, "t", "sys")))

    def test_twins(self):
        x = pg.Topology(XEON)
        self.assertEqual(len(x.cpus), 112)
        self.assertEqual(sorted(len(n["cpus"]) for n in x.nodes.values()), [0] * 4 + [28] * 4)
        self.assertEqual(x.distance(0, 2), 11)  # SNC sibling on the same socket
        lap = pg.Topology(LAPTOP)
        self.assertTrue(lap.hybrid)
        self.assertEqual(sum(1 for c in lap.cpus.values() if c["type"] == "performance"), 16)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=2)
    if RESULTS:
        print(yaml.safe_dump(RESULTS, sort_keys=True))
    sys.exit(not prog.result.wasSuccessful())

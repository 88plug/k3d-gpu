#!/usr/bin/env python3
"""Unit and property tests for console/: schema_check, facts.py and plan().

Property tests draw random hosts (targets), matrices and overrides from a seeded
generator and check plan() against a brute-force oracle written from the rules
in docs/console-design.md (L2, L3), not from plan.py:
  - it picks a cell iff some green cell fits a usable target and no override
    excludes it, and then the one with the highest median decode tok/s (ties:
    smallest id);
  - with no such cell it names no engine, model or placement, and says "fix"
    with the one-line fix of every refused target when there is one, else
    "unmeasured: no green cell for this box; quickstart and agent up still pick by device";
  - every target the facts mark not usable is listed with its fix, picked or not;
  - it never picks an amber or grey cell;
  - it is pure: same output twice, inputs unchanged, row order irrelevant;
  - its output validates against the plan/v1 schema.

Usage: tests/console/test_console.py   (FUZZ_SEED=<int> to replay, FUZZ_N=<int> cases)
Stdlib only.
"""

import copy
import json
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))

import facts as factsmod  # noqa: E402
import plan as planmod  # noqa: E402
import schema_check  # noqa: E402

SEED = int(os.environ.get("FUZZ_SEED", random.SystemRandom().randrange(2**32)))
N = int(os.environ.get("FUZZ_N", "300"))
SCHEMA = factsmod.load_schema()
PIN = "openvino/model_server@sha256:" + "a" * 64
OTHER_IMAGE = "openvino/model_server@sha256:" + "b" * 64
UNMEASURED = "unmeasured: no green cell for this box; quickstart and agent up still pick by device"


# ---- generators -------------------------------------------------------------

def gen_targets(rng):
    hybrid = rng.random() < 0.5
    ts = [factsmod.target(id="cpu", kind="cpu", vendor="intel", cpu={
        "model_name": "cpu", "family_model": rng.choice(["6:183", "6:197", "6:173"]),
        "logical_cpus": 32, "hybrid": hybrid, "p_cpus": "0-15" if hybrid else None,
        "e_cpus": "16-31" if hybrid else None, "p_count": 16 if hybrid else None,
        "e_count": 16 if hybrid else None, "amx": rng.random() < 0.3, "numa_nodes": 1})]
    for i in range(rng.randrange(0, 3)):
        usable = rng.random() < 0.8
        drv = "xe" if usable else None
        ts.append(factsmod.target(
            id="gpu:0000:0%d:00.0" % i, kind="gpu", vendor="intel", pci="0000:0%d:00.0" % i,
            device_id=rng.choice(["0xa780", "0xe20b", "0x7d51"]), platform="p", driver=drv,
            resource="gpu.intel.com/xe" if usable else None, xmx=rng.choice([True, False, None]),
            usable=usable, reason=None if usable else "GuC x/y_guc_70.bin 70.1.1 is older than the 70.5.1 xe wants",
            fix=None if usable else "install linux-firmware with x/y_guc_70.bin 70.5.1 or newer, then reboot"))
    if rng.random() < 0.3:
        ts.append(factsmod.target(id="npu:0000:00:0b.0", kind="npu", vendor="intel", pci="0000:00:0b.0",
                                  device_id="0x7d1d", driver="intel_vpu", resource="npu.intel.com/accel"))
    if rng.random() < 0.3:
        ok = rng.random() < 0.5
        ts.append(factsmod.target(id="hpu", kind="hpu", vendor="habana", resource="habana.ai/gaudi",
                                  driver="habanalabs" if ok else None, usable=ok,
                                  reason=None if ok else "habanalabs driver not loaded",
                                  fix=None if ok else "sudo modprobe habanalabs"))
    return ts


def gen_row(rng, i, targets):
    # Mostly a kind this host has, so a fair share of cases end in a pick.
    kind = rng.choice([t["kind"] for t in targets]) if rng.random() < 0.8 else rng.choice(["cpu", "gpu", "npu", "hpu"])
    match = {"kind": kind}
    ref = rng.choice([t for t in targets if t["kind"] == kind] or [None])
    if kind == "cpu":
        if rng.random() < 0.7:
            match["family_model"] = (ref or targets[0])["cpu"]["family_model"] if rng.random() < 0.8 else "6:1"
        if rng.random() < 0.4:
            match["hybrid"] = rng.random() < 0.5
        placement = rng.choice(["p-cores", "cpu", "cpu", "device"])
    else:
        if rng.random() < 0.7:
            match["device_id"] = ref["device_id"] if ref and ref["device_id"] and rng.random() < 0.8 else "0x0000"
        if kind == "gpu" and rng.random() < 0.3:
            match["xmx"] = rng.random() < 0.5
        placement = rng.choice(["device", "device", "device", "cpu"])
    return {
        "id": "cell-%02d" % i, "family": "f", "device": "d", "match": match,
        "engine": {"name": rng.choice(["ovms", "ovms", "llama.cpp"]),
                   "image": PIN if rng.random() < 0.85 else OTHER_IMAGE, "device": kind.upper()},
        "model": {"name": rng.choice(["m1", "m2"]), "revision": None, "precision": "int4"},
        "placement": placement,
        "source": rng.choice(["report", "report", "report", "ledger", "community"]),
        "evidence": {"ledger": None, "report_id": "r%d" % i if rng.random() < 0.8 else None},
        "measured_on": "2026-10-16", "release": "test", "host_fingerprint": None, "reps": 5,
        "metrics": {"decode_tok_s": {"median": rng.choice([5, 9.5, 12, 12, 20]), "ci95": None}},
        "tool_eval": rng.choice([None, {"result": "pass", "suite": "s"}, {"result": "pass", "suite": "s"},
                                 {"result": "fail", "suite": "s"}]),
    }


def gen_case(rng):
    targets = gen_targets(rng)
    pins = {"ovms": PIN}
    if rng.random() < 0.5:
        pins["llama.cpp"] = PIN
    facts = {"fingerprint": "sha256:" + "0" * 64, "release": {"k3d_gpu_version": "t", "pins": pins},
             "targets": targets}
    matrix = {"schema": "k3d-gpu.matrix/v1", "rows": [gen_row(rng, i, targets) for i in range(rng.randrange(0, 9))]}
    ov = {}
    if rng.random() < 0.2:
        ov["engine"] = rng.choice(["ovms", "llama.cpp"])
    if rng.random() < 0.2:
        ov["device"] = rng.choice(["cpu", "gpu", "npu", "hpu"])
    if rng.random() < 0.2:
        ov["model"] = rng.choice(["m1", "m2"])
    if rng.random() < 0.3:
        ov["bind"] = rng.choice(["127.0.0.1", "::1", "localhost", "0.0.0.0", "192.168.1.5", "127.0.0.2"])
    if rng.random() < 0.2:
        ov["port"] = rng.randrange(1, 65536)
    return facts, matrix, ov


# ---- oracle -----------------------------------------------------------------

def oracle_green(row, pins):
    return (row["source"] != "community" and bool(row["evidence"]["report_id"])
            and row["tool_eval"] is not None and row["tool_eval"]["result"] == "pass"
            and pins.get(row["engine"]["name"]) == row["engine"]["image"])


def oracle_fits(row, t):
    m = row["match"]
    if t["kind"] != m["kind"]:
        return False
    for k in ("device_id", "vendor", "platform", "xmx"):
        if k in m and (t[k] is None or type(t[k]) is not type(m[k]) or t[k] != m[k]):
            return False
    for k in ("family_model", "hybrid", "amx"):
        if k in m and (t["cpu"] is None or type(t["cpu"][k]) is not type(m[k]) or t["cpu"][k] != m[k]):
            return False
    if row["placement"] == "p-cores":
        return t["kind"] == "cpu" and t["cpu"]["hybrid"] is True
    if row["placement"] == "cpu":
        return t["kind"] == "cpu"
    return t["kind"] != "cpu"


def oracle(facts, matrix, ov):
    best = None
    for row in matrix["rows"]:
        if not oracle_green(row, facts["release"]["pins"]):
            continue
        ts = [t for t in facts["targets"] if t["usable"] and oracle_fits(row, t)]
        if not ts:
            continue
        if ov.get("engine", row["engine"]["name"]) != row["engine"]["name"]:
            continue
        if ov.get("model", row["model"]["name"]) != row["model"]["name"]:
            continue
        if ov.get("device", ts[0]["kind"]) != ts[0]["kind"]:
            continue
        key = (row["metrics"]["decode_tok_s"]["median"], [-ord(c) for c in row["id"]])
        if best is None or key > best[0]:
            best = (key, row, ts[0])
    return best


# ---- tests ------------------------------------------------------------------

class PlanProperties(unittest.TestCase):
    def test_plan_matches_oracle(self):
        rng = random.Random(SEED)
        for case in range(N):
            facts, matrix, ov = gen_case(rng)
            before = copy.deepcopy((facts, matrix, ov))
            ctx = "seed=%d case=%d" % (SEED, case)
            got = planmod.plan(facts, matrix, ov)
            self.assertEqual(before, (facts, matrix, ov), "plan() mutated its input, " + ctx)
            self.assertEqual(got, planmod.plan(facts, matrix, ov), "plan() not deterministic, " + ctx)
            shuffled = dict(matrix, rows=rng.sample(matrix["rows"], len(matrix["rows"])))
            self.assertEqual(got, planmod.plan(facts, shuffled, ov), "row order changed the plan, " + ctx)
            schema_check.check(got, SCHEMA, "#/$defs/plan")

            want = oracle(facts, matrix, ov)
            bind = ov.get("bind", "127.0.0.1")
            loop = bind in ("127.0.0.1", "::1", "localhost", "127.0.0.2")
            self.assertEqual(got["auth"], "loopback-key" if loop else "oidc", ctx)
            refused = [t for t in facts["targets"] if not t["usable"]]
            self.assertEqual([f["target"] for f in got["fixes"]], [t["id"] for t in refused], ctx)
            self.assertEqual([f["fix"] for f in got["fixes"]], [t["fix"] for t in refused], ctx)
            for t in refused:
                line = "%s refused: %s; fix: %s" % (t["id"], t["reason"], t["fix"])
                self.assertIn(line, got["summary"], ctx)
            if want is None:
                for k in ("cell", "engine", "model", "placement"):
                    self.assertIsNone(got[k], ctx)
                if refused:
                    self.assertEqual(got["decision"], "fix", ctx)
                    self.assertEqual(got["message"], got["summary"][0], ctx)
                    self.assertTrue(got["message"].startswith(refused[0]["id"] + " refused: "), ctx)
                    blocked = any(oracle_green(r, facts["release"]["pins"]) and oracle_fits(r, t)
                                  for r in matrix["rows"] for t in refused)
                    self.assertEqual(got["summary"][-2], planmod.NO_GREEN_LEFT if blocked else UNMEASURED, ctx)
                else:
                    self.assertEqual(got["decision"], "unmeasured", ctx)
                    self.assertEqual(got["message"], UNMEASURED, ctx)
                    self.assertEqual(got["summary"][0], UNMEASURED, ctx)
            else:
                _, row, tgt = want
                self.assertEqual(got["decision"], "green", ctx)
                self.assertEqual(got["cell"], row["id"], ctx)
                self.assertEqual(got["engine"], row["engine"], ctx)
                self.assertEqual(got["model"], row["model"], ctx)
                self.assertEqual(got["placement"]["target"], tgt["id"], ctx)
                self.assertEqual(got["placement"]["kind"], row["placement"], ctx)
                if row["placement"] == "device":
                    self.assertEqual(got["placement"]["resource"], tgt["resource"], ctx)
                if row["placement"] == "p-cores":
                    self.assertEqual(got["placement"]["cpus"], tgt["cpu"]["p_cpus"], ctx)
                self.assertEqual(got["port"], ov.get("port", 8080 if row["engine"]["name"] == "llama.cpp" else 8000), ctx)
            picked = [c for c in got["considered"] if c["cell"] == got["cell"]]
            for c in picked:
                self.assertEqual(c["status"], "green", ctx)
            for c in got["considered"]:
                self.assertEqual(c["eligible"], c["status"] == "green" and c["excluded"] is None, ctx)


class PlanCases(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "facts.json")) as f:
            self.facts = json.load(f)
        with open(os.path.join(REPO, "console", "matrix.v1.json")) as f:
            self.matrix = json.load(f)

    def green(self):
        m = copy.deepcopy(self.matrix)
        for i, r in enumerate(m["rows"]):
            r["evidence"]["report_id"] = "synthetic-%d" % i
            r["tool_eval"] = {"result": "pass", "suite": "synthetic"}
        return m

    def test_baked_matrix_is_unmeasured_everywhere(self):
        # Nothing in the baked matrix has a report run and a tool-calling eval.
        got = planmod.plan(self.facts, self.matrix)
        self.assertEqual(got["decision"], "unmeasured")
        self.assertEqual({c["status"] for c in got["considered"]}, {"grey"})

    def test_green_picks_fastest_and_device_override(self):
        got = planmod.plan(self.facts, self.green())
        self.assertEqual(got["cell"], "raptorlake-s.cpu-pcores.ovms.qwen3-4b-int4")
        self.assertEqual(got["placement"]["pod_label"], "k3d-gpu.io/cores=performance")
        self.assertEqual(got["placement"]["cpus"], "0-15")
        got = planmod.plan(self.facts, self.green(), {"device": "gpu"})
        self.assertEqual(got["cell"], "raptorlake-s.uhd770.ovms.qwen3-4b-int4")
        self.assertEqual(got["placement"]["resource"], "gpu.intel.com/i915")

    def test_pin_bump_expires_cells(self):
        facts = copy.deepcopy(self.facts)
        facts["release"]["pins"]["ovms"] = OTHER_IMAGE
        got = planmod.plan(facts, self.green())
        self.assertEqual(got["decision"], "unmeasured")
        self.assertTrue(all("engine pin moved" in " ".join(c["reasons"]) for c in got["considered"]))

    def test_community_cell_is_amber_and_never_picked(self):
        m = self.green()
        for r in m["rows"]:
            r["source"] = "community"
        got = planmod.plan(self.facts, m)
        self.assertEqual(got["decision"], "unmeasured")
        self.assertEqual({c["status"] for c in got["considered"]}, {"amber"})

    def test_refused_gpu_falls_back(self):
        # S3: the facts mark the iGPU refused (the gatekeeper's verdict); with every
        # cell green, the pick that was the iGPU under --device gpu becomes a fix.
        facts = copy.deepcopy(self.facts)
        gpu = next(t for t in facts["targets"] if t["id"] == "gpu:0000:00:02.0")
        gpu.update(usable=False, reason="GuC old", fix="install linux-firmware, then reboot")
        got = planmod.plan(facts, self.green())
        self.assertEqual(got["cell"], "raptorlake-s.cpu-pcores.ovms.qwen3-4b-int4")
        self.assertEqual(got["fixes"], [{"target": "gpu:0000:00:02.0", "reason": "GuC old",
                                         "fix": "install linux-firmware, then reboot"}])
        self.assertIn("gpu:0000:00:02.0 refused: GuC old; fix: install linux-firmware, then reboot", got["summary"])
        got = planmod.plan(facts, self.green(), {"device": "gpu"})
        self.assertEqual(got["decision"], "fix")
        self.assertIsNone(got["engine"])
        self.assertEqual(got["summary"][1], planmod.NO_GREEN_LEFT)

    def test_stale_guc_fixture_falls_back_to_vulkan_then_cpu(self):
        with open(os.path.join(REPO, "tests", "fixtures", "raptorlake-stale-guc", "facts.json")) as f:
            facts = json.load(f)
        with open(os.path.join(HERE, "matrix-fallback.json")) as f:
            matrix = json.load(f)
        self.assertEqual(planmod.plan(self.facts, matrix)["cell"], "synthetic.uhd770.ovms")
        got = planmod.plan(facts, matrix)
        self.assertEqual((got["cell"], got["engine"]["name"], got["placement"]["target"]),
                         ("synthetic.nvidia.llama-cpp-vulkan", "llama.cpp", "gpu:nvidia"))
        got = planmod.plan(facts, matrix, {"engine": "ovms"})
        self.assertEqual((got["cell"], got["placement"]["kind"]), ("synthetic.cpu-pcores.ovms", "p-cores"))
        self.assertEqual([f["target"] for f in got["fixes"]], ["gpu:0000:00:02.0"])

    def test_bad_overrides(self):
        for ov in ({"device": "tpu"}, {"port": 0}, {"port": 70000}, {"port": True}, {"bind": ""}, {"color": "x"}):
            with self.assertRaises(ValueError, msg=ov):
                planmod.plan(self.facts, self.matrix, ov)


class FactsUnits(unittest.TestCase):
    def test_gate_marks_the_gpu_refused(self):
        with open(os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "facts.json")) as f:
            facts = json.load(f)
        lookups = {"intel_gpu": {"0xa780": {"platform": "Raptor Lake-S", "xmx": False}},
                   "gpu_gate": {"0000:00:02.0": {"reason": "GuC old", "fix": "update it"}}}
        ts = factsmod.build_targets(facts["host"], lookups, facts["detected"])
        gpu = next(t for t in ts if t["id"] == "gpu:0000:00:02.0")
        self.assertEqual((gpu["usable"], gpu["reason"], gpu["fix"], gpu["resource"]),
                         (False, "GuC old", "update it", "gpu.intel.com/i915"))
        self.assertTrue(all(t["usable"] and t["fix"] is None for t in ts if t is not gpu))
        lookups["gpu_gate"] = {}
        ts = factsmod.build_targets(facts["host"], lookups, facts["detected"])
        self.assertTrue(all(t["usable"] for t in ts))

    def test_every_unusable_fixture_target_has_a_fix(self):
        for name in sorted(os.listdir(os.path.join(REPO, "tests", "fixtures"))):
            path = os.path.join(REPO, "tests", "fixtures", name, "facts.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                for t in json.load(f)["targets"]:
                    self.assertEqual(t["usable"], t["reason"] is None, name)
                    self.assertEqual(t["usable"], t["fix"] is None, name)

    def test_transcript_parse(self):
        text = "\n".join([
            "==> Checking docker...", "  ok: docker running", "==> CPU frequency (x)",
            "  good: fast", "  src:  https://example.invalid/doc",
            "  warn: package throttled", "        from:   /sys/x", "        why:    hot",
            "        apply:  echo 1", "        revert: echo 0", "  ok",
            "WARN: something on stderr", "ERROR: One or more checks failed.", "stray line",
        ])
        t = factsmod.parse_transcript(text, 1)
        self.assertEqual(t["exit"], 1)
        self.assertEqual([s["name"] for s in t["sections"]], ["Checking docker...", "CPU frequency (x)"])
        self.assertEqual(t["sections"][1]["good"], "fast")
        self.assertEqual([(f["level"], f["text"]) for f in t["findings"]], [
            ("ok", "docker running"), ("warn", "package throttled"), ("ok", ""),
            ("warn", "something on stderr"), ("fail", "One or more checks failed."), ("other", "stray line")])
        w = t["findings"][1]
        self.assertEqual((w["from"], w["why"], w["apply"], w["revert"]), ("/sys/x", "hot", "echo 1", "echo 0"))
        self.assertEqual(w["section"], "CPU frequency (x)")

    def test_continuation_line_joins_its_finding(self):
        # doctor prints a fix on an indented line under its finding (the NPU udev rule).
        text = "\n".join([
            "==> Checking Intel NPU...",
            "  warn: /dev/accel/accel0 is mode 644. Add the udev rule",
            '        SUBSYSTEM=="accel", MODE="0660"',
            "  ok: done",
        ])
        t = factsmod.parse_transcript(text, 0)
        self.assertEqual([(f["level"], f["text"]) for f in t["findings"]], [
            ("warn", '/dev/accel/accel0 is mode 644. Add the udev rule SUBSYSTEM=="accel", MODE="0660"'),
            ("ok", "done")])

    def test_intel_gpu_without_xe_or_i915_is_not_usable(self):
        # A GPU bound to vfio-pci (passed through) or to no driver is no place for an engine.
        host = {
            "cpu": {"model": "x", "logical_cpus": 4, "hybrid": False, "p_cores": None, "e_cores": None,
                    "p_core_cpus": 0, "e_core_cpus": 0, "amx": False},
            "intel_gpus": [
                {"pci": "0000:00:02.0", "device_id": "0xa780", "platform": "Raptor Lake-S", "driver": "vfio-pci"},
                {"pci": "0000:03:00.0", "device_id": "0xe20b", "platform": "Battlemage", "driver": None},
                {"pci": "0000:04:00.0", "device_id": "0xe20b", "platform": "Battlemage", "driver": "xe"},
            ],
            "npus": [], "nvidia": {"gpus": []},
            "gaudi": {"pci_devices": [], "accel_nodes": [], "driver_loaded": False},
        }
        gpus = [t for t in factsmod.build_targets(host, {}, []) if t["kind"] == "gpu"]
        self.assertEqual([(t["pci"], t["usable"], t["resource"], t["reason"]) for t in gpus], [
            ("0000:00:02.0", False, None, "no xe or i915 driver bound (driver: vfio-pci)"),
            ("0000:03:00.0", False, None, "no xe or i915 driver bound (driver: none)"),
            ("0000:04:00.0", True, "gpu.intel.com/xe", None)])

    def test_plan_excludes_a_gpu_with_no_intel_driver(self):
        with open(os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "facts.json")) as f:
            facts = json.load(f)
        with open(os.path.join(REPO, "console", "matrix.v1.json")) as f:
            matrix = json.load(f)
        for i, r in enumerate(matrix["rows"]):
            r["evidence"]["report_id"] = "synthetic-%d" % i
            r["tool_eval"] = {"result": "pass", "suite": "synthetic"}
        uhd = next(t for t in facts["targets"] if t["vendor"] == "intel" and t["kind"] == "gpu")
        uhd.update(driver="vfio-pci", usable=False, resource=None,
                   reason="no xe or i915 driver bound (driver: vfio-pci)")
        got = planmod.plan(facts, matrix, {"device": "gpu"})
        self.assertEqual(got["decision"], "unmeasured")
        cell = next(c for c in got["considered"] if c["cell"] == "raptorlake-s.uhd770.ovms.qwen3-4b-int4")
        self.assertFalse(cell["eligible"])
        self.assertEqual(cell["excluded"], "target gpu:0000:00:02.0 not usable: "
                         "no xe or i915 driver bound (driver: vfio-pci)")

    def test_fingerprint_ignores_software_state(self):
        with open(os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "facts.json")) as f:
            facts = json.load(f)
        ts = copy.deepcopy(facts["targets"])
        base = factsmod.fingerprint(ts)
        self.assertEqual(base, facts["fingerprint"])
        ts[1]["driver"], ts[1]["usable"] = None, False
        self.assertEqual(factsmod.fingerprint(ts), base)
        ts[1]["device_id"] = "0xa781"
        self.assertNotEqual(factsmod.fingerprint(ts), base)


class SchemaCheckUnits(unittest.TestCase):
    def test_unknown_keyword_is_an_error(self):
        with self.assertRaises(schema_check.SchemaError):
            schema_check.errors(1, {"type": "integer", "multipleOf": 2})

    def test_bool_is_not_integer_and_true_is_not_one(self):
        self.assertTrue(schema_check.errors(True, {"type": "integer"}))
        self.assertTrue(schema_check.errors(1, {"const": True}))
        self.assertFalse(schema_check.errors(1.0, {"const": 1}))

    def test_additional_properties(self):
        s = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
        self.assertFalse(schema_check.errors({"a": "x"}, s))
        self.assertTrue(schema_check.errors({"a": "x", "b": 1}, s))


if __name__ == "__main__":
    print("console tests: FUZZ_SEED=%d FUZZ_N=%d" % (SEED, N))
    unittest.main(argv=[sys.argv[0], "-v"] if "-v" in sys.argv else [sys.argv[0]])

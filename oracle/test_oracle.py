#!/usr/bin/env python3
"""Tests for the capacity oracle: schema, privacy projection, validator rules
V1-V12, matrix generator and `k3d-gpu share` (stdlib only; no cluster, no
network, no real gh).

A real capacity run is produced once, against bench/apb/test_capacity.py's
local stub server, so the validator is tested on samples the scorer itself
wrote. Privacy is tested as a property: every sim fixture sysroot is copied,
seeded with random planted identifiers (hostname, machine-id, DMI serials and
UUID, MAC, IPs), projected, and the bundle must hold none of them; when an
identifier is planted where the allowlist does copy text (the CPU model
string), the bundle must be rejected instead.

Run: python3 oracle/test_oracle.py      (ORACLE_FUZZ_N=<int> sets the number
of random identifier sets per fixture; ORACLE_UPDATE_GOLDEN=1 rewrites the
golden files)
"""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import pty
import random
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "bench", "apb"))
import bundle as B  # noqa: E402
import matrix as M  # noqa: E402
import project as P  # noqa: E402
import share as S  # noqa: E402
import validate as V  # noqa: E402
import capacity as cap  # noqa: E402
import test_capacity as TC  # noqa: E402

LAUNCHER = os.path.join(REPO, "scripts", "k3d-gpu")
FIXTURES = os.path.join(REPO, "tests", "fixtures")
GOLDEN = os.path.join(HERE, "testdata", "golden")
SEED = os.path.join(HERE, "seed")
UPDATE = os.environ.get("ORACLE_UPDATE_GOLDEN") == "1"
FUZZ_N = int(os.environ.get("ORACLE_FUZZ_N", "2"))
AGENT_IMAGE = "openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58"
MODEL = "OpenVINO/Qwen3-4B-int4-ov"

WORK = tempfile.mkdtemp(prefix="oracle-test-")
RUN = os.path.join(WORK, "run")


def fixtures():
    return sorted(d for d in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, d, "sysroot")))


def report_for(root):
    out = subprocess.run(["bash", LAUNCHER, "report", "--json", "--no-cluster"], capture_output=True, text=True,
                         env={**os.environ, "K3D_GPU_SYSROOT": root, "K3D_GPU_CLUSTER": "oracle-test"},
                         check=True, timeout=120)
    return json.loads(out.stdout)


def make_capacity_run():
    """One real `capacity` run against the stub server: steps 1, 2, 4 at a
    60 ms SLO, the third breaking it. Written to RUN."""
    root = os.path.join(WORK, "caproot")
    bindir = os.path.join(WORK, "capbin")
    os.makedirs(bindir)
    TC.stub_bin(bindir, "nvidia-smi", "exit 9\n")
    old = {k: os.environ.get(k) for k in ("K3D_GPU_SYSROOT", "PATH")}
    os.environ["K3D_GPU_SYSROOT"] = root
    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    TC.fake_host(root, pl1_w=65.0)
    ticker = TC.EnergyTicker(root, 20.0)
    stub = TC.LoadStub(base=0.002, per=0.03)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cap.main(["--url", stub.url, "--model", "m", "--slo", "0.06", "--reps", "2", "--max", "8",
                           "--time-scale", "0", "--quiet-s", "0.3", "--interval", "0.1", "--out", RUN])
    finally:
        stub.close()
        ticker.close()
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert rc == 0, rc
    with open(os.path.join(RUN, "capacity.json")) as f:
        return json.load(f)


CAP = None
HOST_ROOT = None
HOST_REPORT = None


def setUpModule():
    global CAP, HOST_ROOT, HOST_REPORT
    CAP = make_capacity_run()
    HOST_ROOT = os.path.join(WORK, "rpl")
    shutil.copytree(os.path.join(FIXTURES, "raptorlake-nvidia", "sysroot"), HOST_ROOT, symlinks=True)
    HOST_REPORT = report_for(HOST_ROOT)


def tearDownModule():
    shutil.rmtree(WORK, ignore_errors=True)


def cap_bundle(report=None, root=None, **kw):
    kw.setdefault("server_image", AGENT_IMAGE)
    kw.setdefault("model_id", MODEL)
    kw.setdefault("version", "0.1.19")
    return P.project_capacity(report or HOST_REPORT, CAP, RUN, root or HOST_ROOT, **kw)


def seed(arm="cpu"):
    with open(os.path.join(SEED, f"maintainer-i9-13900T-h6-{arm}.json")) as f:
        return json.load(f)


def reseal(b):
    return B.seal(b)


PINS = {"0.1.19": AGENT_IMAGE}


def check(b, **kw):
    kw.setdefault("pins", PINS)
    return V.validate(b, **kw)


def rules(res):
    return {r.split(" ", 1)[0] for r in res.reasons}


# ------------------------------------------------------------------ schema
class SchemaTest(unittest.TestCase):
    def test_seed_and_capacity_bundles_pass(self):
        schema = B.load_schema()
        for b in (seed("cpu"), seed("gpu"), cap_bundle()):
            self.assertEqual(B.check_schema(b, schema), [])

    def mutations(self):
        b = cap_bundle()
        out = []

        def m(name, fn):
            x = copy.deepcopy(b)
            fn(x)
            out.append((name, x))
        m("extra top key", lambda x: x.update(hostname="box"))
        m("extra nested key", lambda x: x["hardware"]["cpu"].update(serial="X1"))
        m("bad enum", lambda x: x["results"].update(engine="tpu"))
        m("bad pattern", lambda x: x["hardware"]["cpu"].update(model="box\nname"))
        m("bad type", lambda x: x["hardware"]["cpu"].update(logical_cpus="32"))
        m("missing key", lambda x: x["power"].pop("pl1_w"))
        m("bad week", lambda x: x.update(week="2026-10-16"))
        m("free text in levers", lambda x: x["levers"][0].update(state="on because my box"))
        m("non-hex digest", lambda x: x["tool"].update(server_image="openvino/model_server:latest"))
        m("pci address", lambda x: x["hardware"]["intel_gpus"][0].update(device_id="0000:00:02.0"))
        m("bool as int", lambda x: x["results"]["capacity"].update(agents_per_box=True))
        return out

    def test_mutations_fail_schema_and_V1(self):
        schema = B.load_schema()
        for name, x in self.mutations():
            with self.subTest(name):
                self.assertTrue(B.check_schema(x, schema), name)
                res = check(reseal(x) if "comparison_key" in x else x)
                self.assertEqual(res.verdict, "reject")
                self.assertIn("V1", rules(res))

    def test_agrees_with_jsonschema_when_installed(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed; the stdlib checker is the one CI runs")
        schema = B.load_schema()
        val = jsonschema.Draft202012Validator(schema)
        for name, x in [("seed", seed()), ("capacity", cap_bundle())] + self.mutations():
            with self.subTest(name):
                self.assertEqual(bool(B.check_schema(x, schema)), not val.is_valid(x), name)

    def test_unknown_schema_keyword_is_an_error(self):
        with self.assertRaises(B.SchemaError):
            B.check_schema(1, {"type": "integer", "multipleOf": 2})


# ------------------------------------------------------------------ privacy
def plant(root, rng):
    """Plant random identifiers where Linux keeps them. Returns the strings."""
    host = f"box-{rng.randrange(16 ** 6):06x}"
    mid = "%032x" % rng.getrandbits(128)
    serials = [f"SN{rng.randrange(10 ** 9):09d}" for _ in range(3)]
    uid = str(uuid.UUID(int=rng.getrandbits(128)))
    mac = ":".join(f"{rng.randrange(256):02x}" for _ in range(6))
    ip = f"10.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"
    user = f"user{rng.randrange(10 ** 6):06d}"
    files = {
        "proc/sys/kernel/hostname": host, "etc/hostname": host, "etc/machine-id": mid,
        "sys/class/dmi/id/product_serial": serials[0], "sys/class/dmi/id/board_serial": serials[1],
        "sys/class/dmi/id/chassis_serial": serials[2], "sys/class/dmi/id/product_uuid": uid,
        "sys/class/net/eth0/address": mac,
        "proc/net/fib_trie": f"  |-- {ip}\n     /32 host LOCAL\n",
        "etc/passwd": f"{user}:x:1000:1000::/home/{user}:/bin/bash\n",
    }
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.islink(p):
            os.unlink(p)
        with open(p, "w") as f:
            f.write(text + "\n")
    return [host, mid, *serials, uid, mac, ip, user, f"/home/{user}"]


def set_cpu_model(root, model):
    path = os.path.join(root, "proc", "cpuinfo")
    with open(path) as f:
        lines = f.read().splitlines()
    out = [f"model name\t: {model}" if ln.startswith("model name") else ln for ln in lines]
    if not any(ln.startswith("model name") for ln in lines):
        out.insert(0, f"model name\t: {model}")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


class PrivacyTest(unittest.TestCase):
    def test_planted_identifiers_never_reach_a_bundle(self):
        rng = random.Random(int(os.environ.get("ORACLE_FUZZ_SEED", "20261016")))
        for fx in fixtures():
            for i in range(FUZZ_N):
                with self.subTest(fixture=fx, case=i):
                    root = os.path.join(WORK, f"fz-{fx}-{i}")
                    shutil.copytree(os.path.join(FIXTURES, fx, "sysroot"), root, symlinks=True)
                    canaries = plant(root, rng)
                    rep = report_for(root)
                    # The report itself does carry the hostname: the projection must drop it.
                    self.assertEqual(rep["hostname"], canaries[0])
                    b = cap_bundle(rep, root)
                    text = B.canonical(b).decode().lower()
                    for c in canaries:
                        self.assertNotIn(c.lower(), text, f"{fx}: {c} leaked")
                    ids = S.local_identifiers(root)
                    for c in canaries[:6]:
                        self.assertIn(c, ids)
                    res = check(b, local_identifiers=ids)
                    self.assertNotEqual(res.verdict, "reject", res.reasons)
                    # An identifier where the allowlist copies text: rejected, never shipped.
                    set_cpu_model(root, f"Intel(R) Xeon(R) CPU {canaries[0]}")
                    b2 = cap_bundle(report_for(root), root)
                    res2 = check(b2, local_identifiers=S.local_identifiers(root))
                    self.assertEqual(res2.verdict, "reject")
                    self.assertIn("V3", rules(res2))
                    shutil.rmtree(root)

    def test_private_registry_and_served_model_names_never_reach_a_bundle(self):
        c = copy.deepcopy(CAP)
        c["endpoint"]["image"] = "harbor.corp-lab.example/openvino/model_server@sha256:" + "a" * 64
        b = P.project_capacity(HOST_REPORT, c, RUN, HOST_ROOT, version="0.1.19", model_id=MODEL)
        self.assertIsNone(b["tool"]["server_image"])
        self.assertNotIn("corp-lab", B.canonical(b).decode())
        for ref in ("docker.io/openvino/model_server", "openvino/model_server", "quay.io/a/b"):
            self.assertTrue(B.public_image(ref), ref)
        # A served name other than the default model needs an explicit --model-id.
        for served in ("alice-ml/private-finetune", "models/qwen", "box-1234/m"):
            c["endpoint"]["model"] = served
            with self.assertRaises(P.ProjectionError):
                P.project_capacity(HOST_REPORT, c, RUN, HOST_ROOT, version="0.1.19", server_image=AGENT_IMAGE)
        c["endpoint"]["model"] = MODEL.split("/")[-1]
        b = P.project_capacity(HOST_REPORT, c, RUN, HOST_ROOT, version="0.1.19", server_image=AGENT_IMAGE)
        self.assertEqual(b["workload"]["model"]["id"], MODEL)

    def test_hostile_values_in_copied_fields_are_dropped(self):
        root = os.path.join(WORK, "hostile")
        shutil.copytree(HOST_ROOT, root, symlinks=True)
        for rel, text in (("sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "box-7f3a"),
                          ("sys/firmware/acpi/platform_profile", "box-7f3a")):
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text + "\n")
        self.assertEqual(P.power(root), {**P.power(root), "epp": None, "platform_profile": None})
        rep = copy.deepcopy(HOST_REPORT)
        host = rep["host"]
        host["cpu"]["model"] = "Intel/box-7f3a"
        host["intel_gpus"] = [{"device_id": "0xa7a0", "platform": "box-7f3a.lan", "driver": "box",
                               "guc": {"version": "box-7f3a"}, "engines": [{"name": "box-7f3a"}]}]
        host["nvidia"] = {"driver": "box", "gpus": [{"device_id": "0x2bb1", "model": "box-7f3a"}]}
        hw = P.hardware(rep, root)
        self.assertIsNone(hw["cpu"]["model"])
        (g,) = hw["intel_gpus"]
        self.assertEqual((g["platform"], g["driver"], g["guc"]), (None, None, None))
        self.assertIsNone(hw["nvidia"]["driver"])
        self.assertIsNone(hw["nvidia"]["gpus"][0]["model"])
        self.assertNotIn("box-7f3a", json.dumps(hw))

    def test_dropped_report_sections(self):
        b = cap_bundle()
        text = B.canonical(b).decode()
        for gone in ('"hostname"', "generated_at", "cluster", "renderD", "card0", "0000:", "intel-rapl",
                     "p_core_cpus\":\"", "loadavg", "run_dir", "127.0.0.1", "http", "/"):
            if gone == "/":
                # Only the schema id, the image name and the model id may hold a slash.
                bare = text.replace(AGENT_IMAGE, "").replace(MODEL, "").replace(B.SCHEMA_ID, "")
                self.assertNotIn("/", bare)
            else:
                self.assertNotIn(gone, text)

    def test_golden_projection_per_fixture(self):
        """Any new key, or a changed value, shows up as a golden diff."""
        os.makedirs(GOLDEN, exist_ok=True)
        for fx in fixtures():
            with self.subTest(fixture=fx):
                root = os.path.join(FIXTURES, fx, "sysroot")
                b = cap_bundle(report_for(root), root)
                got = {k: b[k] for k in ("hardware", "power", "levers")}
                path = os.path.join(GOLDEN, f"{fx}.json")
                if UPDATE:
                    with open(path, "w") as f:
                        json.dump(got, f, indent=1, sort_keys=True)
                        f.write("\n")
                with open(path) as f:
                    self.assertEqual(got, json.load(f))

    def test_session_names_and_times_are_relative(self):
        b = cap_bundle()
        for s in b["results"]["capacity"]["steps"]:
            for r in s["reps"]:
                self.assertEqual(sorted(set(r["session"])), list(range(len(set(r["session"])))))
                self.assertLess(max(r["sent_us"]), 3600 * 10 ** 6)

    def test_extra_body_beyond_thinking_is_refused(self):
        c = dict(CAP, extra_body={"chat_template_kwargs": {"enable_thinking": False}, "user": "alice"})
        with self.assertRaises(P.ProjectionError):
            P.project_capacity(HOST_REPORT, c, RUN, HOST_ROOT, model_id=MODEL)

    def test_memory_rounds_to_standard_size(self):
        self.assertEqual(P.std_gib(131521428), 128)
        self.assertEqual(P.std_gib(65_000_000), 64)
        self.assertEqual(P.std_gib(15_900_000), 16)
        self.assertIsNone(P.std_gib(None))


# ------------------------------------------------------------------ validator
class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.b = cap_bundle()

    def test_honest_capacity_bundle_is_accepted(self):
        res = check(self.b)
        self.assertEqual(res.verdict, "accept", res.reasons)
        self.assertEqual(res.recomputed["agents_per_box"], CAP["agents_per_box"])
        self.assertEqual([s["p95_us"] for s in res.recomputed["steps"]],
                         [V.to_us(s["p95_s"] if s["p95_s"] != "inf" else float("inf")) for s in CAP["steps"]])

    def test_seed_is_accepted_only_when_trusted_and_matches_ledger_H6(self):
        for arm, dec, ttft in (("cpu", 15.66, 11.21), ("gpu", 9.89, 29.53)):
            res = check(seed(arm), trusted=True)
            self.assertEqual(res.verdict, "accept", res.reasons)
            self.assertEqual(res.tags, ["unreleased-build"])
            # EXPERIMENTS.md H6 run table, medians over the 5 rounds.
            self.assertEqual(round(res.recomputed["decode_tok_s"], 2), dec)
            self.assertEqual(round(res.recomputed["prefill_ttft_s"], 2), ttft)
            self.assertEqual(check(seed(arm)).verdict, "reject")

    def test_V1_size_and_columns(self):
        self.assertEqual(check(self.b, raw_size=B.MAX_BYTES + 1).verdict, "reject")
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["steps"][0]["reps"][0]["e2e_us"].pop()
        res = check(reseal(b))
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V1", rules(res))

    def test_V2_hashes_and_duplicates(self):
        b = copy.deepcopy(self.b)
        b["week"] = "2020-W01"
        res = check(b)
        self.assertIn("V2", rules(res))
        self.assertEqual(res.verdict, "reject")
        b = copy.deepcopy(self.b)
        b["comparison_key"] = "0" * 64
        b["content_sha256"] = B.content_hash(b)
        self.assertIn("V2", rules(check(b)))
        res = check(self.b, known_hashes={self.b["content_sha256"]})
        self.assertEqual(res.verdict, "reject")

    def test_V3_patterns(self):
        for name, val in (("ipv4", "Intel Core 10.0.0.12"), ("uuid", "Intel " + str(uuid.uuid4())),
                          ("email", "Intel a.b@example.com"), ("machine-id", "Intel " + "ab" * 16)):
            with self.subTest(name):
                b = copy.deepcopy(self.b)
                b["hardware"]["cpu"]["model"] = val
                res = check(reseal(b))
                self.assertEqual(res.verdict, "reject")
                self.assertIn("V3", rules(res))
        b = copy.deepcopy(self.b)
        b["hardware"]["intel_gpus"][0]["platform"] = "X/home/bob"
        self.assertIn("V3", rules(check(reseal(b))))
        hits = V.canary_hits({"a": "aa:bb:cc:dd:ee:ff", "b": "fe80::1", "c": "fine 1.2.3"})
        self.assertEqual({h.split(" ")[0] for h in hits}, {"$.a", "$.b"})
        self.assertEqual(V.canary_hits({"a": "26.35.39758.10", "b": "590.48", "c": "v1.37.1+k3s1"}), [])

    def test_V4_release_and_provenance(self):
        b = copy.deepcopy(self.b)
        b["tool"]["version"] = "git-" + "a" * 40
        res = check(reseal(b))
        self.assertEqual(res.verdict, "quarantine")
        self.assertIn("V4", rules(res))
        res = check(self.b, pins={"0.1.19": AGENT_IMAGE.replace("a928", "b928")})
        self.assertEqual(res.verdict, "quarantine")
        res = check(self.b, pins={}, repo=WORK)  # no git tags here
        self.assertEqual(res.verdict, "quarantine")
        b = copy.deepcopy(self.b)
        b["provenance"]["source"] = "maintainer-lab"
        self.assertEqual(check(reseal(b)).verdict, "reject")
        self.assertEqual(check(reseal(b), trusted=True).verdict, "accept")

    def test_V5_tampered_claims_are_rejected(self):
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["agents_per_box"] += 1
        res = check(reseal(b))
        self.assertEqual(res.verdict, "reject")
        self.assertTrue(any("agents per box" in r for r in res.reasons))
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["steps"][0]["claimed_p95_us"] //= 2
        self.assertIn("V5", rules(check(reseal(b))))
        # Faster samples at the failing step: the claimed fail no longer reproduces.
        b = copy.deepcopy(self.b)
        last = b["results"]["capacity"]["steps"][-1]
        for r in last["reps"]:
            r["e2e_us"] = [None if v is None else v // 10 for v in r["e2e_us"]]
        res = check(reseal(b))
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V5", rules(res))

    def test_V6_littles_law(self):
        b = copy.deepcopy(self.b)
        for s in b["results"]["capacity"]["steps"]:
            for r in s["reps"]:
                r["e2e_us"] = [None if v is None else v // 3 for v in r["e2e_us"]]
        self.assertIn("V6", rules(check(reseal(b))))

    def test_V7_throughput_ceiling(self):
        old = V.CEILING_SLACK
        V.CEILING_SLACK = 0.01
        try:
            res = check(self.b)
        finally:
            V.CEILING_SLACK = old
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V7", rules(res))
        self.assertGreaterEqual(check(self.b).recomputed["apb_ceiling"], CAP["agents_per_box"])

    def test_V8_bandwidth_roofline(self):
        b = seed("cpu")
        b["hardware"]["memory"].update(channels=2, configured_mts=400)
        b["workload"]["model"]["weights_bytes"] = 10 ** 10
        res = check(reseal(b), trusted=True)
        self.assertEqual(res.verdict, "quarantine")
        self.assertIn("V8", rules(res))
        b["levers"].append({"id": "spec_decode", "detected": True, "state": "on"})
        self.assertEqual(check(reseal(b), trusted=True).verdict, "accept")
        b = seed("cpu")
        b["hardware"]["memory"].update(channels=2, configured_mts=4800)
        b["workload"]["model"]["weights_bytes"] = 2 * 10 ** 9
        self.assertEqual(check(reseal(b), trusted=True).verdict, "accept")

    def test_V5_reply_longer_than_max_tokens(self):
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["steps"][0]["reps"][0]["out_tok"][0] = 10 ** 7
        res = check(reseal(b))
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V5", rules(res))
        s = seed("cpu")
        s["results"]["single_stream"]["decode"]["out_tok"][0] = 257
        res = check(reseal(s), trusted=True)
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V5", rules(res))

    def test_V8_without_roofline_absurd_decode_goes_to_review(self):
        s = seed("cpu")
        d = s["results"]["single_stream"]["decode"]
        d["e2e_us"] = [t + 1000 for t in d["ttft_us"]]
        res = check(reseal(s), trusted=True)
        self.assertEqual(res.verdict, "quarantine")
        self.assertIn("V8", rules(res))
        s["levers"].append({"id": "spec_decode", "detected": True, "state": "on"})
        self.assertEqual(check(reseal(s), trusted=True).verdict, "accept")

    def test_V3_private_registry_image(self):
        b = copy.deepcopy(self.b)
        b["tool"]["server_image"] = "harbor.corp-lab.example/ovms@sha256:" + "a" * 64
        res = check(reseal(b), pins={"0.1.19": b["tool"]["server_image"]})
        self.assertEqual(res.verdict, "reject")
        self.assertIn("V3", rules(res))

    def test_V9_energy(self):
        b = copy.deepcopy(self.b)
        b["power"]["pl1_w"] = 5
        b["power"]["pl2_w"] = None
        self.assertIn("V9", rules(check(reseal(b))))
        b = copy.deepcopy(self.b)
        s = b["results"]["capacity"]["steps"][0]
        s["j_per_turn_pkg"] *= 2
        self.assertIn("V9", rules(check(reseal(b))))
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["steps"][0]["energy_turns"] += 7
        self.assertIn("V9", rules(check(reseal(b))))

    def test_V10_box_state(self):
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["quiet_check_ok"] = False
        self.assertEqual(check(reseal(b)).verdict, "quarantine")
        b = copy.deepcopy(self.b)
        steps = b["results"]["capacity"]["steps"]
        failed = [s for s in steps if s["claimed_verdict"] != "pass"]
        bind = min(failed, key=lambda s: s["agents"]) if failed else max(steps, key=lambda s: s["agents"])
        bind["throttle_events"] = 12
        b["results"]["capacity"]["drift_ratio"] = 1.6
        res = check(reseal(b))
        self.assertEqual(res.verdict, "accept")
        self.assertEqual(sorted(res.tags), ["drifted", "throttled"])

    def test_V10_trusted_maintainer_run_on_a_busy_box_is_tagged(self):
        b = copy.deepcopy(self.b)
        b["results"]["capacity"]["quiet_check_ok"] = False
        b["provenance"]["source"] = "maintainer-lab"
        res = check(reseal(b), trusted=True)
        self.assertNotIn("V10", rules(res))
        self.assertIn("not-quiet", res.tags)
        self.assertIn("V10", rules(check(reseal(b))))

    def test_V11_monotone(self):
        b = copy.deepcopy(self.b)
        s2 = b["results"]["capacity"]["steps"][1]
        self.assertEqual(s2["agents"], 2)
        for r in s2["reps"]:
            r["e2e_us"] = [None if v is None else max(1, v // 50) for v in r["e2e_us"]]
        self.assertIn("V11", rules(check(reseal(b))))

    def test_V12_ab_arm_alone_is_tagged(self):
        b = copy.deepcopy(self.b)
        b["ab"] = {"lever": "pe_pinning", "arm": "on", "pair_nonce": "0123456789abcdef01234567"}
        self.assertIn("ab-unpaired", check(reseal(b)).tags)

    def test_cli_exit_codes(self):
        path = os.path.join(WORK, "b.json")
        with open(path, "w") as f:
            json.dump(self.b, f)
        r = subprocess.run([sys.executable, os.path.join(HERE, "validate.py"), "--pin", f"0.1.19={AGENT_IMAGE}", path],
                           capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "accept")
        r = subprocess.run([sys.executable, os.path.join(HERE, "validate.py"), os.path.join(SEED, "maintainer-i9-13900T-h6-cpu.json")],
                           capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 1)


# ------------------------------------------------------------------ matrix
def ss_variant(scale, source="community", arm="cpu"):
    """A seed copy with decode times scaled (speed / scale), as a community release bundle."""
    b = seed(arm)
    b["provenance"] = {"source": source, "note": "none"}
    b["tool"]["version"] = "0.1.19"
    d = b["results"]["single_stream"]["decode"]
    d["e2e_us"] = [t + int((e - t) * scale) for e, t in zip(d["e2e_us"], d["ttft_us"])]
    return reseal(b)


def write_bundles(d, bundles):
    os.makedirs(d, exist_ok=True)
    for i, b in enumerate(bundles):
        with open(os.path.join(d, f"b{i}-{b['content_sha256'][:8]}.json"), "w") as f:
            json.dump(b, f)


class MatrixTest(unittest.TestCase):
    def test_docs_oracle_matrix_md_is_the_generated_seed_matrix(self):
        m = M.build([SEED], [SEED])
        with open(os.path.join(REPO, "docs", "oracle-matrix.md")) as f:
            self.assertEqual(M.markdown(m), f.read())
        # H6 CPU and iGPU (single stream), H8 A1 (two runs, one cell) and A4.
        self.assertEqual(len(m["cells"]), 4)
        self.assertEqual({c["tier"] for c in m["cells"]}, {"provisional"})
        self.assertEqual(m["review"], [])

    def test_untrusted_seed_goes_to_review(self):
        m = M.build([SEED], [])
        self.assertEqual(m["cells"], [])
        self.assertEqual({r["verdict"] for r in m["review"]}, {"reject"})

    def test_tiers_votes_and_outliers(self):
        d = os.path.join(WORK, "mx1")
        write_bundles(d, [ss_variant(1.0), ss_variant(1.05), ss_variant(0.97)])
        m = M.build([d], pins=PINS)
        (c,) = m["cells"]
        self.assertEqual((c["n"], c["tier"]), (3, "verified"))
        self.assertLess(c["metrics"]["decode_tok_s"]["q1"], c["metrics"]["decode_tok_s"]["q3"])
        # Two bundles from one submitter are one vote.
        subs = {S.load_json(os.path.join(d, f))["content_sha256"]: "same" for f in sorted(os.listdir(d))[:2]}
        (c,) = M.build([d], submitters=subs, pins=PINS)["cells"]
        self.assertEqual((c["n"], c["tier"]), (2, "community"))
        # A fourth box at a third of the speed is an outlier for review.
        d2 = os.path.join(WORK, "mx2")
        write_bundles(d2, [ss_variant(1.0), ss_variant(1.02), ss_variant(0.98), ss_variant(3.0)])
        m = M.build([d2], pins=PINS)
        self.assertEqual([r["verdict"] for r in m["review"]], ["outlier"])
        self.assertEqual(m["cells"][0]["tier"], "provisional")

    def test_maintainer_plus_community_reproduction_is_verified(self):
        d = os.path.join(WORK, "mx3")
        b = ss_variant(1.0, source="maintainer-lab")
        write_bundles(d, [b, ss_variant(1.03)])
        (c,) = M.build([d], [d], pins=PINS)["cells"]
        self.assertEqual(c["tier"], "verified")

    def test_ab_pair_gives_an_uplift_and_a_lone_arm_does_not(self):
        d = os.path.join(WORK, "mx4")
        on, off = ss_variant(0.5), ss_variant(1.0)
        for b, arm in ((on, "on"), (off, "off")):
            b["ab"] = {"lever": "pe_pinning", "arm": arm, "pair_nonce": "00112233445566778899aabb"}
            reseal(b)
        write_bundles(d, [on, off])
        m = M.build([d], pins=PINS)
        (u,) = m["uplifts"]
        self.assertEqual(u["lever"], "pe_pinning")
        self.assertAlmostEqual(u["ratio_on_over_off"]["decode_tok_s"], 2.0, delta=0.05)
        self.assertEqual(m["cells"], [])
        d = os.path.join(WORK, "mx5")
        write_bundles(d, [on])
        self.assertEqual(M.build([d], pins=PINS)["uplifts"], [])

    def test_every_numeric_row_carries_its_data_url(self):
        md = M.markdown(M.build([SEED], [SEED]))
        rows = [ln for ln in md.splitlines() if ln.startswith("| ") and any(ch.isdigit() for ch in ln)
                and not ln.startswith("| SKU")]
        self.assertTrue(rows)
        for ln in rows:
            self.assertIn("https://github.com/88plug/k3d-gpu/blob/main/oracle/seed/", ln)


class LeaderboardCheckTest(unittest.TestCase):
    """CI runs `leaderboard.py --check` on every pull request. A community PR
    adds only data/oracle/<sha>.json, so the check must pass on it; the
    maintainer regenerates after merge."""

    def setUp(self):
        import leaderboard as L
        self.L = L
        self.tmp = tempfile.mkdtemp(dir=WORK)
        self.data = os.path.join(self.tmp, "data", "oracle")
        os.makedirs(self.data)
        self.md, self.js = os.path.join(self.tmp, "leaderboard.md"), os.path.join(self.tmp, "leaderboard.json")
        shutil.copy(os.path.join(REPO, "docs", "leaderboard.md"), self.md)
        shutil.copy(os.path.join(REPO, "docs", "leaderboard.json"), self.js)
        self.saved = (L.DATA, L.MD_OUT, L.JSON_OUT)
        L.DATA = self.data

    def tearDown(self):
        self.L.DATA, self.L.MD_OUT, self.L.JSON_OUT = self.saved

    def run_check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.L.main(["--check", "--md", self.md, "--json", self.js])
        return rc, out.getvalue()

    def test_a_data_only_pull_request_passes_the_ci_steps(self):
        b = cap_bundle()
        self.assertEqual(check(b).verdict, "accept")
        name = f"{b['content_sha256']}.json"
        with open(os.path.join(self.data, name), "w") as f:
            f.write(S.pretty(b))
        # The CI validate step on the data file, then the leaderboard check.
        self.assertEqual(V.validate(S.load_json(os.path.join(self.data, name)), pins=PINS).verdict, "accept")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertIn(f"data/oracle/{name} is pending", out)
        # The maintainer regenerates after merge; the check then lists it as an input.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.L.main(["--md", self.md, "--json", self.js]), 0)
        self.assertEqual(S.load_json(self.js)["inputs"], [name])
        rc, out = self.run_check()
        self.assertEqual((rc, out), (0, ""))
        # Deleting a listed input makes the committed board stale.
        os.remove(os.path.join(self.data, name))
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("is listed as an input but is gone", out)

    def test_committed_board_is_current(self):
        self.L.DATA = self.saved[0]
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)

    def test_sku_names_only_the_serving_device(self):
        b = seed("cpu")
        self.assertEqual(M.sku(b["hardware"], "cpu"), b["hardware"]["cpu"]["model"])
        self.assertNotIn("NVIDIA", M.sku(seed("gpu")["hardware"], "igpu"))
        self.assertIn("Raptor Lake-S", M.sku(seed("gpu")["hardware"], "igpu"))


# ------------------------------------------------------------------ share
GH_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_LOG"
case "$*" in
  "auth status") exit 0 ;;
  "api user --jq .login") echo "${GH_LOGIN:-octo-cat}" ;;
  "api repos/"*"/k3d-gpu --jq .default_branch") echo main ;;
  "api repos/"*"/git/ref/heads/main --jq .object.sha") echo 0123456789abcdef0123456789abcdef01234567 ;;
  "api -X PUT "*) cat > "$GH_LOG.put" ;;
  "pr create "*) echo https://github.com/88plug/k3d-gpu/pull/999 ;;
esac
exit "${GH_FAIL_RC:-0}"
"""


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=WORK)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.tmp, "gh.log")
        TC.stub_bin(self.bin, "gh", GH_STUB.replace("#!/bin/sh\n", ""))
        self.env = {**os.environ, "K3D_GPU_SYSROOT": HOST_ROOT, "GH_LOG": self.log,
                    "PATH": self.bin + os.pathsep + os.environ["PATH"], "XDG_STATE_HOME": os.path.join(self.tmp, "state")}

    def args(self, *extra):
        return ["bash", LAUNCHER, "share", "--capacity", os.path.join(RUN, "capacity.json"),
                "--server-image", AGENT_IMAGE, "--model-id", MODEL, *extra]

    def run_plain(self, *extra, stdin=subprocess.DEVNULL):
        return subprocess.run(self.args(*extra), cwd=self.tmp, env=self.env, stdin=stdin,
                              capture_output=True, text=True, timeout=120, check=False)

    def run_tty(self, answer, *extra):
        """Run with stdin/stdout on a pseudo-terminal and type `answer` at the prompt."""
        master, slave = pty.openpty()
        p = subprocess.Popen(self.args(*extra), cwd=self.tmp, env=self.env, stdin=slave, stdout=slave,
                             stderr=slave, close_fds=True)
        os.close(slave)
        out, sent, deadline = b"", False, time.time() + 120
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            if not sent and b"[y/N]" in out:
                os.write(master, answer.encode() + b"\n")
                sent = True
            if p.poll() is not None and not r:
                break
        p.wait(timeout=10)
        os.close(master)
        return p.returncode, out.decode(errors="replace")

    def gh_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    def files(self):
        return sorted(f for f in os.listdir(self.tmp) if f.startswith("k3d-gpu-share-"))

    def test_dry_run_prints_everything_writes_and_sends_nothing(self):
        r = self.run_plain("--dry-run", "--submit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"schema": "k3d-gpu.oracle.bundle/1"', r.stdout)
        self.assertIn("validator: accept", r.stdout)
        self.assertIn("not anonymous", r.stdout)
        self.assertIn("nothing written, nothing sent", r.stdout)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.gh_calls(), [])

    def test_without_submit_the_file_is_written_0600_and_nothing_sent(self):
        r = self.run_plain()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (name,) = self.files()
        path = os.path.join(self.tmp, name)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            b = json.load(f)
        self.assertEqual(name, f"k3d-gpu-share-{b['content_sha256'][:12]}.json")
        self.assertIn(f"sha256: {b['content_sha256']}", r.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_submit_without_a_terminal_refuses(self):
        r = self.run_plain("--submit")
        self.assertEqual(r.returncode, S.EXIT_NOT_SENT, r.stdout + r.stderr)
        self.assertIn("needs an interactive terminal", r.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_submit_declined_at_the_prompt_sends_nothing(self):
        for answer in ("n", "", "no", "yess", "Y please"):
            with self.subTest(answer=answer):
                rc, out = self.run_tty(answer, "--submit")
                self.assertEqual(rc, S.EXIT_NOT_SENT, out)
                self.assertIn("not sent", out)
                self.assertEqual(self.gh_calls(), [])

    def test_submit_confirmed_opens_one_pull_request_adding_the_bundle(self):
        rc, out = self.run_tty("y", "--submit")
        self.assertEqual(rc, 0, out)
        calls = self.gh_calls()
        self.assertEqual(calls[0], "auth status")
        self.assertIn("repo fork 88plug/k3d-gpu --clone=false --default-branch-only", calls)
        (create,) = [c for c in calls if c.startswith("pr create")]
        self.assertIn("--repo 88plug/k3d-gpu --base main --head octo-cat:oracle-", create)
        self.assertIn("pull/999", out)
        (name,) = self.files()
        with open(os.path.join(self.tmp, name)) as f:
            text = f.read()
        with open(self.log + ".put") as f:
            put = json.load(f)
        self.assertEqual(base64.b64decode(put["content"]).decode(), text)
        sha = json.loads(text)["content_sha256"]
        self.assertEqual(put["branch"], f"oracle-{sha[:12]}")
        (call,) = [c for c in calls if c.startswith("api -X PUT")]
        self.assertIn(f"repos/octo-cat/k3d-gpu/contents/data/oracle/{sha}.json", call)

    def test_submit_with_a_hostile_login_stops_before_any_write(self):
        self.env["GH_LOGIN"] = "x;rm -rf /"
        rc, out = self.run_tty("y", "--submit")
        self.assertEqual(rc, S.EXIT_NOT_SENT, out)
        self.assertIn("unexpected GitHub login", out)
        self.assertFalse([c for c in self.gh_calls() if c.startswith(("api -X", "pr create"))])

    def test_submit_gh_failure_prints_the_manual_steps(self):
        self.env["GH_FAIL_RC"] = "4"
        rc, out = self.run_tty("y", "--submit")
        self.assertEqual(rc, S.EXIT_NOT_SENT, out)
        self.assertIn("gh pr create --repo 88plug/k3d-gpu", out)

    def test_submit_without_gh_prints_the_pull_request_steps(self):
        rc, out = self.run_tty("yes", "--submit", "--no-gh")
        self.assertEqual(rc, 0, out)
        self.assertIn("data/oracle/", out)
        self.assertIn("gh pr create --repo 88plug/k3d-gpu", out)
        self.assertEqual(self.gh_calls(), [])

    def test_printed_bytes_are_the_bundle(self):
        for b in (cap_bundle(), seed("cpu"), seed("gpu")):
            text = S.pretty(b)
            self.assertEqual(json.loads(text), b)
            self.assertLess(len(text), 2 * len(B.canonical(b)))

    def test_identifier_in_the_cpu_model_blocks_the_bundle(self):
        root = os.path.join(self.tmp, "root")
        shutil.copytree(HOST_ROOT, root, symlinks=True)
        host = plant(root, random.Random(7))[0]
        set_cpu_model(root, f"Intel(R) Core(TM) {host}")
        self.env["K3D_GPU_SYSROOT"] = root
        rc, out = self.run_tty("y", "--submit")
        self.assertEqual(rc, S.EXIT_REJECTED, out)
        self.assertIn("identifier of this machine", out)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.gh_calls(), [])

    def test_no_capacity_run_found(self):
        r = subprocess.run(["bash", LAUNCHER, "share", "--dry-run"], cwd=self.tmp, env=self.env,
                           capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, S.EXIT_USAGE)
        self.assertIn("no finished capacity run", r.stdout)

    def test_newest_finished_run_is_the_default(self):
        base = os.path.join(self.tmp, "state", "k3d-gpu", "capacity")
        for name, status in (("cpu-1", "ok"), ("cpu-2", "aborted")):
            os.makedirs(os.path.join(base, name))
            with open(os.path.join(base, name, "capacity.json"), "w") as f:
                json.dump({"status": status}, f)
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        try:
            self.assertEqual(S.newest_capacity(), os.path.join(base, "cpu-1", "capacity.json"))
        finally:
            os.environ.pop("XDG_STATE_HOME")


if __name__ == "__main__":
    unittest.main(verbosity=1)

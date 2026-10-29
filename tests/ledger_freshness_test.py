#!/usr/bin/env python3
"""Tests for hack/ledger-freshness. Each case writes a throwaway repo (ledger, run
sections, images.lock, ledger-rules, a cited doc, a known-issues registry) and
reads the re-measure queue; the last cases run it on this repo and check that
EXPERIMENTS.md and console/ledger-rules.json are byte-identical afterwards.

Usage: python3 tests/ledger_freshness_test.py
"""

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "hack" / "ledger-freshness"

LEDGER = """# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H34 | ovms draft | p | Pre-registered. Result ([H34 result](#h34-result-host-2026-10-17)). | SUPPORTED |
| H57dm | llama mtp | p | Run 2026-10-21 ([H57d run](#h57d-run-host-2026-10-21)). | SUPPORTED |
| H60 | ovms older | p | Run ([H60 run](#h60-run-host-2026-10-22)) \\| piped cell. | SUPPORTED |
| H61 | vllm cpu | p | Pre-registration ([H61 pre-registration](#h61-pre-registration)); run ([H61 run](#h61-run-host-2026-10-23)). | SUPPORTED |
| H62 | no record | p | probe only | SUPPORTED |
| H63 | pending | p | ([H63 run](#h63-run-host)) | PENDING |
| H64 | refuted | p | ([H64 run](#h64-run-host)) | REFUTED |

## Reproduce

### H34 result (host, 2026-10-17)

**Reproduce:** commit `bb382c2`, node image `cryptoandcoffee/k3d-gpu:0.1.55`, OVMS
2026.4.1 (`openvino/model_server@sha256:a928903f...76fc58`), models from `~/m`.

### H57d run (host, 2026-10-21)

**Reproduce:**

```text
# provenance
k3d-gpu commit:  458bca0e, node image
                 cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc
runtimes:        ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fba;
                 openvino/model_server@sha256:a928903fe429f1e3 (2026.4.1)
models:          google/gemma-4-26B-A4B-it-qat-q4_0-gguf @ d1c082be (gemma.gguf sha256 3eca3b8f...);
                 OpenVINO/gemma-4-26b-a4b-it-int4-ov @ 4e5885c3
records:         bench/h57d/results/
```

### H60 run (host, 2026-10-22)

```text
# Reproduce
runtime:         openvino/model_server:2026.3-gpu@sha256:aaaa
```

### H61 pre-registration

Arms on vllm/vllm-openai-cpu:v0.20.0 (not a run; ignored).

### H61 run (host, 2026-10-23)

```text
# Reproduce
runtime:         vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6
node image:      cryptoandcoffee/k3d-gpu:0.1.70
```

### H64 run (host)

```text
# Reproduce
runtime:         openvino/model_server:2025.1
```
"""

LOCK = {"images": [
    {"name": "docker.io/openvino/model_server", "tag": "2026.4.1-gpu", "digest": "sha256:a928903fe429f1e3" + "0" * 48,
     "sources": ["scripts/k3d-gpu NPU_UMD_IMAGE"]},
    {"name": "docker.io/openvino/model_server", "digest": "sha256:a928903fe429f1e3" + "0" * 48,
     "sources": ["scripts/k3d-gpu AGENT_OVMS_IMAGE"]},
    {"name": "ghcr.io/ggml-org/llama.cpp", "tag": "server-b11960", "digest": "sha256:" + "1" * 64,
     "sources": ["scripts/k3d-gpu K3D_GPU_LLAMA_CPU_IMAGE"]},
    {"name": "docker.io/vllm/vllm-openai-cpu", "tag": "v0.32.0", "digest": "sha256:" + "2" * 64,
     "sources": ["scripts/k3d-gpu K3D_GPU_AMX_TEST_IMAGE"]},
    {"name": "docker.io/vllm/vllm-openai-cpu", "tag": "v0.24.0", "digest": "sha256:" + "3" * 64,
     "sources": ["integrations/eat/vllm-values.yaml"]},
]}

RULES = {"rows": [{"id": "H57dm", "verdict": "SUPPORTED", "classes": ["i9-13900t"]},
                  {"id": "H34", "verdict": "SUPPORTED", "classes": []}]}

KNOWN = """
[[issue]]
id = "vllm-fix"
title = "a CPU attention bug"
kind = "bug"
upstream = ""
component = "vllm-openai-cpu"
scheme = "zerover"
affected = "<0.25.0 || >=0.30.0,<0.30.1"
fixed_in = "0.25.0"
adopt = "review"
sites = [{file = "scripts/k3d-gpu", regex = 'vllm-openai-cpu:v(?P<v>[0-9.]+)@'}]
workarounds = []
notes = ""
"""

FILES = {
    "EXPERIMENTS.md": LEDGER,
    "images.lock": json.dumps(LOCK),
    "console/ledger-rules.json": json.dumps(RULES),
    "docs/perf.md": "OVMS decodes 12 tok/s here [ledger:H60].\n",
    "README.md": "k3d-gpu\n",
    "scripts/k3d-gpu": 'VERSION="0.1.79"\nAMX="vllm/vllm-openai-cpu:v0.32.0@sha256:x"\n',
    "pins/known-issues.toml": KNOWN,
}


def run(root, *args):
    p = subprocess.run([sys.executable, str(TOOL), "--root", str(root), *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(tempfile.mkdtemp(prefix="ledger_freshness."))
        for rel, text in FILES.items():
            p = cls.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        rc, out, err = run(cls.root, "--json")
        assert rc == 0, out + err
        cls.data = json.loads(out)
        cls.queue = {q["row"]: q for q in cls.data["queue"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_measured_versions(self):
        m = self.data["measured"]
        self.assertEqual(m["H34"]["openvino/model_server"], "2026.4.1")
        self.assertEqual(m["H34"]["cryptoandcoffee/k3d-gpu"], "0.1.55")
        self.assertEqual(m["H57dm"]["llama.cpp"], "b11749")
        self.assertEqual(m["H57dm"]["openvino/model_server"], "2026.4.1")
        self.assertEqual(m["H57dm"]["hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf"], "d1c082be")
        self.assertEqual(m["H57dm"]["hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov"], "4e5885c3")
        self.assertEqual(m["H60"]["openvino/model_server"], "2026.3")
        self.assertEqual(m["H61"]["vllm-openai-cpu"], "0.24.0")

    def test_only_supported_rows(self):
        self.assertNotIn("H63", self.data["measured"])
        self.assertNotIn("H64", self.data["measured"])

    def test_pre_registration_anchor_is_not_a_run(self):
        self.assertNotIn("0.20.0", json.dumps(self.data["measured"]["H61"]))

    def test_current_versions_from_ours_pins(self):
        c = self.data["current"]
        self.assertEqual(c["openvino/model_server"], "2026.4.1")
        self.assertEqual(c["llama.cpp"], "b11960")
        self.assertEqual(c["vllm-openai-cpu"], "0.32.0")  # the eat-stock v0.24.0 is not ours
        self.assertEqual(c["cryptoandcoffee/k3d-gpu"], "0.1.79")

    def test_ovms_2026_3_against_2026_4_1_is_stale(self):
        q = self.queue["H60"]
        self.assertEqual((q["measured"]["openvino/model_server"], q["current"]["openvino/model_server"]),
                         ("2026.3", "2026.4.1"))
        self.assertIn("minor", q["reason"])
        self.assertFalse(q["priority"])

    def test_same_minor_is_not_stale(self):
        # H34: OVMS 2026.4.1 = current; node image 0.1.55 -> 0.1.79 is a patch.
        self.assertNotIn("H34", self.queue)

    def test_llama_build_behind_is_stale(self):
        self.assertIn("llama.cpp b11749 -> b11960", self.queue["H57dm"]["reason"])

    def test_measured_inside_a_known_issue_is_priority_stale(self):
        q = self.queue["H61"]
        self.assertTrue(q["priority"])
        self.assertEqual(q["known_issues"], ["vllm-fix"])
        self.assertIn("priority-stale", q["reason"])

    def test_unparseable_row_is_listed(self):
        rows = {r["row"]: r["reason"] for r in self.data["not_machine_readable"]}
        self.assertIn("H62", rows)
        self.assertNotIn("H34", rows)

    def test_queue_order(self):
        # ledger-rules rows with classes, then rows docs claims cite, then the rest.
        self.assertEqual([q["row"] for q in self.data["queue"]], ["H57dm", "H60", "H61"])
        self.assertEqual([q["tier"] for q in self.data["queue"]], [1, 2, 3])

    def test_suggested_followup_ids_are_free(self):
        rows = set(self.data["measured"]) | {"H63", "H64", "H62"}
        for q in self.data["queue"]:
            self.assertRegex(q["suggested_followup_id"], r"^H\d+[a-z]{1,2}$")
            self.assertNotIn(q["suggested_followup_id"], rows)
        self.assertEqual(self.queue["H60"]["suggested_followup_id"], "H60r")

    def test_markdown(self):
        rc, out, err = run(self.root, "--md")
        self.assertEqual(rc, 0, err)
        for needle in ("| 1 | H57dm |", "b11749", "| H34 |", "openvino/model_server 2026.4.1", "H62", "priority-stale"):
            self.assertIn(needle, out)
        # A pipe inside a cell (a range union) is escaped, so every queue row keeps 5 cells.
        self.assertIn("<0.25.0 \\|\\| >=0.30.0", out)
        for line in out.splitlines():
            if line.startswith("| 3 |"):
                self.assertEqual(len(line.replace("\\|", "").split("|")), 7, line)

    def test_needs_an_output_mode(self):
        rc, _, _ = run(self.root)
        self.assertEqual(rc, 2)


def digest(paths):
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}


class RealRepo(unittest.TestCase):
    PROTECTED = ("EXPERIMENTS.md", "console/ledger-rules.json")

    def test_runs_read_only_and_lists_the_supported_engine_rows(self):
        before = digest(self.PROTECTED)
        rc, out, err = run(ROOT, "--json")
        self.assertEqual(rc, 0, err)
        rc2, md, err2 = run(ROOT, "--md")
        self.assertEqual(rc2, 0, err2)
        self.assertEqual(before, digest(self.PROTECTED))
        data = json.loads(out)
        self.assertEqual(data["measured"]["H34"]["openvino/model_server"], "2026.4.1")
        self.assertRegex(data["measured"]["H57dm"]["llama.cpp"], r"^b\d+$")
        self.assertIn("| H34 |", md)
        self.assertIn("| H57dm |", md)


if __name__ == "__main__":
    unittest.main(verbosity=1)

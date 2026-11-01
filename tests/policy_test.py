#!/usr/bin/env python3
"""Tests for the bump policy (hack/pins policy, hack/pinlib/policy.py).

Each case builds a throwaway git repo with a small pins/registry.toml, commits a
base, moves pins as a bump would, commits a head, and asks the policy for its
verdict: one case per cell of the policy matrix (pins/README.md), plus the
cross-cutting rules (0.x minors, missing canaries, ledger-defaults,
changes-defaults, known-issue adopt, holds, one group per PR). The verdict comes
from the diff alone; labels are never read.

Usage: python3 tests/policy_test.py
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hack"))

from pinlib import policy  # noqa: E402

ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
           GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64
ENGINES = """
[[engine]]
id = "node"
kind = "node"

[[engine]]
id = "vllm-cpu"
kind = "runtime"

[[engine]]
id = "vllm-xpu"
kind = "surface"
"""


def pin(pid, family, scheme, group="g", cls="ours", var=None, **extra):
    """A registry entry whose one site is VAR=<value> in share/pins.env."""
    var = var or pid.upper().replace("-", "_")
    out = {"id": pid, "class": cls, "group": group, "family": family, "scheme": scheme,
           "source": {"kind": "oci", "image": "x/" + pid, "tag": "{v}"},
           "sites": [{"file": "share/pins.env" if cls == "ours" else "integrations/eat/pins.env", "var": var}]}
    out.update(extra)
    return out


def toml_value(v):
    if isinstance(v, dict):
        return "{ " + ", ".join("%s = %s" % (k, toml_value(x)) for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(v)


def registry_text(pins):
    groups = sorted({p["group"] for p in pins})
    out = ["[groups.%s]\ntests = []\n" % g for g in groups]
    for p in pins:
        out.append("[[pin]]\n" + "".join("%s = %s\n" % (k, toml_value(v)) for k, v in p.items()))
    return "\n".join(out)


class Repo:
    def __init__(self, pins, base, head, engines=ENGINES, issues="", extra_base=None, extra_head=None):
        """base/head: {VAR: value} for share/pins.env (eat-stock: integrations/eat/pins.env)."""
        self.d = tempfile.mkdtemp(prefix="policy_test.")
        self.git("init", "-q", "-b", "main")
        files = {"pins/registry.toml": registry_text(pins), "pins/known-issues.toml": issues}
        if engines:
            files["canary/engines.toml"] = engines
        self.write(files)
        self.write(self.env_files(pins, base))
        self.write(extra_base or {})
        self.base = self.commit("base")
        self.write(self.env_files(pins, head))
        self.write(extra_head or {})
        self.head = self.commit("head")

    @staticmethod
    def env_files(pins, values):
        files = {}
        for p in pins:
            f = p["sites"][0]["file"]
            var = p["sites"][0].get("var")
            if var in values:
                files.setdefault(f, "")
                files[f] += "%s=%s\n" % (var, values[var])
        return files

    def git(self, *args):
        return subprocess.run(["git", "-C", self.d] + list(args), check=True, capture_output=True, text=True,
                              env=ENV).stdout

    def write(self, files):
        for path, text in files.items():
            full = os.path.join(self.d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def judge(self, measured=None, hf=("readme-only", ["README.md"])):
        reg = policy.registry.load(os.path.join(self.d, "pins", "registry.toml"))
        changes = policy.changes(self.d, self.base, self.head, reg)
        return policy.judge(self.d, self.base, self.head, changes, reg, hf_diff=lambda *a: hf,
                            measured={} if measured is None else measured)

    def close(self):
        shutil.rmtree(self.d, ignore_errors=True)


class Matrix(unittest.TestCase):
    def verdict(self, p, old, new, **kw):
        measured = kw.pop("measured", None)
        hf = kw.pop("hf", ("readme-only", ["README.md"]))
        var = p["sites"][0]["var"]
        r = Repo([p], {var: old}, {var: new}, **kw)
        try:
            return r.judge(measured, hf)
        finally:
            r.close()

    def check(self, p, old, new, want, **kw):
        v = self.verdict(p, old, new, **kw)
        self.assertEqual(v["verdict"], want, "%s %s -> %s: %s" % (p["id"], old, new, v["reasons"]))
        return v

    # family cells ------------------------------------------------------------
    def test_k3s(self):
        k3s = pin("k3s", "k3s", "k8s", canary=["node"])
        v = self.check(k3s, "v1.37.1-k3s1", "v1.37.2-k3s1", "auto")
        self.assertEqual((v["update_type"], v["canary"]), ("patch", ["node"]))
        self.check(k3s, "v1.37.1-k3s1", "v1.38.0-k3s1", "review")

    def test_plugins(self):
        p = pin("nvidia-device-plugin", "plugin", "semver", canary=["node"])
        self.check(p, "v0.20.1", "v0.21.0", "auto")  # a 0.x minor: the node canary is a runtime canary
        self.check(p, "v0.20.1", "v1.0.0", "review")

    def test_runtime_engine_zero_minor_needs_its_runtime_canary(self):
        p = pin("vllm-cpu", "runtime-engine", "zerover", canary=["vllm-cpu"], component="vllm-openai-cpu")
        v = self.check(p, "v0.32.0", "v0.33.0", "auto")
        self.assertEqual(v["canary"], ["vllm-cpu"])
        self.check(p, "v0.32.0", "v0.32.1", "auto")
        # no canary/engines.toml (the Canary workflow is not merged): no evidence can come
        v = self.check(p, "v0.32.0", "v0.32.1", "review", engines=None)
        self.assertTrue(any(r.startswith("no canary") for r in v["reasons"]), v["reasons"])
        # without a runtime canary a 0.x minor counts as a major
        q = pin("vllm-xpu", "runtime-engine", "zerover", canary=["vllm-xpu"])
        v = self.check(q, "v0.32.0", "v0.33.0", "review")
        self.assertEqual(v["update_type"], "major")

    def test_ledger_defaults(self):
        p = pin("ovms", "runtime-engine", "calver", component="openvino/model_server")
        measured = {"H34": {"openvino/model_server": "2026.4.1"}}
        v = self.check(p, "2026.4.1", "2026.5.0", "review", measured=measured)
        self.assertTrue(any(r.startswith("ledger-defaults") for r in v["reasons"]))
        self.assertIn("ledger-defaults", policy.labels(v))
        self.check(p, "2026.4.1", "2026.4.2", "auto", measured=measured)  # a patch moves no default
        self.check(p, "2026.4.1", "2026.5.0", "auto", measured={})

    def test_static_engine(self):
        p = pin("dlstreamer", "static-engine", "calver")
        self.check(p, "2026.2.0", "2026.2.1", "auto")
        self.check(p, "2026.2.0", "2026.3.0", "review")

    def test_host_drivers_and_ppa(self):
        self.check(pin("level-zero", "host-driver", "semver"), "1.34.0", "1.34.1", "review")
        ppa = pin("ppa-media", "host-ppa", "apt")
        self.check(ppa, "26.3.5-1~ppa1", "26.3.6-1~ppa1", "auto")
        self.check(ppa, "26.3.5-1~ppa1", "26.4.0-1~ppa1", "review")

    def test_hf(self):
        p = pin("hf-model", "hf", "hf-sha", source={"kind": "hf", "model": "o/m"})
        a, b = "a" * 40, "b" * 40
        v = self.check(p, a, b, "auto")
        self.assertEqual(v["update_type"], "revision")
        v = self.check(p, a, b, "review", hf=("substantive", ["model.safetensors"]))
        self.assertIn("model.safetensors", " ".join(v["reasons"]))

    def test_pypi_tools_telemetry_opt_in(self):
        for fam, scheme, old, minor, major in (("pypi", "pep440", "5.21.0", "5.22.0", "6.0.0"),
                                               ("tools", "semver", "v1.37.2", "v1.38.0", "v2.0.0"),
                                               ("telemetry", "semver", "v3.15.1", "v3.16.0", "v4.0.0"),
                                               ("opt-in", "semver", "v1.0.8", "v1.1.0", "v2.0.0")):
            p = pin("p-" + fam, fam, scheme)
            self.check(p, old, minor, "auto")
            self.check(p, old, major, "review")

    def test_prometheus_patch_is_auto(self):
        self.check(pin("prometheus", "telemetry", "semver"), "v3.15.1", "v3.15.2", "auto")

    def test_chrome_majors_are_auto(self):
        self.check(pin("chrome", "chrome", "semver"), "141.0.7390.54", "142.0.7444.3", "auto")

    def test_base_images(self):
        busybox = pin("busybox", "base-image", "docker-suffix")
        v = self.check(busybox, "1.37@" + D1, "1.38@" + D1, "review")  # a tag line change
        self.assertIn("tag line", " ".join(v["reasons"]))
        py = pin("python", "base-image", "docker-suffix")
        self.check(py, "3.14.8-slim", "3.14.9-slim", "auto")
        self.check(py, "3.14.8-slim", "3.15.0-slim", "review")

    def test_same_tag_digest_is_auto(self):
        p = {"id": "img", "class": "ours", "group": "digests", "family": "base-image", "scheme": "docker-suffix",
             "source": {"kind": "oci", "image": "busybox", "tag": "{v}"},
             "sites": [{"file": "share/app.yml", "ref": "busybox"}]}
        r = Repo([p], {}, {}, extra_base={"share/app.yml": "image: busybox:1.37@%s\n" % D1},
                 extra_head={"share/app.yml": "image: busybox:1.37@%s\n" % D2})
        try:
            v = r.judge()
        finally:
            r.close()
        self.assertEqual((v["verdict"], v["update_type"]), ("auto", "digest"), v["reasons"])

    def test_same_version_new_digest_in_a_var_site_is_a_digest_move(self):
        k3s = pin("k3s", "k3s", "k8s", var="K3S_TAG", canary=["node"])
        k3s["sites"].append({"file": "share/pins.env", "var": "K3S_DIGEST", "key": "digest"})
        r = Repo([k3s], {}, {}, extra_base={"share/pins.env": "K3S_TAG=v1.37.1-k3s1\nK3S_DIGEST=%s\n" % D1},
                 extra_head={"share/pins.env": "K3S_TAG=v1.37.1-k3s1\nK3S_DIGEST=%s\n" % D2})
        try:
            v = r.judge()
        finally:
            r.close()
        self.assertEqual((v["verdict"], v["update_type"]), ("auto", "digest"), v["reasons"])

    def test_eat_stock_is_always_review(self):
        p = pin("eat", "eat-stock", "docker-suffix", cls="eat-stock", group="eat-stock")
        v = self.check(p, "release-1.0.3-hotfix", "release-1.1.0", "review")
        self.assertTrue(v["reasons"][0].startswith("eat-stock"), v["reasons"])
        self.check(pin("eat-vllm", "eat-stock", "zerover", cls="eat-stock", group="eat-stock"),
                   "v0.24.0", "v0.24.1", "review")

    # cross-cutting rules -----------------------------------------------------
    def test_changes_defaults(self):
        p = pin("prometheus", "telemetry", "semver")
        r = Repo([p], {"PROMETHEUS": "v3.15.1"}, {"PROMETHEUS": "v3.15.2"},
                 extra_base={"share/app.yml": "args: [--a]\n"}, extra_head={"share/app.yml": "args: [--a, --b]\n"})
        try:
            v = r.judge()
        finally:
            r.close()
        self.assertEqual(v["verdict"], "review", v["reasons"])
        self.assertIn("changes-defaults", policy.labels(v))

    def test_a_version_only_line_elsewhere_is_no_default_change(self):
        p = pin("prometheus", "telemetry", "semver")
        r = Repo([p], {"PROMETHEUS": "v3.15.1"}, {"PROMETHEUS": "v3.15.2"},
                 extra_base={"share/notes.txt": "built against prometheus v3.15.1 on 2026-10-01\n"},
                 extra_head={"share/notes.txt": "built against prometheus v3.15.2 on 2026-10-29\n"})
        try:
            v = r.judge()
        finally:
            r.close()
        self.assertEqual(v["verdict"], "auto", v["reasons"])

    def test_hold(self):
        p = pin("helm", "tools", "semver", hold={"max": "v4.0.0", "reason": "helm v4 render output differs",
                                                  "review_by": "2027-01-31"})
        self.check(p, "v3.22.1", "v3.22.2", "auto")
        v = self.check(p, "v3.22.1", "v4.0.1", "review")
        self.assertTrue(any(r.startswith(("hold", "major")) for r in v["reasons"]))

    def test_one_group_per_pr(self):
        a = pin("prometheus", "telemetry", "semver", group="telemetry")
        b = pin("kubectl", "tools", "semver", group="tools")
        r = Repo([a, b], {"PROMETHEUS": "v3.15.1", "KUBECTL": "v1.37.2"},
                 {"PROMETHEUS": "v3.15.2", "KUBECTL": "v1.37.3"})
        try:
            v = r.judge()
        finally:
            r.close()
        self.assertEqual((v["verdict"], v["group"]), ("review", "mixed"), v["reasons"])

    def test_a_pin_only_line_in_a_path_no_bot_writes_is_review(self):
        # a version-only line is no default change, but a ledger record, a finished
        # run's file or a workflow is never a bot's to change: review, whatever the diff
        p = pin("prometheus", "telemetry", "semver")
        for path in ("bench/run.sh", "oracle/seed/a.txt", "EXPERIMENTS.md", ".github/workflows/x.yml"):
            r = Repo([p], {"PROMETHEUS": "v3.15.1"}, {"PROMETHEUS": "v3.15.2"},
                     extra_base={path: "image: x/y:v1.0.1\n"}, extra_head={path: "image: x/y:v1.0.2\n"})
            try:
                v = r.judge()
            finally:
                r.close()
            self.assertEqual(v["verdict"], "review", (path, v["reasons"]))
            self.assertTrue(any(x.startswith("class: %s" % path) for x in v["reasons"]), (path, v["reasons"]))

    def test_nothing_moved_is_review(self):
        p = pin("prometheus", "telemetry", "semver")
        v = self.check(p, "v3.15.1", "v3.15.1", "review")
        self.assertIn("no registry pin moved", v["reasons"])


ISSUE = """
[[issue]]
id = "dl-1"
title = "t"
kind = "bug"
upstream = "o/r#1"
component = "dlstreamer"
scheme = "calver"
affected = "<2026.3.0"
fixed_in = "2026.3.0"
adopt = "%s"
sites = [{file = "share/pins.env", regex = '^DLSTREAMER=(?P<v>\\S+)$'}]
workarounds = []
notes = ""
"""


class KnownIssues(unittest.TestCase):
    def judge(self, adopt, old, new):
        p = pin("dlstreamer", "static-engine", "calver")
        r = Repo([p], {"DLSTREAMER": old}, {"DLSTREAMER": new}, issues=ISSUE % adopt)
        try:
            return r.judge()
        finally:
            r.close()

    def test_adopt_auto_lets_a_static_engine_minor_through(self):
        v = self.judge("auto", "2026.2.0", "2026.3.0")
        self.assertEqual(v["verdict"], "auto", v["reasons"])
        self.assertTrue(v["urgent"])
        self.assertEqual(v["fixes"], ["dl-1"])
        self.assertIn("fixes-known-issue", policy.labels(v))

    def test_adopt_review_keeps_review_but_is_urgent(self):
        v = self.judge("review", "2026.2.0", "2026.3.0")
        self.assertEqual(v["verdict"], "review")
        self.assertTrue(v["urgent"])

    def test_adopt_auto_never_lets_a_major_through(self):
        v = self.judge("auto", "2026.2.0", "2027.0.0")
        self.assertEqual(v["verdict"], "review", v["reasons"])

    def test_a_move_that_stays_affected_fixes_nothing(self):
        v = self.judge("auto", "2026.1.0", "2026.2.0")
        self.assertFalse(v["urgent"])
        self.assertEqual(v["fixes"], [])


class Cli(unittest.TestCase):
    def test_hack_pins_policy_prints_the_verdict_json(self):
        p = pin("prometheus", "telemetry", "semver")
        r = Repo([p], {"PROMETHEUS": "v3.15.1"}, {"PROMETHEUS": "v3.15.2"})
        try:
            # a scratch repo has no hack/ledger-freshness: measured is unknown, which
            # only matters for components; prometheus has none
            out = subprocess.run([sys.executable, str(ROOT / "hack" / "pins"), "--root", r.d, "policy", r.base,
                                  r.head], capture_output=True, text=True)
        finally:
            r.close()
        self.assertEqual(out.returncode, 0, out.stderr)
        v = json.loads(out.stdout)
        self.assertEqual(set(v), {"verdict", "reasons", "group", "update_type", "urgent", "canary", "fixes"})
        self.assertEqual((v["verdict"], v["group"], v["update_type"], v["urgent"]), ("auto", "g", "patch", False))


if __name__ == "__main__":
    unittest.main(verbosity=1)

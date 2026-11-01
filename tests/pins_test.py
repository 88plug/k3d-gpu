#!/usr/bin/env python3
"""Tests for `hack/pins check` (offline, CI job pins) and `hack/pins issue-groups`.

The first case runs check on this repo: every site of pins/registry.toml resolves
to its span, derived values equal their parents, holds are not past review_by,
no two pins share a span, Dependabot keeps only github-actions, known-issue
sites lie inside registry sites, and every pinned image, == requirements pin and
Hugging Face revision in an ours or eat-stock file has an owner. The rest break
one rule each in a throwaway repo and expect check to name it.

Usage: python3 tests/pins_test.py
"""

import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PINS = ROOT / "hack" / "pins"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
           GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
D1 = "sha256:" + "1" * 64
REV = "0123456789abcdef0123456789abcdef01234567"

REGISTRY = """
[groups.telemetry]
tests = []
outputs = ["share/rendered/*"]

[groups.hf]
tests = []

[images.default]
family = "base-image"
groups = [{ glob = "*", group = "images" }]

[groups.images]
tests = []

[[pin]]
id = "prometheus"
class = "ours"
group = "telemetry"
family = "telemetry"
scheme = "semver"
source = { kind = "github-release", repo = "prometheus/prometheus", tag = "{v}" }
sites = [
  { file = "share/prometheus.yml", ref = "quay.io/prometheus/prometheus" },
  { file = "share/prometheus.yml", regex = '^# image quay\\.io/prometheus/prometheus:(?P<v>v[0-9.]+)$' },
]
derived = [{ file = "share/notes.txt", regex = '^prometheus (?P<v>[0-9.]+)$', from = "{v_bare}" }]

[[pin]]
id = "hf-model"
class = "ours"
group = "hf"
family = "hf"
scheme = "hf-sha"
source = { kind = "hf", model = "Org/Model" }
sites = [{ file = "scripts/run.sh", regex = 'MODEL_REV="(?P<v>[0-9a-f]{40})"' }]
"""

FILES = {
    "share/prometheus.yml": "# image quay.io/prometheus/prometheus:v3.15.1\n"
                            "image: quay.io/prometheus/prometheus:v3.15.1@%s\n" % D1,
    "share/notes.txt": "prometheus 3.15.1\n",
    "share/app.yml": "image: busybox:1.37@%s\n" % D1,
    "scripts/run.sh": 'MODEL="Org/Model"\nMODEL_REV="%s"\n' % REV,
    "examples/agents/demo/requirements.txt": "",
    "images.unpinned": "",
    ".github/dependabot.yml": "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n",
}


class Scratch:
    def __init__(self, registry=REGISTRY, files=None):
        self.d = tempfile.mkdtemp(prefix="pins_test.")
        subprocess.run(["git", "init", "-q", "-b", "main", self.d], check=True, env=ENV)
        self.write({"pins/registry.toml": registry})
        self.write(dict(FILES, **(files or {})))
        subprocess.run(["git", "-C", self.d, "add", "-A"], check=True, env=ENV)

    def write(self, files):
        for path, text in files.items():
            full = os.path.join(self.d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)
        if hasattr(self, "d") and os.path.isdir(os.path.join(self.d, ".git")):
            subprocess.run(["git", "-C", self.d, "add", "-A"], check=True, env=ENV)

    def pins(self, *args):
        p = subprocess.run([sys.executable, str(PINS), "--root", self.d] + list(args), capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def close(self):
        shutil.rmtree(self.d, ignore_errors=True)


class RealTree(unittest.TestCase):
    def test_check_passes_on_this_repo(self):
        p = subprocess.run([sys.executable, str(PINS), "check"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("every site resolves", p.stdout)

    def test_every_group_names_existing_tests_and_known_hooks(self):
        sys.path.insert(0, str(ROOT / "hack"))
        from pinlib import registry
        bump = (ROOT / "hack" / "bump").read_text()
        reg = registry.load(str(ROOT / "pins" / "registry.toml"))
        for g, conf in reg["groups"].items():
            for t in conf.get("tests", []):
                self.assertTrue((ROOT / t).is_file(), "group %s: test %s does not exist" % (g, t))
            for h in conf.get("hooks", []):
                self.assertIn('"%s": hook_' % h, bump, "group %s: hook %s is not one of hack/bump's" % (g, h))
        used = {p["group"] for p in reg["pin"]} | {x["group"] for x in reg["images"]["default"]["groups"]}
        self.assertEqual(set(reg["groups"]) - used - {"digests"}, set(), "groups with no pin")


class Check(unittest.TestCase):
    def run_check(self, registry=REGISTRY, files=None):
        s = Scratch(registry, files)
        try:
            return s.pins("check")
        finally:
            s.close()

    def test_the_scratch_tree_passes(self):
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)

    def test_schema(self):
        bad = REGISTRY.replace('scheme = "semver"', 'scheme = "sem-ver"').replace('class = "ours"\ngroup = "hf"',
                                                                                  'class = "arm"\ngroup = "hf"')
        bad += '\n[[pin]]\nid = "prometheus"\nclass = "ours"\ngroup = "nope"\nfamily = "x"\nscheme = "semver"\n' \
               'source = { kind = "ftp" }\nsites = []\ncolor = "red"\n'
        rc, out = self.run_check(bad)
        self.assertEqual(rc, 1, out)
        for want in ("unknown scheme 'sem-ver'", "class must be ours or eat-stock", "duplicate id",
                     "group 'nope' is not in [groups]", "unknown source kind 'ftp'", "unknown keys ['color']",
                     "no sites", "unknown family 'x'"):
            self.assertIn(want, out)

    def test_a_site_that_no_longer_matches(self):
        rc, out = self.run_check(files={"share/prometheus.yml": "# image moved\nimage: quay.io/prometheus/"
                                                                "prometheus:v3.15.1@%s\n" % D1})
        self.assertEqual(rc, 1, out)
        self.assertIn("matches 0 time(s), want 1", out)

    def test_sites_that_disagree(self):
        rc, out = self.run_check(files={"share/prometheus.yml": "# image quay.io/prometheus/prometheus:v3.15.2\n"
                                                                "image: quay.io/prometheus/prometheus:v3.15.1@%s\n" % D1})
        self.assertEqual(rc, 1, out)
        self.assertIn("sites disagree on v", out)

    def test_derived_out_of_sync(self):
        rc, out = self.run_check(files={"share/notes.txt": "prometheus 3.15.0\n"})
        self.assertEqual(rc, 1, out)
        self.assertIn("derived share/notes.txt holds '3.15.0', its parent gives '3.15.1'", out)

    def test_expired_hold(self):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        reg = REGISTRY.replace('source = { kind = "github-release", repo = "prometheus/prometheus", tag = "{v}" }',
                               'source = { kind = "github-release", repo = "prometheus/prometheus", tag = "{v}" }\n'
                               'hold = { max = "v4.0.0", reason = "r", review_by = "%s" }' % past)
        rc, out = self.run_check(reg)
        self.assertEqual(rc, 1, out)
        self.assertIn("hold (r) expired on %s" % past, out)

    def test_two_owners_of_one_span(self):
        reg = REGISTRY + ('\n[[pin]]\nid = "prom2"\nclass = "ours"\ngroup = "telemetry"\nfamily = "telemetry"\n'
                          'scheme = "semver"\nsource = { kind = "github-release", repo = "p/p" }\n'
                          'sites = [{ file = "share/notes.txt", regex = \'^prometheus (?P<v>[0-9.]+)$\' }]\n')
        rc, out = self.run_check(reg)
        self.assertEqual(rc, 1, out)
        self.assertIn("both own share/notes.txt", out)

    def test_dependabot_beside_the_registry(self):
        rc, out = self.run_check(files={".github/dependabot.yml": FILES[".github/dependabot.yml"]
                                        + "  - package-ecosystem: docker\n    directory: /\n"})
        self.assertEqual(rc, 1, out)
        self.assertIn("ecosystem docker overlaps the registry", out)

    def test_coverage_unowned_requirement_and_revision(self):
        rc, out = self.run_check(files={"examples/agents/demo/requirements.txt": "openai==0.0.1\n",
                                        "share/models.yml": 'model: Org/Other\nrevision: "%s"\n' % ("f" * 40),
                                        # a git commit beside no model id is not a Hub revision
                                        "share/commits.json": '{"profile": "main",\n "rev": "%s"}\n' % ("e" * 40)})
        self.assertEqual(rc, 1, out)
        self.assertIn("openai==0.0.1 has no owner", out)
        self.assertIn("Hugging Face revision ffffffffffff has no owner", out)
        self.assertNotIn("eeeeeeeeeeee", out)

    def test_coverage_generated_owner_for_any_other_image(self):
        # busybox in share/app.yml has no explicit pin: images.default owns it
        s = Scratch()
        try:
            rc, out = s.pins("outdated", "--json", "--group", "nonexistent")
        finally:
            s.close()
        self.assertEqual((rc, out.strip()), (0, "[]"), out)

    def test_known_issue_site_outside_every_registry_site(self):
        ki = ('[[issue]]\nid = "x-1"\ntitle = "t"\nkind = "bug"\nupstream = ""\ncomponent = "c"\nscheme = "semver"\n'
              'affected = "*"\nfixed_in = ""\nadopt = "review"\nworkarounds = []\nnotes = ""\nsites = [%s]\n')
        inside = "{file = \"share/prometheus.yml\", regex = 'prometheus:(?P<v>v[0-9.]+)@'}"
        outside = "{file = \"share/app.yml\", regex = 'busybox:(?P<v>[0-9.]+)'}"
        rc, out = self.run_check(files={"pins/known-issues.toml": ki % inside})
        self.assertEqual(rc, 0, out)
        rc, out = self.run_check(files={"pins/known-issues.toml": ki % outside,
                                        "share/app.yml": "image: busybox:1.37\n"})
        self.assertEqual(rc, 1, out)
        self.assertIn("x-1: share/app.yml:1 reads 1.37 outside every registry site", out)


class Report(unittest.TestCase):
    """The dashboard body and the lag tripwire (hack/pins report)."""

    NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)

    def rows(self):
        def row(pid, group, family, utype, hours, **kw):
            pub = (self.NOW - datetime.timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
            r = {"id": pid, "group": group, "class": "ours", "family": family, "update_type": utype,
                 "current": "1.0.0", "latest": "1.0.1", "behind": True, "published_at": pub}
            r.update(kw)
            return r
        return [
            row("prom", "telemetry", "telemetry", "patch", 80),             # auto-eligible, > 72 h: alarm
            row("fresh", "telemetry2", "telemetry", "patch", 10),           # auto-eligible, < 72 h
            row("drv", "host-intel", "host-driver", "patch", 100),          # review, < 7 days
            row("drv-old", "host-intel2", "host-driver", "patch", 200),     # review, > 7 days: alarm
            row("k3s", "k3s", "k3s", "patch", 100, behind=False, unconfirmed=True),  # unconfirmed lags too
            row("eat", "eat-stock", "eat-stock", "line", 999, **{"class": "eat-stock"}),
            row("open", "tools", "tools", "patch", 999),                    # has an open PR
        ]

    def test_lag_tripwire(self):
        sys.path.insert(0, str(ROOT / "hack"))
        from pinlib import report
        lag = report.lag_alarms(self.rows(), [{"headRefName": "autobump/tools"}], self.NOW)
        self.assertEqual(sorted(a["key"] for a in lag), ["blind-spot:drv-old", "blind-spot:k3s", "blind-spot:prom"])
        self.assertTrue(all("no open PR" in a["title"] for a in lag))

    def test_dashboard_sections(self):
        sys.path.insert(0, str(ROOT / "hack"))
        from pinlib import report
        md, lag = report.render(str(ROOT), self.rows(), [{"number": 7, "headRefName": "autobump/tools",
                                                          "verdict": "review", "blocking": "a human approval"}],
                                "ci-main", now=self.NOW, actions=False)
        for heading in ("# Freshness dashboard", "## Open alarms", "## Pins behind", "## Open bot PRs (1)",
                        "## EAT stock (always review)", "## PENDING arms", "## Known issues and stale workarounds",
                        "## Ledger re-measure queue", "## Actions behind", "## Lag tripwire"):
            self.assertIn(heading, md)
        self.assertIn("| #7 | tools | review | a human approval |", md)
        self.assertEqual(len(lag), 3)


class Frozen(unittest.TestCase):
    """[images.default] frozen: an exact reference the default rule never moves (an
    image an earlier release installed, which the launcher recognises by it)."""

    FROZEN = ('groups = [{ glob = "*", group = "images" }]\n'
              'frozen = [{ file = "share/app.yml", ref = "busybox:1.37", reason = "an earlier release installed it" }]')

    def outdated(self, s):
        fx = os.path.join(s.d, "no-network.json")
        with open(fx, "w") as f:
            json.dump({}, f)  # any request fails: a frozen pin must not ask
        p = subprocess.run([sys.executable, str(PINS), "--root", s.d, "outdated", "--json", "--all", "--group", "images"],
                           capture_output=True, text=True, env=dict(os.environ, PINLIB_FIXTURES=fx, PINLIB_HTTP_TRIES="1"))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_a_frozen_reference_never_resolves(self):
        s = Scratch(registry=REGISTRY.replace('groups = [{ glob = "*", group = "images" }]', self.FROZEN))
        try:
            rows = [r for r in self.outdated(s) if "busybox" in r["id"]]
            self.assertEqual(len(rows), 1, rows)
            self.assertEqual((rows[0]["behind"], rows[0].get("error"), rows[0]["note"]),
                             (False, None, "an earlier release installed it"), rows[0])
            self.assertEqual(s.pins("check"), (0, s.pins("check")[1]))
        finally:
            s.close()
        s = Scratch()  # the same reference unfrozen asks the registry
        try:
            rows = [r for r in self.outdated(s) if "busybox" in r["id"]]
            self.assertTrue(rows and rows[0].get("error"), rows)
        finally:
            s.close()

    def test_a_frozen_entry_that_matches_nothing_fails_check(self):
        s = Scratch(registry=REGISTRY.replace('groups = [{ glob = "*", group = "images" }]',
                                              self.FROZEN.replace("busybox:1.37", "busybox:1.36")))
        try:
            rc, out = s.pins("check")
            self.assertEqual(rc, 1, out)
            self.assertIn("frozen", out)
            self.assertIn("busybox:1.36", out)
        finally:
            s.close()


class IssueGroups(unittest.TestCase):
    def test_newly_fixed_issue_maps_to_the_groups_that_own_its_sites(self):
        ki = ('[[issue]]\nid = "prom-1"\ntitle = "t"\nkind = "bug"\nupstream = ""\ncomponent = "c"\n'
              'scheme = "semver"\naffected = "*"\nfixed_in = ""\nadopt = "review"\nworkarounds = []\nnotes = ""\n'
              "sites = [{file = \"share/prometheus.yml\", regex = 'prometheus:(?P<v>v[0-9.]+)@'}]\n")
        s = Scratch(files={"pins/known-issues.toml": ki})
        poll = os.path.join(s.d, "poll.json")
        rows = [{"id": "prom-1", "state": "released", "fixed_in": "v3.16.0", "recorded": ""},
                {"id": "gone", "state": "released", "fixed_in": "1.0", "recorded": ""}]
        with open(poll, "w") as f:
            json.dump(rows, f)
        try:
            rc, out = s.pins("issue-groups", poll)
            self.assertEqual((rc, out), (0, "prom-1 telemetry\n"))
            # an advisory entry's patched version counts as its fix; an unreleased fix does not
            for state, want in (("advisory", "prom-1 telemetry\n"), ("merged-unreleased", ""), ("not-shipped", "")):
                rows[0]["state"] = state
                with open(poll, "w") as f:
                    json.dump(rows, f)
                self.assertEqual(s.pins("issue-groups", poll), (0, want), state)
            rows[0]["state"] = "released"
            rows[0]["recorded"] = "v3.16.0"  # already recorded: nothing to do
            with open(poll, "w") as f:
                json.dump(rows, f)
            rc, out = s.pins("issue-groups", poll)
            self.assertEqual((rc, out), (0, ""))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)

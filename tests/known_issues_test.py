#!/usr/bin/env python3
"""Tests for hack/known-issues. Each case writes a throwaway repo tree (registry,
site files, markers, a small ledger) and runs `check` or `poll` on it; poll runs
against a stubbed GitHub API. The last cases run check on this repo.

Usage: python3 tests/known_issues_test.py
"""

import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import contextlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "hack" / "known-issues"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("known_issues", str(TOOL))
    spec = importlib.util.spec_from_loader("known_issues", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ki = load_tool()

LEDGER = """# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H53 | stock arm | p | probe | PENDING |
| H54a | engine | p | probe | INCONCLUSIVE |

## Falsification log
"""

VLLM = """
[[issue]]
id = "vllm-fix"
title = "a CPU attention bug"
kind = "bug"
upstream = "vllm-project/vllm#45844"
fix = {repo = "vllm-project/vllm", sha = "26eb87204d58d9d8a25c378fbaec9da23db3fb85"}
component = "vllm-openai-cpu"
scheme = "zerover"
affected = "<0.25.0"
fixed_in = "0.25.0"
adopt = "review"
image = "vllm/vllm-openai-cpu:{tag}"
sites = [
  {file = "scripts/tool.sh", regex = '^CPU_IMAGE="vllm/vllm-openai-cpu:v(?P<v>[0-9.]+)@'},
  {file = "integrations/eat/values.yaml", regex = 'tag: "v(?P<v>[0-9.]+)@'},
]
workarounds = []
ledger_refs = [{row = "H53", path = "bench/live.sh", regex = 'B1_TAG=v(?P<v>[0-9.]+)'}]
notes = ""
"""

OPEN = """
[[issue]]
id = "open-bug"
title = "an open upstream bug"
kind = "bug"
upstream = "acme/engine#7"
fix = {repo = "acme/engine", pr = 7}
component = "engine"
scheme = "semver"
affected = "*"
fixed_in = ""
adopt = "review"
sites = [{file = "console/opt.py", regex = 'ENGINE = "(?P<v>[0-9.]+)"'}]
workarounds = ["console/opt.py"]
notes = ""
"""

FILES = {
    "EXPERIMENTS.md": LEDGER,
    "scripts/tool.sh": 'CPU_IMAGE="vllm/vllm-openai-cpu:v0.32.0@sha256:' + "a" * 64 + '"\n',
    "integrations/eat/values.yaml": 'image:\n  tag: "v0.24.0@sha256:' + "b" * 64 + '"\n',
    "bench/live.sh": "B1_TAG=v0.24.0\n",
    "console/opt.py": textwrap.dedent("""\
        ENGINE = "1.4.0"
        # known-issue-begin: open-bug
        NOTE = "set ENGINE_FALLBACK=1 until the fix ships"
        # known-issue-end: open-bug
        """),
}


def make_tree(registry, files=None, extra=None):
    d = pathlib.Path(tempfile.mkdtemp(prefix="known_issues."))
    tree = dict(FILES if files is None else files)
    if 'id = "open-bug"' not in registry:
        tree.pop("console/opt.py", None)  # its markers name open-bug
    tree.update(extra or {})
    tree["pins/known-issues.toml"] = registry
    for rel, text in tree.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return d


def run_check(root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ki.main(["--root", str(root), "check"])
    return rc, out.getvalue()


def run_check_json(root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ki.main(["--root", str(root), "check", "--json"])
    return rc, json.loads(out.getvalue())


class Check(unittest.TestCase):
    def setUp(self):
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def tree(self, registry, files=None, extra=None):
        d = make_tree(registry, files, extra)
        self.dirs.append(d)
        return d

    def assertFails(self, root, needle):
        rc, out = run_check(root)
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        return out

    def test_fixture_passes(self):
        rc, out = run_check(self.tree(VLLM + OPEN))
        self.assertEqual(rc, 0, out)

    def test_ours_site_in_range_with_fixed_in_fails_with_bump_message(self):
        files = dict(FILES)
        files["scripts/tool.sh"] = files["scripts/tool.sh"].replace("v0.32.0", "v0.24.0")
        self.assertFails(self.tree(VLLM + OPEN, files), "bump scripts/tool.sh to >= 0.25.0")

    def test_in_range_site_with_fixed_in_empty_passes(self):
        # open-bug: affected "*" covers console/opt.py's 1.4.0, fixed_in is empty.
        rc, data = run_check_json(self.tree(OPEN))
        self.assertEqual(rc, 0)
        site = data["issues"][0]["sites"][0]
        self.assertEqual((site["versions"], site["in_affected"]), (["1.4.0"], True))

    def test_eat_stock_in_range_only_reports(self):
        rc, data = run_check_json(self.tree(VLLM))
        self.assertEqual(rc, 0)
        self.assertTrue(any("integrations/eat/values.yaml" in r and "eat-stock" in r for r in data["reports"]),
                        data["reports"])

    def test_ledger_ref_in_range_only_reports(self):
        rc, data = run_check_json(self.tree(VLLM))
        self.assertEqual(rc, 0)
        self.assertTrue(any("H53" in r and "bench/live.sh" in r for r in data["reports"]), data["reports"])
        ref = data["issues"][0]["ledger_refs"][0]
        self.assertEqual((ref["row"], ref["versions"], ref["in_affected"]), ("H53", ["0.24.0"], True))

    def stale(self, retained=""):
        reg = OPEN.replace('fixed_in = ""', 'fixed_in = "1.5.0"').replace('affected = "*"', 'affected = "<1.5.0"')
        reg += retained
        files = dict(FILES)
        files["console/opt.py"] = files["console/opt.py"].replace('"1.4.0"', '"1.5.0"')
        return self.tree(reg, files)

    def test_stale_workaround_without_retained_fails(self):
        self.assertFails(self.stale(), "open-bug: workaround markers remain")

    def test_stale_workaround_with_retained_passes(self):
        rc, out = run_check(self.stale('retained = {reason = "the note helps older engines", pr = "#12"}\n'))
        self.assertEqual(rc, 0, out)

    def test_workaround_not_stale_while_an_ours_site_is_behind(self):
        reg = OPEN.replace('fixed_in = ""', 'fixed_in = "1.5.0"').replace('affected = "*"', 'affected = "<1.5.0"')
        # console/opt.py stays at 1.4.0: the bump message fires, the stale rule does not.
        out = self.assertFails(self.tree(reg), "bump console/opt.py to >= 1.5.0")
        self.assertNotIn("workaround markers remain", out)

    def test_site_regex_without_match_fails(self):
        reg = VLLM.replace("CPU_IMAGE=", "GPU_IMAGE=")
        self.assertFails(self.tree(reg), "matches nothing in scripts/tool.sh")

    def test_missing_site_file_fails(self):
        reg = VLLM.replace('file = "scripts/tool.sh"', 'file = "scripts/gone.sh"')
        self.assertFails(self.tree(reg), "scripts/gone.sh")

    def test_unparseable_site_version_fails(self):
        files = dict(FILES)
        files["scripts/tool.sh"] = files["scripts/tool.sh"].replace("v0.32.0", "v0.32.x")
        reg = VLLM.replace("[0-9.]+)@'}", "[0-9.x]+)@'}", 1)
        self.assertFails(self.tree(reg, files), "0.32.x")

    def test_marker_in_unlisted_file_fails(self):
        self.assertFails(self.tree(OPEN, extra={"docs/guide.md": "Use the fallback. <!-- known-issue: open-bug -->\n"}),
                         "docs/guide.md")

    def test_listed_workaround_without_marker_fails(self):
        files = dict(FILES)
        files["console/opt.py"] = 'ENGINE = "1.4.0"\n'
        self.assertFails(self.tree(OPEN, files), "console/opt.py has no known-issue marker for open-bug")

    def test_marker_for_unknown_issue_fails(self):
        self.assertFails(self.tree(OPEN, extra={"scripts/x.sh": "# known-issue: no-such-issue\n"}),
                         "no-such-issue")

    def test_marker_in_ledger_file_fails(self):
        reg = OPEN.replace('workarounds = ["console/opt.py"]', 'workarounds = ["console/opt.py", "bench/live.sh"]')
        files = dict(FILES)
        files["bench/live.sh"] += "# known-issue: open-bug\n"
        self.assertFails(self.tree(reg, files), "bench/live.sh")

    def test_unpaired_block_fails(self):
        files = dict(FILES)
        files["console/opt.py"] = 'ENGINE = "1.4.0"\n# known-issue-begin: open-bug\nNOTE = 1\n'
        self.assertFails(self.tree(OPEN, files), "known-issue-begin: open-bug")

    def test_fixed_in_inside_affected_fails(self):
        self.assertFails(self.tree(VLLM.replace('affected = "<0.25.0"', 'affected = "<=0.25.0"')),
                         "fixed_in 0.25.0 is inside affected")

    def test_unknown_key_fails(self):
        self.assertFails(self.tree(OPEN.replace('notes = ""', 'notes = ""\nfixed_by = "x"')), "fixed_by")

    def test_missing_field_fails(self):
        self.assertFails(self.tree(OPEN.replace('adopt = "review"\n', "")), "adopt")

    def test_bad_enum_and_scheme_fail(self):
        self.assertFails(self.tree(OPEN.replace('kind = "bug"', 'kind = "wish"')), "kind")
        self.assertFails(self.tree(OPEN.replace('scheme = "semver"', 'scheme = "romver"')), "romver")

    def test_bad_upstream_fails(self):
        self.assertFails(self.tree(OPEN.replace('"acme/engine#7"', '"acme/engine/7"')), "upstream")

    def test_duplicate_id_fails(self):
        self.assertFails(self.tree(OPEN + OPEN), "duplicate id open-bug")

    def test_ledger_ref_row_must_exist(self):
        self.assertFails(self.tree(VLLM.replace('row = "H53"', 'row = "H99"')), "H99")

    def test_site_in_ledger_file_fails(self):
        reg = VLLM.replace('file = "scripts/tool.sh", regex = \'^CPU_IMAGE="vllm/vllm-openai-cpu:v(?P<v>[0-9.]+)@\'',
                           'file = "bench/live.sh", regex = \'B1_TAG=v(?P<v>[0-9.]+)\'')
        self.assertFails(self.tree(reg), "bench/live.sh")

    def test_pins_classes_file_decides_the_class(self):
        # pins/classes (first match wins) makes scripts/ eat-stock: the in-range site only reports.
        files = dict(FILES)
        files["scripts/tool.sh"] = files["scripts/tool.sh"].replace("v0.32.0", "v0.24.0")
        rc, out = run_check(self.tree(VLLM, files, {"pins/classes": "scripts/** eat-stock test\n"}))
        self.assertEqual(rc, 0, out)

    def test_json_shape(self):
        rc, data = run_check_json(self.tree(VLLM + OPEN))
        self.assertEqual(rc, 0)
        self.assertEqual(set(data), {"ok", "errors", "reports", "issues"})
        self.assertEqual([i["id"] for i in data["issues"]], ["vllm-fix", "open-bug"])
        self.assertEqual(data["issues"][0]["status"], "fixed")
        self.assertEqual(data["issues"][1]["status"], "open")
        self.assertEqual(data["issues"][1]["markers"][0]["file"], "console/opt.py")


# --- poll ----------------------------------------------------------------------

SHA = "26eb87204d58d9d8a25c378fbaec9da23db3fb85"


def releases(*tags, pre=()):
    return [{"tag_name": t, "prerelease": t in pre, "draft": False,
             "html_url": "https://github.com/x/releases/tag/" + t, "published_at": "2026-07-0%dT00:00:00Z" % (i + 1)}
            for i, t in enumerate(tags)]


class Api:
    """GitHub API stub: routes path -> JSON; 404 for anything else. Records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, path):
        self.calls.append(path)
        if path in self.routes:
            return self.routes[path]
        raise ki.ApiError(404, path)


def vllm_routes(compare):
    r = {"repos/vllm-project/vllm/releases?per_page=100&page=1":
         releases("v0.26.0", "v0.25.0", "v0.25.0rc1", "v0.24.0", "v0.23.0", pre=("v0.25.0rc1",)),
         "repos/vllm-project/vllm/releases?per_page=100&page=2": []}
    for tag, status in compare.items():
        r["repos/vllm-project/vllm/compare/%s...%s" % (tag, SHA)] = {"status": status}
    return r


class Poll(unittest.TestCase):
    def setUp(self):
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def poll(self, registry, api, files=None, digests=None):
        d = make_tree(registry, files)
        self.dirs.append(d)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()}
        resolved = []

        def digest(ref):
            resolved.append(ref)
            return (digests or {}).get(ref, "sha256:" + "c" * 64)

        out = ki.poll(ki.load(d), d, api, digest)
        after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "poll wrote to the tree")
        return {o["id"]: o for o in out}, resolved

    SEQ = {"v0.23.0": "ahead", "v0.24.0": "diverged", "v0.25.0": "behind", "v0.26.0": "behind"}

    def test_first_containing_release_walking_up(self):
        # Lowest site is 0.24.0 (diverged): walk up, v0.25.0 is behind -> contains the fix.
        api = Api(vllm_routes(self.SEQ))
        got, resolved = self.poll(VLLM.replace('fixed_in = "0.25.0"', 'fixed_in = ""').replace('"<0.25.0"', '"*"'), api)
        o = got["vllm-fix"]
        self.assertEqual((o["fixed_in"], o["tag"], o["state"]), ("0.25.0", "v0.25.0", "released"))
        self.assertEqual(o["release_url"], "https://github.com/x/releases/tag/v0.25.0")
        self.assertEqual(resolved, ["vllm/vllm-openai-cpu:v0.25.0"])
        self.assertFalse(any("rc1" in c for c in api.calls))

    def test_first_containing_release_walking_down(self):
        # Every site at 0.26.0 (behind): walk down past v0.25.0 (behind) to v0.24.0 (diverged).
        files = dict(FILES)
        files["integrations/eat/values.yaml"] = files["integrations/eat/values.yaml"].replace("v0.24.0", "v0.26.0")
        files["scripts/tool.sh"] = files["scripts/tool.sh"].replace("v0.32.0", "v0.26.0")
        got, _ = self.poll(VLLM, Api(vllm_routes(self.SEQ)), files)
        o = got["vllm-fix"]
        self.assertEqual((o["fixed_in"], o["recorded"], o["mismatch"]), ("0.25.0", "0.25.0", False))

    def test_recorded_mismatch_is_flagged(self):
        seq = dict(self.SEQ, **{"v0.25.0": "diverged"})
        got, _ = self.poll(VLLM, Api(vllm_routes(seq)))
        o = got["vllm-fix"]
        self.assertEqual((o["fixed_in"], o["mismatch"]), ("0.26.0", True))

    def test_merged_but_unreleased(self):
        seq = {t: "diverged" for t in self.SEQ}
        got, resolved = self.poll(VLLM, Api(vllm_routes(seq)))
        self.assertEqual((got["vllm-fix"]["state"], got["vllm-fix"]["fixed_in"]), ("merged-unreleased", ""))
        self.assertEqual(resolved, [])

    def test_open_pr_gives_empty(self):
        api = Api({"repos/acme/engine/pulls/7": {"state": "open", "merged": False, "merge_commit_sha": None}})
        got, _ = self.poll(OPEN, api)
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("open", ""))

    def test_merged_pr_uses_its_merge_commit(self):
        routes = {"repos/acme/engine/pulls/7": {"state": "closed", "merged": True, "merge_commit_sha": "f" * 40},
                  "repos/acme/engine/releases?per_page=100&page=1": releases("v1.6.0", "v1.5.0", "v1.4.0"),
                  "repos/acme/engine/releases?per_page=100&page=2": [],
                  "repos/acme/engine/compare/v1.4.0..." + "f" * 40: {"status": "ahead"},
                  "repos/acme/engine/compare/v1.5.0..." + "f" * 40: {"status": "identical"}}
        got, _ = self.poll(OPEN, Api(routes))
        self.assertEqual((got["open-bug"]["fixed_in"], got["open-bug"]["state"]), ("1.5.0", "released"))

    def test_closed_issue_reads_the_closing_commit_from_the_timeline(self):
        reg = OPEN.replace('fix = {repo = "acme/engine", pr = 7}', "fix = {}")
        routes = {"repos/acme/engine/issues/7": {"state": "closed", "state_reason": "completed"},
                  "repos/acme/engine/issues/7/timeline?per_page=100&page=1": [
                      {"event": "labeled"},
                      {"event": "closed", "commit_id": "e" * 40,
                       "commit_url": "https://api.github.com/repos/acme/engine/commits/" + "e" * 40}],
                  "repos/acme/engine/issues/7/timeline?per_page=100&page=2": [],
                  "repos/acme/engine/releases?per_page=100&page=1": releases("v1.5.0", "v1.4.0"),
                  "repos/acme/engine/releases?per_page=100&page=2": [],
                  "repos/acme/engine/compare/v1.4.0..." + "e" * 40: {"status": "behind"},
                  "repos/acme/engine/compare/v1.3.0..." + "e" * 40: {"status": "ahead"}}
        got, _ = self.poll(reg, Api(routes))
        self.assertEqual(got["open-bug"]["fixed_in"], "1.4.0")

    def test_open_issue_gives_empty(self):
        reg = OPEN.replace('fix = {repo = "acme/engine", pr = 7}', "fix = {}")
        got, _ = self.poll(reg, Api({"repos/acme/engine/issues/7": {"state": "open"}}))
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("open", ""))

    def test_content_probe(self):
        reg = OPEN.replace('fix = {repo = "acme/engine", pr = 7}',
                           'fix = {repo = "acme/engine", path = "charts/runtimes.yaml", contains = "name: ovms"}')

        def content(text):
            return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}

        routes = {"repos/acme/engine/releases?per_page=100&page=1": releases("v1.4.0", "v1.3.0"),
                  "repos/acme/engine/releases?per_page=100&page=2": [],
                  "repos/acme/engine/contents/charts/runtimes.yaml?ref=v1.4.0": content("name: sklearn\n"),
                  "repos/acme/engine/contents/charts/runtimes.yaml": content("name: sklearn\n")}
        got, _ = self.poll(reg, Api(routes))
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("not-shipped", ""))
        routes["repos/acme/engine/contents/charts/runtimes.yaml"] = content("name: ovms\n")
        got, _ = self.poll(reg, Api(routes))
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("merged-unreleased", ""))
        routes["repos/acme/engine/releases?per_page=100&page=1"] = releases("v1.5.0", "v1.4.0", "v1.3.0")
        routes["repos/acme/engine/contents/charts/runtimes.yaml?ref=v1.5.0"] = content("name: ovms\n")
        got, _ = self.poll(reg, Api(routes))
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("released", "1.5.0"))

    def test_advisories(self):
        reg = textwrap.dedent("""
            [[issue]]
            id = "lib-advisories"
            title = "advisories"
            kind = "advisory"
            upstream = ""
            fix = {ecosystem = "pip", package = "lib"}
            component = "lib"
            scheme = "pep440"
            affected = "<1.5.0"
            fixed_in = "1.5.0"
            adopt = "review"
            advisories = ["GHSA-aaaa-aaaa-aaaa"]
            sites = [{file = "req.txt", regex = '^lib==(?P<v>\\S+)$'}]
            workarounds = []
            notes = ""
            """)

        def adv(ghsa, *vulns):
            return {"ghsa_id": ghsa, "html_url": "https://github.com/advisories/" + ghsa,
                    "vulnerabilities": [{"package": {"ecosystem": "pip", "name": "lib"},
                                         "vulnerable_version_range": r, "first_patched_version": p} for r, p in vulns]}

        routes = {"advisories?ecosystem=pip&affects=lib%401.2.0&per_page=100": [
            adv("GHSA-aaaa-aaaa-aaaa", ("< 1.5.0", "1.5.0")),
            adv("GHSA-bbbb-bbbb-bbbb", ("< 1.6.2", "1.6.2"), (">= 1.7.0, < 1.7.3", "1.7.3")),
        ]}
        got, _ = self.poll(reg, Api(routes), files={"req.txt": "lib==1.2.0\n"})
        o = got["lib-advisories"]
        self.assertEqual((o["state"], o["fixed_in"], o["mismatch"]), ("advisory", "1.6.2", True))
        self.assertEqual(o["new_advisories"], ["GHSA-bbbb-bbbb-bbbb"])

    def test_untracked_entry(self):
        reg = OPEN.replace('upstream = "acme/engine#7"', 'upstream = ""').replace(
            'fix = {repo = "acme/engine", pr = 7}', "fix = {}")
        got, _ = self.poll(reg, Api({}))
        self.assertEqual((got["open-bug"]["state"], got["open-bug"]["fixed_in"]), ("untracked", ""))

    def test_api_error_is_reported_per_entry(self):
        got, _ = self.poll(VLLM + OPEN, Api({"repos/acme/engine/pulls/7": {"state": "open", "merged": False}}))
        self.assertIn("error", got["vllm-fix"])
        self.assertEqual(got["open-bug"]["state"], "open")


# --- this repo -----------------------------------------------------------------

class RealRepo(unittest.TestCase):
    def test_check_passes_on_this_repo(self):
        p = subprocess.run([sys.executable, str(TOOL), "--root", str(ROOT), "check"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_registry_seeds_the_known_items(self):
        with open(ROOT / "pins" / "known-issues.toml", "rb") as f:
            ids = {e["id"]: e for e in tomllib.load(f)["issue"]}
        for want in ("vllm-45844", "vllm-50801", "vllm-pr-51493", "linux-npu-driver-56", "kserve-4592",
                     "eat-9", "litellm-1.83.14-advisories", "vllm-0.32-num-spec-tokens"):
            self.assertIn(want, ids)
        self.assertEqual((ids["vllm-45844"]["affected"], ids["vllm-45844"]["fixed_in"]), ("<0.25.0", "0.25.0"))
        self.assertEqual(ids["kserve-4592"]["fixed_in"], "")
        self.assertEqual(ids["vllm-pr-51493"]["fixed_in"], "")

    def test_moving_the_ours_vllm_cpu_pin_to_0_24_fails_with_the_bump_message(self):
        """Copy the registry and every file it names into a fixture; move AMX_TEST_IMAGE to v0.24.0."""
        d = pathlib.Path(tempfile.mkdtemp(prefix="known_issues_real."))
        try:
            with open(ROOT / "pins" / "known-issues.toml", "rb") as f:
                reg = tomllib.load(f)
            paths = {"pins/known-issues.toml", "EXPERIMENTS.md"}
            for e in reg["issue"]:
                paths |= {s["file"] for s in e["sites"]} | set(e["workarounds"])
                paths |= {r["path"] for r in e.get("ledger_refs", [])}
            if (ROOT / "pins" / "classes").is_file():
                paths.add("pins/classes")
            for rel in paths:
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / rel, d / rel)
            rc, out = run_check(d)
            self.assertEqual(rc, 0, out)
            k = d / "scripts" / "k3d-gpu"
            text = k.read_text()
            moved = text.replace("K3D_GPU_AMX_TEST_IMAGE:-vllm/vllm-openai-cpu:v0.32.0@",
                                 "K3D_GPU_AMX_TEST_IMAGE:-vllm/vllm-openai-cpu:v0.24.0@")
            self.assertNotEqual(text, moved)
            k.write_text(moved)
            rc, out = run_check(d)
            self.assertEqual(rc, 1, out)
            self.assertIn("bump scripts/k3d-gpu to >= 0.25.0", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)

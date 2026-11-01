#!/usr/bin/env python3
"""Tests for hack/pin-only-diff: a diff passes when every changed line differs from
its partner only in version, digest, revision or ISO-date tokens, or lies inside a
declared registry site or a group's hook output; anything else (a new flag, a
reworded line, an added or deleted line outside those) fails and is printed.

Usage: python3 tests/pin_only_diff_test.py
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "hack" / "pin-only-diff"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
           GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64

REGISTRY = """
[groups.tools]
tests = []
outputs = ["share/lock.json"]

[groups.k3s]
tests = []
hooks = ["readme-history"]

[[pin]]
id = "tool"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "github-release", repo = "o/tool" }
sites = [{ file = "share/tool.sh", regex = 'TOOL_URL="https://example\\.com/(?P<v>v[0-9.]+)/[a-z0-9-]+\\.tar"' }]
"""
BASE = {
    "pins/registry.toml": REGISTRY,
    "share/tool.sh": 'TOOL_URL="https://example.com/v1.2.0/tool-amd64.tar"\nARGS="--a"\n',
    "share/notes.txt": "tool v1.2.0 (2026-10-01), digest %s, rev %s\n" % (D1, "a" * 40),
    "share/lock.json": '{"x": 1}\n',
    "README.md": "# x\n\n## Release History\n\n| Date | K3s | NV | Intel | Gaudi |\n"
                 "|------------|-----|----|-------|-------|\n| 2026-10-01 | a | b | v1 | c |\n",
}


class Repo:
    def __init__(self, head):
        self.d = tempfile.mkdtemp(prefix="pod_test.")
        self.git("init", "-q", "-b", "main")
        self.write(BASE)
        self.base = self.commit()
        self.write(head)
        self.head = self.commit()

    def git(self, *a):
        return subprocess.run(["git", "-C", self.d] + list(a), check=True, capture_output=True, text=True,
                              env=ENV).stdout

    def write(self, files):
        for path, text in files.items():
            full = os.path.join(self.d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if text is None:
                os.remove(full)
                continue
            with open(full, "w") as f:
                f.write(text)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        return self.git("rev-parse", "HEAD").strip()

    def run(self, *groups):
        args = [sys.executable, str(TOOL), "--root", self.d]
        for g in groups:
            args += ["--group", g]
        p = subprocess.run(args + [self.base, self.head], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


class PinOnlyDiff(unittest.TestCase):
    def run_case(self, head, *groups):
        r = Repo(head)
        try:
            return r.run(*groups)
        finally:
            shutil.rmtree(r.d, ignore_errors=True)

    def test_version_digest_revision_and_date_tokens_pass(self):
        rc, out = self.run_case({"share/notes.txt": "tool v1.3.1 (2026-10-29), digest %s, rev %s\n" % (D2, "b" * 40)})
        self.assertEqual(rc, 0, out)

    def test_a_site_may_change_beyond_its_tokens(self):
        # the site regex covers the whole URL, so a reshaped asset name is the pin's own move
        rc, out = self.run_case({"share/tool.sh": 'TOOL_URL="https://example.com/v1.3.0/tool-linux.tar"\nARGS="--a"\n'})
        self.assertEqual(rc, 0, out)

    def test_a_new_flag_fails(self):
        rc, out = self.run_case({"share/tool.sh": 'TOOL_URL="https://example.com/v1.2.0/tool-amd64.tar"\n'
                                                  'ARGS="--a --b"\n'})
        self.assertEqual(rc, 1, out)
        self.assertIn('share/tool.sh:2: +ARGS="--a --b"', out)

    def test_an_added_line_fails(self):
        rc, out = self.run_case({"share/notes.txt": BASE["share/notes.txt"] + "extra\n"})
        self.assertEqual(rc, 1, out)
        self.assertIn("share/notes.txt:2: +extra", out)

    def test_a_deleted_file_fails(self):
        rc, out = self.run_case({"share/notes.txt": None})
        self.assertEqual(rc, 1, out)
        self.assertIn("share/notes.txt: file deleted", out)

    def test_hook_outputs_pass_only_for_their_group(self):
        head = {"share/lock.json": '{"x": 2, "y": [1, 2]}\n'}
        self.assertEqual(self.run_case(head, "tools")[0], 0)
        rc, out = self.run_case(head, "k3s")
        self.assertEqual(rc, 1, out)

    def test_release_history_row_passes_for_a_readme_history_group(self):
        readme = BASE["README.md"].replace("|------------|-----|----|-------|-------|\n",
                                           "|------------|-----|----|-------|-------|\n| 2026-10-29 | v1.37.2-k3s1 | "
                                           "v0.21.0 | v0.37.1 | 1.24.2-481 |\n")
        self.assertEqual(self.run_case({"README.md": readme}, "k3s")[0], 0)
        rc, out = self.run_case({"README.md": readme}, "tools")
        self.assertEqual(rc, 1, out)


if __name__ == "__main__":
    unittest.main(verbosity=1)

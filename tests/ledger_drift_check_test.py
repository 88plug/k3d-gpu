#!/usr/bin/env python3
"""Tests for tests/ledger_drift_check.py. Each case writes a throwaway repo with a
small ledger and one doc, then runs the guard on it.

Usage: python3 tests/ledger_drift_check_test.py
"""

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ledger_drift_check as guard  # noqa: E402

LEDGER = """# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H1 | one | p | probe \\| piped | INCONCLUSIVE |
| H2 | two | p | probe | **REFUTED** |
| H3 | three | p | probe | SUPPORTED |
| H4 | four | p | probe | PENDING |
| H5 | five | p | probe | INCONCLUSIVE ([run](#h5-run)) |

## Falsification log

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| H9 | not a hypothesis row | x | x | x |
"""


def run(doc, ledger=LEDGER, path="docs/guide.md"):
    """(exit code, output) of the guard on a repo holding one doc."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "docs" / "intel").mkdir(parents=True)
        (root / "EXPERIMENTS.md").write_text(ledger)
        (root / path).write_text(doc)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = guard.main(["x", str(root)])
        return rc, out.getvalue()


class Ledger(unittest.TestCase):
    def test_reads_the_first_verdict_word_of_the_last_cell(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "EXPERIMENTS.md").write_text(LEDGER)
            rows = guard.ledger(pathlib.Path(d))
        self.assertEqual(rows, {"H1": "INCONCLUSIVE", "H2": "REFUTED", "H3": "SUPPORTED",
                                "H4": "PENDING", "H5": "INCONCLUSIVE"})

    def test_a_lettered_id_is_a_row_of_its_own(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "EXPERIMENTS.md").write_text(LEDGER.replace(
                "| H2 | two", "| H1b | one, re-run | p | probe | PENDING |\n| H2 | two"))
            rows = guard.ledger(pathlib.Path(d))
        self.assertEqual(rows["H1b"], "PENDING")
        self.assertEqual(rows["H1"], "INCONCLUSIVE")

    def test_a_row_without_a_verdict_fails(self):
        rc, out = run("ok\n", LEDGER.replace("| PENDING |", "| maybe |"))
        self.assertEqual(rc, 1)
        self.assertIn("H4 has no verdict word", out)


class Agrees(unittest.TestCase):
    def assertClean(self, doc, path="docs/guide.md"):
        rc, out = run(doc, path=path)
        self.assertEqual(rc, 0, out)

    def test_matching_prose(self):
        self.assertClean("H2 is REFUTED, and H4 is PENDING.\n")

    def test_matching_reference(self):
        self.assertClean("Status: INCONCLUSIVE [ledger:H1].\n")

    def test_matching_table_cell(self):
        self.assertClean("| [ledger:H4] | ollama | not run | PENDING |\n")

    def test_matching_heading_section(self):
        self.assertClean("### REFUTED (changed defaults)\n\n| Row | What |\n|---|---|\n| H2 | x |\n")

    def test_matching_counts(self):
        self.assertClean("- One is SUPPORTED. Two are INCONCLUSIVE. 1 hypothesis is fine.\n")

    def test_verdict_words_without_an_id(self):
        self.assertClean("- SUPPORTED: the penalty halves.\n- REFUTED: no level reaches half.\n")

    def test_lowercase_verdict_words_are_not_verdicts(self):
        self.assertClean("H2 was refuted, then H3 stayed pending in a draft.\n")

    def test_id_in_a_longer_token_is_not_an_id(self):
        self.assertClean("The H264 decoder and GH200-H3x stay PENDING.\n")

    def test_code_fences_inline_code_and_comments_are_skipped(self):
        self.assertClean("```\nH2 is PENDING\n```\n`H3 is REFUTED`\n<!-- H4 is SUPPORTED -->\n")

    def test_a_sentence_end_breaks_the_clause(self):
        self.assertClean("H3 backs the claim. The old rule is REFUTED [ledger:H2].\n")

    def test_another_id_between_breaks_the_clause(self):
        self.assertClean("H3 and H2 is REFUTED.\n")


class Drift(unittest.TestCase):
    def assertDrift(self, doc, pattern, path="docs/guide.md"):
        rc, out = run(doc, path=path)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, pattern)

    def test_prose(self):
        self.assertDrift("H2 is PENDING today.\n", r"guide.md:1: H2 reads PENDING \(prose\).*REFUTED")

    def test_prose_with_parenthesis(self):
        self.assertDrift("H1 (EAT on one box) reads PENDING on main.\n", r"H1 reads PENDING")

    def test_prose_split_over_two_lines(self):
        self.assertDrift("while H5\nis PENDING it never suggests\n", r"guide.md:2: H5 reads PENDING")

    def test_a_paragraph_break_ends_a_split(self):
        rc, out = run("about H5\n\nis PENDING elsewhere\n")
        self.assertEqual(rc, 0, out)

    def test_verdict_before_reference(self):
        self.assertDrift("| H3 | x | PENDING [ledger:H3] |\n", r"H3 reads PENDING \(verdict before reference\).*SUPPORTED")

    def test_table_cell(self):
        self.assertDrift("| [ledger:H3] | ollama | not run | PENDING |\n", r"H3 reads PENDING \(table cell\)")

    def test_heading_section(self):
        self.assertDrift("### SUPPORTED\n\n| Row | What |\n|---|---|\n| H3 | x |\n| H2, H4 | y |\n",
                         r"H2 reads SUPPORTED \(section heading\)")

    def test_verdict_list_line(self):
        self.assertDrift("PENDING with a pre-registration: H4 (x), H3 (y).\n", r"H3 reads PENDING \(verdict list\)")

    def test_count(self):
        self.assertDrift("- Six are PENDING.\n", r"says 6 PENDING but EXPERIMENTS.md has 1")

    def test_lettered_id_reference(self):
        rc, out = run("Status: PENDING [ledger:H1b].\n", LEDGER.replace(
            "| H2 | two", "| H1b | one, re-run | p | probe | SUPPORTED |\n| H2 | two"))
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"H1b reads PENDING \(verdict before reference\).*SUPPORTED")

    def test_unknown_id(self):
        self.assertDrift("Still PENDING [ledger:H9].\n", r"H9 .*not in the EXPERIMENTS.md hypothesis table")

    def test_docs_intel_is_scanned(self):
        self.assertDrift("H2 is PENDING.\n", r"docs/intel/report.md:1", path="docs/intel/report.md")

    def test_readme_is_scanned(self):
        self.assertDrift("H2 is PENDING.\n", r"README.md:1", path="README.md")


class RealRepo(unittest.TestCase):
    def test_this_repo_agrees_with_its_ledger(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = guard.main(["x", str(pathlib.Path(__file__).resolve().parent.parent)])
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()

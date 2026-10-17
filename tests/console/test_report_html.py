#!/usr/bin/env python3
"""Unit tests for console/report_html.py and tests/console/offline_check.py.

  - every fixture renders, offline-clean, the same bytes twice, inputs unchanged
  - text from the facts document is escaped, never markup
  - every http(s) URL in a finding becomes a deep link, and only that
  - the renderer prints the plan's own lines; it decides nothing
  - a plan for another host, or a document that is not facts/v1, is refused
  - offline_check catches each kind of fetch it claims to (mutation controls)

Usage: tests/console/test_report_html.py   Stdlib only.
"""

import copy
import glob
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))
sys.path.insert(0, HERE)

import facts as factsmod  # noqa: E402
import plan as planmod  # noqa: E402
import report_html  # noqa: E402
import offline_check  # noqa: E402

FIXTURES = sorted(glob.glob(os.path.join(REPO, "tests", "fixtures", "*", "facts.json")))
MATRIX = json.load(open(os.path.join(REPO, "console", "matrix.v1.json"), encoding="utf-8"))
GREEN = json.load(open(os.path.join(HERE, "matrix-green.json"), encoding="utf-8"))
RAPTOR = os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "facts.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def render(facts, matrix=MATRIX, overrides=None):
    return report_html.render(facts, planmod.plan(facts, matrix, overrides))


class Fixtures(unittest.TestCase):
    def test_at_least_14_fixtures(self):
        self.assertGreaterEqual(len(FIXTURES), 14)

    def test_every_fixture_renders_offline_clean_and_stable(self):
        for path in FIXTURES:
            with self.subTest(fixture=path):
                facts = load(path)
                before = copy.deepcopy(facts)
                p = planmod.plan(facts, MATRIX)
                a = report_html.render(facts, p)
                b = report_html.render(facts, p)
                self.assertEqual(a, b)
                self.assertEqual(facts, before)
                self.assertEqual(offline_check.static(a), [])
                self.assertIn(facts["fingerprint"], a)
                if p["decision"] != "fix":
                    self.assertIn("unmeasured: no green cell for this box; quickstart and agent up still pick by device", a)
                for t in facts["targets"]:
                    self.assertIn(report_html.esc(t["id"]), a)

    def test_every_finding_is_shown(self):
        for path in FIXTURES:
            facts = load(path)
            out = render(facts)
            for f in facts["doctor"]["findings"] + facts["perf"]["findings"]:
                if f["text"]:
                    with self.subTest(fixture=path, text=f["text"]):
                        self.assertIn(report_html.text(f["text"]), out)


class Content(unittest.TestCase):
    def setUp(self):
        self.facts = load(RAPTOR)

    def test_green_plan_lines_are_printed_verbatim(self):
        p = planmod.plan(self.facts, GREEN)
        self.assertEqual(p["decision"], "green")
        out = report_html.render(self.facts, p)
        for line in p["summary"]:
            self.assertIn(report_html.text(line), out)
        self.assertIn('class="badge green"', out)

    def test_fix_decision_is_labelled_and_lists_the_fix(self):
        facts = load(os.path.join(REPO, "tests", "fixtures", "raptorlake-stale-guc", "facts.json"))
        p = planmod.plan(facts, MATRIX)
        self.assertEqual(p["decision"], "fix")
        out = report_html.render(facts, p)
        self.assertIn('<div class="verdict fix"><span class="badge fail">fix first</span>', out)
        self.assertNotIn('class="verdict unmeasured"', out)
        for line in p["summary"]:
            self.assertIn(report_html.text(line), out)
        gpu = next(t for t in facts["targets"] if t["fix"])
        self.assertIn("fix: " + report_html.text(gpu["fix"]), out)

    def test_considered_cells_are_listed(self):
        p = planmod.plan(self.facts, GREEN)
        out = report_html.render(self.facts, p)
        for c in p["considered"]:
            self.assertIn(report_html.esc(c["cell"]), out)

    def test_markup_in_facts_is_escaped(self):
        f = copy.deepcopy(self.facts)
        evil = '<script>alert(1)</script><img src=x onerror=alert(2)>"\'&'
        f["hostname"] = evil
        f["doctor"]["findings"][0]["text"] = evil
        f["doctor"]["findings"][0]["why"] = evil
        f["labels"][evil] = evil
        f["host"]["kernel"] = evil
        out = render(f)
        self.assertNotIn("<script>", out)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)
        self.assertEqual(offline_check.static(out), [])

    def test_url_in_text_is_a_deep_link_only(self):
        f = copy.deepcopy(self.facts)
        f["doctor"]["findings"][0]["text"] = "see https://example.org/a?b=1&c=2. Or (https://example.net/x)"
        out = render(f)
        self.assertIn('<a href="https://example.org/a?b=1&amp;c=2" rel="noreferrer">https://example.org/a?b=1&amp;c=2</a>.', out)
        self.assertIn('<a href="https://example.net/x" rel="noreferrer">https://example.net/x</a>)', out)
        self.assertEqual(offline_check.static(out), [])

    def test_quote_in_url_cannot_break_out_of_href(self):
        f = copy.deepcopy(self.facts)
        f["doctor"]["findings"][0]["text"] = 'https://e.org/"onmouseover="alert(1)'
        out = render(f)
        self.assertNotIn('onmouseover="', out)
        self.assertEqual(offline_check.static(out), [])

    def test_http_url_is_a_deep_link_too(self):
        f = copy.deepcopy(self.facts)
        f["doctor"]["findings"][0]["text"] = "plain http://example.org/x"
        out = render(f)
        self.assertIn('<a href="http://example.org/x" rel="noreferrer">http://example.org/x</a>', out)
        self.assertEqual(offline_check.static(out), [])

    def test_capacity_rows_render(self):
        f = copy.deepcopy(self.facts)
        f["capacity"] = [{
            "engine": "ovms", "model": "Qwen3-4B", "precision": "int4", "agents_at_p95": 7,
            "slo_p95_s": 2.0, "p95_s": 1.8, "measured_on": "2026-10-16", "report_id": "r-1",
            "release": "0.0.0", "host_fingerprint": f["fingerprint"], "reps": 3, "ci95": [1.7, 1.9]}]
        factsmod.schema_check.check(f, factsmod.load_schema())
        out = render(f)
        self.assertIn('<td class="n">7</td>', out)
        self.assertIn("1.7–1.9", out)
        self.assertNotIn("No capacity run recorded", out)

    def test_no_capacity_says_so(self):
        self.assertIn("No capacity run recorded", render(self.facts))

    def test_plan_for_another_host_is_refused(self):
        p = planmod.plan(self.facts, MATRIX)
        p["fingerprint"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            report_html.render(self.facts, p)

    def test_non_facts_document_is_refused(self):
        with self.assertRaises(ValueError):
            report_html.render({"schema": "k3d-gpu.facts/v1"}, planmod.plan(self.facts, MATRIX))

    def test_no_clock(self):
        # Rendering the same documents later gives the same bytes; the report
        # names no time of its own.
        out = render(self.facts)
        self.assertNotRegex(out, r"20\d\d-\d\d-\d\dT")


class OfflineCheck(unittest.TestCase):
    """Mutation controls: each injected fetch must be caught."""

    def setUp(self):
        self.clean = render(load(RAPTOR))

    def inject(self, snippet):
        return self.clean.replace("</main>", snippet + "</main>", 1)

    def test_clean_report_passes(self):
        self.assertEqual(offline_check.static(self.clean), [])

    def test_mutations_are_caught(self):
        for snippet in (
            '<img src="https://example.com/x.png">',
            '<img src="x.png">',
            '<script>fetch("/x")</script>',
            '<script src="a.js"></script>',
            '<link rel="stylesheet" href="https://example.com/a.css">',
            '<iframe src="about:blank"></iframe>',
            '<div style="background:url(https://example.com/a.png)"></div>',
            '<style>@import "https://example.com/a.css";</style>',
            '<style>@font-face{font-family:x;src:local(x)}</style>',
            '<p>https://example.com/naked</p>',
            '<a href="javascript:alert(1)">x</a>',
            '<a href="file:///etc/passwd">x</a>',
            '<a href="https://example.com/a">https://example.com/b</a>',
            '<video poster="p.png"></video>',
            '<meta http-equiv="refresh" content="0;url=https://example.com/">',
            '<div onclick="x()">x</div>',
            '<svg><image href="https://example.com/a.png"/></svg>',
            '<form action="https://example.com/"></form>',
        ):
            with self.subTest(snippet=snippet):
                self.assertNotEqual(offline_check.static(self.inject(snippet)), [])

    def test_missing_csp_is_caught(self):
        stripped = self.clean.replace("default-src 'none'", "default-src *")
        self.assertNotEqual(offline_check.static(stripped), [])

    def netlog(self, *requests):
        events = [{"type": 7, "params": {"url": u, "initiator": i, "network_isolation_key": k, "method": "GET"}}
                  for u, i, k in requests]
        return {"constants": {"logEventTypes": {"URL_REQUEST_START_JOB": 7, "OTHER": 8}}, "events": events}

    def test_netlog_ignores_browser_traffic(self):
        doc = self.netlog(("https://www.google.com/", "not an origin", "https://google.com https://google.com"))
        self.assertEqual(offline_check.netlog(doc), [])

    def test_netlog_catches_page_requests(self):
        doc = self.netlog(("https://example.com/x.png", "null", "file:// [internally: file://] file://"))
        self.assertNotEqual(offline_check.netlog(doc), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)

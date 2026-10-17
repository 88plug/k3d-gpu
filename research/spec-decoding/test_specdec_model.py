#!/usr/bin/env python3
"""Unit tests for research/spec-decoding/specdec_model.py. Run: python3 research/spec-decoding/test_specdec_model.py"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import specdec_model as m  # noqa: E402


def simulate(alpha, gamma, iters, rng):
    """Monte Carlo of the accept/reject loop: each of gamma draft tokens is
    accepted with prob alpha until the first rejection; the target always adds
    one token. Returns (tokens per iteration, accepted / offered)."""
    tokens = accepted = 0
    for _ in range(iters):
        a = 0
        while a < gamma and rng.random() < alpha:
            a += 1
        accepted += a
        tokens += a + 1
    return tokens / iters, accepted / (iters * gamma)


class TokensPerIter(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(m.tokens_per_iter(0.0, 5), 1.0)
        self.assertEqual(m.tokens_per_iter(1.0, 5), 6.0)
        self.assertEqual(m.tokens_per_iter(0.7, 0), 1.0)

    def test_matches_monte_carlo(self):
        rng = random.Random(1)
        for alpha in (0.3, 0.6, 0.8, 0.95):
            for gamma in (1, 3, 5, 8):
                sim_tok, sim_rate = simulate(alpha, gamma, 40000, rng)
                self.assertAlmostEqual(m.tokens_per_iter(alpha, gamma), sim_tok, delta=0.05)
                self.assertAlmostEqual(m.rate_from_alpha(alpha, gamma), sim_rate, delta=0.01)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            m.tokens_per_iter(1.2, 5)
        with self.assertRaises(ValueError):
            m.tokens_per_iter(0.5, -1)


class Conversion(unittest.TestCase):
    def test_round_trip(self):
        for alpha in (0.0, 0.25, 0.5, 0.8, 0.99):
            for gamma in (1, 5, 10):
                r = m.rate_from_alpha(alpha, gamma)
                self.assertAlmostEqual(m.alpha_from_rate(r, gamma), alpha, places=6)

    def test_gamma_one_rate_is_alpha(self):
        self.assertAlmostEqual(m.rate_from_alpha(0.62, 1), 0.62)

    def test_rate_below_alpha_for_long_drafts(self):
        # Candidates after the first rejection count as offered but never accepted.
        self.assertLess(m.rate_from_alpha(0.8, 5), 0.8)


class Speedup(unittest.TestCase):
    def test_leviathan_closed_form(self):
        # beta=0, o=0: speedup = (1-a^(g+1)) / ((1-a)(g*c+1)), Leviathan 2023 Thm 3.8.
        a, g, c = 0.8, 4, 0.1
        want = (1 - a ** (g + 1)) / ((1 - a) * (g * c + 1))
        self.assertAlmostEqual(m.speedup(a, g, 10.0, 1.0), want, places=12)

    def test_zero_acceptance_loses(self):
        self.assertLess(m.speedup(0.0, 5, 40.0, 8.0), 1.0)

    def test_free_perfect_draft(self):
        self.assertAlmostEqual(m.speedup(1.0, 5, 40.0, 0.0), 6.0)

    def test_pipelined_never_worse(self):
        for a in (0.0, 0.5, 0.9):
            for td in (1.0, 8.0, 30.0):
                self.assertGreaterEqual(m.speedup(a, 5, 40.0, td, 0.05, 2.0, pipelined=True),
                                        m.speedup(a, 5, 40.0, td, 0.05, 2.0))

    def test_slower_draft_engine_is_worse(self):
        # Sequential execution: the only lever a draft engine has is its own t_d.
        self.assertGreater(m.speedup(0.7, 5, 40.0, 8.0), m.speedup(0.7, 5, 40.0, 12.0))

    def test_rejects_bad_latency(self):
        with self.assertRaises(ValueError):
            m.speedup(0.5, 5, 0.0, 1.0)


class BreakEven(unittest.TestCase):
    def test_break_even_is_unity(self):
        a = m.break_even_alpha(5, 40.0, 8.0, beta=0.05, o=1.0)
        self.assertIsNotNone(a)
        self.assertAlmostEqual(m.speedup(a, 5, 40.0, 8.0, 0.05, 1.0), 1.0, places=6)

    def test_hopeless_draft(self):
        # Draft as slow as the target: even alpha=1 gives 6 tokens for 6*t_t.
        self.assertIsNone(m.break_even_alpha(5, 40.0, 41.0))

    def test_best_gamma_in_range(self):
        g, s = m.best_gamma(0.8, 40.0, 4.0)
        self.assertTrue(1 <= g <= 16)
        self.assertGreaterEqual(s, m.speedup(0.8, 5, 40.0, 4.0) - 1e-12)


class PromptLookup(unittest.TestCase):
    def test_no_hits_is_baseline(self):
        self.assertAlmostEqual(m.prompt_lookup_speedup(0.0, 0.9, 5, 40.0, 0.05, 1.0), 1.0)

    def test_all_hits_free_draft(self):
        self.assertAlmostEqual(m.prompt_lookup_speedup(1.0, 0.8, 5, 40.0),
                               m.speedup(0.8, 5, 40.0, 0.0))

    def test_wasted_hits_cost_verify(self):
        self.assertLess(m.prompt_lookup_speedup(1.0, 0.0, 5, 40.0, beta=0.05), 1.0)


class Cli(unittest.TestCase):
    def test_runs(self):
        import io
        import json
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(m.main(["--t-target", "40", "--t-draft", "8", "--rate", "0.5"]), 0)
        out = json.loads(buf.getvalue())
        self.assertAlmostEqual(m.rate_from_alpha(out["alpha"], 5), 0.5, places=3)


if __name__ == "__main__":
    unittest.main()

import random
import time
import unittest

import receipts as r


def ok_handler(name, args, variant):
    return {"content": [{"type": "text", "text": f"{name}:{variant}"}], "isError": False}


class Clock:
    def __init__(self, step_ns):
        self.t = 0
        self.step = step_ns

    def __call__(self):
        self.t += self.step
        return self.t


class BaggageTest(unittest.TestCase):
    def test_roundtrip_preserves_foreign_members_and_properties(self):
        h = "userId=alice;p1, k3dgpu.joule_budget=500,k3dgpu.joules_spent=123.5,x=a%2Cb"
        m = r.parse_baggage(h)
        self.assertEqual([x.key for x in m],
                         ["userId", "k3dgpu.joule_budget", "k3dgpu.joules_spent", "x"])
        self.assertEqual(m[0].properties, ["p1"])
        self.assertEqual(m[3].value, "a,b")
        again = r.parse_baggage(r.format_baggage(m))
        self.assertEqual([(x.key, x.value, x.properties) for x in again],
                         [(x.key, x.value, x.properties) for x in m])

    def test_malformed_members_skipped(self):
        m = r.parse_baggage("novalue, =x, ok=1,,")
        self.assertEqual([(x.key, x.value) for x in m], [("ok", "1")])

    def test_budget_read_and_bad_values(self):
        _, jb = r.read_budget({"baggage": "k3dgpu.joule_budget=500,k3dgpu.joules_spent=123"})
        self.assertEqual((jb.budget, jb.spent, jb.remaining), (500.0, 123.0, 377.0))
        for bad in ("k3dgpu.joule_budget=nan", "k3dgpu.joule_budget=-5",
                    "k3dgpu.joule_budget=abc", ""):
            _, jb = r.read_budget({"baggage": bad})
            self.assertIsNone(jb.budget, bad)
        _, jb = r.read_budget(None)
        self.assertEqual((jb.budget, jb.spent), (None, 0.0))

    def test_charge_without_budget_leaves_baggage_unchanged(self):
        m, jb = r.read_budget({"baggage": "userId=alice"})
        self.assertEqual(r.charge(m, jb, 10.0), "userId=alice")


class MetaKeyTest(unittest.TestCase):
    # MCP 2026-07-28 basic/index.mdx: prefix = dot-separated labels + "/", label starts with a
    # letter and ends with a letter or digit; second label "modelcontextprotocol"/"mcp" reserved.
    LABEL = r"[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?"

    def test_vendor_keys_are_valid_and_unreserved(self):
        import re
        for key in (r.RECEIPT_KEY, r.VARIANT_KEY):
            prefix, _, name = key.partition("/")
            labels = prefix.split(".")
            self.assertTrue(all(re.fullmatch(self.LABEL, x) for x in labels), key)
            self.assertNotIn(labels[1], ("modelcontextprotocol", "mcp"), key)
            self.assertTrue(name)
        self.assertEqual(r.BAGGAGE_KEY, "baggage")  # reserved unprefixed key


class ReceiptTest(unittest.TestCase):
    def test_sigma_terms(self):
        # 2 W background error over 50 ms dominates the 1 ms edge term at 40 W host power
        s = r.apportion_sigma(40.0, 2.0, 0.05)
        self.assertAlmostEqual(s, (0.1 ** 2 + (2 ** 0.5 * 40 * 0.001 / 12 ** 0.5) ** 2) ** 0.5)
        self.assertGreater(r.apportion_sigma(40.0, 0.0, 1.0), 0.0)  # edge term never zero

    def test_confidence_bounds(self):
        self.assertEqual(r.confidence(5.0, 0.0), 1.0)
        self.assertAlmostEqual(r.confidence(1.0, 1.0), 0.5)
        self.assertLess(r.confidence(0.0, 0.1), 1e-6)

    def test_build_receipt_sums_and_marks_modelled(self):
        reading = r.LedgerReading(1, 2, {
            "cpu_p": r.DomainReading(1.0, 0.3, "modelled"),
            "nvidia0": r.DomainReading(4.0, 0.4, "measured"),
            "dram": r.DomainReading(0.5, 0.0, "measured"),  # unknown -> residual
        }, device="nvidia0")
        rc = r.build_receipt(reading)
        self.assertAlmostEqual(rc["joules_total"], 5.5)
        self.assertAlmostEqual(rc["sigma_joules"], 0.5)
        self.assertEqual(rc["by_domain"]["residual"], 0.5)
        self.assertEqual(rc["sources"], {"cpu_p": "modelled", "nvidia0": "measured",
                                         "residual": "measured"})
        self.assertEqual(set(rc["by_domain"]), set(r.RECEIPT_DOMAINS))


class SteererTest(unittest.TestCase):
    def mk(self, **kw):
        return r.Steerer({"t": ["p", "e", "igpu"]}, {"t": "p"}, epsilon=0.0, **kw)

    def test_cold_start_goes_to_default(self):
        self.assertEqual(self.mk().choose("t"), "p")

    def test_picks_min_cost_within_slo(self):
        s = self.mk()
        s.observe("t", "p", 2.0, 0.100, 1.0)
        s.observe("t", "e", 1.0, 0.105, 1.0)   # within 10 %
        s.observe("t", "igpu", 0.5, 0.300, 1.0)  # cheapest but too slow
        self.assertEqual(s.choose("t"), "e")
        self.assertEqual(s.choose("t", pressure=True), "igpu")

    def test_total_mode_prices_idle_power(self):
        s = self.mk(idle_watts=20.0, latency_slack=10.0)
        s.observe("t", "p", 2.0, 0.10, 1.0)   # 2 + 2 = 4
        s.observe("t", "e", 1.0, 0.20, 1.0)   # 1 + 4 = 5
        self.assertEqual(s.choose("t"), "p")
        s.cost_mode = "incremental"
        self.assertEqual(s.choose("t"), "e")

    def test_gate_drops_low_confidence_joules_but_keeps_latency(self):
        s = self.mk()
        s.observe("t", "e", 9.0, 0.1, 0.2)
        st = s.stats[("t", "e")]
        self.assertEqual((st.n_j, st.n_l), (0, 1))
        s.gate = False
        s.observe("t", "e", 9.0, 0.1, 0.2)
        self.assertEqual(s.stats[("t", "e")].n_j, 1)

    def test_epsilon_explores_but_not_under_pressure(self):
        s = r.Steerer({"t": ["p", "e"]}, {"t": "p"}, epsilon=1.0, rng=random.Random(1))
        self.assertIn("e", {s.choose("t") for _ in range(50)})
        s.observe("t", "p", 1.0, 0.1, 1.0)
        s.observe("t", "e", 5.0, 0.1, 1.0)
        self.assertEqual({s.choose("t", pressure=True) for _ in range(50)}, {"p"})


class MiddlewareTest(unittest.TestCase):
    def test_receipt_in_meta_and_window_matches_call(self):
        led = r.MockLedger(watts=10.0)
        mw = r.EnergyMiddleware(led, ok_handler, lambda n, v: f"/kubepods/{n}-{v}",
                                clock_ns=Clock(50_000_000))
        out = mw.call({"name": "grep", "arguments": {}})
        rc = out["_meta"][r.RECEIPT_KEY]
        self.assertEqual(led.calls, [("/kubepods/grep-default", 50_000_000, 100_000_000)])
        self.assertAlmostEqual(rc["joules_total"], 0.5)
        self.assertEqual(out["content"][0]["text"], "grep:default")
        self.assertNotIn("baggage", out["_meta"])

    def test_budget_is_charged_and_reported(self):
        led = r.MockLedger(watts=10.0)
        mw = r.EnergyMiddleware(led, ok_handler, lambda n, v: "cg", clock_ns=Clock(100_000_000))
        meta = {"baggage": "userId=a,k3dgpu.joule_budget=1.5,k3dgpu.joules_spent=0.6"}
        out = mw.call({"name": "x", "_meta": meta})
        rc = out["_meta"][r.RECEIPT_KEY]
        self.assertEqual(rc["budget"]["joules_spent"], 1.6)
        self.assertFalse(rc["budget"]["within"])
        _, jb = r.read_budget(out["_meta"])
        self.assertAlmostEqual(jb.spent, 1.6)
        self.assertTrue(out["_meta"]["baggage"].startswith("userId=a,"))

    def test_nested_call_spend_is_absorbed(self):
        led = r.MockLedger(watts=10.0)
        inner = r.EnergyMiddleware(led, ok_handler, lambda n, v: "inner",
                                   clock_ns=Clock(100_000_000))

        def outer_handler(name, args, variant):
            nested = inner.call({"name": "leaf", "_meta": {"baggage": r.current_baggage()}})
            r.absorb_nested(nested["_meta"])
            return ok_handler(name, args, variant)

        outer = r.EnergyMiddleware(led, outer_handler, lambda n, v: "outer",
                                   clock_ns=Clock(100_000_000))
        out = outer.call({"name": "root", "_meta": {"baggage": "k3dgpu.joule_budget=10"}})
        _, jb = r.read_budget(out["_meta"])
        self.assertAlmostEqual(jb.spent, 2.0)  # 1 J nested + 1 J own window
        self.assertIsNone(r.current_baggage())

    def test_steered_variant_used_and_learned(self):
        st = r.Steerer({"emb": ["p", "igpu"]}, {"emb": "p"}, epsilon=0.0)
        seen = []
        mw = r.EnergyMiddleware(r.MockLedger(watts=5.0), lambda n, a, v: seen.append(v) or {},
                                lambda n, v: v, steerer=st, clock_ns=Clock(10_000_000))
        mw.call({"name": "emb"})
        mw.call({"name": "emb", "_meta": {r.VARIANT_KEY: "igpu"}})
        mw.call({"name": "emb", "_meta": {r.VARIANT_KEY: "bogus"}})
        self.assertEqual(seen[:2], ["p", "igpu"])
        self.assertIn(seen[2], ("p", "igpu"))
        self.assertEqual(st.stats[("emb", "p")].n_l + st.stats[("emb", "igpu")].n_l, 3)

    def test_handler_exception_restores_context(self):
        def boom(n, a, v):
            raise RuntimeError("x")
        mw = r.EnergyMiddleware(r.MockLedger(), boom, lambda n, v: "c")
        with self.assertRaises(RuntimeError):
            mw.call({"name": "t", "_meta": {"baggage": "a=1"}})
        self.assertIsNone(r.current_baggage())


class OverheadTest(unittest.TestCase):
    """H5: wrapper-only overhead (mock ledger), p99 <= 2 ms."""

    def test_p99_overhead(self):
        st = r.Steerer({"t": ["p", "e", "igpu", "nv0"]}, {"t": "p"})
        mw = r.EnergyMiddleware(r.MockLedger(bg_sigma_watts=2.0), ok_handler, lambda n, v: "c",
                                steerer=st)
        params = {"name": "t", "arguments": {}, "_meta": {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
            "baggage": "userId=a,k3dgpu.joule_budget=500,k3dgpu.joules_spent=12"}}
        raw, wrapped = [], []
        for _ in range(3000):
            t = time.perf_counter_ns(); ok_handler("t", {}, "p"); raw.append(time.perf_counter_ns() - t)
            t = time.perf_counter_ns(); mw.call(params); wrapped.append(time.perf_counter_ns() - t)
        raw.sort(); wrapped.sort()
        p99 = (wrapped[int(0.99 * len(wrapped))] - raw[int(0.99 * len(raw))]) / 1e6
        print(f"\nH5 wrapper p99 added = {p99:.4f} ms")
        self.assertLessEqual(p99, 2.0)


if __name__ == "__main__":
    unittest.main()

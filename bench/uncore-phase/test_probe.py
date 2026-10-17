#!/usr/bin/env python3
"""Unit tests for bench/uncore-phase/probe.py: cell order, sign-test confidence, the
pre-registered verdict rules, and the knob writes/restore against a fake sysfs tree."""
import contextlib, importlib.util, io, json, os, sys, tempfile, unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="h29-test-")
os.environ["K3D_GPU_SYSROOT"] = TMP
spec = importlib.util.spec_from_file_location("probe", os.path.join(HERE, "probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def put(rel, val):
    p = TMP + rel
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(f"{val}\n")


def fake_tree():
    unc = "/sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00"
    for k, v in (("initial_min_freq_khz", 800000), ("initial_max_freq_khz", 4700000),
                 ("min_freq_khz", 800000), ("max_freq_khz", 4700000)):
        put(f"{unc}/{k}", v)
    for c in range(4):
        put(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_max_freq", 5100000)


def args(**kw):
    d = dict(core_cap_khz=2800000, uncore_low_khz=2000000, swap_flag_pages=25600)
    d.update(kw)
    return SimpleNamespace(**d)


def cell(r, arm, tok, j, ring=4700, core=5000):
    return {"round": r, "arm": arm, "decode_tok_s": tok, "marg_j_tok": j, "gross_j_tok": j * 2,
            "ring_mhz": ring, "pod_cpu_mhz": core, "load1": 1.0, "temp_max_c": 70,
            "throttle_delta": 0, "pswpin": 0}


def run_analyze(rows):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        probe.analyze(f.name, args())
    os.unlink(f.name)
    return buf.getvalue()


def table(uc, c, u=(15.6, 1.0), a=(15.6, 1.0), l_tok=13.0, rounds=5, ring_l=2000, core_c=2800):
    """uc, c, u, a: (tok/s, marginal J/token) per arm, identical in every round."""
    rows = []
    for r in range(rounds):
        rows += [cell(r, "A", *a), cell(r, "U", *u), cell(r, "C", *c, core=core_c), cell(r, "UC", *uc, core=core_c)]
        if r % 2 == 0:
            rows.append(cell(r, "L", l_tok, 1.0, ring=ring_l))
    return rows


class Order(unittest.TestCase):
    def test_counts(self):
        cells = probe.order(5)
        self.assertEqual(len(cells), 23)
        for arm in probe.PRIMARY:
            self.assertEqual(sum(1 for _, x in cells if x == arm), 5)
        self.assertEqual([r for r, x in cells if x == "L"], [0, 2, 4])

    def test_each_round_has_every_primary_arm_once(self):
        for r in range(5):
            arms = [x for rr, x in probe.order(5) if rr == r and x != "L"]
            self.assertEqual(sorted(arms), sorted(probe.PRIMARY))

    def test_every_arm_takes_every_position(self):
        for pos in range(4):
            col = [[x for rr, x in probe.order(4) if rr == r and x != "L"][pos] for r in range(4)]
            self.assertEqual(sorted(col), sorted(probe.PRIMARY))

    def test_every_ordered_pair_once(self):
        pairs = set()
        for r in range(4):
            o = [x for rr, x in probe.order(4) if rr == r and x != "L"]
            pairs |= set(zip(o, o[1:]))
        self.assertEqual(len(pairs), 12)


class Sign(unittest.TestCase):
    def test_values(self):
        self.assertEqual(probe.sign_conf([-1] * 5), 0.969)
        self.assertEqual(probe.sign_conf([-1] * 4 + [1]), 0.812)
        self.assertEqual(probe.sign_conf([1] * 5), 0.0)
        self.assertIsNone(probe.sign_conf([]))


class Verdict(unittest.TestCase):
    def test_supported(self):
        out = run_analyze(table(uc=(15.4, 0.85), c=(14.0, 0.86)))
        self.assertIn("VERDICT SUPPORTED", out)

    def test_refuted_split(self):
        out = run_analyze(table(uc=(15.4, 0.85), c=(15.4, 0.85)))
        self.assertIn("VERDICT REFUTED (split)", out)

    def test_refuted_speed(self):
        out = run_analyze(table(uc=(14.0, 0.80), c=(14.0, 0.80)))
        self.assertIn("VERDICT REFUTED:", out)

    def test_refuted_energy(self):
        out = run_analyze(table(uc=(15.6, 0.95), c=(14.0, 0.96)))
        self.assertIn("VERDICT REFUTED:", out)

    def test_inconclusive_too_few_cells(self):
        out = run_analyze(table(uc=(15.4, 0.85), c=(14.0, 0.86), rounds=3))
        self.assertIn("VERDICT INCONCLUSIVE: < 4 valid", out)

    def test_throttled_cells_are_excluded(self):
        rows = table(uc=(15.4, 0.85), c=(14.0, 0.86))
        for x in rows:
            if x["arm"] == "UC" and x["round"] < 2:
                x["throttle_delta"] = 3
        self.assertIn("VERDICT INCONCLUSIVE: < 4 valid", run_analyze(rows))

    def test_inconclusive_noisy_baseline(self):
        rows = table(uc=(15.4, 0.85), c=(14.0, 0.86))
        next(x for x in rows if x["arm"] == "A")["decode_tok_s"] = 12.0
        self.assertIn("baseline tok/s spread", run_analyze(rows))

    def test_inconclusive_uncore_not_honoured(self):
        out = run_analyze(table(uc=(15.4, 0.85), c=(14.0, 0.86), ring_l=4700))
        self.assertIn("uncore writes not honoured", out)

    def test_inconclusive_core_cap_not_honoured(self):
        out = run_analyze(table(uc=(15.4, 0.85), c=(14.0, 0.86), core_c=4900))
        self.assertIn("core cap not honoured", out)


class KnobWrites(unittest.TestCase):
    def setUp(self):
        fake_tree()
        self.log = os.path.join(TMP, "writes.log")
        if os.path.exists(self.log):
            os.unlink(self.log)

    def tearDown(self):
        for k in getattr(self, "knobs", []):
            if k.log:
                k.log.close()

    def val(self, rel):
        return probe.rd(TMP + rel)

    def test_apply_and_restore(self):
        k = probe.Knobs([0, 1], self.log, dry=False)
        self.knobs = [k]
        unc = "/sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00"
        k.apply("UC", args())
        self.assertEqual(self.val(f"{unc}/min_freq_khz"), 4700000)
        self.assertEqual(self.val(f"{unc}/max_freq_khz"), 4700000)
        self.assertEqual(self.val("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"), 2800000)
        self.assertEqual(self.val("/sys/devices/system/cpu/cpu2/cpufreq/scaling_max_freq"), 5100000)
        k.apply("L", args())
        self.assertEqual(self.val(f"{unc}/max_freq_khz"), 2000000)
        self.assertEqual(self.val(f"{unc}/min_freq_khz"), 2000000)
        self.assertEqual(self.val("/sys/devices/system/cpu/cpu1/cpufreq/scaling_max_freq"), 5100000)
        k.restore()
        self.assertEqual(self.val(f"{unc}/min_freq_khz"), 800000)
        self.assertEqual(self.val(f"{unc}/max_freq_khz"), 4700000)
        with open(self.log) as f:
            lines = f.read().splitlines()
        self.assertTrue(all(" write " in x or x.endswith(" restored") for x in lines))
        self.assertTrue(lines[-1].endswith(" restored"))

    def test_uncore_write_order_keeps_min_le_max(self):
        k = probe.Knobs([0], None, dry=True)
        seen = []
        k.w = lambda path, val: seen.append((os.path.basename(path), val))
        k.uncore(2000000, 2000000)
        self.assertEqual(seen, [("min_freq_khz", 800000), ("max_freq_khz", 2000000), ("min_freq_khz", 2000000)])

    def test_dry_run_writes_nothing(self):
        k = probe.Knobs([0, 1], None, dry=True)
        with contextlib.redirect_stdout(io.StringIO()):
            k.apply("UC", args())
        self.assertEqual(self.val("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"), 5100000)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], verbosity=1)

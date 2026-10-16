"""Checks for the review of the Joule Ledger prototype: daemon exposure,
manifest placement, pinned sim deps, and README numbers matching sim.py.

Run: python3 -m pytest -q tests/joule_ledger
"""

import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)

from joule_ledger.daemon import Ledger, make_server  # noqa: E402

README = os.path.join(ROOT, "tools", "joule_ledger", "README.md")
MANIFEST = os.path.join(ROOT, "tools", "joule_ledger", "example-daemonset.yml")
SEED = 20261016


def _zone(root, name, rel, val):
    d = os.path.join(root, "sys/class/powercap", rel)
    os.makedirs(d)
    for k, v in (("name", name), ("energy_uj", str(val)), ("max_energy_range_uj", "262143328850")):
        with open(os.path.join(d, k), "w") as f:
            f.write(v + "\n")


class DaemonExposureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _zone(self.tmp.name, "package-0", "intel-rapl:0", 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def _get(self, srv, path, token=None):
        url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _serve(self, token=None):
        led = Ledger(self.tmp.name, 0.0, "libdoes-not-exist.so")
        led.tick()
        srv = make_server(led, "127.0.0.1:0", token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def test_raw_edges_not_served(self):
        srv = self._serve()
        code, _ = self._get(srv, "/v1/edges?domain=rapl.package-0&since=0")
        self.assertEqual(code, 404)
        code, body = self._get(srv, "/v1/domains")
        self.assertEqual((code, body), (200, ["rapl.package-0"]))

    def test_token_required_when_configured(self):
        srv = self._serve(token="s3cret")
        self.assertEqual(self._get(srv, "/v1/domains")[0], 401)
        self.assertEqual(self._get(srv, "/v1/domains", token="wrong")[0], 401)
        self.assertEqual(self._get(srv, "/v1/domains", token="s3cret")[0], 200)

    def test_default_listen_is_loopback(self):
        import joule_ledger.daemon as d
        src = open(d.__file__).read()
        self.assertIn('ap.add_argument("--listen", default="127.0.0.1:9810")', src)


class ManifestTest(unittest.TestCase):
    def test_not_on_launcher_or_package_path(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "share", "joule-ledger.yml")))

    def test_example_is_locked_down(self):
        m = open(MANIFEST).read()
        self.assertTrue(m.startswith("# EXAMPLE ONLY."))
        self.assertIn("kind: NetworkPolicy", m)
        self.assertIn("--token-file", m)
        self.assertIn("$(POD_IP):9810", m)
        self.assertNotIn("0.0.0.0", m)
        self.assertNotIn("k3d-gpu-joule-ledger:0.1.0", m)


class SimDepsTest(unittest.TestCase):
    def test_numpy_scipy_pinned(self):
        req = open(os.path.join(HERE, "requirements.txt")).read()
        pins = dict(re.findall(r"^([a-z]+)==([0-9][0-9.]*)$", req, re.M))
        self.assertEqual(sorted(pins), ["numpy", "scipy"])


class SimReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sim
        cls.sim = sim
        cls.draw = sim.run_draw(SEED + 11)   # conc >= 2 draw
        cls.readme = open(README).read()

    def test_split_kepler_never_double_counts(self):
        sim = self.sim
        import numpy as np
        rng = np.random.default_rng(SEED + 11)
        p = sim.draw_params(rng)
        reqs, truth_dyn, E, polls, shown, power, sat = sim.gpu_world(rng, p)
        est, pod = sim.kepler_split_estimates(reqs, polls, shown, p["p_idle"], 5.0)
        full, pod_full = sim.kepler_estimates(
            [sim.Span(str(i), s, e) for i, (s, e) in enumerate(reqs)],
            polls, shown, p["p_idle"], 5.0)
        self.assertAlmostEqual(pod, pod_full)
        self.assertLessEqual(est.sum(), pod * (1 + 1e-9))
        self.assertGreater(full.sum(), est.sum())

    def test_c1_tightness_reported(self):
        d = self.draw
        for k in ("c1_hi_over_truth", "c1_width_over_truth", "c1_lo_zero_frac",
                  "c2_kepler5_split", "c2_kepler1_split"):
            self.assertIn(k, d)
        self.assertGreaterEqual(d["c1_hi_over_truth"], 1.0)
        self.assertEqual(len(d["c1_mf"]), 3)

    def test_readme_c5_matches_default_seed(self):
        w = self.sim.c5(SEED)
        for name in ("coupled_dvfs", "decoupled_dvfs", "coupled_pinned"):
            row = [ln for ln in self.readme.splitlines() if ln.strip().startswith(f"| {name} ")]
            self.assertEqual(len(row), 1, name)
            self.assertIn(f"{w[name]['naive_err']:.3f}", row[0], name)

    def test_readme_c5_multiseed_matches_flag(self):
        v = self.sim.c5_seeds(SEED, 10)["coupled_dvfs"]
        self.assertIn("--c5-seeds 10", self.readme)
        lo, med, hi = v["naive_err"]
        self.assertIn(f"{med:.3f} ({lo:.3f}-{hi:.3f}", self.readme)
        self.assertIn(f"{v['refuse'][0] * 100:.0f}-{v['refuse'][2] * 100:.0f}% refuse", self.readme)

    def test_readme_labels_c3_unit_check_and_c2_marginal(self):
        self.assertRegex(self.readme, r"\| C3 [^|]*unit check")
        self.assertRegex(self.readme, r"\| C2 [^|]*\| marginal")


if __name__ == "__main__":
    unittest.main()

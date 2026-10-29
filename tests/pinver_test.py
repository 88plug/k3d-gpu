#!/usr/bin/env python3
"""Tests for hack/pinver.py: every versioning scheme, ranges, update types,
prerelease exclusion and the CLI.

Usage: python3 tests/pinver_test.py
"""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hack"))
import pinver  # noqa: E402


class Semver(unittest.TestCase):
    def test_v_prefix_is_optional(self):
        self.assertEqual(pinver.compare("semver", "v1.2.3", "1.2.3"), 0)

    def test_numeric_order(self):
        self.assertEqual(pinver.compare("semver", "1.9.0", "1.10.0"), -1)
        self.assertEqual(pinver.compare("semver", "2.0.0", "1.99.99"), 1)

    def test_prerelease_sorts_before_release(self):
        self.assertEqual(pinver.compare("semver", "1.2.3-rc.1", "1.2.3"), -1)
        self.assertEqual(pinver.compare("semver", "1.2.3-rc.2", "1.2.3-rc.10"), -1)
        self.assertEqual(pinver.compare("semver", "1.2.3-alpha", "1.2.3-alpha.1"), -1)

    def test_prerelease_flag(self):
        self.assertTrue(pinver.is_prerelease("semver", "v1.2.3-rc1"))
        self.assertFalse(pinver.is_prerelease("semver", "v1.2.3"))

    def test_build_metadata_ignored_in_order(self):
        self.assertEqual(pinver.compare("semver", "1.2.3+a", "1.2.3+b"), 0)

    def test_update_types(self):
        self.assertEqual(pinver.update_type("semver", "1.2.3", "1.2.4"), "patch")
        self.assertEqual(pinver.update_type("semver", "1.2.3", "1.3.0"), "minor")
        self.assertEqual(pinver.update_type("semver", "1.2.3", "2.0.0"), "major")
        self.assertEqual(pinver.update_type("semver", "v1.2.3", "1.2.3"), "digest")

    def test_breaking_is_major_only(self):
        self.assertFalse(pinver.breaking("semver", "1.2.3", "1.3.0"))
        self.assertTrue(pinver.breaking("semver", "1.2.3", "2.0.0"))
        # Under plain semver a 0.x minor is not breaking; zerover says it is.
        self.assertFalse(pinver.breaking("semver", "0.24.0", "0.25.0"))

    def test_garbage_is_rejected(self):
        for bad in ("", "latest", "1.2.x", "v"):
            with self.assertRaises(ValueError, msg=bad):
                pinver.parse("semver", bad)


class Zerover(unittest.TestCase):
    def test_zerover_minor_is_minor_but_breaking(self):
        self.assertEqual(pinver.update_type("zerover", "v0.24.0", "v0.25.0"), "minor")
        self.assertTrue(pinver.breaking("zerover", "v0.24.0", "v0.25.0"))
        self.assertEqual(pinver.classify("zerover", "v0.24.0", "v0.25.0"),
                         {"type": "minor", "breaking": True})

    def test_zerover_patch_is_not_breaking(self):
        self.assertEqual(pinver.update_type("zerover", "v0.25.0", "v0.25.1"), "patch")
        self.assertFalse(pinver.breaking("zerover", "v0.25.0", "v0.25.1"))

    def test_zerover_past_one_is_semver(self):
        self.assertFalse(pinver.breaking("zerover", "1.2.0", "1.3.0"))
        self.assertTrue(pinver.breaking("zerover", "0.32.0", "1.0.0"))

    def test_vllm_fixed_in(self):
        self.assertTrue(pinver.in_range("zerover", "v0.24.0", "<0.25.0"))
        self.assertFalse(pinver.in_range("zerover", "v0.25.0", "<0.25.0"))
        self.assertFalse(pinver.in_range("zerover", "v0.32.0", "<0.25.0"))


class K8s(unittest.TestCase):
    def test_plus_and_dash_k3s_forms(self):
        self.assertEqual(pinver.compare("k8s", "v1.37.1+k3s1", "v1.37.1-k3s1"), 0)
        self.assertEqual(pinver.compare("k8s", "v1.37.1+k3s1", "v1.37.1+k3s2"), -1)
        self.assertEqual(pinver.compare("k8s", "v1.37.1+k3s9", "v1.37.2+k3s1"), -1)

    def test_k8s_minor_is_breaking(self):
        self.assertEqual(pinver.update_type("k8s", "v1.37.1+k3s1", "v1.38.0+k3s1"), "minor")
        self.assertTrue(pinver.breaking("k8s", "v1.37.1+k3s1", "v1.38.0+k3s1"))

    def test_k8s_patch_and_distro_build(self):
        self.assertEqual(pinver.update_type("k8s", "v1.37.1+k3s1", "v1.37.2+k3s1"), "patch")
        self.assertEqual(pinver.update_type("k8s", "v1.37.1+k3s1", "v1.37.1+k3s2"), "patch")
        self.assertFalse(pinver.breaking("k8s", "v1.37.1+k3s1", "v1.37.2+k3s1"))

    def test_k8s_rc_is_prerelease(self):
        self.assertTrue(pinver.is_prerelease("k8s", "v1.38.0-rc1+k3s1"))
        self.assertEqual(pinver.compare("k8s", "v1.38.0-rc1+k3s1", "v1.38.0+k3s1"), -1)


class Llama(unittest.TestCase):
    def test_build_numbers_compare_numerically(self):
        self.assertEqual(pinver.compare("llama", "b9999", "b11749"), -1)
        self.assertEqual(pinver.compare("llama", "server-b11960", "b11960"), 0)
        self.assertEqual(pinver.compare("llama", "b11749", "b11960"), -1)

    def test_build_bump_is_minor_and_not_breaking(self):
        self.assertEqual(pinver.update_type("llama", "b11749", "b11960"), "minor")
        self.assertFalse(pinver.breaking("llama", "b11749", "b11960"))

    def test_releases_are_zerover(self):
        self.assertEqual(pinver.compare("llama", "v0.7.0", "v0.8.0"), -1)
        self.assertTrue(pinver.breaking("llama", "v0.7.0", "v0.8.0"))

    def test_build_and_release_are_incomparable(self):
        self.assertFalse(pinver.comparable("llama", "b11960", "v0.8.0"))
        with self.assertRaises(ValueError):
            pinver.compare("llama", "b11960", "v0.8.0")

    def test_build_range(self):
        self.assertTrue(pinver.in_range("llama", "b11749", "<b11900"))
        self.assertFalse(pinver.in_range("llama", "b11960", "<b11900"))


class Calver(unittest.TestCase):
    def test_order_and_short_forms(self):
        self.assertEqual(pinver.compare("calver", "2026.3", "2026.4.1"), -1)
        self.assertEqual(pinver.compare("calver", "2026.4", "2026.4.0"), 0)
        self.assertEqual(pinver.compare("calver", "2025.4.9", "2026.0"), -1)

    def test_update_types(self):
        self.assertEqual(pinver.update_type("calver", "2026.3", "2026.4.1"), "minor")
        self.assertEqual(pinver.update_type("calver", "2026.4.1", "2026.4.2"), "patch")
        self.assertEqual(pinver.update_type("calver", "2025.4", "2026.0"), "major")
        self.assertFalse(pinver.breaking("calver", "2026.3", "2026.4.1"))


class Pep440(unittest.TestCase):
    def test_pre_post_dev_order(self):
        order = ["1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.1"]
        for a, b in zip(order, order[1:]):
            self.assertEqual(pinver.compare("pep440", a, b), -1, (a, b))

    def test_local_label(self):
        self.assertEqual(pinver.compare("pep440", "2.9.0", "2.9.0+cpu"), -1)
        # A range without a local label ignores the candidate's local label.
        self.assertTrue(pinver.in_range("pep440", "2.9.0+cpu", ">=2.9.0,<2.10"))

    def test_prerelease_flag(self):
        for v in ("1.0a1", "1.0rc1", "1.0.dev3"):
            self.assertTrue(pinver.is_prerelease("pep440", v), v)
        for v in ("1.0", "1.0.post1", "1.0+cpu"):
            self.assertFalse(pinver.is_prerelease("pep440", v), v)

    def test_update_types(self):
        self.assertEqual(pinver.update_type("pep440", "1.83.14", "1.84.0"), "minor")
        self.assertEqual(pinver.update_type("pep440", "1.83.14", "1.83.14.post1"), "patch")
        self.assertEqual(pinver.update_type("pep440", "1.83.14", "2.0"), "major")
        self.assertEqual(pinver.update_type("pep440", "1.83.14", "1.83.14"), "digest")


class DockerSuffix(unittest.TestCase):
    def test_core_order_within_one_flavour(self):
        self.assertEqual(pinver.compare("docker-suffix", "3.14.8-slim", "3.14.10-slim"), -1)
        self.assertEqual(pinver.compare("docker-suffix", "contrib-v1.40.0", "contrib-v1.41.0"), -1)

    def test_different_suffix_or_prefix_is_incomparable(self):
        self.assertFalse(pinver.comparable("docker-suffix", "3.14.8-slim", "3.14.9-alpine"))
        self.assertFalse(pinver.comparable("docker-suffix", "contrib-v1.40.0", "v1.41.0"))
        with self.assertRaises(ValueError):
            pinver.update_type("docker-suffix", "3.14.8-slim", "3.14.9-alpine")

    def test_latest_ignores_a_different_suffix(self):
        got = pinver.latest("docker-suffix", "3.14.8-slim",
                            ["3.14.9-slim", "3.15.0-alpine", "3.15.0-slim-bookworm", "3.14.7-slim"])
        self.assertEqual(got, "3.14.9-slim")

    def test_update_types_follow_the_core(self):
        self.assertEqual(pinver.update_type("docker-suffix", "3.14.8-slim", "3.15.0-slim"), "minor")
        self.assertEqual(pinver.update_type("docker-suffix", "release-1.0.3-hotfix", "release-1.0.4-hotfix"), "patch")

    def test_range_reads_the_core(self):
        self.assertTrue(pinver.in_range("docker-suffix", "v1.83.14-stable.patch.2", "<1.84.0"))
        self.assertFalse(pinver.in_range("docker-suffix", "v1.88.6-stable", "<1.84.0"))


class Apt(unittest.TestCase):
    def test_dpkg_order(self):
        self.assertEqual(pinver.compare("apt", "1.20.1-1", "1.20.1-2"), -1)
        self.assertEqual(pinver.compare("apt", "1.20.1-1", "1.20.10-1"), -1)
        self.assertEqual(pinver.compare("apt", "1.0~rc1-1", "1.0-1"), -1)
        self.assertEqual(pinver.compare("apt", "1:0.9-1", "2.0-1"), 1)
        self.assertEqual(pinver.compare("apt", "22.9.0-1ubuntu1", "22.10.0-1"), -1)

    def test_update_types(self):
        self.assertEqual(pinver.update_type("apt", "1.20.1-1", "1.20.1-2"), "patch")
        self.assertEqual(pinver.update_type("apt", "1.20.1-1", "1.21.0-1"), "minor")
        self.assertEqual(pinver.update_type("apt", "1.20.1-1", "2.0.0-1"), "major")

    def test_tilde_is_prerelease(self):
        self.assertTrue(pinver.is_prerelease("apt", "1.0~rc1-1"))
        self.assertFalse(pinver.is_prerelease("apt", "1.0-1"))


class HfSha(unittest.TestCase):
    def test_no_ordering(self):
        self.assertFalse(pinver.comparable("hf-sha", "b467368d", "d1c082be"))
        with self.assertRaises(ValueError):
            pinver.compare("hf-sha", "b467368d", "d1c082be")

    def test_update_type_is_digest(self):
        self.assertEqual(pinver.update_type("hf-sha", "b467368d", "d1c082be"), "digest")

    def test_ranges_are_exact_only(self):
        self.assertTrue(pinver.in_range("hf-sha", "b467368d", "*"))
        self.assertTrue(pinver.in_range("hf-sha", "b467368d", "==b467368d"))
        with self.assertRaises(ValueError):
            pinver.in_range("hf-sha", "b467368d", "<d1c082be")

    def test_rejects_non_hex(self):
        with self.assertRaises(ValueError):
            pinver.parse("hf-sha", "main")


class Ranges(unittest.TestCase):
    def test_star(self):
        self.assertTrue(pinver.in_range("semver", "9.9.9", "*"))

    def test_and_clause(self):
        spec = ">=0.24.0,<0.25.0"
        self.assertTrue(pinver.in_range("zerover", "0.24.0", spec))
        self.assertTrue(pinver.in_range("zerover", "0.24.9", spec))
        self.assertFalse(pinver.in_range("zerover", "0.23.9", spec))
        self.assertFalse(pinver.in_range("zerover", "0.25.0", spec))

    def test_union(self):
        spec = "<1.95.1 || >=1.96.0,<1.96.2"
        self.assertTrue(pinver.in_range("pep440", "1.83.14", spec))
        self.assertFalse(pinver.in_range("pep440", "1.95.1", spec))
        self.assertTrue(pinver.in_range("pep440", "1.96.1", spec))
        self.assertFalse(pinver.in_range("pep440", "1.96.2", spec))

    def test_eq_and_ne(self):
        self.assertTrue(pinver.in_range("semver", "1.2.3", "==1.2.3"))
        self.assertFalse(pinver.in_range("semver", "1.2.3", "!=1.2.3"))
        self.assertTrue(pinver.in_range("semver", "1.2.4", "<=1.2.4,>1.2.3"))

    def test_bad_specs(self):
        for bad in ("", "~1.2", "<", "<1.2.3,,>1", "1.2.3"):
            with self.assertRaises(ValueError, msg=bad):
                pinver.in_range("semver", "1.2.3", bad)

    def test_check_range_reports_syntax_only(self):
        pinver.check_range("zerover", "<0.25.0 || >=0.26.0,<0.26.1")
        with self.assertRaises(ValueError):
            pinver.check_range("zerover", "<0.25.x")


class Latest(unittest.TestCase):
    def test_prerelease_excluded(self):
        got = pinver.latest("zerover", "v0.24.0", ["v0.25.0", "v0.26.0rc1", "v0.26.0-rc.1", "v0.24.1"])
        self.assertEqual(got, "v0.25.0")

    def test_never_backwards(self):
        self.assertIsNone(pinver.latest("zerover", "v0.32.0", ["v0.25.0", "v0.31.0"]))

    def test_unparseable_candidates_skipped(self):
        self.assertEqual(pinver.latest("calver", "2026.4.1", ["latest", "2026.5.0", "nightly"]), "2026.5.0")


class UnknownScheme(unittest.TestCase):
    def test_unknown_scheme(self):
        with self.assertRaises(ValueError):
            pinver.parse("romver", "1.0")


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "hack" / "pinver.py"), *args],
                              capture_output=True, text=True)

    def test_compare(self):
        p = self.run_cli("compare", "zerover", "v0.24.0", "0.25.0")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "-1"))

    def test_in_range_exit_code(self):
        self.assertEqual(self.run_cli("in-range", "zerover", "v0.24.0", "<0.25.0").returncode, 0)
        self.assertEqual(self.run_cli("in-range", "zerover", "v0.32.0", "<0.25.0").returncode, 1)

    def test_update_type(self):
        p = self.run_cli("update-type", "zerover", "v0.24.0", "v0.25.0")
        self.assertEqual(p.stdout.strip(), "minor breaking")

    def test_bad_input_exit_2(self):
        p = self.run_cli("compare", "semver", "x", "1.0.0")
        self.assertEqual(p.returncode, 2)
        self.assertIn("semver", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)

#!/usr/bin/env python3
"""Tests for the pin sources (hack/pinlib/sources.py) on recorded responses.

Every case serves the HTTP requests a source makes from a fixture
(PINLIB_FIXTURES), so the suite is offline. The pins are the registry's own
entries (pins/registry.toml) wherever one exists, so a registry edit that breaks
how a pin resolves fails here. Rules under test: a source returns the newest
stable version, skips prereleases and yanked releases, never moves a pin
backwards, errors when it cannot find the tag shape it expects (never falling
back to the current value), and marks a pin unconfirmed when its second source
disagrees.

Usage: python3 tests/pinlib_test.py
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hack"))

from pinlib import http, sources  # noqa: E402
from pinlib import versions as V  # noqa: E402

with open(ROOT / "pins" / "registry.toml", "rb") as f:
    REGISTRY = tomllib.load(f)
PINS = {p["id"]: p for p in REGISTRY["pin"]}
GH = "https://api.github.com/"
HUB = "https://registry-1.docker.io/v2/"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
SHA_A = "a" * 40
SHA_B = "b" * 40


def ok(data=None, body=None, headers=None):
    rec = {"status": 200, "headers": headers or {}}
    if body is not None:
        rec["body"] = body
    else:
        rec["json"] = data
    return rec


def status(code, headers=None):
    return {"status": code, "headers": headers or {}, "body": ""}


def manifest(digest):
    return ok(body="", headers={"Docker-Content-Digest": digest})


def rel(tag, prerelease=False, commit="main", published="2026-10-20T00:00:00Z", body="", assets=()):
    return {"tag_name": tag, "prerelease": prerelease, "draft": False, "target_commitish": commit,
            "published_at": published, "html_url": "https://github.com/x/y/releases/tag/" + tag, "body": body,
            "assets": list(assets)}


class Fixtures(unittest.TestCase):
    """Each test writes its recorded responses and resolves against them."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pinlib_test.")
        self.old = os.environ.get("PINLIB_FIXTURES")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PINLIB_FIXTURES", None)
        else:
            os.environ["PINLIB_FIXTURES"] = self.old
        http.reset_fixtures()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, records):
        path = os.path.join(self.dir, "fixtures.json")
        with open(path, "w") as f:
            json.dump(records, f)
        os.environ["PINLIB_FIXTURES"] = path
        http.reset_fixtures()


class K3s(Fixtures):
    """rancher/k3s stopped publishing the per-arch -amd64 tags after v1.34.1; the old
    nightly read those and kept k3s at v1.34.1 for 331 days without a word."""

    RELEASES = [rel("v1.37.2+k3s1", published="2026-10-27T00:00:00Z"), rel("v1.38.0-rc1+k3s1", prerelease=True),
                rel("v1.36.6+k3s1"), rel("v1.37.1+k3s1")]

    def records(self, tags, with_manifest):
        recs = {GH + "repos/k3s-io/k3s/releases?per_page=100&page=1": ok(self.RELEASES),
                HUB + "rancher/k3s/tags/list?n=1000": ok({"tags": tags})}
        if with_manifest:
            recs[HUB + "rancher/k3s/manifests/v1.37.2-k3s1"] = manifest(D2)
        return recs

    def test_newest_release_with_its_image(self):
        self.serve(self.records(["v1.37.1-k3s1", "v1.37.2-k3s1", "v1.34.1-k3s1-amd64"], True))
        r = sources.resolve(PINS["k3s"], "v1.37.1-k3s1")
        self.assertEqual(r.v, "v1.37.2-k3s1")
        self.assertEqual(r.values["digest"], D2)
        self.assertFalse(r.values.get("unconfirmed"))

    def test_per_arch_tags_only_is_an_error_not_the_old_value(self):
        # Docker Hub serves only the old per-arch tags: no image for the release
        self.serve(self.records(["v1.34.1-k3s1-amd64", "v1.34.1-k3s1-arm64"], False))
        with self.assertRaises(sources.SourceError) as cm:
            sources.resolve(PINS["k3s"], "v1.37.1-k3s1")
        self.assertIn("rancher/k3s", str(cm.exception))

    def test_an_oci_source_with_no_tag_of_the_pins_shape_errors(self):
        pin = {"id": "k3s-oci", "scheme": "k8s", "source": {"kind": "oci", "image": "rancher/k3s", "tag": "{v}"}}
        self.serve({HUB + "rancher/k3s/tags/list?n=1000": ok({"tags": ["v1.34.1-k3s1-amd64", "latest"]})})
        with self.assertRaises(sources.SourceError):
            sources.resolve(pin, "v1.37.1-k3s1")

    def test_confirm_disagreement_marks_unconfirmed(self):
        # GitHub says v1.37.2+k3s1 (the image exists), but the tag list lags behind
        recs = self.records(["v1.37.1-k3s1"], True)
        self.serve(recs)
        r = sources.resolve(PINS["k3s"], "v1.37.1-k3s1")
        self.assertTrue(r.values.get("unconfirmed"))
        self.assertIn("unconfirmed", r.note)


class Oci(Fixtures):
    def test_docker_hub_pagination(self):
        pin = {"id": "busybox", "scheme": "docker-suffix", "source": {"kind": "oci", "image": "busybox", "tag": "{v}"}}
        page2 = HUB + "library/busybox/tags/list?last=1.36&n=1000"
        self.serve({
            HUB + "library/busybox/tags/list?n=1000": ok(
                {"tags": ["1.35", "1.36"]},
                headers={"Link": '</v2/library/busybox/tags/list?last=1.36&n=1000>; rel="next"'}),
            page2: ok({"tags": ["1.37", "1.38.0", "1.38", "1.38.1", "1.39-rc1", "latest", "1.38-uclibc"]}),
            HUB + "library/busybox/manifests/1.38": manifest(D1),
        })
        r = sources.resolve(pin, "1.37")
        self.assertEqual(r.v, "1.38")
        self.assertEqual(r.values["digest"], D1)

    def test_ghcr_anonymous_token(self):
        pin = {"id": "owui", "scheme": "semver",
               "source": {"kind": "oci", "image": "ghcr.io/open-webui/open-webui", "tag": "{v}"}}
        tags = "https://ghcr.io/v2/open-webui/open-webui/tags/list?n=1000"
        challenge = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:open-webui/open-webui:pull"'
        self.serve({
            tags: [status(401, {"WWW-Authenticate": challenge}), ok({"tags": ["v0.12.2", "v0.12.5", "v0.13.0-rc.1", "main"]})],
            "https://ghcr.io/token?service=ghcr.io&scope=repository%3Aopen-webui%2Fopen-webui%3Apull": ok({"token": "t"}),
            "https://ghcr.io/v2/open-webui/open-webui/manifests/v0.12.5": manifest(D2),
        })
        r = sources.resolve(pin, "v0.12.2")
        self.assertEqual((r.v, r.values["digest"]), ("v0.12.5", D2))

    def test_never_backwards(self):
        pin = {"id": "x", "scheme": "semver", "source": {"kind": "oci", "image": "x/y", "tag": "{v}"}}
        self.serve({HUB + "x/y/tags/list?n=1000": ok({"tags": ["1.2.0", "1.1.0"]}),
                    HUB + "x/y/manifests/1.2.0": manifest(D1)})
        with self.assertRaises(sources.SourceError) as cm:
            sources.resolve(pin, "1.3.0")
        self.assertIn("older than the pin", str(cm.exception))

    def test_word_tag_moves_only_its_digest(self):
        pin = {"id": "debian", "scheme": "docker-suffix", "source": {"kind": "oci", "image": "debian", "tag": "{v}"}}
        self.serve({HUB + "library/debian/manifests/trixie-slim": manifest(D2)})
        r = sources.resolve(pin, "trixie-slim")
        self.assertEqual((r.v, r.values["digest"]), ("trixie-slim", D2))

    def test_frozen_pin_never_resolves(self):
        pin = {"id": "eat-img", "scheme": "docker-suffix", "frozen": "EAT stock",
               "source": {"kind": "oci", "image": "busybox", "tag": "{v}"}}
        self.serve({})
        self.assertEqual(sources.resolve(pin, "1.36").v, "1.36")


class PyPI(Fixtures):
    def test_yanked_and_prereleases_skipped(self):
        files = [{"yanked": False, "upload_time_iso_8601": "2026-10-01T00:00:00Z"}]
        self.serve({"https://pypi.org/pypi/transformers/json": ok({"releases": {
            "5.21.0": files, "5.22.0": files, "5.23.0rc1": files, "5.23.0": [{"yanked": True}],
            "5.24.0.dev0": files, "5.20.0": []}})})
        r = sources.resolve(PINS["gguf-transformers"], "5.21.0")
        self.assertEqual(r.v, "5.22.0")
        self.assertEqual(r.published_at, "2026-10-01T00:00:00Z")


class HuggingFace(Fixtures):
    def test_main_revision_and_file_diff(self):
        model = "Qwen/Qwen3-4B"
        old, new, newer = SHA_A, SHA_B, "c" * 40

        def files(readme, weights):
            return ok({"sha": "x", "siblings": [
                {"rfilename": "README.md", "blobId": readme}, {"rfilename": ".gitattributes", "blobId": "g"},
                {"rfilename": "model.safetensors", "blobId": weights, "lfs": {"sha256": "f" * 64}}]})
        self.serve({
            "https://huggingface.co/api/models/%s" % model: ok({"sha": new, "lastModified": "2026-10-20T00:00:00Z"}),
            "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (model, old): files("r1", "w1"),
            "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (model, new): files("r2", "w1"),
            "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (model, newer): files("r2", "w2"),
        })
        r = sources.resolve(PINS["hf-qwen3-4b"], old)
        self.assertEqual(r.v, new)
        self.assertEqual(sources.hf_diff(model, old, new), ("readme-only", ["README.md"]))
        self.assertEqual(sources.hf_diff(model, new, newer), ("substantive", ["model.safetensors"]))

    def test_lfs_sha_of_a_named_file(self):
        model = "OpenVINO/Qwen3-4B-int4-ov"
        self.serve({
            "https://huggingface.co/api/models/%s" % model: ok({"sha": SHA_B}),
            "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (model, SHA_B): ok({"siblings": [
                {"rfilename": "openvino_model.bin", "blobId": "x", "lfs": {"sha256": "e" * 64}}]}),
        })
        r = sources.resolve(PINS["hf-qwen3-4b-int4-ov"], SHA_A)
        self.assertEqual((r.v, r.values["sha"]), (SHA_B, "e" * 64))


class NvidiaApt(Fixtures):
    def test_toolkit_version_every_package_carries(self):
        idx = "https://nvidia.github.io/libnvidia-container/stable/deb/amd64/Packages"
        pkgs = []
        for p in ("nvidia-container-toolkit", "nvidia-container-toolkit-base", "libnvidia-container-tools",
                  "libnvidia-container1"):
            for v in ("1.20.1-1", "1.20.2-1", "1.21.0~rc.1-1"):
                pkgs.append("Package: %s\nVersion: %s\n" % (p, v))
        pkgs.append("Package: nvidia-container-toolkit\nVersion: 1.20.3-1\n")  # the libraries lag: not taken
        self.serve({idx: ok(body="\n".join(pkgs))})
        self.assertEqual(sources.resolve(PINS["nvctk"], "1.20.1-1").v, "1.20.2-1")


class Eat(Fixtures):
    VALUES = "vllm:\n  image:\n    repository: vllm/vllm-openai-cpu\n    tag: v0.28.0\n"

    def records(self, latest):
        return {
            GH + "repos/intel/enterprise-agent-toolkit/releases/latest": ok(latest),
            GH + "repos/intel/enterprise-agent-toolkit/commits/release-1.1.0": ok({"sha": SHA_B}),
            "https://raw.githubusercontent.com/intel/enterprise-agent-toolkit/release-1.1.0/core/helm-charts/vllm/"
            "xeon-values.yaml": ok(body=self.VALUES),
            HUB + "vllm/vllm-openai-cpu/manifests/v0.28.0": manifest(D1),
        }

    def test_latest_release_after_the_hotfix_became_a_prerelease(self):
        self.serve(self.records(rel("release-1.1.0", published="2026-10-22T17:16:26Z")))
        r = sources.resolve(PINS["eat"], "release-1.0.3-hotfix")
        self.assertEqual((r.v, r.values["commit"]), ("release-1.1.0", SHA_B))
        r = sources.resolve(PINS["eat-vllm-cpu"], "v0.24.0")
        self.assertEqual((r.v, r.values["digest"]), ("v0.28.0", D1))

    def test_a_prerelease_latest_is_an_error(self):
        self.serve(self.records(rel("release-1.2.0-rc1", prerelease=True)))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eat"], "release-1.1.0")

    def test_the_eat_tag_never_moves_backwards(self):
        # EAT's tags order as one stream (hack/pinver.py scheme eat): a hotfix after
        # its release, a latest release older than the pin is an error
        self.assertEqual(PINS["eat"]["scheme"], "eat")
        self.serve(self.records(rel("release-1.1.0")))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eat"], "release-1.2.0")
        self.assertEqual(sources.resolve(PINS["eat"], "release-1.1.0").v, "release-1.1.0")


class Llama(Fixtures):
    def test_release_maps_to_its_build_tag(self):
        commit = "8642541b255bfb5dc39cd351a178be09013a55d6"
        self.serve({
            GH + "repos/ggml-org/llama.cpp/releases/latest": ok(rel("v0.8.0", commit=commit)),
            GH + "repos/ggml-org/llama.cpp/releases?per_page=100&page=1": ok([
                rel("b11988", prerelease=True, commit="9" * 40), rel("b11960", prerelease=True, commit=commit),
                rel("b11959", prerelease=True, commit="8" * 40)]),
        })
        r = sources.resolve(PINS["llama-cpp"], "v0.7.2")
        self.assertEqual((r.v, r.values["build"], r.values["commit"]), ("v0.8.0", "b11960", commit))

    def test_no_build_of_the_release_commit_errors(self):
        self.serve({
            GH + "repos/ggml-org/llama.cpp/releases/latest": ok(rel("v0.8.0", commit=SHA_A)),
            GH + "repos/ggml-org/llama.cpp/releases?per_page=100&page=1": ok([rel("b11988", True, SHA_B)]),
        })
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["llama-cpp"], "v0.7.2")


class GithubRelease(Fixtures):
    def test_hold_keeps_helm_below_v4(self):
        sums = "https://get.helm.sh/helm-v3.22.2-linux-amd64.tar.gz.sha256sum"
        self.serve({
            GH + "repos/helm/helm/releases?per_page=100&page=1": ok([
                rel("v4.3.1"), rel("v3.22.2"), rel("v3.22.1"), rel("v3.23.0-rc.1", prerelease=True)]),
            sums: ok(body="%s  helm-v3.22.2-linux-amd64.tar.gz\n" % ("d" * 64)),
        })
        r = sources.resolve(PINS["helm"], "v3.22.1")
        self.assertEqual((r.v, r.values["sha"]), ("v3.22.2", "d" * 64))

    def test_latest_that_is_older_than_the_pin_errors(self):
        self.serve({GH + "repos/prometheus/prometheus/releases/latest": ok(rel("v3.14.0"))})
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["prometheus"], "v3.15.1")

    def test_release_asset_digests(self):
        assets = [{"name": "libze1_1.35.0+u24.04_amd64.deb", "digest": "sha256:" + "4" * 64}]
        self.serve({GH + "repos/oneapi-src/level-zero/releases/latest": ok(rel("v1.35.0", assets=assets))})
        r = sources.resolve(PINS["level-zero"], "1.34.0")
        self.assertEqual((r.v, r.values["sha"]), ("1.35.0", "4" * 64))


class Versions(unittest.TestCase):
    def test_update_types(self):
        cases = [
            ("docker-suffix", "1.37", "1.38", "line"),
            ("docker-suffix", "26.04", "28.04", "line"),
            ("docker-suffix", "3.13.18-slim", "3.13.19-slim", "patch"),
            ("docker-suffix", "contrib-v1.40.0", "contrib-v1.40.2", "patch"),
            ("docker-suffix", "release-1.0.3-hotfix", "release-1.1.0", "line"),
            ("eat", "release-1.0.3-hotfix", "release-1.1.0", "minor"),
            ("eat", "release-1.0.3", "release-1.0.3-hotfix", "patch"),
            ("docker-suffix", "trixie-slim", "trixie-slim", "none"),
            ("semver", "v3.15.1", "v3.16.0", "minor"),
            ("zerover", "v0.32.0", "v0.33.0", "minor"),
            ("k8s", "v1.37.1-k3s1", "v1.37.2-k3s1", "patch"),
            ("k8s", "v1.37.1-k3s1", "v1.38.0-k3s1", "minor"),
            ("hf-sha", SHA_A, SHA_B, "revision"),
            ("calver", "2026.4.1", "2026.5.0", "minor"),
            ("apt", "1.20.1-1", "1.20.2-1", "patch"),
        ]
        for scheme, old, new, want in cases:
            self.assertEqual(V.update_type(scheme, old, new), want, (scheme, old, new))

    def test_zero_minor(self):
        self.assertTrue(V.zero_minor("zerover", "v0.32.0", "v0.33.0"))
        self.assertFalse(V.zero_minor("zerover", "v0.32.0", "v0.32.1"))
        self.assertFalse(V.zero_minor("semver", "v1.2.0", "v1.3.0"))

    def test_stability(self):
        for v in ("1.39-rc1", "v1.2.0-nightly", "2.0.0-dev-ubuntu24", "latest"):
            self.assertFalse(V.is_stable("docker-suffix", v), v)
        for v in ("1.38", "trixie-slim", "17.5.0-debian-12-r20"):
            self.assertTrue(V.is_stable("docker-suffix", v), v)


if __name__ == "__main__":
    unittest.main(verbosity=1)

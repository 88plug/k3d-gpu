#!/usr/bin/env python3
"""Tests for the pin sources (hack/pinlib/sources.py) on recorded responses.

Every case serves the HTTP requests a source makes from a fixture
(PINLIB_FIXTURES), so the suite is offline. The pins are the registry's own
entries (pins/registry.toml) wherever one exists, so a registry edit that breaks
how a pin resolves fails here. Rules under test: a source returns the newest
stable version, skips prereleases and yanked releases, never moves a pin
backwards, errors when it cannot find the tag shape it expects (never falling
back to the current value), marks a pin unconfirmed when its second source
disagrees, and keeps a held pin below its hold (the registry's holds included).

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

from pinlib import http, registry, sources  # noqa: E402
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

    def test_released_caps_at_the_tag_that_shares_latests_digest(self):
        # Fedora numbers its branched (45) and rawhide (46) images before they ship;
        # `latest` is the released one (44): source.released = "latest" stops there
        d3 = "sha256:" + "3" * 64
        pin = {"id": "fedora", "scheme": "docker-suffix",
               "source": {"kind": "oci", "image": "fedora", "tag": "{v}", "released": "latest"}}
        recs = {
            HUB + "library/fedora/tags/list?n=1000": ok({"tags": ["42", "43", "44", "45", "46", "latest", "rawhide"]}),
            HUB + "library/fedora/manifests/latest": manifest(D1),
            HUB + "library/fedora/manifests/46": manifest(d3),
            HUB + "library/fedora/manifests/45": manifest(D2),
            HUB + "library/fedora/manifests/44": manifest(D1),
        }
        self.serve(recs)
        r = sources.resolve(pin, "43")
        self.assertEqual((r.v, r.values["digest"]), ("44", D1))
        http.reset_fixtures()
        recs[HUB + "library/fedora/manifests/45"] = manifest(D1)  # 45 released: latest moves to it
        recs[HUB + "library/fedora/manifests/latest"] = manifest(D1)
        recs[HUB + "library/fedora/manifests/44"] = manifest(D2)
        self.serve(recs)
        self.assertEqual(sources.resolve(pin, "44").v, "45")
        http.reset_fixtures()
        recs[HUB + "library/fedora/manifests/latest"] = manifest("sha256:" + "9" * 64)  # no numbered tag matches
        self.serve(recs)
        with self.assertRaises(sources.SourceError):
            sources.resolve(pin, "44")

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
        pin = {k: v for k, v in PINS["gguf-transformers"].items() if k != "hold"}  # the Holds cases keep its hold
        r = sources.resolve(pin, "5.21.0")
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
    """EAT 1.x (the eat pin, integrations/eat/eat-up.sh) reads every release below
    release-2.0.0, which removed the standalone installer and its core/helm-charts."""

    VALUES = "vllm:\n  image:\n    repository: vllm/vllm-openai-cpu\n    tag: v0.28.0\n"
    RELEASES = GH + "repos/intel/enterprise-agent-toolkit/releases?per_page=100&page=1"

    def records(self, releases):
        return {
            self.RELEASES: ok(releases),
            GH + "repos/intel/enterprise-agent-toolkit/commits/release-1.1.0": ok({"sha": SHA_B}),
            "https://raw.githubusercontent.com/intel/enterprise-agent-toolkit/release-1.1.0/core/helm-charts/vllm/"
            "xeon-values.yaml": ok(body=self.VALUES),
            HUB + "vllm/vllm-openai-cpu/manifests/v0.28.0": manifest(D1),
        }

    def test_the_newest_1x_release_after_the_hotfix_became_a_prerelease(self):
        self.serve(self.records([rel("release-2.0.0"), rel("release-1.1.0", published="2026-10-22T17:16:26Z"),
                                 rel("release-1.0.3-hotfix", prerelease=True)]))
        r = sources.resolve(PINS["eat"], "release-1.0.3-hotfix")
        self.assertEqual((r.v, r.values["commit"]), ("release-1.1.0", SHA_B))
        self.assertFalse(r.values.get("held"))
        r = sources.resolve(PINS["eat-vllm-cpu"], "v0.24.0")
        self.assertEqual((r.v, r.values["digest"], r.values["eat"]), ("v0.28.0", D1, "release-1.1.0"))

    def test_the_chart_pins_read_below_release_2_0_0(self):
        # release-2.0.0 has no core/helm-charts (a 404 there since it shipped): eat-vllm-cpu
        # and eat-litellm read the newest release below it
        for pid in ("eat-vllm-cpu", "eat-litellm"):
            self.assertEqual(PINS[pid]["source"].get("below"), "release-2.0.0", pid)
        self.serve(self.records([rel("release-2.0.0"), rel("release-1.1.0")]))
        self.assertEqual(sources.resolve(PINS["eat-vllm-cpu"], "v0.28.0").values["eat"], "release-1.1.0")

    def test_only_prereleases_is_an_error(self):
        self.serve(self.records([rel("release-1.2.0-rc1", prerelease=True)]))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eat"], "release-1.1.0")
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eat-vllm-cpu"], "v0.28.0")

    def test_the_eat_tag_never_moves_backwards(self):
        # EAT's tags order as one stream (hack/pinver.py scheme eat): a hotfix after
        # its release, a newest release older than the pin is an error
        self.assertEqual(PINS["eat"]["scheme"], "eat")
        self.serve(self.records([rel("release-1.1.0")]))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eat"], "release-1.2.0")
        self.assertEqual(sources.resolve(PINS["eat"], "release-1.1.0").v, "release-1.1.0")


class EasRelease(Fixtures):
    """EAS's latest release and the enterprise-inference and EAT commits its agent-toolkit
    manifest pins (hack/eas-release-pins, the eas-release pin), each a tagged release the
    release notes name."""

    EAS = "a051f00fcb2dd473fe763610471cd509db82c433"
    EI = "d035b1ef1981fbf5084ff05c9a337e620478bb87"
    EAT = "7b827e2d95cec826a20c7e3f5a30eb4818ed2992"
    NOTES = ("| [enterprise-agent-toolkit](https://github.com/intel/enterprise-agent-toolkit) | "
             "[v2.0.0](https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-2.0.0) |\n"
             "| [ai-inference](https://github.com/intel/enterprise-inference) | "
             "[v1.1.0](https://github.com/intel/enterprise-inference/releases/tag/v1.1.0) |\n")
    MANIFEST = (ROOT / "tests/fixtures/eas/trees/a051f00/configs/repos/repos.agent-toolkit.yaml").read_text()

    def records(self, notes=NOTES, ei_tag_commit=EI, manifest=MANIFEST):
        eas = rel("v1.2.0", published="2026-11-06T13:53:39Z", body=notes)
        return {
            GH + "repos/intel/enterprise-ai-solutions/releases/latest": ok(eas),
            GH + "repos/intel/enterprise-ai-solutions/commits/v1.2.0": ok({"sha": self.EAS}),
            "https://raw.githubusercontent.com/intel/enterprise-ai-solutions/%s/configs/repos/repos.agent-toolkit.yaml"
            % self.EAS: ok(body=manifest),
            GH + "repos/intel/enterprise-inference/commits/v1.1.0": ok({"sha": ei_tag_commit}),
            GH + "repos/intel/enterprise-agent-toolkit/commits/release-2.0.0": ok({"sha": self.EAT}),
            GH + "repos/intel/enterprise-inference/releases/tags/v1.1.0": ok(rel("v1.1.0", published="2026-11-06T06:22:30Z")),
            GH + "repos/intel/enterprise-agent-toolkit/releases/tags/release-2.0.0":
                ok(rel("release-2.0.0", published="2026-11-06T06:05:17Z")),
        }

    def test_the_release_and_the_commits_its_manifest_pins(self):
        pin = PINS["eas-release"]
        self.assertEqual((pin["class"], pin["group"], pin["scheme"], pin["source"]["kind"]),
                         ("eat-stock", "eat-stock", "semver", "cmd"))
        self.assertNotIn("hold", pin)
        self.serve(self.records())
        r = sources.resolve(pin, "v1.2.0")
        self.assertEqual(r.values, {
            "v": "v1.2.0", "commit": self.EAS, "date": "2026-11-06",
            "ei": "v1.1.0", "ei_commit": self.EI, "ei_date": "2026-11-06", "ei_line": "16",
            "eat": "release-2.0.0", "eat_commit": self.EAT, "eat_date": "2026-11-06", "eat_line": "25"})

    def test_the_pins_json_rows_are_its_sites(self):
        tree = registry.Tree(str(ROOT))
        vals = registry.current(PINS["eas-release"], registry.locate(tree, PINS["eas-release"]))
        rows = {r["name"]: r for r in json.loads((ROOT / "integrations/eas/pins.json").read_text())["pins"]
                if r["profile"] == "release"}
        self.assertEqual((vals["v"], vals["commit"], vals["ei"], vals["ei_commit"], vals["eat"], vals["eat_commit"]),
                         (rows["eas"]["ref"], rows["eas"]["sha"], rows["ei"]["ref"], rows["ei"]["sha"],
                          rows["eat"]["ref"], rows["eat"]["sha"]))

    def test_a_manifest_rev_no_named_tag_points_at_is_refused(self):
        self.serve(self.records(ei_tag_commit=SHA_A))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eas-release"], "v1.2.0")

    def test_release_notes_that_do_not_name_the_component_are_refused(self):
        self.serve(self.records(notes="| ai-inference | v1.1.0 |\n"))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eas-release"], "v1.2.0")

    def test_a_manifest_rev_that_is_a_branch_is_refused(self):
        self.serve(self.records(manifest=self.MANIFEST.replace(self.EI, "release-1.1")))
        with self.assertRaises(sources.SourceError):
            sources.resolve(PINS["eas-release"], "v1.2.0")


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


class Holds(Fixtures):
    """A hold keeps a pin below its max whatever the source kind. github-release, oci
    and pypi take the newest release below it; a source that reads only upstream's
    latest release (eat, llama) keeps the pin where it is, with a held note."""

    FILES = [{"yanked": False, "upload_time_iso_8601": "2026-11-01T00:00:00Z"}]

    def pypi(self, pkg, versions):
        return {"https://pypi.org/pypi/%s/json" % pkg: ok({"releases": {v: self.FILES for v in versions}})}

    def test_pypi_takes_the_newest_release_below_the_hold(self):
        self.assertEqual(PINS["docs-mkdocs"]["hold"]["max"], "2.0.0")
        self.serve(self.pypi("mkdocs", ["1.6.1", "1.6.2", "2.0.0", "2.1.0"]))
        self.assertEqual(sources.resolve(PINS["docs-mkdocs"], "1.6.1").v, "1.6.2")

    def test_gguf_convert_pins_stay_where_h59w_registered_them(self):
        # H59 amendment R1: the drafter conversion Job runs with these four pins
        registered = {"gguf-transformers": ("transformers", "5.21.0", ["5.21.0.post1", "5.21.1", "5.22.2"]),
                      "gguf-huggingface-hub": ("huggingface-hub", "2.3.0", ["2.3.0.post1", "2.3.1", "2.4.0"]),
                      "gguf-tokenizers": ("tokenizers", "0.23.3", ["0.23.4", "0.24.0"]),
                      "gguf-safetensors": ("safetensors", "0.8.0", ["0.8.1", "0.9.0"])}
        recs = {}
        for pkg, cur, newer in registered.values():
            recs.update(self.pypi(pkg, [cur] + newer))
        self.serve(recs)
        for pid, (_, cur, _) in registered.items():
            self.assertIn("H59w is PENDING", PINS[pid]["hold"]["reason"], pid)
            self.assertEqual(sources.resolve(PINS[pid], cur).v, cur, pid)

    def test_llama_cpp_stays_at_the_build_h59w_registered(self):
        # server-b11960 and full-b11960 are release v0.8.0; upstream's latest is v0.9.0
        self.serve({
            GH + "repos/ggml-org/llama.cpp/releases/latest": ok(rel("v0.9.0", commit=SHA_B)),
            GH + "repos/ggml-org/llama.cpp/releases?per_page=100&page=1": ok([
                rel("b12100", prerelease=True, commit=SHA_B),
                rel("b11960", prerelease=True, commit="8642541b255bfb5dc39cd351a178be09013a55d6")]),
        })
        self.assertIn("H59w is PENDING", PINS["llama-cpp"]["hold"]["reason"])
        r = sources.resolve(PINS["llama-cpp"], "v0.8.0")
        self.assertEqual(r.v, "v0.8.0")
        self.assertTrue(r.values.get("held"))
        self.assertIn("upstream v0.9.0 reaches the hold v0.8.1", r.note)

    EAT_RELEASES = GH + "repos/intel/enterprise-agent-toolkit/releases?per_page=100&page=1"

    def test_eat_stays_below_release_2_0_0(self):
        # EAT 1.x's standalone installer is gone at release-2.0.0: the eat pin lists every
        # release, takes the newest below the hold and is not "held" while 2.x moves on
        self.assertIn("integrations/eas", PINS["eat"]["hold"]["reason"])
        self.assertEqual(PINS["eat"]["hold"]["max"], "release-2.0.0")
        self.serve({self.EAT_RELEASES: ok([rel("release-2.1.0"), rel("release-2.0.0"), rel("release-1.1.0")]),
                    GH + "repos/intel/enterprise-agent-toolkit/commits/release-1.1.0": ok({"sha": SHA_B})})
        r = sources.resolve(PINS["eat"], "release-1.1.0")
        self.assertEqual(r.v, "release-1.1.0")
        self.assertFalse(r.values.get("held"))
        self.assertIsNone(r.note)

    def test_eat_below_the_hold_still_moves(self):
        self.serve({self.EAT_RELEASES: ok([rel("release-2.0.0"), rel("release-1.1.0-hotfix"), rel("release-1.1.0")]),
                    GH + "repos/intel/enterprise-agent-toolkit/commits/release-1.1.0-hotfix": ok({"sha": SHA_A})})
        r = sources.resolve(PINS["eat"], "release-1.1.0")
        self.assertEqual((r.v, r.values["commit"]), ("release-1.1.0-hotfix", SHA_A))
        self.assertFalse(r.values.get("held"))


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

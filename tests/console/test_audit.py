#!/usr/bin/env python3
"""Unit and property tests for console/audit.py.

The property tests draw random observations (listeners, containerd images,
pulls, secret files, apps) against the repo's images.lock and check audit()
against an oracle written from the S5 rules in docs/console-design.md, not
from audit.py:
  - verdict fail iff some listener off loopback has auth none/unknown, some
    image has no digest images.lock pins for its name, anything was pulled,
    a secret file is group/other readable, an Open WebUI runs without sign-in,
    or no image was observed at all;
  - one listener check per listener, and it fails exactly by that rule;
  - it is pure: same output twice, inputs unchanged, input order irrelevant;
  - its output validates against the audit/v1 schema.

Usage: tests/console/test_audit.py   (FUZZ_SEED=<int> to replay, FUZZ_N=<int> cases)
Stdlib only.
"""

import copy
import json
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))

import audit as auditmod  # noqa: E402
import facts as factsmod  # noqa: E402
import schema_check  # noqa: E402

SEED = int(os.environ.get("FUZZ_SEED", random.SystemRandom().randrange(2**32)))
N = int(os.environ.get("FUZZ_N", "300"))
SCHEMA = factsmod.load_schema()
with open(os.path.join(REPO, "images.lock"), encoding="utf-8") as f:
    LOCK = json.load(f)

LOOPBACK = ["127.0.0.1", "127.9.9.9", "::1", "localhost", "[::1]"]
OFF_LOOPBACK = ["0.0.0.0", "::", "10.1.2.3", "192.168.1.5", "fd00::5", "box.example", "[::]"]
AUTHS = ["none", "unknown", "key", "client-cert", "oidc"]
OTHER_DIGEST = "sha256:" + "e" * 64


def observed(**kw):
    doc = {"schema": "k3d-gpu.observed/v1", "cluster": "gpu", "listeners": [], "images": [],
           "pulls": [], "secrets": [], "apps": []}
    doc.update(kw)
    return doc


def locked_image(e, node="k3d-gpu-server-0"):
    tags = [e["name"] + ":" + e["tag"]] if e["tag"] else []
    return {"node": node, "tags": tags, "digests": [e["name"] + "@" + e["digest"]]}


def default_images():
    return [locked_image(e) for e in LOCK["images"] if e["name"].endswith("mirrored-pause")]


# ---- oracle, from the rules, not from audit.py ----

def oracle_listener_fails(ls):
    return ls["address"] in OFF_LOOPBACK and ls["auth"] in ("none", "unknown")


def oracle_image_ok(img):
    pins = {(e["name"], e["digest"]) for e in LOCK["images"]}
    for d in img["digests"]:
        name, digest = d.split("@")
        full = auditmod.parse_ref(name)[0]
        if (full, digest) in pins:
            return True
    return False


def oracle_verdict(obs):
    bad = any(oracle_listener_fails(ls) for ls in obs["listeners"])
    bad |= not obs["images"] or not all(oracle_image_ok(i) for i in obs["images"])
    bad |= bool(obs["pulls"])
    bad |= any(int(s["mode"], 8) & 0o077 for s in obs["secrets"])
    bad |= any(a["auth"] is not True for a in obs["apps"])
    return "fail" if bad else "pass"


def random_observed(rng):
    entries = LOCK["images"]
    listeners = [{"name": "svc%d" % i, "address": rng.choice(LOOPBACK + OFF_LOOPBACK),
                  "port": rng.choice([None, 80, 6443, 8000, 65535]), "auth": rng.choice(AUTHS),
                  "source": rng.choice(["docker port 6443/tcp on n0", "fixture"])}
                 for i in range(rng.randrange(4))]
    images = []
    for _ in range(rng.randrange(6)):
        e = rng.choice(entries)
        kind = rng.choice(["ok", "ok", "ok", "short", "moved", "unknown", "nodigest"])
        node = rng.choice(["k3d-gpu-server-0", "k3d-gpu-agent-0"])
        if kind == "ok":
            images.append(locked_image(e, node))
        elif kind == "short":  # docker.io names as containerd may print them, short form
            short = e["name"].replace("docker.io/library/", "").replace("docker.io/", "")
            images.append({"node": node, "tags": [], "digests": [short + "@" + e["digest"]]})
        elif kind == "moved":
            images.append({"node": node, "tags": [e["name"] + ":" + (e["tag"] or "x")],
                           "digests": [e["name"] + "@" + OTHER_DIGEST]})
        elif kind == "unknown":
            images.append({"node": node, "tags": ["docker.io/bitnami/redis:7"],
                           "digests": ["docker.io/bitnami/redis@" + e["digest"]]})
        else:
            images.append({"node": node, "tags": ["docker.io/library/app:dev"], "digests": []})
    pulls = [{"node": "k3d-gpu-server-0", "ref": "docker.io/library/app:dev"}] * rng.choice([0, 0, 0, 1])
    secrets = [{"path": "/home/u/.kube/config%d" % i, "mode": rng.choice(["600", "0600", "400", "640", "644", "604"])}
               for i in range(rng.randrange(3))]
    apps = [{"kind": "open-webui", "name": "ns/deployment/w%d" % i, "auth": rng.choice([True, True, False, None])}
            for i in range(rng.randrange(3))]
    return observed(listeners=listeners, images=images, pulls=pulls, secrets=secrets, apps=apps)


class Properties(unittest.TestCase):
    def test_random_observations(self):
        rng = random.Random(SEED)
        for case in range(N):
            obs = random_observed(rng)
            before = copy.deepcopy(obs)
            doc = auditmod.audit(obs, LOCK)
            ctx = "seed %d case %d: %s" % (SEED, case, json.dumps(obs))
            self.assertEqual(obs, before, ctx)
            schema_check.check(doc, SCHEMA, "#/$defs/audit")
            self.assertEqual(doc["verdict"], oracle_verdict(obs), ctx)
            lst = [c for c in doc["checks"] if c["rule"] == "listener"]
            self.assertEqual(len(lst), len(obs["listeners"]), ctx)
            self.assertEqual(sum(c["status"] == "fail" for c in lst),
                             sum(oracle_listener_fails(ls) for ls in obs["listeners"]), ctx)
            self.assertEqual(sum(c["rule"] == "fetch" for c in doc["checks"]), len(obs["pulls"]), ctx)
            for c in doc["checks"]:
                self.assertEqual(c["fix"] is None, c["status"] == "pass", ctx)
            self.assertEqual(factsmod.dumps(doc), factsmod.dumps(auditmod.audit(obs, LOCK)), ctx)
            shuffled = copy.deepcopy(obs)
            for key in ("listeners", "images", "secrets", "apps"):
                rng.shuffle(shuffled[key])
            self.assertEqual(factsmod.dumps(doc), factsmod.dumps(auditmod.audit(shuffled, LOCK)), ctx)


class Rules(unittest.TestCase):
    def run_one(self, **kw):
        kw.setdefault("images", default_images())
        return auditmod.audit(observed(**kw), LOCK)

    def failed(self, doc):
        return [c for c in doc["checks"] if c["status"] == "fail"]

    def test_default_loopback_layout_passes(self):
        doc = self.run_one(listeners=[
            {"name": "kube-apiserver", "address": "0.0.0.0", "port": 39651, "auth": "client-cert", "source": "docker"},
            {"name": "agent", "address": "127.0.0.1", "port": 8000, "auth": "none", "source": "port-forward"}],
            secrets=[{"path": "/k", "mode": "600"}], apps=[{"kind": "open-webui", "name": "w", "auth": True}])
        self.assertEqual(doc["verdict"], "pass", doc["summary"])

    def test_eat9_unauthenticated_listener_fails(self):
        for addr in ("0.0.0.0", "::", "10.0.0.7"):
            doc = self.run_one(listeners=[{"name": "vllm", "address": addr, "port": 8000, "auth": "none", "source": "f"}])
            self.assertEqual(doc["verdict"], "fail")
            self.assertIn("unauthenticated listener off loopback", self.failed(doc)[0]["detail"])

    def test_unknown_auth_off_loopback_fails(self):
        doc = self.run_one(listeners=[{"name": "p", "address": "0.0.0.0", "port": 80, "auth": "unknown", "source": "f"}])
        self.assertEqual(doc["verdict"], "fail")

    def test_ipv6_subject_is_bracketed(self):
        doc = self.run_one(listeners=[{"name": "v", "address": "::", "port": 8000, "auth": "none", "source": "f"}])
        self.assertTrue(self.failed(doc)[0]["subject"].startswith("[::]:8000 "))

    def test_image_missing_from_lock_fails(self):
        doc = self.run_one(images=default_images() + [
            {"node": "n", "tags": ["docker.io/bitnami/redis:7"], "digests": ["docker.io/bitnami/redis@" + OTHER_DIGEST]}])
        self.assertEqual(doc["verdict"], "fail")
        self.assertIn("not in images.lock: docker.io/bitnami/redis", self.failed(doc)[0]["detail"])

    def test_moved_digest_fails(self):
        e = next(x for x in LOCK["images"] if x["name"] == "docker.io/intel/intel-gpu-plugin")
        doc = self.run_one(images=[{"node": "n", "tags": ["docker.io/intel/intel-gpu-plugin:0.37.1"],
                                    "digests": ["docker.io/intel/intel-gpu-plugin@" + OTHER_DIGEST]}])
        self.assertEqual(doc["verdict"], "fail")
        self.assertIn("images.lock pins " + e["digest"], self.failed(doc)[0]["detail"])

    def test_no_digest_fails(self):
        doc = self.run_one(images=[{"node": "n", "tags": ["docker.io/library/app:dev"], "digests": []}])
        self.assertIn("no registry digest", self.failed(doc)[0]["detail"])

    def test_same_image_on_two_nodes_is_one_check(self):
        img = default_images()[0]
        doc = self.run_one(images=[img, dict(img, node="k3d-gpu-agent-0")])
        imgs = [c for c in doc["checks"] if c["rule"] == "image"]
        self.assertEqual(len(imgs), 1)
        self.assertIn("on k3d-gpu-agent-0, k3d-gpu-server-0", imgs[0]["detail"])

    def test_no_images_fails(self):
        self.assertEqual(self.run_one(images=[])["verdict"], "fail")

    def test_runtime_fetch_fails(self):
        doc = self.run_one(pulls=[{"node": "n", "ref": "docker.io/library/app:dev"}])
        self.assertEqual([c["rule"] for c in self.failed(doc)], ["fetch"])

    def test_secret_modes(self):
        for mode, want in (("600", "pass"), ("400", "pass"), ("0600", "pass"), ("640", "fail"), ("604", "fail"), ("1600", "pass")):
            doc = self.run_one(secrets=[{"path": "/k", "mode": mode}])
            self.assertEqual(doc["verdict"], want, mode)

    def test_open_webui_without_sign_in_fails(self):
        for auth in (False, None):
            doc = self.run_one(apps=[{"kind": "open-webui", "name": "w", "auth": auth}])
            self.assertEqual(doc["verdict"], "fail")

    def test_plan_endpoint_is_a_listener(self):
        plan = {"bind": "0.0.0.0", "port": 8000, "auth": "oidc"}
        self.assertEqual(auditmod.audit(observed(images=default_images()), LOCK, plan)["verdict"], "pass")
        plan = {"bind": "0.0.0.0", "port": 8000, "auth": "something-new"}
        self.assertEqual(auditmod.audit(observed(images=default_images()), LOCK, plan)["verdict"], "fail")
        plan = {"bind": "0.0.0.0", "port": 8000, "auth": "loopback-key"}
        self.assertEqual(auditmod.audit(observed(images=default_images()), LOCK, plan)["verdict"], "fail")
        plan = {"bind": "127.0.0.1", "port": None, "auth": "loopback-key"}
        doc = auditmod.audit(observed(images=default_images()), LOCK, plan)
        self.assertEqual(doc["verdict"], "pass")
        self.assertIn("127.0.0.1:(port not set) planned endpoint", [c["subject"] for c in doc["checks"]])


class Refs(unittest.TestCase):
    def test_parse_ref(self):
        cases = {
            "busybox:1.37": ("docker.io/library/busybox", "1.37", None),
            "intel/intel-gpu-plugin:0.37.1": ("docker.io/intel/intel-gpu-plugin", "0.37.1", None),
            "openvino/model_server@sha256:" + "a" * 64: ("docker.io/openvino/model_server", None, "sha256:" + "a" * 64),
            "registry.k8s.io/nfd/node-feature-discovery:v0.19.0@sha256:" + "b" * 64:
                ("registry.k8s.io/nfd/node-feature-discovery", "v0.19.0", "sha256:" + "b" * 64),
            "localhost:5000/app": ("localhost:5000/app", None, None),
            "docker.io/rancher/mirrored-pause:3.10.2": ("docker.io/rancher/mirrored-pause", "3.10.2", None),
        }
        for ref, want in cases.items():
            self.assertEqual(auditmod.parse_ref(ref), want, ref)


class Observe(unittest.TestCase):
    def test_raw_to_observed(self):
        pause = next(e for e in LOCK["images"] if e["name"].endswith("mirrored-pause"))
        raw = {"cluster": "gpu", "secrets": [{"path": "/k", "mode": "600"}], "nodes": [{
            "name": "k3d-gpu-server-0",
            "ports": {"6443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "39651"}], "80/tcp": None,
                      "30080/tcp": [{"HostIp": "", "HostPort": "8080"}, {"HostIp": "::", "HostPort": "8080"}]},
            "images": {"images": [{"repoTags": None, "repoDigests": [pause["name"] + "@" + pause["digest"]]}]}}],
            "workloads": {"items": [
                {"kind": "Deployment", "metadata": {"namespace": "a", "name": "w1"}, "spec": {"template": {"spec": {
                    "containers": [{"image": "ghcr.io/open-webui/open-webui@sha256:" + "c" * 64}]}}}},
                {"kind": "Deployment", "metadata": {"namespace": "a", "name": "w2"}, "spec": {"template": {"spec": {
                    "containers": [{"image": "ghcr.io/open-webui/open-webui:v0.12.2",
                                    "env": [{"name": "WEBUI_AUTH", "value": "False"}]}]}}}},
                {"kind": "StatefulSet", "metadata": {"namespace": "a", "name": "w3"}, "spec": {"template": {"spec": {
                    "containers": [{"image": "ghcr.io/open-webui/open-webui:v0.12.2",
                                    "env": [{"name": "WEBUI_AUTH", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]}]}}}},
                {"kind": "Deployment", "metadata": {"namespace": "b", "name": "other"}, "spec": {"template": {"spec": {
                    "containers": [{"image": "nginx:1"}]}}}}]}}
        obs = auditmod.observe(raw)
        schema_check.check(obs, SCHEMA, "#/$defs/observed")
        got = [(x["name"], x["address"], x["port"], x["auth"]) for x in obs["listeners"]]
        self.assertEqual(sorted(got), sorted([
            ("published node port 30080/tcp", "0.0.0.0", 8080, "unknown"),
            ("published node port 30080/tcp", "::", 8080, "unknown"),
            ("kube-apiserver", "0.0.0.0", 39651, "client-cert")]))
        self.assertEqual([(a["name"], a["auth"]) for a in obs["apps"]],
                         [("a/deployment/w1", True), ("a/deployment/w2", False), ("a/statefulset/w3", None)])
        self.assertEqual(obs["images"][0]["tags"], [])
        self.assertEqual(obs["pulls"], [])

    def test_pulled_events_to_pulls(self):
        def ev(msg, host=None, instance=None):
            e = {"reason": "Pulled", "message": msg}
            if host:
                e["source"] = {"component": "kubelet", "host": host}
            if instance:
                e["reportingInstance"] = instance
            return e
        raw = {"cluster": "gpu", "nodes": [], "workloads": {"items": []}, "events": {"items": [
            ev('Successfully pulled image "busybox:1.37" in 1.5s (1.5s including waiting). Image size: 2 bytes.', "n1"),
            ev('Successfully pulled image "busybox:1.37" in 1.1s', "n1"),
            ev('Successfully pulled image "nvidia/cuda:13" in 5s', instance="n2"),
            ev('Container image "busybox:1.37" already present on machine and can be accessed by the pod', "n1"),
            {"reason": "Pulling", "message": 'Pulling image "x:1"', "source": {"host": "n1"}},
            ev('Successfully pulled image "orphan:1" in 1s')]}}
        obs = auditmod.observe(raw)
        schema_check.check(obs, SCHEMA, "#/$defs/observed")
        self.assertEqual(obs["pulls"], [{"node": "(unknown node)", "ref": "orphan:1"},
                                        {"node": "n1", "ref": "busybox:1.37"},
                                        {"node": "n2", "ref": "nvidia/cuda:13"}])
        self.assertEqual(auditmod.observe(dict(raw, events=None))["pulls"], [])
        doc = auditmod.audit(dict(obs, images=default_images()), LOCK)
        self.assertEqual(doc["verdict"], "fail")
        self.assertEqual(sum(c["rule"] == "fetch" and c["status"] == "fail" for c in doc["checks"]), 3)


if __name__ == "__main__":
    print("FUZZ_SEED=%d FUZZ_N=%d" % (SEED, N))
    unittest.main(verbosity=1)

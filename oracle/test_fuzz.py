#!/usr/bin/env python3
"""Property fuzz for the capacity oracle's privacy scrubber and validator.

  scrubber   project.hardware() over a real `report --json --no-cluster` with
             identifiers planted in random string leaves and in extra keys:
             no planted token may reach the projected hardware block.
  validator  validate() over random mutations of the seed bundles (values
             replaced, keys dropped or added, identifiers planted, numbers
             scaled): it never raises, always returns accept/quarantine/reject,
             rejects any bundle with a planted identifier, and accepts only
             bundles whose content hash matches.

FUZZ_N cases per property (default 100), FUZZ_SEED to replay.
"""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle as B  # noqa: E402
import project as P  # noqa: E402
import validate as V  # noqa: E402

N = int(os.environ.get("FUZZ_N", "100"))
SEED_ENV = os.environ.get("FUZZ_SEED")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "raptorlake-nvidia", "sysroot")
SEEDS = [os.path.join(HERE, "seed", f) for f in sorted(os.listdir(os.path.join(HERE, "seed"))) if f.endswith(".json")]


def token(rng):
    """One identifier of a kind a host could leak."""
    h = lambda n: "".join(rng.choice("0123456789abcdef") for _ in range(n))  # noqa: E731
    kind = rng.randrange(7)
    if kind == 0:
        return ":".join(h(2) for _ in range(6))                       # MAC
    if kind == 1:
        return ".".join(str(rng.randrange(1, 255)) for _ in range(4))  # IPv4
    if kind == 2:
        return f"{h(8)}-{h(4)}-{h(4)}-{h(4)}-{h(12)}"                   # UUID
    if kind == 3:
        return f"user{h(6)}@example.org"                               # e-mail
    if kind == 4:
        return f"/home/u{h(6)}/k3d"                                    # home path
    if kind == 5:
        return h(32)                                                   # machine-id
    return f"2001:db8::{h(4)}:{h(4)}"                                  # IPv6


def string_leaves(x, path=()):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from string_leaves(v, path + (k,))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from string_leaves(v, path + (i,))
    elif isinstance(x, str):
        yield path


def set_at(x, path, value):
    for p in path[:-1]:
        x = x[p]
    x[path[-1]] = value


def all_leaves(x, path=()):
    if isinstance(x, dict):
        if not x:
            yield path
        for k, v in x.items():
            yield from all_leaves(v, path + (k,))
    elif isinstance(x, list):
        if not x:
            yield path
        for i, v in enumerate(x):
            yield from all_leaves(v, path + (i,))
    else:
        yield path


class Fuzz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = int(SEED_ENV) if SEED_ENV else random.SystemRandom().randrange(2 ** 31)
        print(f"oracle fuzz: FUZZ_SEED={cls.seed} FUZZ_N={N}", file=sys.stderr)
        out = subprocess.run(["bash", os.path.join(REPO, "scripts", "k3d-gpu"), "report", "--json", "--no-cluster"],
                             capture_output=True, text=True, check=True, timeout=120,
                             env={**os.environ, "K3D_GPU_SYSROOT": FIXTURE, "K3D_GPU_CLUSTER": "oracle-fuzz"})
        cls.report = json.loads(out.stdout)
        cls.bundles = []
        for p in SEEDS:
            with open(p) as f:
                cls.bundles.append(json.load(f))

    def test_scrubber_drops_every_planted_identifier(self):
        rng = random.Random(self.seed)
        paths = list(string_leaves(self.report))
        self.assertTrue(paths)
        for i in range(N):
            r = copy.deepcopy(self.report)
            planted = []
            for path in rng.sample(paths, k=min(len(paths), rng.randint(1, 6))):
                t = token(rng)
                old = r
                for p in path:
                    old = old[p]
                # Replace the value, or splice the token into it.
                set_at(r, path, t if rng.random() < 0.5 else f"{old} {t}")
                planted.append(t)
            host = r.setdefault("host", {})
            for k in ("hostname", "machine_id", "serial", "product_uuid", "user"):
                if rng.random() < 0.5:
                    t = token(rng)
                    host[k] = t
                    planted.append(t)
            hw = P.hardware(r, FIXTURE)
            text = json.dumps(hw)
            for t in planted:
                self.assertNotIn(t, text, f"case {i}: {t!r} reached the bundle (FUZZ_SEED={self.seed})")

    def test_validator_total_and_hash_bound(self):
        rng = random.Random(self.seed + 1)
        for i in range(N):
            b = copy.deepcopy(rng.choice(self.bundles))
            leaves = list(all_leaves(b))
            op = rng.randrange(5)
            path = rng.choice(leaves)
            if op == 0:
                set_at(b, path, rng.choice([None, -1, 0, 1e12, "x", [], {}, True, "\u0000", "9" * 300]))
            elif op == 1 and len(path) > 1 and isinstance(path[-1], str):
                parent = b
                for p in path[:-1]:
                    parent = parent[p]
                parent.pop(path[-1], None)
            elif op == 2:
                parent = b
                for p in path[:-1]:
                    parent = parent[p]
                if isinstance(parent, dict):
                    parent[f"extra_{rng.randrange(1000)}"] = token(rng)
            elif op == 3:
                v = b
                for p in path:
                    v = v[p]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    set_at(b, path, v * rng.choice([0.5, 2, 10, -1]))
            resealed = rng.random() < 0.5
            if resealed:
                try:
                    b = B.seal(b)
                except Exception:  # noqa: BLE001 - an unsealable mutation stays unsealed
                    resealed = False
            res = V.validate(b, trusted=True, repo=REPO)
            self.assertIn(res.verdict, ("accept", "quarantine", "reject"), f"case {i} (FUZZ_SEED={self.seed})")
            if not resealed and op in (0, 1, 2, 3) and res.verdict == "accept":
                self.assertEqual(b.get("content_sha256"), B.content_hash(b),
                                 f"case {i}: accepted a bundle whose hash does not match (FUZZ_SEED={self.seed})")

    def test_validator_rejects_any_planted_identifier(self):
        rng = random.Random(self.seed + 2)
        for i in range(N):
            b = copy.deepcopy(rng.choice(self.bundles))
            paths = [p for p in string_leaves(b)
                     if not str(p[-1]).endswith(("content_sha256", "comparison_key", "weights_sha256", "revision"))]
            path = rng.choice(paths)
            t = token(rng)
            old = b
            for p in path:
                old = old[p]
            set_at(b, path, t if rng.random() < 0.5 else f"{old} {t}")
            b = B.seal(b)
            res = V.validate(b, trusted=True, repo=REPO)
            self.assertEqual(res.verdict, "reject", f"case {i}: {t!r} at {path} -> {res.verdict} "
                             f"(FUZZ_SEED={self.seed})")


if __name__ == "__main__":
    unittest.main(verbosity=1)

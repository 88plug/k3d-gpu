#!/usr/bin/env python3
"""Cross-check console/schema_check.py against the `jsonschema` package.

Every document given on the command line is validated by both, against the
facts/v1 schema (or its matrix / plan $defs, picked by the document's "schema"
member), and then mutated at random (a key dropped, a value retyped, a key
added, a string replaced) to see that both still give the same verdict. Exits
1 on any disagreement, 0 when they agree, 77 when jsonschema is not installed.

Usage: crosscheck.py doc.json...   (FUZZ_SEED=<int> to replay, FUZZ_N=<int> mutations per doc)
"""

import copy
import importlib.metadata
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "console"))

import facts as factsmod  # noqa: E402
import schema_check  # noqa: E402

try:
    import jsonschema
except ImportError:
    print("crosscheck: jsonschema not installed, skipped")
    sys.exit(77)

SEED = int(os.environ.get("FUZZ_SEED", random.SystemRandom().randrange(2**32)))
N = int(os.environ.get("FUZZ_N", "40"))
ROOT = factsmod.load_schema()
REFS = {"k3d-gpu.facts/v1": None, "k3d-gpu.matrix/v1": "#/$defs/matrix", "k3d-gpu.plan/v1": "#/$defs/plan"}


def theirs(doc, ref):
    schema = ROOT if ref is None else {"$schema": ROOT["$schema"], "$defs": ROOT["$defs"], "$ref": ref}
    return not list(jsonschema.Draft202012Validator(schema).iter_errors(doc))


def ours(doc, ref):
    return not schema_check.errors(doc, schema_check.resolve(ref, ROOT) if ref else ROOT, ROOT)


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k in node:
            yield from paths(node[k], prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, prefix + (i,))


def mutate(doc, rng):
    doc = copy.deepcopy(doc)
    path = rng.choice([p for p in paths(doc) if p])
    parent = doc
    for k in path[:-1]:
        parent = parent[k]
    key = path[-1]
    op = rng.choice(["drop", "retype", "extra", "string"])
    if op == "drop" and isinstance(parent, dict):
        del parent[key]
    elif op == "extra" and isinstance(parent, dict):
        parent["zz_extra"] = 1
    elif op == "string":
        parent[key] = rng.choice(["", "x", "sha256:0", "6:183", "pass", "green"])
    else:
        parent[key] = rng.choice([None, 0, -1, 1.5, True, "s", [], {}])
    return doc


def main(files):
    rng = random.Random(SEED)
    bad = 0
    count = invalid = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        ref = REFS[doc["schema"]]
        for i, d in enumerate([doc] + [mutate(doc, rng) for _ in range(N)]):
            a, b = ours(d, ref), theirs(d, ref)
            count += 1
            invalid += not b
            if i == 0 and not (a and b):
                print("FAIL %s: not valid (schema_check %s, jsonschema %s)" % (f, a, b))
                bad += 1
            elif a != b:
                print("FAIL %s mutation %d: schema_check says %s, jsonschema says %s" % (f, i, a, b))
                bad += 1
    print("crosscheck: %d documents (%d invalid), %d disagreements (jsonschema %s, FUZZ_SEED=%d)" % (
        count, invalid, bad, importlib.metadata.version("jsonschema"), SEED))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

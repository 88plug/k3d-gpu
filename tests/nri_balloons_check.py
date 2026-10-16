#!/usr/bin/env python3
"""Check share/nri-balloons.yml against the CRD it ships.

The API server prunes fields a structural CRD schema does not list, so a
misspelt balloon option (hideHyperThreads for hideHyperthreads) applies cleanly
and then does nothing. This walks every BalloonsPolicy in the manifest against
the openAPIV3Schema of balloonspolicies.config.nri from the same file and
reports unknown fields, enum misses, type errors and missing required keys.

Usage: tests/nri_balloons_check.py [manifest]   (default: share/nri-balloons.yml)
"""
import os
import sys

import yaml


def check(obj, schema, path, errs):
    kind = schema.get("type")
    if "enum" in schema and obj not in schema["enum"]:
        errs.append("%s: %r not in %s" % (path, obj, schema["enum"]))
    if kind == "object":
        if not isinstance(obj, dict):
            errs.append("%s: want an object" % path)
            return
        props = schema.get("properties")
        extra = schema.get("additionalProperties")
        for key, val in obj.items():
            if props is not None and key in props:
                check(val, props[key], "%s.%s" % (path, key), errs)
            elif isinstance(extra, dict):
                check(val, extra, "%s.%s" % (path, key), errs)
            elif props is not None or extra is False:
                errs.append("%s.%s: not in the CRD schema (pruned on apply)" % (path, key))
        for key in schema.get("required", []):
            if key not in obj:
                errs.append("%s: missing required %s" % (path, key))
    elif kind == "array":
        if not isinstance(obj, list):
            errs.append("%s: want an array" % path)
            return
        for i, val in enumerate(obj):
            check(val, schema.get("items", {}), "%s[%d]" % (path, i), errs)
    elif kind == "string" and not isinstance(obj, str):
        errs.append("%s: want a string, got %r" % (path, obj))
    elif kind == "integer" and (isinstance(obj, bool) or not isinstance(obj, int)):
        errs.append("%s: want an integer, got %r" % (path, obj))
    elif kind == "boolean" and not isinstance(obj, bool):
        errs.append("%s: want a boolean, got %r" % (path, obj))


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(repo, "share", "nri-balloons.yml")
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    crd = next(d for d in docs if d["kind"] == "CustomResourceDefinition"
               and d["metadata"]["name"] == "balloonspolicies.config.nri")
    schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    errs = []
    policies = [d for d in docs if d["kind"] == "BalloonsPolicy"]
    if not policies:
        errs.append("no BalloonsPolicy in %s" % path)
    for d in policies:
        check(d, schema, "BalloonsPolicy/" + d["metadata"]["name"], errs)
    for e in errs:
        print(e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

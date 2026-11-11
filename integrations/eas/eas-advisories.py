#!/usr/bin/env python3
"""Is a deployed LiteLLM or vLLM below a published security fix: the warning
`k3d-gpu eas check` prints before Intel's validate.

  kubectl get pods -A -o json | eas-advisories.py advisories.json
  eas-advisories.py advisories.json --in-range <component> <version>

Reads the image of every container and init container of each pod in the List on stdin
(a Succeeded or Failed pod runs nothing and is skipped), matches its repository against
each component's `images` in the table (docker.io/, index.docker.io/ and library/ are
the same repository), and reads the version from the tag: v1.84.0, a tag with a digest
(v0.28.0@sha256:...), a suffixed LiteLLM tag (v1.83.14-stable.patch.2 reads 1.83.14) and
a pre-release (v1.104.0rc1). For each component and version inside a listed range:

  warn: LiteLLM 1.84.0 (Intel's stock ...) is below published security fixes; 1.100.4 is ...
    <image> in <namespace>/<pod>, ...
    <id> (<severity>): fixed in <version>. <summary>

A version inside no range is an ok line, an image with no version in its tag an info
line. Ranges are the GitHub advisory database's vulnerable_version_range strings
(comma-separated clauses of <, <=, >, >=, =), compared as release numbers with a, b and
rc below the release.

Exit 0 on every graded input, warnings included: they never fail eas check. Exit 2 when
the table cannot be read or stdin is no kubectl List. --in-range prints the ids whose
ranges hold <version> and exits 0, or prints nothing and exits 1.
Python standard library only.
"""

import json
import re
import sys

PRE = {"a": 0, "b": 1, "rc": 2}
VERSION = re.compile(r"(\d+(?:\.\d+)*)(?:[-.]?(a|b|rc)\.?(\d+))?")
# A tag's leading version: an optional word prefix (main-v1.84.0), then the version; what
# follows it (-stable.patch.2, -cpu) is not part of it.
TAG = re.compile(r"(?:[A-Za-z]+-)?v?(\d+(?:\.\d+)+)(?:[-.]?(a|b|rc)\.?(\d+))?(?!\d)")
CLAUSE = re.compile(r"(<=|>=|<|>|=)\s*(\S+)")
SHOWN_PODS = 4


def vkey(text):
    m = VERSION.fullmatch(text.strip())
    if not m:
        raise ValueError("not a version: %r" % text)
    nums = [int(x) for x in m.group(1).split(".")]
    return tuple(nums + [0] * (6 - len(nums))), PRE.get(m.group(2), 3), int(m.group(3) or 0)


def in_range(rng, version):
    v = vkey(version)
    for clause in rng.split(","):
        m = CLAUSE.fullmatch(clause.strip())
        if not m:
            raise ValueError("not a range clause: %r" % clause)
        op, bound = m.group(1), vkey(m.group(2))
        if not {"<": v < bound, "<=": v <= bound, ">": v > bound, ">=": v >= bound, "=": v == bound}[op]:
            return False
    return True


def hits(comp, version):
    """(advisory, fixed) for each advisory of comp with a range holding version."""
    out = []
    for a in comp["advisories"]:
        for v in a["vulnerable"]:
            if in_range(v["range"], version):
                out.append((a, v["fixed"]))
                break
    return out


def repository(name):
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name[len("library/"):] if name.startswith("library/") else name


def split_image(ref):
    """(repository, tag) of an image reference; tag "" when it names none."""
    name = ref.split("@", 1)[0]
    colon = name.rfind(":")
    if colon > name.rfind("/"):
        return repository(name[:colon]), name[colon + 1:]
    return repository(name), ""


def tag_version(tag):
    m = TAG.match(tag)
    if not m:
        return None
    return m.group(1) + (m.group(2) + m.group(3) if m.group(2) else "")


def load_table(path):
    try:
        with open(path) as f:
            t = json.load(f)
        comps = t["components"]
        for c in comps:
            c["repos"] = {repository(i) for i in c["images"]}
            for a in c["advisories"]:
                for v in a["vulnerable"]:
                    in_range(v["range"], v["fixed"])  # both parse
        return t
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("eas-advisories.py: cannot read %s: %s" % (path, e), file=sys.stderr)
        sys.exit(2)


def pods_text(pods):
    shown = ", ".join(pods[:SHOWN_PODS])
    return shown + (" and %d more" % (len(pods) - SHOWN_PODS) if len(pods) > SHOWN_PODS else "")


def report(table, items):
    comps = table["components"]
    groups = {}     # (component index, version) -> {image: [pods]}
    ungraded = {}   # image -> [pods]
    for p in items:
        if (p.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        meta = p.get("metadata") or {}
        where = "%s/%s" % (meta.get("namespace"), meta.get("name"))
        spec = p.get("spec") or {}
        for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
            image = c.get("image") or ""
            repo, tag = split_image(image)
            for i, comp in enumerate(comps):
                if repo not in comp["repos"]:
                    continue
                version = tag_version(tag)
                bucket = ungraded if version is None else groups.setdefault((i, version), {})
                pods = bucket.setdefault(image, [])
                if where not in pods:
                    pods.append(where)
    lines, clean = [], []
    warned = False
    for (i, version) in sorted(groups, key=lambda k: (k[0], vkey(k[1]))):
        comp = comps[i]
        found = hits(comp, version)
        if not found:
            clean.append("%s %s" % (comp["label"], version))
            continue
        warned = True
        stock = " (Intel's stock, %s)" % table.get("stock_release", "") if version == comp["stock"] else ""
        lines.append("warn: %s %s%s is below published security fixes; %s is the first release outside every listed range"
                     % (comp["label"], version, stock, comp["clean"]))
        for image, pods in groups[(i, version)].items():
            lines.append("  %s in %s" % (image, pods_text(pods)))
        for a, fixed in found:
            lines.append("  %s (%s): fixed in %s. %s" % (a["id"], a["severity"], fixed, a["summary"]))
    for image, pods in ungraded.items():
        lines.append("info: %s in %s names no version in its tag; not graded" % (image, pods_text(pods)))
    if clean:
        lines.append("ok: %s: no listed advisory (table checked %s)" % (", ".join(clean), table.get("checked")))
    if warned:
        lines.append("note: warnings only, eas check goes on; k3d-gpu keeps Intel's pins (table checked %s, "
                     "docs/intel/eas.md#component-advisories)" % table.get("checked"))
    if not groups and not ungraded:
        lines.append("info: no %s image in the cluster's pods" % " or ".join(c["label"] for c in comps))
    return lines


def main(argv):
    if len(argv) not in (1, 4) or (len(argv) == 4 and argv[1] != "--in-range"):
        print("usage: eas-advisories.py <table> [--in-range <component> <version>] (pods List on stdin)", file=sys.stderr)
        return 2
    table = load_table(argv[0])
    if len(argv) == 4:
        comp = next((c for c in table["components"] if c["name"] == argv[2]), None)
        if comp is None:
            print("eas-advisories.py: no component %r" % argv[2], file=sys.stderr)
            return 2
        ids = [a["id"] for a, _ in hits(comp, argv[3])]
        print(" ".join(ids))
        return 0 if ids else 1
    try:
        doc = json.load(sys.stdin)
        items = doc["items"]
        if doc.get("kind") != "List" or not isinstance(items, list):
            raise ValueError("kind is %r" % doc.get("kind"))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print("eas-advisories.py: stdin is not a kubectl List: %s" % e, file=sys.stderr)
        return 2
    print("\n".join(report(table, items)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

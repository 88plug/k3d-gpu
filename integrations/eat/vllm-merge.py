#!/usr/bin/env python3
"""vllm-merge.py: k3d-gpu's values merged over one of EAT's vLLM chart values files.

  vllm-merge.py [--kv-gib GIB] [--require-digest] BASE OUT [OVERLAY...]

BASE is EAT's file (vllm/xeon-values.yaml, or vllm/guardrail-values.yaml for the
guard). Each OVERLAY is merged over it in order, a mapping into a mapping and
anything else replaced. brownfield.sh passes vllm-values.yaml, then the release's
image overlay when eat-up.sh mounts one (EAT_VLLM_IMAGE_VALUES). With --kv-gib,
every modelConfigs entry and defaultModelConfigs that sets VLLM_CPU_KVCACHE_SPACE
gets GIB instead. OUT is written only when the checks pass; BASE is never written.

Checks, each a non-zero exit with one line on stderr:
  - the merged image.tag, up to any '@', is the tag BASE names. EAT's model configs
    are written for the vLLM its release ships, so an overlay may add a digest to
    that tag but not swap the version (release-1.1.0's configs on v0.24.0 is the
    stall in EXPERIMENTS.md crescent amendment E1)
  - with --require-digest, the merged tag is <tag>@sha256:<64 hex>

Needs python3 and PyYAML (the bastion has both).
"""

import argparse
import os
import re
import sys

import yaml

PINNED = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def die(msg):
    sys.stderr.write("vllm-merge: %s\n" % msg)
    sys.exit(1)


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v


def image_tag(values):
    tag = (values.get("image") or {}).get("tag")
    return None if tag is None else str(tag)


def main(argv):
    ap = argparse.ArgumentParser(prog="vllm-merge.py")
    ap.add_argument("--kv-gib", help="VLLM_CPU_KVCACHE_SPACE for every entry that sets it")
    ap.add_argument("--require-digest", action="store_true", help="the merged image tag must carry @sha256:<digest>")
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("overlays", nargs="*")
    a = ap.parse_args(argv[1:])

    values = load(a.base)
    eat_tag = image_tag(values)
    if eat_tag is None:
        die("%s names no image.tag to check the overlays against" % a.base)
    for path in a.overlays:
        merge(values, load(path))
    tag = image_tag(values) or ""
    if tag.split("@")[0] != eat_tag.split("@")[0]:
        die("the merged image tag is %s, but EAT's %s names %s: its model configs are for %s. "
            "This EAT release needs its own image overlay (eat-up.sh EAT_RELEASE)"
            % (tag, a.base, eat_tag, eat_tag.split("@")[0]))
    if a.require_digest and not PINNED.match(tag):
        die("image.tag %s is not <tag>@sha256:<digest>" % tag)
    if a.kv_gib is not None:
        for cfg in [*(values.get("modelConfigs") or {}).values(), values.get("defaultModelConfigs")]:
            cmv = (cfg or {}).get("configMapValues") or {}
            if "VLLM_CPU_KVCACHE_SPACE" in cmv:
                cmv["VLLM_CPU_KVCACHE_SPACE"] = a.kv_gib

    with open(a.out + ".tmp", "w", encoding="utf-8") as f:
        yaml.safe_dump(values, f, sort_keys=False)
    os.replace(a.out + ".tmp", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

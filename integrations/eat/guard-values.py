#!/usr/bin/env python3
"""guard-values.py: the guard's vLLM image by digest, in a copy of EAT's charts.

  guard-values.py GUARD_VALUES VLLM_VALUES

GUARD_VALUES is vllm/guardrail-values.yaml in a copy of EAT's core/helm-charts,
the values deploy-guardrails.yml installs the guard's vLLM with (release-1.1.0:
lines 115 and 133-135). EAT names vllm/vllm-openai-cpu there by tag only. Its
image.tag is rewritten to the tag VLLM_VALUES pins for the same image, the
digest k3d-gpu already serves the models with, when:

  - VLLM_VALUES pins image.tag as <tag>@sha256:<digest>
  - both files name the same image.repository
  - EAT's tag is the <tag> part of that pin, so the pin only adds a digest

Otherwise it exits 1 naming both, and GUARD_VALUES is left as it was. Every
other key is kept. Needs python3 and PyYAML (the bastion has both).
"""

import re
import sys

import yaml

PINNED = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def die(msg):
    sys.stderr.write("guard-values: %s\n" % msg)
    sys.exit(1)


def image(path):
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    img = doc.get("image")
    if not isinstance(img, dict) or not img.get("repository") or not img.get("tag"):
        die("%s has no image with a repository and a tag" % path)
    return doc, img


def main():
    if len(sys.argv) != 3:
        die("usage: guard-values.py GUARD_VALUES VLLM_VALUES")
    guard_path, vllm_path = sys.argv[1:]
    guard, g = image(guard_path)
    _, v = image(vllm_path)
    pin = str(v["tag"])
    if not PINNED.match(pin):
        die("%s: image.tag %r is not <tag>@sha256:<digest>" % (vllm_path, pin))
    if g["repository"] != v["repository"]:
        die("EAT's guard runs %s, %s pins %s" % (g["repository"], vllm_path, v["repository"]))
    if str(g["tag"]) != pin.split("@")[0]:
        die("EAT's guard runs %s:%s, %s pins %s:%s" % (g["repository"], g["tag"], vllm_path, v["repository"], pin))
    g["tag"] = pin
    with open(guard_path, "w") as f:
        yaml.safe_dump(guard, f, sort_keys=False)
    print("guard image: %s:%s" % (g["repository"], pin))


if __name__ == "__main__":
    main()

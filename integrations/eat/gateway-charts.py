#!/usr/bin/env python3
"""gateway-charts.py: pinned copies of EAT's GenAI Gateway chart directories.

  gateway-charts.py HELM_CHARTS GATEWAY_VALUES TRACE_VALUES OUT

HELM_CHARTS is EAT's core/helm-charts. Writes OUT/genai-gateway and
OUT/genai-gateway-trace, the two directories deploy-genai-gateway.yml syncs and
installs (its helm_charts_base_genai and helm_charts_base_genai_trace), with:

  - genai-gateway/Chart.yaml: each dependency at the exact version GATEWAY_VALUES
    gives under k3d-gpu.dependencies, in place of EAT's open ranges
  - genai-gateway/values.yaml: GATEWAY_VALUES (minus its k3d-gpu key) merged over it
  - genai-gateway/templates/deployment.yaml: the literal busybox init image replaced
    by k3d-gpu.waitImage
  - genai-gateway-trace/charts/langfuse/values.yaml: TRACE_VALUES merged over it

Each image mapping in an overlay must meet the same repository in EAT's values, and
the same tag where EAT sets one, so a pin only adds a digest. EAT's files are read,
never written. Needs python3 and PyYAML (the bastion has both).
"""

import os
import re
import shutil
import sys

import yaml

PINNED = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def die(msg):
    sys.stderr.write("gateway-charts: %s\n" % msg)
    sys.exit(1)


def check_images(base, overlay, path):
    """Every overlay mapping with a repository: pinned, and the same image in base."""
    for k, v in overlay.items():
        if not isinstance(v, dict):
            continue
        where = path + [k]
        b = base.get(k) if isinstance(base, dict) else None
        if "repository" in v:
            name = ".".join(where)
            if not isinstance(b, dict):
                die("%s: EAT's values have no %s to pin" % (name, name))
            if b.get("repository") != v["repository"]:
                die("%s: the overlay pins %s, EAT's values name %s" % (name, v["repository"], b.get("repository")))
            tag = str(v.get("tag", ""))
            if not PINNED.match(tag):
                die("%s: tag %r is not <tag>@sha256:<digest>" % (name, tag))
            if b.get("tag") is not None and str(b["tag"]) != tag.split("@")[0]:
                die("%s: the overlay pins tag %s, EAT's values set %s" % (name, tag.split("@")[0], b["tag"]))
        check_images(b if isinstance(b, dict) else {}, v, where)


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def values_over(values_path, overlay):
    base = load(values_path)
    check_images(base, overlay, [])
    merge(base, overlay)
    dump(base, values_path)


def main(argv):
    if len(argv) != 5:
        die("usage: gateway-charts.py HELM_CHARTS GATEWAY_VALUES TRACE_VALUES OUT")
    charts, gateway_values, trace_values, out = argv[1:]
    gw_overlay, trace_overlay = load(gateway_values), load(trace_values)
    k3d = gw_overlay.pop("k3d-gpu", None) or die("%s has no k3d-gpu key" % gateway_values)

    os.makedirs(out, exist_ok=True)
    for d in ("genai-gateway", "genai-gateway-trace"):
        if not os.path.isdir(os.path.join(charts, d)):
            die("%s/%s not found" % (charts, d))
        shutil.rmtree(os.path.join(out, d), ignore_errors=True)
        shutil.copytree(os.path.join(charts, d), os.path.join(out, d))
    gw = os.path.join(out, "genai-gateway")

    chart_path = os.path.join(gw, "Chart.yaml")
    chart = load(chart_path)
    deps = {d.get("name"): d for d in chart.get("dependencies") or []}
    for name, version in k3d["dependencies"].items():
        if name not in deps:
            die("%s has no %s dependency to pin" % (chart_path, name))
        deps[name]["version"] = str(version)
    dump(chart, chart_path)

    wait = k3d["waitImage"]
    if not PINNED.match(str(wait["tag"])):
        die("k3d-gpu.waitImage tag %r is not <tag>@sha256:<digest>" % wait["tag"])
    old = "image: %s:%s\n" % (wait["repository"], wait["tag"].split("@")[0])
    tpl_path = os.path.join(gw, "templates", "deployment.yaml")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    if tpl.count(old) != 1:
        die("%s names '%s' %d times, want once" % (tpl_path, old.strip(), tpl.count(old)))
    with open(tpl_path, "w", encoding="utf-8") as f:
        f.write(tpl.replace(old, "image: %s:%s\n" % (wait["repository"], wait["tag"])))

    values_over(os.path.join(gw, "values.yaml"), gw_overlay)
    values_over(os.path.join(out, "genai-gateway-trace", "charts", "langfuse", "values.yaml"), trace_overlay)
    print("gateway charts: %s (dependencies %s; images pinned by digest)"
          % (out, ", ".join("%s %s" % kv for kv in k3d["dependencies"].items())))


if __name__ == "__main__":
    main(sys.argv)

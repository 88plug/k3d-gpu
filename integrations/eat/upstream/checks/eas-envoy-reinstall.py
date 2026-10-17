#!/usr/bin/env python3
"""Re-install check for patch 0006, no cluster needed.

EnvoyProxy is a custom resource, so kubernetes.core.k8s (state: present, no
apply) updates an existing object with a JSON merge patch (RFC 7386): its
strategic-merge attempt is rejected for CRDs and it falls back to "merge"
(kubernetes.core 6.3.0, plugins/module_utils/k8s/service.py, update()). A merge
patch only removes a key the patch sets to null. On create, the API server
prunes nulls from fields that are not nullable (Kubernetes CRD docs,
"Defaulting and Nullable").

This takes the EnvoyProxy definition from an unpatched EAS checkout (what an
earlier install left in the cluster) and from a patched one, then checks:
  fresh install: no nodeSelector, control-plane affinity present
  re-install over the unpatched object: same

Usage: integrations/eat/upstream/checks/eas-envoy-reinstall.py <unpatched-eas> <patched-eas>
Exit 0 when both cases pass. Needs PyYAML.
"""
import sys

import yaml

# EAS #42 (88fd2ae) moved the task from install.yaml to controller.yaml.
TASKS = ("roles/envoy_gateway/tasks/controller.yaml", "roles/envoy_gateway/tasks/install.yaml")
NAME = "envoy_gateway | Create EnvoyProxy config (DaemonSet on control-plane nodes)"


def definition(checkout):
    for path in TASKS:
        try:
            with open("%s/%s" % (checkout, path)) as f:
                tasks = yaml.safe_load(f)
        except FileNotFoundError:
            continue
        for task in tasks:
            if task.get("name") == NAME:
                return task["kubernetes.core.k8s"]["definition"]
    sys.exit("task %r not found in %s" % (NAME, checkout))


def merge_patch(target, patch):
    """RFC 7386 JSON merge patch."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


def prune_nulls(obj):
    """What the API server stores on create for non-nullable fields."""
    if isinstance(obj, dict):
        return {k: prune_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [prune_nulls(v) for v in obj]
    return obj


def pod(obj):
    return obj["spec"]["provider"]["kubernetes"]["envoyDaemonSet"]["pod"]


def check(label, obj):
    p = pod(obj)
    terms = (p.get("affinity", {}).get("nodeAffinity", {})
             .get("requiredDuringSchedulingIgnoredDuringExecution", {})
             .get("nodeSelectorTerms", []))
    exists = any(e.get("key") == "node-role.kubernetes.io/control-plane"
                 and e.get("operator") == "Exists"
                 for t in terms for e in t.get("matchExpressions", []))
    ok = "nodeSelector" not in p and exists
    print("%s %-28s nodeSelector=%s affinity-exists=%s"
          % ("ok  " if ok else "FAIL", label, p.get("nodeSelector"), exists))
    return ok


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    old, new = definition(sys.argv[1]), definition(sys.argv[2])
    results = [
        check("fresh install", prune_nulls(new)),
        check("re-install over unpatched", merge_patch(prune_nulls(old), new)),
    ]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

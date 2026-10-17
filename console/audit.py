#!/usr/bin/env python3
"""audit(observed, lock, plan): the posture checks behind `k3d-gpu audit`.

A pure function: no I/O, no clock, inputs left unchanged. Rules
(docs/console-design.md, sections 4-6, slice S criterion S5):

  listener  A listener off loopback with no authentication (or none this tool
            can establish) fails: the EAT#9 class, a model server open to the
            network. Loopback listeners pass; so do client-cert, key and OIDC.
  image     Every image in containerd must carry a registry digest that
            images.lock pins for that image name. An image missing from the
            lock, pinned to another digest, or with no digest at all fails.
  fetch     Any image pulled after install fails: the layout must run from
            baked images.
  secret    A key, token or kubeconfig file readable by group or others fails;
            these files are 0600.
  app       Open WebUI with sign-in off (WEBUI_AUTH=False) fails: that mode
            cannot later switch to accounts.

The verdict is fail when any check fails, and an observation with no images
fails too (it audited no cluster). A plan/v1 document, when given, adds the
endpoint it plans as a listener.

  audit.py observe < raw.json                  observed/v1 from the launcher's raw read
  audit.py check --lock F [--plan F] [--mode text|json] < observed.json

Python standard library only.
"""

import argparse
import ipaddress
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import facts as factsmod  # noqa: E402  (stdlib-only sibling modules)
import schema_check  # noqa: E402

AUDIT_SCHEMA = "k3d-gpu.audit/v1"
OBSERVED_SCHEMA = "k3d-gpu.observed/v1"
UNAUTHENTICATED = ("none", "unknown")
RULES = ("listener", "image", "fetch", "secret", "app")
# Node container ports k3d publishes, and how each is authenticated.
# k3s runs kube-apiserver with --anonymous-auth=false (pkg/daemons/control/server.go).
KNOWN_PORTS = {"6443/tcp": ("kube-apiserver", "client-cert")}
OPEN_WEBUI = "ghcr.io/open-webui/open-webui"
PLAN_AUTH = {"loopback-key": "key", "oidc": "oidc"}
# kubelet's Pulled event when the image came from a registry; a baked image reads
# 'Container image "<ref>" already present on machine'.
PULLED_RE = re.compile(r'^Successfully pulled image "([^"]+)"')


def parse_ref(ref):
    """'intel/x:1@sha256:..' -> (name, tag, digest), docker.io and library/ filled in."""
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = None
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    parts = ref.split("/")
    if len(parts) == 1 or not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = ["docker.io"] + parts
    if parts[0] == "docker.io" and len(parts) == 2:
        parts.insert(1, "library")
    return "/".join(parts), tag, digest


def is_loopback(address):
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address.strip("[]")).is_loopback
    except ValueError:
        return False


def endpoint(address, port):
    host = "[%s]" % address if ":" in address and not address.startswith("[") else address
    return "%s:%s" % (host, port if port is not None else "(port not set)")


def check_listener(ls):
    subject = "%s %s" % (endpoint(ls["address"], ls["port"]), ls["name"])
    where = "loopback" if is_loopback(ls["address"]) else "off loopback"
    detail = "%s, auth %s (%s)" % (where, ls["auth"], ls["source"])
    if where == "off loopback" and ls["auth"] in UNAUTHENTICATED:
        why = "no authentication" if ls["auth"] == "none" else "no authentication this audit can establish"
        return ("fail", subject, "unauthenticated listener off loopback: %s; %s" % (why, detail),
                "bind it to 127.0.0.1, or put OIDC in front of it (k3d-gpu plan --bind picks oidc off loopback)")
    return "pass", subject, detail, None


def lock_index(lock):
    by_name = {}
    for e in lock["images"]:
        by_name.setdefault(e["name"], []).append(e)
    return by_name


def check_image(img, by_name):
    """One containerd image (as seen on one or more nodes) against images.lock."""
    subject = " ".join(sorted(img["tags"]) or sorted(img["digests"])) or "(an image with no name)"
    pinned = [parse_ref(d) for d in img["digests"]]
    pinned = [(n, d) for n, _, d in pinned if d]
    for name, digest in pinned:
        for e in by_name.get(name, []):
            if e["digest"] == digest:
                return "pass", subject, "%s in images.lock (%s)" % (digest[:19], ", ".join(e["sources"])), None
    if not pinned:
        return ("fail", subject, "no registry digest, so it cannot match images.lock",
                "remove it, or load the image from a registry reference that images.lock pins")
    known = [(n, d) for n, d in pinned if n in by_name]
    if known:
        name, digest = known[0]
        locked = ", ".join(sorted("%s%s" % (e["digest"], " (tag %s)" % e["tag"] if e["tag"] else "")
                                  for e in by_name[name]))
        return ("fail", subject, "%s is %s, images.lock pins %s" % (name, digest, locked),
                "redeploy from the pinned digest, or bump the pin with scripts/images-lock update")
    names = ", ".join(sorted({n for n, _ in pinned}))
    return ("fail", subject, "not in images.lock: %s" % names,
            "remove the workload that runs it, or pin it in the repo and run scripts/images-lock update")


def audit(observed, lock, plan=None):
    """The audit/v1 document. Pure: reads its arguments only."""
    by_name = lock_index(lock)
    checks = []

    listeners = list(observed["listeners"])
    if plan is not None:
        auth = PLAN_AUTH.get(plan["auth"], "unknown")
        if plan["auth"] == "loopback-key" and not is_loopback(plan["bind"]):
            auth = "none"  # loopback mode has no login; off loopback it is open
        listeners.append({"name": "planned endpoint", "address": plan["bind"], "port": plan["port"],
                          "auth": auth, "source": "plan/v1 auth " + plan["auth"]})
    for ls in listeners:
        checks.append(("listener",) + check_listener(ls))

    groups = {}
    for img in observed["images"]:
        key = (tuple(sorted(img["tags"])), tuple(sorted(img["digests"])))
        groups.setdefault(key, []).append(img["node"])
    for (tags, digests), nodes in groups.items():
        status, subject, detail, fix = check_image({"tags": list(tags), "digests": list(digests)}, by_name)
        checks.append(("image", status, subject, "%s; on %s" % (detail, ", ".join(sorted(set(nodes)))), fix))
    if not observed["images"]:
        checks.append(("image", "fail", "(none)", "no images observed: no running cluster was audited",
                       "run k3d-gpu up, then k3d-gpu audit"))

    for p in observed["pulls"]:
        checks.append(("fetch", "fail", p["ref"], "pulled at runtime on %s" % p["node"],
                       "pre-load the image (baked node image or air-gap bundle) so pods start with no pull"))

    for s in observed["secrets"]:
        mode = int(s["mode"], 8)
        if mode & 0o077:
            checks.append(("secret", "fail", s["path"], "mode %04o: readable beyond its owner" % mode,
                           "chmod 600 %s" % s["path"]))
        else:
            checks.append(("secret", "pass", s["path"], "mode %04o" % mode, None))

    for a in observed["apps"]:
        subject = "%s %s" % (a["kind"], a["name"])
        if a["auth"] is True:
            checks.append(("app", "pass", subject, "sign-in on (WEBUI_AUTH)", None))
        else:
            why = "sign-in off (WEBUI_AUTH=False)" if a["auth"] is False else "WEBUI_AUTH not readable as a literal"
            checks.append(("app", "fail", subject,
                           why + "; a no-auth Open WebUI cannot later switch to accounts",
                           "set WEBUI_AUTH to \"True\" as share/agent-chat-ui.yml does"))

    checks.sort(key=lambda c: (RULES.index(c[0]), c[1] != "fail", c[2], c[3]))
    docs = [{"rule": r, "status": st, "subject": su, "detail": d, "fix": f} for r, st, su, d, f in checks]
    failed = [c for c in docs if c["status"] == "fail"]
    verdict = "fail" if failed else "pass"
    summary = ["audit %s: %d of %d checks failed (images.lock: %d images, k3s %s)"
               % (verdict.upper(), len(failed), len(docs), len(lock["images"]), lock["k3s_tag"])]
    for c in failed:
        summary.append("FAIL %s %s: %s" % (c["rule"], c["subject"], c["detail"]))
        summary.append("     fix: " + c["fix"])
    return {"schema": AUDIT_SCHEMA, "verdict": verdict, "cluster": observed["cluster"],
            "lock": {"k3s_tag": lock["k3s_tag"], "images": len(lock["images"])},
            "checks": docs, "summary": summary}


def render_checks(doc):
    lines = [doc["summary"][0]]
    for c in doc["checks"]:
        lines.append("%s %s %s: %s" % ("ok  " if c["status"] == "pass" else "FAIL", c["rule"], c["subject"], c["detail"]))
        if c["fix"]:
            lines.append("     fix: " + c["fix"])
    return lines


def observe(raw):
    """observed/v1 from the launcher's raw read: per node `docker inspect` ports and
    `crictl images -o json`, the cluster's workloads, its kubelet Pulled events
    (kept for the API server's event TTL, 1 h by default), and kubeconfig file modes."""
    listeners, images, apps = [], [], []
    for node in raw["nodes"]:
        for cport, binds in sorted((node.get("ports") or {}).items()):
            name, auth = KNOWN_PORTS.get(cport, ("published node port " + cport, "unknown"))
            for b in binds or []:
                address = b.get("HostIp") or "0.0.0.0"
                listeners.append({"name": name, "address": address, "port": int(b["HostPort"]),
                                  "auth": auth, "source": "docker port %s on %s" % (cport, node["name"])})
        for img in (node.get("images") or {}).get("images") or []:
            images.append({"node": node["name"], "tags": sorted(img.get("repoTags") or []),
                           "digests": sorted(img.get("repoDigests") or [])})
    for item in (raw.get("workloads") or {}).get("items") or []:
        meta = item.get("metadata", {})
        spec = item.get("spec", {}).get("template", {}).get("spec", {})
        for c in spec.get("containers") or []:
            if parse_ref(c.get("image", ""))[0] != OPEN_WEBUI:
                continue
            auth = True  # Open WebUI's own default when WEBUI_AUTH is unset
            for env in c.get("env") or []:
                if env.get("name") == "WEBUI_AUTH":
                    v = env.get("value")
                    auth = None if v is None else v.strip().lower() == "true"
            apps.append({"kind": "open-webui", "auth": auth,
                         "name": "%s/%s/%s" % (meta.get("namespace", ""), item.get("kind", "").lower(), meta.get("name", ""))})
    pulls = set()
    for ev in (raw.get("events") or {}).get("items") or []:
        m = PULLED_RE.match(ev.get("message") or "") if ev.get("reason") == "Pulled" else None
        if m:
            node = (ev.get("source") or {}).get("host") or ev.get("reportingInstance") or "(unknown node)"
            pulls.add((node, m.group(1)))
    listeners.sort(key=lambda x: (x["source"], x["address"], x["port"]))
    images.sort(key=lambda x: (x["node"], x["tags"], x["digests"]))
    apps.sort(key=lambda x: x["name"])
    secrets = sorted(({"path": s["path"], "mode": s["mode"]} for s in raw.get("secrets") or []), key=lambda x: x["path"])
    return {"schema": OBSERVED_SCHEMA, "cluster": raw.get("cluster"), "listeners": listeners, "images": images,
            "pulls": [{"node": n, "ref": r} for n, r in sorted(pulls)], "secrets": secrets, "apps": apps}


def load(path, schema, ref):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    schema_check.check(doc, schema, ref)
    return doc


def main(argv):
    ap = argparse.ArgumentParser(prog="k3d-gpu audit", description="posture checks against images.lock")
    ap.add_argument("command", choices=("observe", "check"))
    ap.add_argument("--lock")
    ap.add_argument("--plan")
    ap.add_argument("--mode", choices=("text", "json"), default="text")
    args = ap.parse_args(argv[1:])
    schema = factsmod.load_schema()
    try:
        doc = json.load(sys.stdin)
        if args.command == "observe":
            out = observe(doc)
            schema_check.check(out, schema, "#/$defs/observed")
            sys.stdout.buffer.write(factsmod.dumps(out))
            return 0
        if not args.lock:
            raise ValueError("--lock is required")
        schema_check.check(doc, schema, "#/$defs/observed")
        lock = load(args.lock, schema, "#/$defs/images_lock")
        plan = load(args.plan, schema, "#/$defs/plan") if args.plan else None
        out = audit(doc, lock, plan)
        schema_check.check(out, schema, "#/$defs/audit")
    except (ValueError, OSError, KeyError, TypeError) as e:
        sys.stderr.write("audit: %s\n" % e)
        return 2
    if args.mode == "json":
        sys.stdout.buffer.write(factsmod.dumps(out))
    else:
        sys.stdout.buffer.write(("\n".join(render_checks(out)) + "\n").encode("utf-8"))
    return 1 if out["verdict"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

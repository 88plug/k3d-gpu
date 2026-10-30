#!/usr/bin/env python3
"""The canary's engine table (canary/engines.toml): where each engine's image is pinned,
what to check, which engines a change selects, and the merged report.

  plan.py ids                          the engine ids
  plan.py ref ID                       the image reference ID's locator matches (exactly one)
  plan.py show ID                      the engine as JSON: its table, the resolved pin (ref,
                                       image, tag, digest), the version the tag implies, the
                                       flags canary/flags.sh names for it, its derived
                                       constants and its golden
  plan.py shell ID                     the same as shell assignments, for canary/run.sh
  plan.py model-dir ID                 the model cache directory (CANARY_MODELS, default
                                       ~/.cache/k3d-gpu-canary)
  plan.py matrix [--mode auto|all] [--engines CSV] [--base REF | --base-root DIR]
                                       the engines to run and the workflow's job matrices
  plan.py size REF                     the linux/amd64 image's compressed size, in bytes
  plan.py report --selected JSON --parts DIR --sha SHA --ref REF --out FILE
                                       canary-result.json from the per-engine results; exits
                                       non-zero unless every selected engine is green

Every subcommand takes --root DIR (default: this repo). In auto mode an engine is selected
when, between the base tree and this one, its resolved image reference, its table in
engines.toml, its golden, one of its derived constants or one of the flags canary/flags.sh
names for it changed (the node engine: also any file under its paths). A change to the
harness itself (canary/ outside engines.toml and goldens/, the defaults table, or
.github/workflows/canary.yml) selects every engine. --base REF compares with
`git merge-base REF HEAD`; --base-root compares with a directory.

Python standard library only (tomllib: Python 3.11+).
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOML = "canary/engines.toml"
WORKFLOW = ".github/workflows/canary.yml"
KINDS = ("runtime", "surface", "node")
VERDICTS = ("pass", "fail", "isa-skip", "too-big", "unparseable")
RESULT_KEYS = ("kind", "pinned_ref", "verdict", "hard", "exact", "cpu_flags", "detail")
# Files every plan reads besides the locators, derived files and node paths the table names.
BASE_PATHS = ("canary", WORKFLOW, "scripts/k3d-gpu", "console/model_opt.py", "console/fit.py", "bench/h54/h54.py")


class PlanError(Exception):
    pass


def load(root):
    path = os.path.join(root, TOML)
    if not os.path.exists(path):
        return {}, {}
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    engines = {}
    for e in doc.get("engine", []):
        if e.get("id") in engines:
            raise PlanError("%s: engine %s is listed twice" % (TOML, e["id"]))
        if e.get("kind") not in KINDS:
            raise PlanError("%s: engine %s has kind %r, not one of %s" % (TOML, e.get("id"), e.get("kind"), ", ".join(KINDS)))
        engines[e["id"]] = e
    return doc.get("defaults", {}), engines


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def split_ref(ref):
    """name, tag ('' when none) and digest of name[:tag]@sha256:<hex>."""
    m = re.fullmatch(r"([^@\s]+)@(sha256:[0-9a-f]{64})", ref)
    if not m:
        raise PlanError("%r is not name[:tag]@sha256:<64 hex>" % ref)
    name, digest = m.groups()
    tag = ""
    if ":" in name.rsplit("/", 1)[-1]:
        name, tag = name.rsplit(":", 1)
    return name, tag, digest


def resolve(eng, root):
    """The engine's pin: {ref, image (its name), tag, digest}, from exactly one locator match."""
    loc = eng["locator"]
    try:
        text = read(root, loc["file"])
    except FileNotFoundError:
        raise PlanError("%s: locator file %s does not exist" % (eng["id"], loc["file"]))
    matches = list(re.finditer(loc["regex"], text, re.M))
    if len(matches) != 1:
        raise PlanError("%s: locator %r matches %d image references in %s, not exactly one"
                        % (eng["id"], loc["regex"], len(matches), loc["file"]))
    g = matches[0].groupdict()
    if g.get("ref"):
        name, tag, digest = split_ref(g["ref"])
    else:
        name, tag, digest = loc["name"], g.get("tag") or "", g["digest"]
    ref = "%s%s@%s" % (name, ":" + tag if tag else "", digest)
    return {"ref": ref, "image": name, "tag": tag, "digest": digest}


def expected_version(probe, tag):
    """The version the pinned tag names, read with version_probe.tag; '' when the pin
    has no tag or the probe names no tag regex."""
    rx = (probe or {}).get("tag")
    if not rx or not tag:
        return ""
    m = re.search(rx, tag)
    if not m:
        raise PlanError("tag %r does not match version_probe.tag %r" % (tag, rx))
    return m.group("v")


_flags_cache = {}


def flags_lines(root):
    if root not in _flags_cache:
        out = subprocess.run([os.path.join(HERE, "flags.sh"), "--root", root], capture_output=True, text=True)
        if out.returncode != 0:
            raise PlanError("canary/flags.sh failed on %s: %s" % (root, out.stderr.strip()))
        _flags_cache[root] = [dict(zip(("family", "source", "kind", "name"), ln.split("\t")))
                              for ln in out.stdout.splitlines() if ln]
    return _flags_cache[root]


def flags_for(eng, root):
    srcs = set(eng.get("flags_source", []))
    return [{"source": r["source"], "kind": r["kind"], "name": r["name"]} for r in flags_lines(root)
            if r["family"] == eng.get("family") and r["source"] in srcs]


def derived_for(eng, root):
    out = []
    for d in eng.get("derived", []):
        try:
            text = read(root, d["file"])
        except FileNotFoundError:
            raise PlanError("%s: derived file %s does not exist" % (eng["id"], d["file"]))
        ms = list(re.finditer(d["regex"], text, re.M))
        if len(ms) != 1:
            raise PlanError("%s: derived %r matches %d times in %s, not exactly once" % (eng["id"], d["regex"], len(ms), d["file"]))
        out.append({"file": d["file"], "regex": d["regex"], "of": d["of"], "value": ms[0].group("v")})
    return out


def model_dir(eng):
    base = os.environ.get("CANARY_MODELS") or os.path.join(os.path.expanduser("~"), ".cache", "k3d-gpu-canary")
    return os.path.join(base, model_slug(eng))


def model_slug(eng):
    return "%s@%s" % (eng["model"].replace("/", "__"), eng["revision"])


def subst(s, eng, name):
    mount = eng.get("mount", "").replace("{model}", eng.get("model", ""))
    return (s.replace("{mount}", mount).replace("{model}", eng.get("model", "")).replace("{name}", name)
            .replace("{threads}", str(os.cpu_count() or 1)))


def show(eid, root):
    defaults, engines = load(root)
    if eid not in engines:
        raise PlanError("no engine %r in %s (engines: %s)" % (eid, TOML, ", ".join(sorted(engines))))
    eng = dict(engines[eid])
    doc = {"ready_timeout": defaults.get("ready_timeout", 600), "size_factor": defaults.get("size_factor", 3.5),
           "name": defaults.get("name", "canary"), "informational": False}
    doc.update(eng)
    doc.update(resolve(eng, root))
    doc["pinned_ref"] = doc["ref"]
    doc["expected_version"] = expected_version(eng.get("version_probe"), doc["tag"])
    if eng["kind"] != "node":
        doc["flags"] = flags_for(eng, root)
    doc["derived"] = derived_for(eng, root)
    if eng["kind"] == "runtime":
        doc["model_dir"] = model_dir(eng)
        doc["golden_doc"] = json.loads(read(root, eng["golden"]))
        name = doc["name"]
        for k in ("args", "env", "prepare", "prepare_env"):
            doc[k] = [subst(a, eng, name) for a in eng.get(k, [])]
        doc["mount"] = subst(eng.get("mount", "/models"), eng, name)
    return doc


def shell(doc):
    def one(k, v):
        return "%s=%s" % (k, shlex.quote(str(v)))

    def arr(k, vs):
        return "%s=(%s)" % (k, " ".join(shlex.quote(str(v)) for v in vs))
    probe = doc.get("version_probe") or {}
    lines = [one("E_ID", doc["id"]), one("E_KIND", doc["kind"]), one("E_INFORMATIONAL", "1" if doc["informational"] else ""),
             one("E_REF", doc["ref"]), one("E_TAG", doc["tag"]), one("E_SIZE_FACTOR", doc["size_factor"]),
             one("E_READY_TIMEOUT", doc["ready_timeout"]), one("E_SURFACE", (doc.get("surface") or {}).get("driver", "")),
             one("E_ISA_SIGNAL", doc.get("isa_signal", "")), one("E_EXPECT_VERSION", doc["expected_version"])]
    if doc["kind"] == "runtime":
        lines += [one("E_MODEL", doc["model"]), one("E_REVISION", doc["revision"]), one("E_MODEL_DIR", doc["model_dir"]),
                  arr("E_FILES", doc["files"]), one("E_MOUNT", doc["mount"]), one("E_WRITABLE", doc.get("writable", "")),
                  arr("E_ARGS", doc["args"]), arr("E_ENV", doc["env"]), arr("E_PREPARE", doc["prepare"]),
                  arr("E_PREPARE_ENV", doc["prepare_env"]), arr("E_CAP_ADD", doc.get("cap_add", [])),
                  one("E_SHM_SIZE", doc.get("shm_size", "")),
                  one("E_API", doc["api"]), one("E_PORT", doc["port"]), one("E_NAME", doc["name"]),
                  one("E_GOLDEN", doc["golden"]), one("E_VERSION_PATH", probe.get("path", "")),
                  one("E_VERSION_KEY", probe.get("key", "")), one("E_VERSION_REGEX", probe.get("regex", ""))]
    return "\n".join(lines)


# ---- planning ----------------------------------------------------------------------

def digest_paths(root, paths):
    h = hashlib.sha256()
    for p in paths:
        full = os.path.join(root, p)
        files = []
        if os.path.isdir(full):
            for d, _, fs in os.walk(full):
                files += [os.path.join(d, f) for f in fs]
        elif os.path.exists(full):
            files = [full]
        for f in sorted(files):
            h.update(os.path.relpath(f, root).encode() + b"\0")
            with open(f, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def fingerprint(eid, root):
    """What an engine's canary depends on in a tree; an error is part of it (a broken
    locator in the head tree is a change that must run, and fail)."""
    defaults, engines = load(root)
    eng = engines.get(eid)
    if eng is None:
        return None
    fp = {"table": json.dumps(eng, sort_keys=True)}
    for key, fn in (("pin", lambda: resolve(eng, root)["ref"]),
                    ("derived", lambda: [d["value"] for d in derived_for(eng, root)]),
                    ("flags", lambda: sorted(json.dumps(f, sort_keys=True) for f in flags_for(eng, root))
                     if eng["kind"] != "node" else []),
                    ("golden", lambda: read(root, eng["golden"]) if eng.get("golden") else ""),
                    ("paths", lambda: digest_paths(root, eng.get("paths", [])))):
        try:
            fp[key] = fn()
        except (PlanError, OSError) as e:
            fp[key] = "error: %s" % e
    return fp


def harness(root):
    """The canary's own code and settings: a change here runs every engine."""
    h = hashlib.sha256()
    canary = os.path.join(root, "canary")
    for d, _, fs in sorted(os.walk(canary)) if os.path.isdir(canary) else []:
        rel = os.path.relpath(d, root)
        if rel == "canary/goldens" or rel.startswith("canary/goldens/") or "__pycache__" in rel:
            continue
        for f in sorted(fs):
            if (rel == "canary" and f == "engines.toml") or f.endswith(".md"):
                continue
            h.update(os.path.join(rel, f).encode() + b"\0")
            with open(os.path.join(d, f), "rb") as fh:
                h.update(fh.read())
    defaults, _ = load(root)
    h.update(json.dumps(defaults, sort_keys=True).encode())
    wf = os.path.join(root, WORKFLOW)
    if os.path.exists(wf):
        with open(wf, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def git(root, *args):
    out = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise PlanError("git %s: %s" % (" ".join(args), out.stderr.strip()))
    return out.stdout.strip()


def base_tree(root, ref, dest):
    """The files a plan reads, from `git merge-base REF HEAD`, written under dest."""
    commit = git(root, "merge-base", ref, "HEAD")
    _, engines = load(root)
    paths = set(BASE_PATHS)
    for e in engines.values():
        paths.add(e["locator"]["file"])
        paths.update(d["file"] for d in e.get("derived", []))
        paths.update(p.rstrip("/") for p in e.get("paths", []))
    present = [p for p in sorted(paths)
               if subprocess.run(["git", "-C", root, "cat-file", "-e", "%s:%s" % (commit, p)], capture_output=True).returncode == 0]
    if present:
        arc = subprocess.run(["git", "-C", root, "archive", "--format=tar", commit, "--", *present], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", dest], input=arc.stdout, check=True)
    return commit


def matrix(root, mode, engines_csv, base=None, base_root=None):
    defaults, engines = load(root)
    ids = list(engines)
    if engines_csv and engines_csv != "auto":
        want = [e.strip() for e in engines_csv.split(",") if e.strip()]
        unknown = [e for e in want if e not in engines]
        if unknown:
            raise PlanError("unknown engine id(s): %s (engines: %s)" % (", ".join(unknown), ", ".join(ids)))
        chosen = [e for e in ids if e in want]
    elif mode == "all":
        chosen = ids
    else:
        tmp = None
        try:
            if base_root is None:
                tmp = tempfile.mkdtemp(prefix="canary-base.")
                base_tree(root, base or "origin/main", tmp)
                base_root = tmp
            if harness(base_root) != harness(root):
                chosen = ids
            else:
                chosen = [e for e in ids if fingerprint(e, base_root) != fingerprint(e, root)]
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    selected, runtime, surface, node = [], [], [], False
    for eid in chosen:
        e = engines[eid]
        try:
            ref = resolve(e, root)["ref"]
        except PlanError:
            ref = ""
        selected.append({"id": eid, "kind": e["kind"], "informational": bool(e.get("informational")), "pinned_ref": ref})
        if e["kind"] == "runtime":
            slug = model_slug(e)
            runtime.append({"id": eid, "model": e["model"], "revision": e["revision"],
                            "cache_key": "canary-model-" + slug, "cache_path": "~/.cache/k3d-gpu-canary/" + slug})
        elif e["kind"] == "surface":
            surface.append({"id": eid})
        else:
            node = True
    return {"selected": selected, "runtime": runtime, "surface": surface, "node": node}


# ---- images --------------------------------------------------------------------------

def image_bytes(ref):
    """Compressed bytes of the linux/amd64 image (layers and config), from the registry."""
    def raw(r):
        out = subprocess.run(["docker", "buildx", "imagetools", "inspect", "--raw", r], capture_output=True, text=True)
        if out.returncode != 0:
            raise PlanError("cannot read the manifest of %s: %s" % (r, out.stderr.strip()))
        return json.loads(out.stdout)
    m = raw(ref)
    if "manifests" in m:
        amd = [x for x in m["manifests"] if (x.get("platform") or {}).get("os") == "linux"
               and (x.get("platform") or {}).get("architecture") == "amd64"]
        if not amd:
            raise PlanError("%s has no linux/amd64 image" % ref)
        m = raw("%s@%s" % (ref.split("@")[0], amd[0]["digest"]))
    return sum(int(x.get("size", 0)) for x in m.get("layers", [])) + int((m.get("config") or {}).get("size", 0))


# ---- report --------------------------------------------------------------------------

def report(selected, parts, sha, ref, out, summary=None, github_output=None):
    engines, red, isa = {}, [], []
    for s in selected:
        p = os.path.join(parts, s["id"] + ".json")
        r = None
        try:
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            if not all(k in r for k in RESULT_KEYS) or r["verdict"] not in VERDICTS:
                r = dict(r, verdict="fail", hard=False, exact=False,
                         detail="malformed result (%s): %s" % (p, json.dumps(r)[:200]))
        except (OSError, ValueError):
            r = {"kind": s.get("kind", "runtime"), "pinned_ref": s.get("pinned_ref", ""), "verdict": "fail", "hard": False,
                 "exact": False, "cpu_flags": [], "detail": "no result: the job failed or was cancelled before writing one"}
        r = {k: r.get(k) for k in RESULT_KEYS}
        r["kind"] = r["kind"] if r["kind"] in KINDS else s.get("kind", "runtime")
        r["pinned_ref"] = str(r["pinned_ref"] or s.get("pinned_ref", ""))
        r["hard"], r["exact"] = bool(r["hard"]), bool(r["exact"])
        r["cpu_flags"] = list(r["cpu_flags"] or [])
        r["detail"] = str(r["detail"] or "")
        engines[s["id"]] = r
        green = r["verdict"] == "pass" and r["hard"]
        if not green and not s.get("informational"):
            red.append(s["id"])
        if r["verdict"] == "isa-skip" and not s.get("informational"):
            isa.append(s["id"])  # the workflow re-dispatches these, hoping for another CPU
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"sha": sha, "ref": ref, "engines": engines}, f, indent=1, sort_keys=True)
        f.write("\n")
    rows = ["| engine | kind | verdict | hard | exact | pinned | detail |", "|---|---|---|---|---|---|---|"]
    info = {s["id"] for s in selected if s.get("informational")}
    for eid, r in engines.items():
        rows.append("| %s%s | %s | %s | %s | %s | `%s` | %s |" % (
            eid, " (informational)" if eid in info else "", r["kind"], r["verdict"], r["hard"], r["exact"],
            r["pinned_ref"].split("@")[0], r["detail"].replace("|", "/").replace("\n", " ")[:300]))
    text = "## Canary %s\n\n%s\n\n%s\n" % (
        "green" if not red else "red: " + ", ".join(red),
        "\n".join(rows) if engines else "No engine selected: nothing an engine depends on changed.",
        "Green means verdict pass with hard true. exact false (a golden mismatch) stays green but is not auto-merge evidence.")
    print(text)
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("isa_skip=%s\ngreen=%s\n" % (",".join(isa), "false" if red else "true"))
    return 1 if red else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", default=REPO)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ids")
    for c in ("ref", "show", "shell", "model-dir"):
        s = sub.add_parser(c)
        s.add_argument("id")
        s.add_argument("--root", default=argparse.SUPPRESS)
    m = sub.add_parser("matrix")
    m.add_argument("--mode", choices=("auto", "all"), default="auto")
    m.add_argument("--engines", default="auto")
    m.add_argument("--base")
    m.add_argument("--base-root")
    m.add_argument("--root", default=argparse.SUPPRESS)
    m.add_argument("--github-output")
    s = sub.add_parser("size")
    s.add_argument("ref")
    r = sub.add_parser("report")
    r.add_argument("--selected", required=True, help="JSON list, or @FILE")
    r.add_argument("--parts", required=True)
    r.add_argument("--sha", required=True)
    r.add_argument("--ref", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--summary")
    r.add_argument("--github-output")
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.cmd == "ids":
            print("\n".join(load(root)[1]))
        elif a.cmd == "ref":
            _, engines = load(root)
            if a.id not in engines:
                raise PlanError("no engine %r" % a.id)
            print(resolve(engines[a.id], root)["ref"])
        elif a.cmd == "show":
            print(json.dumps(show(a.id, root), indent=1, sort_keys=True))
        elif a.cmd == "shell":
            print(shell(show(a.id, root)))
        elif a.cmd == "model-dir":
            print(model_dir(load(root)[1][a.id]))
        elif a.cmd == "matrix":
            doc = matrix(root, a.mode, a.engines, a.base, os.path.abspath(a.base_root) if a.base_root else None)
            print(json.dumps(doc, indent=1))
            if a.github_output:
                with open(a.github_output, "a", encoding="utf-8") as f:
                    for k in ("runtime", "surface", "selected"):
                        f.write("%s=%s\n" % (k, json.dumps(doc[k], separators=(",", ":"))))
                    f.write("node=%s\n" % ("true" if doc["node"] else "false"))
        elif a.cmd == "size":
            print(image_bytes(a.ref))
        elif a.cmd == "report":
            sel = a.selected
            if sel.startswith("@"):
                with open(sel[1:], encoding="utf-8") as f:
                    sel = f.read()
            return report(json.loads(sel), a.parts, a.sha, a.ref, a.out, a.summary, a.github_output)
    except PlanError as e:
        print("plan.py: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

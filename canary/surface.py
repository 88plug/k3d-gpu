#!/usr/bin/env python3
"""The canary's surface check: every flag and env name k3d-gpu emits for an engine
(canary/flags.sh) exists in the pinned image, and the constants k3d-gpu derives from the
image (console/plan.py's vLLM versions, the NPU driver and firmware facts) still match it.
Runs with no device: nothing here needs a GPU, an NPU or a Gaudi.

  surface.py check --engine FILE --out FILE [--canary DIR]
      host side: FILE is `canary/plan.py show <id>`; runs the image with docker and
      writes {verdict: pass|fail|unparseable, detail, method, version, missing}
  surface.py inside --pkg NAME [--root DIR]
      inside an image (docker run --entrypoint python3 -v canary:/canary:ro ...): finds the
      installed package on sys.path without importing it and prints what its sources
      define, as JSON {root, version, flags, envs}; exit 3 with {error} when unparseable

Drivers (engines.toml surface.driver):
  vllm           `vllm serve --help=all` gives the flags when it runs on the CPU; otherwise
                 the static parse of vllm/engine/arg_utils.py (add_argument literals) and
                 every vllm/entrypoints/**/cli_args.py (add_argument literals and the
                 fields of its *Args dataclasses, which become --kebab-case flags), where
                 --no-X counts as present when --X is (BooleanOptionalAction). Env names
                 are always the keys of vllm/envs.py's environment_variables dict; the
                 version is the package's dist-info. Underscores and dashes are one flag
                 (vLLM's FlexibleArgumentParser).
  llama-server   llama-server --help
  llama-convert  convert_hf_to_gguf.py --help, and llama-quantize must answer --help
  ovms           ovms --help; with `deb`, that package's version (dpkg-query); with
                 `firmware`, the build date inside that firmware file
  gst            gst-inspect-1.0 per element: it must exist and have each property;
                 elements named in `device_only` (prefixes) register only on a device
  sglang         python3 -m sglang.launch_server --help, and the package version
A help or parse that fails, or yields no flags, is unparseable: red, never skipped.

Python 3.8+ standard library only (the `inside` mode runs on the image's Python).
"""

import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

FLAG = re.compile(r"-{1,2}[A-Za-z][A-Za-z0-9_.-]*")
HELP_MIN = 3  # fewer flags than this in a help text is no help text


class Unparseable(Exception):
    pass


# ---- help output ---------------------------------------------------------------------

def help_flags(text):
    """Flags an option listing defines: lines that start (after indentation) with an
    option, as argparse, cxxopts and llama.cpp print them ("-a, --bb B", "--c, --no-c",
    "-ngl,  --gpu-layers, --n-gpu-layers N   text"). Usage lines and prose are skipped."""
    flags = set()
    for line in text.splitlines():
        s = line.strip()
        # options sit at most 8 columns in; deeper lines continue a description
        if not s.startswith("-") or s.startswith("---") or len(line) - len(line.lstrip()) > 8:
            continue
        # the option column ends where the description starts (two spaces after a word)
        col = re.split(r"(?<=[^,\s])\s{2,}", s, maxsplit=1)[0]
        for item in col.split(","):
            word = item.strip().split(" ", 1)[0].split("=", 1)[0]
            if not word:
                continue
            if not FLAG.fullmatch(word):
                break
            flags.add(word)
    if len(flags) < HELP_MIN:
        raise Unparseable("no option listing in the help output (%d flags)" % len(flags))
    return flags


def gst_properties(text):
    """Property names of a gst-inspect-1.0 element page."""
    props, inside = set(), False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            inside = line.startswith("Element Properties")
            continue
        m = re.match(r"^  ([a-z][a-z0-9-]*)\s*:", line)
        if inside and m:
            props.add(m.group(1))
    return props


# ---- static sources ------------------------------------------------------------------

def parse(path):
    try:
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read(), path)
    except FileNotFoundError:
        raise Unparseable("%s is missing" % path)
    except (SyntaxError, ValueError, UnicodeDecodeError) as e:
        raise Unparseable("%s does not parse: %s" % (path, e))


def add_argument_flags(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and FLAG.fullmatch(a.value):
                    out.add(a.value)
    return out


def args_class_fields(tree):
    """--kebab-case of every annotated field of a class named *Args (vLLM registers each
    field of FrontendArgs as a flag)."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name.endswith("Args"):
            for s in n.body:
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and not s.target.id.startswith("_"):
                    out.add("--" + s.target.id.replace("_", "-"))
    return out


def env_names(tree):
    for n in tree.body:
        target = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == "environment_variables" and isinstance(n.value, ast.Dict):
            names = {k.value for k in n.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if names:
                return names
    raise Unparseable("envs.py has no environment_variables dict of env names")


def norm_dist(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def package_version(site, pkg):
    for d in sorted(glob.glob(os.path.join(site, "*.dist-info"))):
        meta = os.path.join(d, "METADATA")
        if not os.path.exists(meta):
            continue
        with open(meta, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        n = re.search(r"^Name: (.+)$", head, re.M)
        v = re.search(r"^Version: (.+)$", head, re.M)
        if n and v and norm_dist(n.group(1).strip()) == norm_dist(pkg):
            return v.group(1).strip()
    try:
        import importlib.metadata as md  # reads dist-info on sys.path; imports nothing of pkg
        return md.version(pkg)
    except Exception:
        pass
    for name in ("_version.py", "version.py"):
        p = os.path.join(site, pkg, name)
        if os.path.exists(p):
            for n in ast.walk(parse(p)):
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) \
                        and any(isinstance(t, ast.Name) and t.id in ("__version__", "version") for t in n.targets):
                    return n.value.value
    return None


def static_package(site, pkg):
    """What a package's sources define, read without importing it."""
    root = os.path.join(site, pkg)
    if not os.path.isdir(root):
        raise Unparseable("no package %s under %s" % (pkg, site))
    doc = {"root": root, "version": package_version(site, pkg), "flags": set(), "envs": set(), "files": []}
    if pkg != "vllm":
        return doc
    engine = os.path.join(root, "engine", "arg_utils.py")
    flags = add_argument_flags(parse(engine))
    if not flags:
        raise Unparseable("%s has no add_argument flags" % engine)
    clis = sorted(glob.glob(os.path.join(root, "entrypoints", "**", "cli_args.py"), recursive=True))
    if not clis:
        raise Unparseable("no vllm/entrypoints/**/cli_args.py (the frontend flags) under %s" % root)
    for p in clis:
        t = parse(p)
        flags |= add_argument_flags(t) | args_class_fields(t)
    envs = env_names(parse(os.path.join(root, "envs.py")))
    doc.update(flags=flags, envs=envs, files=[engine] + clis + [os.path.join(root, "envs.py")])
    return doc


def find_site(pkg, paths):
    """The directory holding the package: a sys.path entry, or for an editable install
    whose finder maps the name, the parent of the location importlib finds. find_spec
    on a top-level name runs the finders only; it does not execute the package."""
    for p in paths:
        if p and os.path.isdir(os.path.join(p, pkg)) and (os.path.exists(os.path.join(p, pkg, "__init__.py"))
                                                          or os.path.isdir(os.path.join(p, pkg, "engine"))):
            return p
    try:
        import importlib.util
        spec = importlib.util.find_spec(pkg)
        locs = list(spec.submodule_search_locations or []) if spec else []
        if locs and os.path.isdir(locs[0]):
            return os.path.dirname(os.path.abspath(locs[0]))
    except (ImportError, ValueError):
        pass
    raise Unparseable("package %s is not on sys.path (%s)" % (pkg, ":".join(x for x in paths if x)))


# ---- comparisons ---------------------------------------------------------------------

def release(v):
    """The X.Y[.Z] a version string starts with (v1.35.0, 0.32.0+cpu, 0.26.0b2,
    1.35.0.20260722-1~ubuntu24.04), or None."""
    m = re.match(r"v?(\d+\.\d+(?:\.\d+)?)", (v or "").strip())
    return m.group(1) if m else None


def fw_date(data):
    """YYYYMMDD from an NPU firmware's version string ("20260630*MTL..." or
    "Jun 30 2026*NPU40xx*", the scripts/k3d-gpu npu_fw_date rule)."""
    m = re.search(rb"([0-9]{8}|[A-Z][a-z]{2} [ 0-9][0-9] [0-9]{4})\*(MTL|NPU)", data)
    if not m:
        return None
    v = m.group(1).decode()
    if v.isdigit():
        return v
    mon, day, year = v.split()
    i = "JanFebMarAprMayJunJulAugSepOctNovDec".find(mon)
    return "%s%02d%02d" % (year, i // 3 + 1, int(day)) if i >= 0 else None


def norm_flag(name, driver):
    name = name.split("=", 1)[0]
    if driver == "vllm" and name.startswith("--"):
        name = "--" + name[2:].replace("_", "-")
    return name


def missing(want, flags, envs, driver, lenient_no=False):
    have = {norm_flag(f, driver) for f in flags}
    out = []
    for w in want:
        if w["kind"] == "env":
            if w["name"] not in envs:
                out.append(w)
            continue
        n = norm_flag(w["name"], driver)
        if n in have or (lenient_no and n.startswith("--no-") and "--" + n[5:] in have):
            continue
        out.append(w)
    return out


def derived_mismatch(derived, facts):
    bad = []
    for d in derived:
        fact = facts.get(d["of"])
        same = fact is not None and (fact == d["value"] if d["of"] == "firmware" else release(fact) == release(d["value"]))
        if not same:
            bad.append(dict(d, image=fact))
    return bad


# ---- host side -----------------------------------------------------------------------

def docker(args, timeout=600):
    try:
        p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout


def run_image(image, args, entrypoint=None, extra=()):
    opts = ["run", "--rm", "--network", "none", *extra]
    if entrypoint:
        opts += ["--entrypoint", entrypoint]
    return docker(opts + [image, *args])


def help_of(image, args, entrypoint=None):
    rc, out = run_image(image, args, entrypoint)
    if rc != 0:
        raise Unparseable("%s%s exited %d: %s" % (entrypoint + " " if entrypoint else "", " ".join(args), rc, tail(out)))
    return help_flags(out)


def tail(text, n=300):
    text = " ".join(text.split())
    return text[-n:]


def inside(image, pkg, canary, python="python3"):
    rc, out = run_image(image, ["/canary/surface.py", "inside", "--pkg", pkg], python, ("-v", "%s:/canary:ro" % canary))
    try:
        doc = json.loads(out[out.index("{"):])
    except ValueError:
        raise Unparseable("surface.py inside exited %d: %s" % (rc, tail(out)))
    if rc != 0 or "error" in doc:
        raise Unparseable(doc.get("error") or "surface.py inside exited %d" % rc)
    return doc


def check(doc, canary):
    image, sv = doc["ref"], doc.get("surface") or {}
    driver = sv.get("driver")
    want = doc.get("flags", [])
    facts, notes, method = {}, [], "help"
    flags, envs, lenient = set(), set(), False
    try:
        if driver == "vllm":
            st = inside(image, "vllm", canary, sv.get("python", "python3"))
            envs, facts["package"] = set(st["envs"]), st["version"]
            try:
                flags = help_of(image, ["serve", "--help=all"], "vllm")
            except Unparseable as e:
                flags, lenient, method = set(st["flags"]), True, "static"
                notes.append("vllm serve --help=all did not run here (%s), flags from the sources" % e)
        elif driver == "llama-server":
            flags = help_of(image, ["--help"])
        elif driver == "llama-convert":
            flags = help_of(image, ["/app/convert_hf_to_gguf.py", "--help"], "python3")
            rc, out = run_image(image, ["--help"], "/app/llama-quantize")
            if "usage:" not in out:
                raise Unparseable("llama-quantize --help printed no usage (rc %d): %s" % (rc, tail(out)))
        elif driver == "ovms":
            flags = help_of(image, ["--help"])
            if sv.get("deb"):
                rc, out = run_image(image, ["-W", "-f", "${Version}", sv["deb"]], "dpkg-query")
                if rc != 0 or not out.strip():
                    raise Unparseable("dpkg-query %s exited %d: %s" % (sv["deb"], rc, tail(out)))
                facts["deb"] = out.strip().splitlines()[-1]
            if sv.get("firmware"):
                facts["firmware"] = firmware_date(image, sv["firmware"])
        elif driver == "gst":
            method = "gst-inspect"
            device_only = tuple(sv.get("device_only", []))
            for w in want:
                if w["kind"] != "element":
                    continue
                el = w["name"]
                if el.startswith(device_only):
                    notes.append("%s registers only on a device: not checked" % el)
                    flags.add(el)
                    flags.update(x["name"] for x in want if x["name"].startswith(el + "."))
                    continue
                rc, out = run_image(image, [el], "gst-inspect-1.0")
                if rc == 0:
                    flags.add(el)
                    flags.update("%s.%s" % (el, p) for p in gst_properties(out))
        elif driver == "sglang":
            flags = help_of(image, ["-m", "sglang.launch_server", "--help"], "python3")
            facts["package"] = inside(image, "sglang", canary)["version"]
        else:
            raise Unparseable("unknown surface driver %r" % driver)
        version = probe_version(doc, facts)
    except Unparseable as e:
        return {"verdict": "unparseable", "detail": "surface: %s" % e, "method": method, "version": None, "missing": []}
    lost = missing(want, flags, envs, driver, lenient_no=lenient)
    bad = derived_mismatch(doc.get("derived", []), facts)
    problems = []
    if lost:
        problems.append("not in the image: " + ", ".join("%s (%s)" % (m["name"], m["source"]) for m in lost))
    for d in bad:
        problems.append("%s says %s, the image's %s is %s" % (d["file"], d["value"], d["of"], d["image"]))
    exp = doc.get("expected_version")
    if exp and version and (release(version) or version) != (release(exp) or exp):
        problems.append("the image reports version %s, its tag %s says %s" % (version, doc.get("tag"), exp))
    ok = "%d names checked (%s)%s" % (len(want), method, ", version %s" % version if version else "")
    if notes:
        ok += "; " + "; ".join(notes)
    if problems:
        return {"verdict": "fail", "detail": "surface: " + "; ".join(problems), "method": method, "version": version,
                "missing": [m["name"] for m in lost]}
    return {"verdict": "pass", "detail": "surface: " + ok, "method": method, "version": version, "missing": []}


def probe_version(doc, facts):
    probe = doc.get("version_probe") or {}
    if "package" in probe:
        v = facts.get("package")
        if not v:
            raise Unparseable("no version for package %s" % probe["package"])
        return v
    if "args" in probe:
        rc, out = run_image(doc["ref"], probe["args"], probe.get("entrypoint"))
        m = re.search(probe["regex"], out)
        if not m:
            raise Unparseable("%s printed no version matching %r (rc %d): %s" % (" ".join(probe["args"]), probe["regex"], rc, tail(out)))
        return m.group("v")
    return None  # a server endpoint: canary/probe.py reads it


def firmware_date(image, path):
    name = "canary-fw-%d" % os.getpid()
    rc, out = docker(["create", "--name", name, image])
    if rc != 0:
        raise Unparseable("docker create: %s" % tail(out))
    try:
        with tempfile.TemporaryDirectory() as d:
            rc, out = docker(["cp", "-L", "%s:%s" % (name, path), os.path.join(d, "fw")])
            if rc != 0:
                raise Unparseable("no %s in the image: %s" % (path, tail(out)))
            with open(os.path.join(d, "fw"), "rb") as f:
                date = fw_date(f.read())
    finally:
        docker(["rm", "-f", name])
    if not date:
        raise Unparseable("no build date in %s" % path)
    return date


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--engine", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--canary", default=os.path.dirname(os.path.abspath(__file__)))
    i = sub.add_parser("inside")
    i.add_argument("--pkg", required=True)
    i.add_argument("--root")
    a = p.parse_args()
    if a.cmd == "inside":
        try:
            site = a.root or find_site(a.pkg, sys.path)
            doc = static_package(site, a.pkg)
        except Unparseable as e:
            print(json.dumps({"error": str(e)}))
            return 3
        print(json.dumps({"root": doc["root"], "version": doc["version"], "flags": sorted(doc["flags"]),
                          "envs": sorted(doc["envs"])}))
        return 0
    with open(a.engine, encoding="utf-8") as f:
        doc = json.load(f)
    res = check(doc, os.path.abspath(a.canary))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(res["detail"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

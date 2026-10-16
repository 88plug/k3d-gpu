#!/usr/bin/env python3
"""Property-based tests for scripts/k3d-gpu's input handling.

Random inputs come from seeded generators; every result is checked against a
Python reference oracle. A failing input is shrunk (greedy delta debugging) to a
minimal one and printed with a shell repro. Failures that match a documented
launcher bug in KNOWN are reported but do not fail the run; each KNOWN entry also
has a pinned input that must still reproduce it, so a fixed bug fails the run
until its entry is removed (and the property then guards the fix).

Usage: tests/fuzz/props.py            (FUZZ_SEED=<int> to replay, FUZZ_N=<int> cases)
Stdlib only; no cluster, no network. Runs the launcher's functions via shim.sh.
"""

import itertools
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SHIM = os.path.join(HERE, "shim.sh")
LAUNCHER = os.path.join(REPO, "scripts", "k3d-gpu")
VENDORS = ["nvidia", "intel", "npu", "gaudi", "dsa", "iaa", "qat", "amx", "hybrid"]
SEED = int(os.environ.get("FUZZ_SEED", random.SystemRandom().randrange(2**32)))
N = int(os.environ.get("FUZZ_N", "300"))
WORK = tempfile.mkdtemp(prefix="k3d-gpu-fuzz-")
# cpulist_expand word-splits its input unquoted, so glob characters expand
# against the working directory. A file named "7" makes that observable.
CWD = os.path.join(WORK, "cwd")
os.makedirs(CWD)
open(os.path.join(CWD, "7"), "w").close()
# Oracle bound on CPU numbers; generators stay far below it.
MAX_CPU = 8191
EMPTY_ROOT = os.path.join(WORK, "empty-root")
os.makedirs(EMPTY_ROOT)


def q(s):
    """Bash $'...' quoting, readable for control characters."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "$'" + "".join(out) + "'"


def shim(mode, records, sysroot=None):
    """Run shim.sh over NUL-separated records; returns [(rc, output)]."""
    args = ["bash", SHIM, mode] + ([sysroot] if sysroot else [])
    data = "".join(r + "\0" for r in records).encode("utf-8", "surrogateescape")
    res = subprocess.run(args, input=data, capture_output=True, cwd=CWD, timeout=120)
    if res.returncode != 0:
        raise RuntimeError("shim %s failed: %s" % (mode, res.stderr.decode(errors="replace")))
    out = []
    for rec in res.stdout.decode("utf-8", "surrogateescape").split("\0")[:-1]:
        rc, _, text = rec.partition("|")
        out.append((int(rc), text))
    return out


# ---- reference oracles --------------------------------------------------------

def parse_cpulist(s):
    """Kernel %*pbl list ("0-3,8") -> list of CPUs in list order, or None if invalid.

    Only what the kernel prints is valid: decimal, no leading zeros, no
    whitespace, a-b with a <= b, no empty entries.
    """
    if s == "":
        return None
    cpus = []  # numbers above MAX_CPU are rejected so the oracle stays cheap
    for part in s.split(","):
        m = re.fullmatch(r"(0|[1-9][0-9]*)(?:-(0|[1-9][0-9]*))?", part)
        if not m:
            return None
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) is not None else lo
        if hi < lo or hi > MAX_CPU:
            return None
        cpus.extend(range(lo, hi + 1))
    return cpus


def oracle_vendors(req):
    """Intended K3D_GPU_VENDORS semantics: whitespace (any kind) is ignored, entries
    are comma-separated, empty entries skipped, duplicates collapsed in first-seen
    order, any unknown entry is an error naming it. Returns (ok, active|bad-entry)."""
    flat = re.sub(r"\s", "", req)
    if flat in ("", "auto"):
        return True, []  # auto mode; the test sysroot is empty, so nothing is detected
    active = []
    for v in flat.split(","):
        if v == "":
            continue
        if v not in VENDORS:
            return False, v
        if v not in active:
            active.append(v)
    return True, active


def launcher_vendors(req):
    """Model of resolve_vendors as written (scripts/k3d-gpu): only spaces are
    removed, and `read` stops at the first newline. Used to classify a failure as
    one of the documented bugs only when the launcher does exactly this."""
    if req in ("", "auto"):
        return (0, "")
    first = req.replace(" ", "").split("\n")[0]
    active = []
    fields = first.split(",")
    if fields and fields[-1] == "":
        fields.pop()  # read -a drops one trailing empty field
    for v in fields:
        if v == "":
            continue
        if v not in VENDORS:
            return (1, v)
        if v not in active:
            active.append(v)
    return (0, " ".join(active))


def observed_vendors(req):
    rc, text = shim("vendors", [req], EMPTY_ROOT)[0]
    if rc == 0:
        return (0, text)
    m = re.search(r"unknown vendor '(.*)' in K3D_GPU_VENDORS", text, re.S)
    return (1, m.group(1) if m else text)


# ---- generators --------------------------------------------------------------

def gen_cpulist(r, maxcpu=255):
    """A valid, possibly non-canonical (unsorted, overlapping) CPU list."""
    parts = []
    for _ in range(r.randint(1, 6)):
        lo = r.randint(0, maxcpu)
        if r.random() < 0.5:
            parts.append(str(lo))
        else:
            parts.append("%d-%d" % (lo, min(maxcpu, lo + r.randint(0, 16))))
    return ",".join(parts)


def gen_subset_pair(r):
    """(a, b) where a is often a subset of b, so both outcomes are exercised."""
    b = gen_cpulist(r, 63)
    bs = sorted(set(parse_cpulist(b)))
    picks = r.sample(bs, r.randint(1, min(4, len(bs))))
    if r.random() < 0.4:
        picks.append(r.randint(0, 70))
    return ",".join(str(c) for c in picks), b


HOSTILE_TOKENS = ["", "-", ",", " ", "\t", "\n", "*", "?", "[0-9]", "07", "3-1", "5-", "-2",
                  "1e2", "0x10", "a", "1-2-3", "18446744073709551616", "99999999999999999999999",
                  "1,,2", "$(id)", "`id`", "${HOME}", "~", "!", "\\", "'", '"']


def gen_hostile_cpulist(r):
    """A string that is NOT a kernel CPU list: valid pieces mixed with junk."""
    while True:
        bits = []
        for _ in range(r.randint(1, 4)):
            bits.append(r.choice(HOSTILE_TOKENS) if r.random() < 0.6 else gen_cpulist(r, 31))
        s = r.choice([",", "", " "]).join(bits)
        if s and parse_cpulist(s) is None and len(s) < 80 and not huge_range(s):
            return s


def huge_range(s):
    """True when the launcher's own a-b split would hand seq a span over 4096
    (that case belongs to the `bounded` property, which runs under a timeout)."""
    for part in re.split(r"[ ,\t\n]", s):
        if "-" in part:
            lo, hi = part.rsplit("-", 1)[0], part.split("-", 1)[1]
            try:
                if float(hi) - float(lo) > 4096:
                    return True
            except ValueError:
                pass
    return False


def gen_huge_pair(r):
    """A kernel-shaped list pair where one side holds a range of 10^8+ CPUs."""
    lo = r.randint(0, 7)
    huge = "%d-%d" % (lo, lo + 10 ** r.randint(8, 25))
    small = gen_cpulist(r, 7)
    return (huge, small) if r.random() < 0.5 else (small, huge)


def gen_vendor_request(r):
    toks = []
    for _ in range(r.randint(1, 6)):
        x = r.random()
        if x < 0.7:
            t = r.choice(VENDORS)
        elif x < 0.8:
            t = r.choice(["NVIDIA", "gpu", "auto", "nvidia2", "intel-gpu", "cpu"])
        else:
            t = ""
        pad = lambda: "".join(r.choice(" \t\n") if r.random() < 0.15 else " " * r.randint(0, 1)
                              for _ in range(r.randint(0, 2)))
        toks.append(pad() + t + pad())
    return ",".join(toks)


def gen_image(r):
    """Image references: mostly valid OCI references, some hostile strings."""
    if r.random() < 0.6:
        name = "/".join(r.choice(["busybox", "library", "nvidia", "cuda", "a.b", "x_y", "z-1"])
                        for _ in range(r.randint(1, 3)))
        if r.random() < 0.4:
            name = r.choice(["ghcr.io/", "localhost:5000/", "registry.example.com/"]) + name
        if r.random() < 0.7:
            name += ":" + r.choice(["1.37", "latest", "13.4.2-base-ubuntu26.04", "v1_2"])
        if r.random() < 0.2:
            name += "@sha256:" + "".join(r.choice("0123456789abcdef") for _ in range(64))
        return name
    alphabet = ['"', "\\", "\n", "\t", "\x01", "{", "}", ":", ",", "'", "$", "é", "😀", "a", "1", "/"]
    return "".join(r.choice(alphabet) for _ in range(r.randint(1, 12)))


def mutate(r, base):
    """A sysfs attribute value: the kernel's "<value>\\n" or a hostile variant."""
    if r.random() < 0.6:
        return base + "\n"
    return r.choice([
        base, base + " ", " " + base, base + "\n\n", "\n" + base, base + "\t",
        base + "\n" + r.choice(["0x8086", "0x1da3", "0x10de"]),
        base + " " + r.choice(["0x8086", "0x1da3"]), "", "\n", " ",
        base * r.randint(2, 3), "0x" + "f" * r.randint(1, 4096), base.upper(),
    ])


def gen_sysfs(r):
    def dev(drivers):
        return {"vendor": mutate(r, r.choice(["0x8086", "0x1da3", "0x10de"])),
                "device": mutate(r, r.choice(["0x7d55", "0x643e", "0x1060", "0xe20b"])),
                "driver": r.choice(drivers)}
    cpus = lambda: None if r.random() < 0.2 else mutate(r, gen_cpulist(r, 31))
    flags = ["fpu", "vme", "amx_tile", "amx_bf16", "amx_int8", "amx_fp16", "sse4_2"]
    cpuinfo = None
    if r.random() < 0.8:
        fl = " ".join(r.sample(flags, r.randint(0, len(flags))))
        if r.random() < 0.3:
            fl += r.choice([" amx_A.b/c", " amx_x@y;z", " amx_", "\tamx_tile", " amx_é"])
        cpuinfo = r.choice(["processor\t: 0\n", ""]) + "flags\t\t: " + fl + "\n" \
            + r.choice(["", "flags\t\t: amx_complex\n"])
    return {
        "render": [dev([None, "xe", "i915", "amdgpu"]) for _ in range(r.randint(0, 2))],
        "accel": [dev([None, "intel_vpu", "habanalabs"]) for _ in range(r.randint(0, 2))],
        "core": cpus(), "atom": cpus(), "cpuinfo": cpuinfo,
    }


# ---- sysfs trees ---------------------------------------------------------------

def sysfs_files(t):
    """{relative path: content} plus [(link, target)] for a generated host."""
    files, links, n = {}, [], 0
    for kind, cls, prefix in (("render", "drm", "renderD"), ("accel", "accel", "accel")):
        for i, d in enumerate(t[kind]):
            pci = "sys/devices/pci0000:00/0000:00:%02x.0" % n
            n += 1
            files[pci + "/vendor"] = d["vendor"]
            files[pci + "/device"] = d["device"]
            idx = 128 + i if kind == "render" else i
            links.append(("sys/class/%s/%s%d/device" % (cls, prefix, idx), pci))
            if d["driver"]:
                files["sys/bus/pci/drivers/%s/.keep" % d["driver"]] = ""
                links.append((pci + "/driver", "sys/bus/pci/drivers/" + d["driver"]))
    for kind in ("core", "atom"):
        if t[kind] is not None:
            files["sys/devices/cpu_%s/cpus" % kind] = t[kind]
    if t["cpuinfo"] is not None:
        files["proc/cpuinfo"] = t["cpuinfo"]
    return files, links


def build_tree(t, root):
    files, links = sysfs_files(t)
    for rel, content in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(content)
    for rel, target in links:
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        os.symlink(os.path.join(root, target), os.path.join(root, rel))


def tree_script(t):
    files, links = sysfs_files(t)
    lines = ['R=$(mktemp -d)']
    for rel, content in files.items():
        lines.append('mkdir -p "$R/%s"; printf %%s %s > "$R/%s"' % (os.path.dirname(rel), q(content), rel))
    for rel, target in links:
        lines.append('mkdir -p "$R/%s"; ln -s "$R/%s" "$R/%s"' % (os.path.dirname(rel), target, rel))
    lines.append('K3D_GPU_SYSROOT="$R" scripts/k3d-gpu detect')
    return "\n    ".join(lines)


def oracle_sysfs(t):
    """Intended detection: attribute values compared after the trailing newline the
    kernel appends is removed; any other byte, whitespace included, is part of the value."""
    val = lambda s: s.rstrip("\n")
    found = []
    if any(val(d["vendor"]) == "0x8086" and d["driver"] in ("xe", "i915") for d in t["render"]):
        found.append("intel")
    if any(val(d["vendor"]) == "0x8086" and d["driver"] == "intel_vpu" for d in t["accel"]):
        found.append("npu")
    if any(val(d["vendor"]) == "0x1da3" for d in t["accel"]):
        found.append("gaudi")
    if t["cpuinfo"] is not None:
        line = next((ln for ln in t["cpuinfo"].split("\n") if ln.startswith("flags")), "")
        # kernel flag names use [a-z0-9_] only (x86_cap_flags); other tokens are not flags
        if any(re.fullmatch(r"amx_[a-z0-9_]+", tok) for tok in line.partition(":")[2].split(" ")):
            found.append("amx")
    if t["core"] is not None and t["atom"] is not None and val(t["core"]) and val(t["atom"]):
        found.append("hybrid")
    return found


# ---- properties ------------------------------------------------------------------
# check(values) -> list of None (pass) or a short failure description.

FUNCS = 'source <(sed "/^case \\"\\${1:-help}\\" in$/,\\$d" scripts/k3d-gpu); set +e; '


def check_expand(vals):
    res = shim("expand", vals)
    out = []
    for v, (_, text) in zip(vals, res):
        want = " ".join(map(str, parse_cpulist(v)))
        out.append(None if text == want else "expanded to %r, oracle %r" % (text[:80], want[:80]))
    return out


def check_within(pairs):
    res = shim("within", [x for p in pairs for x in p])
    out = []
    for (a, b), (rc, _) in zip(pairs, res):
        want = set(parse_cpulist(a)) <= set(parse_cpulist(b))
        out.append(None if (rc == 0) == want else "returned %d, oracle says %s" % (rc, want))
    return out


def check_within_fail_closed(pairs):
    res = shim("within", [x for p in pairs for x in p])
    return [None if rc != 0 else "accepted a malformed CPU list (exit 0)" for (_, b), (rc, _) in zip(pairs, res)]


BOUND_SECONDS = 3


def check_bounded(pairs):
    """cpulist_within must answer quickly whatever the list sizes."""
    out = []
    for a, b in pairs:
        # Own process group, so the timeout kills seq too, not just bash.
        p = subprocess.Popen(["bash", "-c", FUNCS + 'cpulist_within "$1" "$2"', "_", a, b], cwd=REPO,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            p.wait(timeout=BOUND_SECONDS)
            out.append(None)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            out.append("timeout: no answer within %ds" % BOUND_SECONDS)
    return out


def check_vendors(vals):
    res = shim("vendors", vals, EMPTY_ROOT)
    out = []
    for v, (rc, text) in zip(vals, res):
        ok, want = oracle_vendors(v)
        if ok and (rc != 0 or text.split(" ") != (want or [""])):
            out.append("rc=%d active=%r, oracle %r" % (rc, text[:120], want))
        elif not ok and (rc == 0 or "unknown vendor '%s'" % want not in text):
            out.append("rc=%d out=%r, oracle: error naming %r" % (rc, text[:120], want))
        else:
            out.append(None)
    return out


REFERENCE_IMAGE = "busybox:1.37"
_reference = []


def overrides_reference():
    if not _reference:
        rc, text = shim("overrides", [REFERENCE_IMAGE], EMPTY_ROOT_HYBRID)[0]
        if rc != 0:
            return "cmd_test exited %d with image %s" % (rc, REFERENCE_IMAGE)
        try:
            docs = [json.loads(line) for line in text.split("\n")]
        except ValueError as e:
            return "overrides for image %s are not JSON (%s): %s" % (REFERENCE_IMAGE, e, text[:300])
        if len(docs) != 11:
            return "expected 11 test pods with every vendor active, got %d" % len(docs)
        for d in docs:
            cs = d.get("spec", {}).get("containers", [])
            if len(cs) != 1 or cs[0].get("image") != REFERENCE_IMAGE or not cs[0].get("command"):
                return "test pod spec lacks one container running %s: %s" % (REFERENCE_IMAGE, json.dumps(d))
        _reference.extend(docs)
    return _reference


def swap_image(doc, img):
    """The reference document with every container image set to img."""
    doc = json.loads(json.dumps(doc))
    for c in doc["spec"]["containers"]:
        c["image"] = img
    return doc


OCI_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]*")


def check_overrides(vals):
    ref = overrides_reference()
    if isinstance(ref, str):
        return ["reference: " + ref] * len(vals)
    res = shim("overrides", vals, EMPTY_ROOT_HYBRID)
    out = []
    for v, (rc, text) in zip(vals, res):
        # Refusing a string outside the OCI reference alphabet is as safe as escaping
        # it (such an image can never be pulled); refusing a valid ref is a bug.
        if rc != 0 and text == "":  # cmd_test refused before building any pod spec
            out.append(None if not OCI_REF.fullmatch(v) else "valid image ref %r refused" % v)
            continue
        lines = text.split("\n")
        try:
            docs = [json.loads(line) for line in lines]
        except ValueError as e:
            out.append("overrides are not JSON (%s)" % e)
            continue
        if len(docs) != len(ref):
            out.append("%d documents, expected %d" % (len(docs), len(ref)))
        else:
            bad = [i for i, (d, r) in enumerate(zip(docs, ref)) if d != swap_image(r, v)]
            out.append("document %d differs from the reference spec: %s" % (bad[0], lines[bad[0]][:200])
                       if bad else None)
    return out


LABEL = re.compile(r"feature\.node\.kubernetes\.io/cpu-cpuid\.[A-Z0-9]+=true")


def check_sysfs(trees):
    out = []
    for i, t in enumerate(trees):
        root = os.path.join(WORK, "tree")
        shutil.rmtree(root, ignore_errors=True)
        build_tree(t, root)
        env = dict(os.environ, K3D_GPU_SYSROOT=root)
        env.pop("K3D_GPU_VENDORS", None)
        res = subprocess.run(["bash", LAUNCHER, "detect"], capture_output=True, cwd=CWD, env=env, timeout=60)
        got = [line.split(" ")[0] for line in res.stdout.decode(errors="replace").splitlines()]
        want = oracle_sysfs(t)
        if res.returncode != 0:
            out.append("detect exited %d: %s" % (res.returncode, res.stderr.decode(errors="replace")[:200]))
            continue
        if got != want:
            out.append("detected %s, oracle %s" % (got, want))
            continue
        labels = shim("amx_labels", [], root)[0][1]
        badl = [ln for ln in labels.split("\n") if ln and not LABEL.fullmatch(ln)]
        out.append("invalid AMX node label %r" % badl[0] if badl else None)
    return out


# ---- known launcher bugs -----------------------------------------------------------
# id -> (property, matcher(value, failure), pinned input, one-line description)

def hostile_ws(t):
    vals = [d[k] for kind in ("render", "accel") for d in t[kind] for k in ("vendor",)]
    vals += [t[k] for k in ("core", "atom") if t[k] is not None]
    return any(re.search(r"[ \t]|\n(?!\n*$)", s) for s in vals)


KNOWN = {
    # Launcher bugs still open go here: name -> (property, match(value, failure),
    # pinned repro, description). Every bug this harness found is fixed; the
    # properties now guard them.
}

# ---- shrinking ----------------------------------------------------------------------

def candidates(v):
    """Strictly simpler variants of v, coarsest first. Every candidate is shorter,
    has a smaller number in it, or has fewer non-alphanumeric characters, so
    shrinking terminates."""
    if isinstance(v, str):
        fields = v.split(",")
        if len(fields) > 1:  # whole list entries first
            for i in range(len(fields)):
                yield ",".join(fields[:i] + fields[i + 1:])
        n = len(v)
        size = n // 2
        while size >= 1:
            for i in range(0, n - size + 1, size):
                yield v[:i] + v[i + size:]
            size //= 2
        for m in re.finditer(r"[0-9]+", v):  # smaller numbers
            for smaller in ("0", str(int(m.group()) // 2)):
                if int(smaller) < int(m.group()) or len(smaller) < len(m.group()):
                    yield v[:m.start()] + smaller + v[m.end():]
        for i, ch in enumerate(v):
            if not ch.isalnum():
                yield v[:i] + "a" + v[i + 1:]
    elif isinstance(v, tuple):
        for i, x in enumerate(v):
            for c in candidates(x):
                yield v[:i] + (c,) + v[i + 1:]
    elif isinstance(v, list):
        for i in range(len(v)):
            yield v[:i] + v[i + 1:]
        for i, x in enumerate(v):
            for c in candidates(x):
                yield v[:i] + [c] + v[i + 1:]
    elif isinstance(v, dict):
        for k, x in v.items():  # drop optional parts first
            if x is not None and k in ("core", "atom", "cpuinfo", "driver"):
                yield dict(v, **{k: None})
        for k, x in v.items():  # then drop list entries
            if isinstance(x, list):
                for i in range(len(x)):
                    yield dict(v, **{k: x[:i] + x[i + 1:]})
        for k, x in v.items():
            if x is not None:
                for c in candidates(x):
                    yield dict(v, **{k: c})


def shrink(prop, value, failure, still_valid, keep=None, budget=1500):
    """Greedy delta debugging: adopt the first simpler candidate that still fails
    the same way (same failure kind, or keep(candidate, failure) when given),
    until no candidate does or `budget` candidates have been run."""
    kind = failure.split(" ")[0]
    keep = keep or (lambda c, f: f.split(" ")[0] == kind)
    runs = 0
    progress = True
    while progress and runs < budget:
        progress = False
        pool = (c for c in candidates(value) if still_valid(c))
        while runs < budget:
            chunk = list(itertools.islice(pool, 8))  # small chunks: stop at the first hit
            if not chunk:
                break
            runs += len(chunk)
            hit = next(((c, f) for c, f in zip(chunk, prop(chunk)) if f is not None and keep(c, f)), None)
            if hit:
                value, failure = hit
                progress = True
                break
    return value, failure


# ---- driver ----------------------------------------------------------------------------

def repro(name, v):
    if name in ("expand",):
        return "bash -c '%scpulist_expand \"$1\"' _ %s" % (FUNCS.replace("'", "'\\''"), q(v))
    if name in ("within", "within_fail_closed", "bounded"):
        return "bash -c '%scpulist_within \"$1\" \"$2\"; echo $?' _ %s %s" % (
            FUNCS.replace("'", "'\\''"), q(v[0]), q(v[1]))
    if name == "vendors":
        return "K3D_GPU_VENDORS=%s K3D_GPU_SYSROOT=$(mktemp -d) scripts/k3d-gpu detect" % q(v)
    if name == "overrides":
        return ("K3D_GPU_DEVICE_TEST_IMAGE=%s scripts/k3d-gpu test   "
                "(inspect the --overrides argument run_pod passes to kubectl run)" % q(v))
    return tree_script(v)


PROPS = [
    # name, check, generator, still_valid (constrains shrinking to the property's domain), cases,
    # shrink budget (candidate runs; 0 for properties whose failure costs a timeout)
    ("expand", check_expand, gen_cpulist, lambda v: parse_cpulist(v) is not None, N, 1500),
    ("within", check_within, gen_subset_pair,
     lambda v: parse_cpulist(v[0]) is not None and parse_cpulist(v[1]) is not None, N, 1500),
    ("within_fail_closed", check_within_fail_closed,
     lambda r: (gen_hostile_cpulist(r), gen_cpulist(r, 31)),
     lambda v: v[0] != "" and parse_cpulist(v[0]) is None and parse_cpulist(v[1]) is not None, N, 1500),
    ("bounded", check_bounded, gen_huge_pair, lambda v: True, 2, 0),
    ("vendors", check_vendors, gen_vendor_request, lambda v: True, N, 1500),
    ("overrides", check_overrides, gen_image, lambda v: v != "", max(20, N // 5), 1500),
    ("sysfs", check_sysfs, gen_sysfs, lambda v: True, max(40, N // 2), 1500),
]


def main():
    sys.stdout.reconfigure(line_buffering=True)
    global EMPTY_ROOT_HYBRID
    EMPTY_ROOT_HYBRID = os.path.join(WORK, "hybrid-root")
    build_tree({"render": [], "accel": [], "core": "0-3\n", "atom": "0-7\n",
                "cpuinfo": "flags\t\t: fpu amx_tile\n"}, EMPTY_ROOT_HYBRID)
    print("seed %d (replay: FUZZ_SEED=%d %s)" % (SEED, SEED, " ".join(sys.argv)))
    checks = {name: chk for name, chk, *_ in PROPS}
    fails = 0
    seen_known = {}
    for name, chk, gen, valid, n, budget in PROPS:
        r = random.Random("%d/%s" % (SEED, name))
        vals = [gen(r) for _ in range(n)]
        results = chk(vals)
        unknown_here = 0
        for v, f in zip(vals, results):
            if f is None:
                continue
            known = next((k for k, (p, match, *_) in KNOWN.items() if p == name and match(v, f)), None)
            if known:
                if known not in seen_known:  # shrink the first hit of each known bug
                    match = KNOWN[known][1]
                    mv, mf = shrink(chk, v, f, valid, lambda c, cf, m=match: m(c, cf), budget)
                    seen_known[known] = [0, mv]
                seen_known[known][0] += 1
                continue
            unknown_here += 1
            if unknown_here > 3:
                continue  # report a few distinct minimal repros per property, not hundreds
            mv, mf = shrink(chk, v, f, valid, budget=budget)
            fails += 1
            print("FAIL %s: %s\n  minimal input: %r\n  repro:\n    %s" % (name, mf, mv, repro(name, mv)))
        bad = sum(f is not None for f in results)
        print("%-4s %-19s %d cases, %d failing (%d known)" % (
            "ok" if unknown_here == 0 else "FAIL", name, n, bad, bad - unknown_here))
    for k, (p, match, pinned, desc) in KNOWN.items():
        f = checks[p]([pinned])[0]
        if f is None or not match(pinned, f):
            fails += 1
            print("XPASS known bug %s no longer reproduces (%s): remove it from KNOWN" % (k, desc))
        else:
            hits, shrunk = seen_known.get(k, [0, None])
            print("known %-17s %s\n      pinned: %s\n      repro: %s" % (
                k, desc, f, repro(p, pinned).replace("\n", "\n      ")))
            if hits:
                print("      hits this run: %d, smallest shrunk: %r" % (hits, shrunk))
    shutil.rmtree(WORK, ignore_errors=True)
    print("%s: %d unexpected failure(s)" % ("FAIL" if fails else "PASS", fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

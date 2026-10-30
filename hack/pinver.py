#!/usr/bin/env python3
"""pinver: version schemes, ranges and update types for the pins k3d-gpu tracks.

A library (hack/known-issues, hack/ledger-freshness and the freshness tooling
import it) and a small CLI. Python standard library only.

Schemes:
  semver         1.2.3, v1.2.3, 1.2 (padded); a prerelease (-rc.1, rc1) sorts
                 before its release; build metadata (+x) is ignored. Breaking: major.
  zerover        semver, but while the major is 0 a minor is breaking (vLLM 0.x).
  k8s            v1.37.1+k3s1 or v1.37.1-k3s1, -rcN prereleases; the distro build
                 (k3s1, k3s2) orders after the patch. Breaking: minor and major.
  llama          llama.cpp: build tags bNNNNN (server-b11960, full-b11960 read as
                 b11960), compared numerically, any change a non-breaking minor;
                 and vX.Y.Z releases under zerover rules. A build and a release
                 are incomparable.
  calver         2026.4.1 (2026.4 = 2026.4.0); year = major. Breaking: major.
  pep440         Python versions: epoch, release, aN/bN/rcN, .postN, .devN, +local.
                 A range without a local label ignores the candidate's. Breaking: major.
  docker-suffix  numeric core with a prefix and suffix that must match:
                 3.14.8-slim, contrib-v1.40.0. Tags of another flavour are
                 incomparable (latest() skips them); a range reads the core
                 only. Breaking: major.
  eat            Intel EAT release tags: release-1.1.0, and release-1.0.3-hotfix
                 (after release-1.0.3), one stream. Breaking: major.
  apt            Debian versions (dpkg order: epoch, upstream, revision, ~ before
                 the end). Breaking: major of the upstream's numeric prefix.
  hf-sha         a Hugging Face revision: no ordering; ranges are * and ==/!= only.

API (stable; later tooling imports it):
  parse(scheme, v) -> Version          compare(scheme, a, b) -> -1 | 0 | 1
  comparable(scheme, a, b) -> bool     is_prerelease(scheme, v) -> bool
  in_range(scheme, v, spec) -> bool    check_range(scheme, spec)  (raises ValueError)
  update_type(scheme, old, new) -> "digest" | "patch" | "minor" | "major"
  breaking(scheme, old, new) -> bool   classify(scheme, old, new) -> {type, breaking}
  latest(scheme, current, candidates) -> newest stable comparable candidate
                                         above current, or None

update_type reports where two versions differ ("digest" when they are the same
version); breaking applies the scheme's rule, so a zerover 0.24 -> 0.25 is a
"minor" that is breaking. Range spec: "*", or clauses joined by "||", each a
comma-separated list of <, <=, >, >=, ==, != comparators ("<0.25.0",
">=0.24.0,<0.25.0", "<1.95.1 || >=1.96.0,<1.96.2"). Bad input raises ValueError.

CLI:
  pinver.py parse SCHEME V                 JSON of the parsed version
  pinver.py compare SCHEME A B             -1, 0 or 1
  pinver.py in-range SCHEME V SPEC         exit 0 inside, 1 outside
  pinver.py update-type SCHEME OLD NEW     "minor breaking", "patch", ...
  pinver.py latest SCHEME CURRENT CAND...  the newest stable candidate, or nothing
Exit 2 on bad input.
"""

import functools
import json
import re
import sys

SCHEMES = ("semver", "zerover", "k8s", "llama", "calver", "pep440", "docker-suffix", "eat", "apt", "hf-sha")
TYPES = ("digest", "patch", "minor", "major")


class Version:
    """A parsed version. key orders versions of one flavour; release is the
    numeric (major, minor, patch, ...) tuple update_type reads."""

    __slots__ = ("scheme", "raw", "flavour", "key", "release", "pre", "local")

    def __init__(self, scheme, raw, flavour, key, release, pre, local=None):
        self.scheme, self.raw, self.flavour, self.key = scheme, raw, flavour, key
        self.release, self.pre, self.local = release, pre, local

    def as_dict(self):
        return {"scheme": self.scheme, "raw": self.raw, "flavour": list(self.flavour) if self.flavour else None,
                "release": list(self.release), "prerelease": self.pre}

    def __repr__(self):
        return "Version(%s %r)" % (self.scheme, self.raw)


def _bad(scheme, v, why="not a version"):
    return ValueError("%s: %r is %s" % (scheme, v, why))


def _pad(nums, n=3):
    nums = tuple(nums)
    return nums + (0,) * (n - len(nums)) if len(nums) < n else nums


# --- semver / zerover -----------------------------------------------------------

SEMVER_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z][0-9A-Za-z.-]*)|((?:rc|alpha|beta|a|b|dev|pre)\.?\d+[0-9A-Za-z.]*))?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$")


def _pre_ids(text):
    out = []
    for part in re.split(r"[.-]", text):
        if not part:
            raise ValueError("empty prerelease identifier")
        out.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(out)


def _semver(scheme, v):
    m = SEMVER_RE.match(v)
    if not m:
        raise _bad(scheme, v)
    rel = _pad(int(x) for x in m.group(1, 2, 3) if x is not None)
    pre_text = m.group(4) or m.group(5)
    try:
        pre_key = (0,) + _pre_ids(pre_text) if pre_text else (1,)
    except ValueError:
        raise _bad(scheme, v) from None
    return Version(scheme, v, ("",), (rel, pre_key), rel, bool(pre_text))


# --- k8s -----------------------------------------------------------------------

K8S_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.?(\d+))?(?:[+-]([a-z][a-z0-9]*?)(\d+))?$")
K8S_PRE = {"alpha": 0, "beta": 1, "rc": 2}


def _k8s(scheme, v):
    m = K8S_RE.match(v)
    if not m:
        raise _bad(scheme, v)
    rel = tuple(int(x) for x in m.group(1, 2, 3))
    pre = (0, K8S_PRE[m.group(4)], int(m.group(5))) if m.group(4) else (1, 0, 0)
    distro = m.group(6) or ""
    build = int(m.group(7)) if m.group(7) else 0
    return Version(scheme, v, (distro,), (rel, pre, build), rel, bool(m.group(4)))


# --- llama.cpp -----------------------------------------------------------------

LLAMA_BUILD_RE = re.compile(r"^(?:[a-z][a-z0-9]*(?:-[a-z0-9]+)*-)?b(\d+)$")


def _llama(scheme, v):
    m = LLAMA_BUILD_RE.match(v)
    if m:
        n = int(m.group(1))
        return Version(scheme, v, ("build",), (n,), (n,), False)
    if not re.match(r"^v\d+\.\d+\.\d+$", v):
        raise _bad(scheme, v)
    s = _semver(scheme, v)
    return Version(scheme, v, ("release",), s.key, s.release, s.pre)


# --- calver --------------------------------------------------------------------

CALVER_RE = re.compile(r"^(\d{4})\.(\d+)(?:\.(\d+))?$")


def _calver(scheme, v):
    m = CALVER_RE.match(v)
    if not m:
        raise _bad(scheme, v)
    rel = _pad(int(x) for x in m.group(1, 2, 3) if x is not None)
    return Version(scheme, v, ("",), (rel,), rel, False)


# --- pep440 --------------------------------------------------------------------

PEP440_RE = re.compile(
    r"^v?(?:(\d+)!)?(\d+(?:\.\d+)*)"
    r"(?:[-_.]?(a|b|rc|c|alpha|beta|pre|preview)[-_.]?(\d+)?)?"
    r"(?:-(\d+)|[-_.]?(post|rev|r)[-_.]?(\d+)?)?"
    r"(?:[-_.]?(dev)[-_.]?(\d+)?)?"
    r"(?:\+([a-z0-9]+(?:[-_.][a-z0-9]+)*))?$", re.I)
PEP440_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


def _pep440(scheme, v):
    m = PEP440_RE.match(v.strip())
    if not m:
        raise _bad(scheme, v)
    epoch = int(m.group(1) or 0)
    release = tuple(int(x) for x in m.group(2).split("."))
    trimmed = release
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed = trimmed[:-1]
    pre_l, pre_n = m.group(3), m.group(4)
    post_n = m.group(5) or (m.group(7) or "0" if m.group(6) else None)
    dev = m.group(8) is not None
    dev_n = int(m.group(9) or 0)
    if pre_l:
        pre = (PEP440_PRE[pre_l.lower()], int(pre_n or 0))
    elif post_n is None and dev:
        pre = (-1, 0)  # 1.0.dev1 sorts before 1.0a1
    else:
        pre = (9, 0)
    post = (int(post_n),) if post_n is not None else (-1,)
    devk = (0, dev_n) if dev else (1, 0)
    local = None
    lockey = (0,)
    if m.group(10):
        local = m.group(10).lower()
        segs = tuple((1, int(s), "") if s.isdigit() else (0, 0, s) for s in re.split(r"[-_.]", local))
        lockey = (1, segs)
    key = (epoch, trimmed, pre, post, devk, lockey)
    return Version(scheme, v, ("",), key, _pad(release), bool(pre_l) or dev, local)


# --- docker-suffix -------------------------------------------------------------

DOCKER_RE = re.compile(r"^(\D*)(\d+(?:\.\d+)*)(.*)$")
DOCKER_PRE_RE = re.compile(r"^[-.]?(?:rc|alpha|beta|a|b|dev)\d", re.I)


def _docker(scheme, v):
    m = DOCKER_RE.match(v)
    if not m or not v:
        raise _bad(scheme, v)
    prefix, core, suffix = m.groups()
    nums = tuple(int(x) for x in core.split("."))
    rel = _pad(nums)
    return Version(scheme, v, (prefix, suffix), (_pad(nums, 8),), rel, bool(DOCKER_PRE_RE.match(suffix)))


# --- eat -----------------------------------------------------------------------

EAT_RE = re.compile(r"^release-(\d+)\.(\d+)\.(\d+)(-hotfix)?$")


def _eat(scheme, v):
    m = EAT_RE.match(v)
    if not m:
        raise _bad(scheme, v)
    rel = tuple(int(x) for x in m.group(1, 2, 3))
    return Version(scheme, v, ("",), (rel, bool(m.group(4))), rel, False)


# --- apt (dpkg) ----------------------------------------------------------------

APT_RE = re.compile(r"^(?:(\d+):)?([0-9][A-Za-z0-9.+~:-]*)$")


def _dpkg_order(c):
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a, b):
    """dpkg's verrevcmp: alternating non-digit and digit runs."""
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _dpkg_order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _dpkg_order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return -1 if ac < bc else 1
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = (a[i] > b[j]) - (a[i] < b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


_DPKG_KEY = functools.cmp_to_key(_verrevcmp)


def _apt(scheme, v):
    m = APT_RE.match(v)
    if not m:
        raise _bad(scheme, v)
    epoch = int(m.group(1) or 0)
    rest = m.group(2)
    upstream, revision = (rest.rsplit("-", 1) if "-" in rest else (rest, "0"))
    if not upstream or not upstream[0].isdigit() or not revision:
        raise _bad(scheme, v)
    nums = re.match(r"\d+(?:\.\d+)*", upstream).group(0)
    rel = _pad(int(x) for x in nums.split("."))
    key = (epoch, _DPKG_KEY(upstream), _DPKG_KEY(revision))
    return Version(scheme, v, ("",), key, rel, "~" in upstream)


# --- hf-sha --------------------------------------------------------------------

def _hf(scheme, v):
    if not re.match(r"^[0-9a-f]{7,40}$", v):
        raise _bad(scheme, v, "not a hex revision")
    return Version(scheme, v, ("",), None, (), False)


PARSERS = {"semver": _semver, "zerover": _semver, "k8s": _k8s, "llama": _llama, "calver": _calver,
           "pep440": _pep440, "docker-suffix": _docker, "eat": _eat, "apt": _apt, "hf-sha": _hf}


def parse(scheme, v):
    if scheme not in PARSERS:
        raise ValueError("unknown scheme %r (want one of %s)" % (scheme, ", ".join(SCHEMES)))
    if not isinstance(v, str):
        raise _bad(scheme, v, "not a string")
    return PARSERS[scheme](scheme, v.strip())


def _parsed(scheme, v):
    return v if isinstance(v, Version) else parse(scheme, v)


def comparable(scheme, a, b):
    try:
        a, b = _parsed(scheme, a), _parsed(scheme, b)
    except ValueError:
        return False
    return scheme != "hf-sha" and a.flavour == b.flavour


def compare(scheme, a, b):
    pa, pb = _parsed(scheme, a), _parsed(scheme, b)
    if scheme == "hf-sha":
        raise ValueError("hf-sha: revisions have no order (%r, %r)" % (pa.raw, pb.raw))
    if pa.flavour != pb.flavour:
        raise ValueError("%s: %r and %r are different flavours and do not compare" % (scheme, pa.raw, pb.raw))
    return (pa.key > pb.key) - (pa.key < pb.key)


def is_prerelease(scheme, v):
    return _parsed(scheme, v).pre


# --- ranges --------------------------------------------------------------------

CMP_RE = re.compile(r"^(<=|>=|==|!=|<|>)\s*(\S+)$")
OPS = {"<": lambda c: c < 0, "<=": lambda c: c <= 0, ">": lambda c: c > 0,
       ">=": lambda c: c >= 0, "==": lambda c: c == 0, "!=": lambda c: c != 0}


def _clauses(scheme, spec):
    """[[(op, Version), ...], ...] of a spec; [] for "*"."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("%s: empty range" % scheme)
    if spec.strip() == "*":
        return []
    out = []
    for clause in spec.split("||"):
        comps = []
        for part in clause.split(","):
            m = CMP_RE.match(part.strip())
            if not m:
                raise ValueError("%s: bad comparator %r in range %r" % (scheme, part.strip(), spec))
            op = m.group(1)
            if scheme == "hf-sha" and op not in ("==", "!="):
                raise ValueError("hf-sha: only ==, != and * ranges (got %r)" % spec)
            comps.append((op, parse(scheme, m.group(2))))
        out.append(comps)
    return out


def check_range(scheme, spec):
    _clauses(scheme, spec)


def _range_cmp(scheme, v, bound):
    if scheme == "hf-sha":
        same = v.raw.startswith(bound.raw) or bound.raw.startswith(v.raw)
        return 0 if same else 1
    if scheme == "docker-suffix":
        return (v.key > bound.key) - (v.key < bound.key)
    if scheme == "pep440" and v.local is not None and bound.local is None:
        k = v.key[:-1] + ((0,),)
        return (k > bound.key) - (k < bound.key)
    return compare(scheme, v, bound)


def in_range(scheme, v, spec):
    pv = _parsed(scheme, v)
    clauses = _clauses(scheme, spec)
    if not clauses:
        return True
    return any(all(OPS[op](_range_cmp(scheme, pv, bound)) for op, bound in clause) for clause in clauses)


# --- update types --------------------------------------------------------------

def update_type(scheme, old, new):
    po, pn = _parsed(scheme, old), _parsed(scheme, new)
    if scheme == "hf-sha":
        return "digest"
    if compare(scheme, po, pn) == 0:
        return "digest"
    if scheme == "llama" and po.flavour == ("build",):
        return "minor"
    ro, rn = _pad(po.release), _pad(pn.release)
    if ro[0] != rn[0]:
        return "major"
    if ro[1] != rn[1]:
        return "minor"
    return "patch"


def breaking(scheme, old, new):
    po, pn = _parsed(scheme, old), _parsed(scheme, new)
    t = update_type(scheme, po, pn)
    if t == "major":
        return True
    if t != "minor":
        return False
    if scheme == "k8s":
        return True
    zero = scheme == "zerover" or (scheme == "llama" and po.flavour == ("release",))
    return zero and po.release[0] == 0


def classify(scheme, old, new):
    return {"type": update_type(scheme, old, new), "breaking": breaking(scheme, old, new)}


def latest(scheme, current, candidates):
    cur = _parsed(scheme, current)
    best = None
    for c in candidates:
        try:
            pc = parse(scheme, c)
        except ValueError:
            continue
        if pc.pre or not comparable(scheme, cur, pc) or compare(scheme, pc, cur) <= 0:
            continue
        if best is None or compare(scheme, pc, best) > 0:
            best = pc
    return best.raw if best else None


# --- CLI -----------------------------------------------------------------------

def main(argv):
    usage = __doc__.split("CLI:", 1)[1].split("Exit 2", 1)[0]
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print("usage:" + usage, file=sys.stderr)
        return 2
    cmd, scheme, args = argv[0], argv[1], argv[2:]
    try:
        if cmd == "parse" and len(args) == 1:
            print(json.dumps(parse(scheme, args[0]).as_dict(), sort_keys=True))
        elif cmd == "compare" and len(args) == 2:
            print(compare(scheme, *args))
        elif cmd == "in-range" and len(args) == 2:
            return 0 if in_range(scheme, *args) else 1
        elif cmd == "update-type" and len(args) == 2:
            c = classify(scheme, *args)
            print(c["type"] + (" breaking" if c["breaking"] else ""))
        elif cmd == "latest" and args:
            got = latest(scheme, args[0], args[1:])
            if got:
                print(got)
        else:
            print("usage:" + usage, file=sys.stderr)
            return 2
    except ValueError as e:
        print("pinver: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""pins/registry.toml: every pin the freshness bumper owns, and where it lives.

An entry ([[pin]]) names its source, version scheme, group and sites. A site is a
span of a file that holds one of the pin's values:

  {file, regex}            named groups (v, digest, commit, ...) are the spans;
                           count = N (default 1) or "any" (one or more)
  {file, var, key?}        a shell/env assignment VAR=value (key default "v")
  {file, arg, key?}        a Dockerfile ARG NAME=value / ARG NAME="value"
  {file, ref, tag?}        every name:<tag>@sha256:<digest> reference of image
                           `ref` (as written) in file; tag is the "{v}" template
                           of its tag. digest_only = true: references written
                           name@sha256:<digest> (their version lives in another site);
                           line = '<regex>': only references on lines it matches
                           (two pins of one image in one file)
  values = true            with ref: a Helm values mapping's repository: / tag: pair

derived = [{file, regex, from}]: group v must read the format string `from`
filled with the pin's values ({v}, {v_bare} = v without a leading "v", any other
key). Every site of a key must hold the same value.

[images.default] owns every other pinned image reference in an ours or eat-stock
file: one generated pin per image name, tag and file group, source oci, scheme
docker-suffix.
"""

import fnmatch
import os
import re
import string
import subprocess
import tomllib

from . import classes, oci
from . import versions as V

ROOT = oci.ROOT
REGISTRY = os.path.join(ROOT, "pins", "registry.toml")
KINDS = ("oci", "github-release", "pypi", "hf", "apt-index", "eat", "llama", "habana", "json", "cmd", "helm",
         "github-branch")
FAMILIES = ("k3s", "plugin", "runtime-engine", "static-engine", "host-driver", "host-ppa", "hf", "pypi", "tools",
            "chrome", "telemetry", "opt-in", "base-image", "eat-stock")
PIN_KEYS = {"id", "class", "source", "scheme", "sites", "derived", "group", "family", "policy", "canary", "confirm",
            "hold", "tests", "writer", "notes", "component", "patterns"}
DIGEST = r"sha256:[0-9a-f]{64}"
# what may not precede an image name (a "-" may, after a non-name char: ${X:-name})
NAME_LEAD = r"(?<![A-Za-z0-9._/])(?<![A-Za-z0-9]-)"


class RegistryError(Exception):
    pass


def load(path=REGISTRY):
    with open(path, "rb") as f:
        return tomllib.load(f)


def helpers(values):
    """values plus v_bare (v without a leading "v"), v_major and v_mm (major.minor)."""
    out = dict(values)
    v = str(values.get("v", ""))
    bare = v[1:] if v.startswith("v") and v[1:2].isdigit() else v
    out.setdefault("v_bare", bare)
    nums = re.findall(r"\d+", bare)
    if nums:
        out.setdefault("v_major", nums[0])
        out.setdefault("v_mm", ".".join(nums[:2]))
    return out


class Tree:
    """The files of the working tree (ref None) or of a commit, read lazily."""

    def __init__(self, root=ROOT, ref=None):
        self.root, self.ref, self._cache, self._files = root, ref, {}, None

    def _vcs(self, *args):
        return subprocess.run(["git", "-C", self.root] + list(args), capture_output=True)

    def read(self, path):
        if path not in self._cache:
            text = None
            if self.ref is None:
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8") as f:
                        text = f.read()
                except (FileNotFoundError, IsADirectoryError, NotADirectoryError, UnicodeDecodeError):
                    text = None
            else:
                r = self._vcs("show", "%s:%s" % (self.ref, path))
                try:
                    text = r.stdout.decode("utf-8") if r.returncode == 0 else None
                except UnicodeDecodeError:
                    text = None
            self._cache[path] = text
        return self._cache[path]

    def files(self):
        """Repo-relative paths images-lock scans (tracked, minus prose and recorded data)."""
        if self._files is None:
            il = oci.images_lock()
            if self.ref is None:
                self._files = il.scanned_files(self.root)
            else:
                r = self._vcs("ls-tree", "-r", "-z", "--name-only", self.ref)
                if r.returncode:
                    raise RegistryError("cannot list %s: %s" % (self.ref, r.stderr.decode().strip()))
                self._files = [p for p in sorted(x for x in r.stdout.decode().split("\0") if x)
                               if not p.endswith(".md") and not p.startswith(il.SKIP_PREFIXES)
                               and "/results/" not in "/" + p and p not in ("images.lock", il.ALLOW_FILE)]
        return self._files

    def set(self, path, text):
        self._cache[path] = text


class Span:
    """One located value: file, [start, end) and the pin value key it holds."""

    __slots__ = ("file", "start", "end", "key", "text", "site", "derived", "image", "tag_tmpl", "match")

    def __init__(self, file, start, end, key, text, site, derived=None, image=None, tag_tmpl=None, match=None):
        self.file, self.start, self.end, self.key, self.text = file, start, end, key, text
        self.site, self.derived, self.image, self.tag_tmpl, self.match = site, derived, image, tag_tmpl, match

    def line(self, tree):
        return tree.read(self.file).count("\n", 0, self.start) + 1


def tag_keys(tmpl):
    """The value keys a tag template names, in order."""
    return [f for _, f, _, _ in string.Formatter().parse(tmpl) if f]


ANY_KEY = r"[0-9A-Za-z][0-9A-Za-z._+-]*?"
_NUM = r"v?[0-9]+(?:\.[0-9]+)*"
SCHEME_V = {"semver": _NUM, "zerover": _NUM, "calver": r"[0-9]{4}(?:\.[0-9]+)*", "llama": r"v[0-9]+\.[0-9]+\.[0-9]+",
            "hf-sha": r"[0-9a-f]{7,64}"}


def key_patterns(pin):
    """{key: regex} a tag template's placeholder of each key matches."""
    out = {"v": SCHEME_V.get(pin.get("scheme"), ANY_KEY)} if pin else {}
    out.update((pin or {}).get("patterns") or {})
    return out


def _tag_pattern(tmpl, patterns=None):
    out, seen = "", set()
    for lit, field, _, _ in string.Formatter().parse(tmpl):
        out += re.escape(lit)
        if field:
            pat = (patterns or {}).get(field, ANY_KEY)
            out += "(?P=%s)" % field if field in seen else "(?P<%s>%s)" % (field, pat)
            seen.add(field)
    return out


def _ref_regex(name, tmpl, digest_only, patterns=None):
    lead = NAME_LEAD
    if digest_only:
        return re.compile(lead + re.escape(name) + r"@(?P<digest>" + DIGEST + r")(?![0-9a-f])")
    return re.compile(lead + re.escape(name) + r":(?P<tag>" + _tag_pattern(tmpl, patterns) + r")@(?P<digest>" + DIGEST
                      + r")(?![0-9a-f])")


def _values_regex(name, tmpl, patterns=None):
    return re.compile(r"""^(?P<i>[ \t]*)repository:[ \t]*["']?""" + re.escape(name)
                      + r"""["']?[ \t]*\n(?P=i)tag:[ \t]*["']?(?P<tag>""" + _tag_pattern(tmpl, patterns) + r")@(?P<digest>"
                      + DIGEST + r")", re.M)


def site_regex(site, patterns=None):
    """(compiled regex, {group: key}) for a site; patterns: key_patterns(pin)."""
    if "regex" in site:
        rx = re.compile(site["regex"], re.M)
        return rx, {g: g for g in rx.groupindex}
    key = site.get("key", "v")
    if "var" in site:
        rx = re.compile(r"""^[ \t]*(?:export[ \t]+|local[ \t]+|readonly[ \t]+)?%s=["']?(?P<%s>[^"'\s;]+)["']?"""
                        % (re.escape(site["var"]), key), re.M)
        return rx, {key: key}
    if "arg" in site:
        rx = re.compile(r"""^ARG[ \t]+%s=["']?(?P<%s>[^"'\s]+)["']?[ \t]*$""" % (re.escape(site["arg"]), key), re.M)
        return rx, {key: key}
    if "ref" in site:
        keys = {k: k for k in tag_keys(site.get("tag", "{v}"))}
        keys["digest"] = "digest"
        if site.get("values"):
            return _values_regex(site["ref"], site.get("tag", "{v}"), patterns), keys
        if site.get("digest_only"):
            if "digest_tag" not in site:
                raise RegistryError("digest_only site %s %s needs digest_tag" % (site["file"], site["ref"]))
            return _ref_regex(site["ref"], "", True), {"digest": "digest"}
        return _ref_regex(site["ref"], site.get("tag", "{v}"), False, patterns), keys
    raise RegistryError("site %r has no regex, var, arg or ref" % (site,))


def _label(site):
    return site.get("regex") or site.get("var") or site.get("arg") or site.get("ref")


def locate(tree, pin):
    """[Span] for every site and derived entry of pin; RegistryError on a site that
    resolves to the wrong number of matches."""
    spans = []
    for site in pin.get("sites", []):
        text = tree.read(site["file"])
        if text is None:
            raise RegistryError("%s: site file %s does not exist" % (pin["id"], site["file"]))
        rx, groups = site_regex(site, key_patterns(pin))
        hits = [m for m in rx.finditer(text) if "match_tag" not in site or m.group("tag") == site["match_tag"]]
        if "line" in site:  # a ref site scoped to the lines matching this regex
            line_rx = re.compile(site["line"])
            hits = [m for m in hits if line_rx.search(
                text[text.rfind("\n", 0, m.start()) + 1:(text.find("\n", m.end()) + 1 or len(text) + 1) - 1])]
        want = site.get("count", "any" if "ref" in site else 1)
        if (want == "any" and not hits) or (want != "any" and len(hits) != want):
            raise RegistryError("%s: site %s %s matches %d time(s), want %s" % (
                pin["id"], site["file"], _label(site), len(hits), "one or more" if want == "any" else want))
        for m in hits:
            for g, key in groups.items():
                if m.group(g) is None:
                    continue
                spans.append(Span(site["file"], m.start(g), m.end(g), key, m.group(g), site,
                                  image=site.get("ref"), tag_tmpl=site.get("tag", "{v}") if "ref" in site else None,
                                  match=(m.start(), m.end())))
    for d in pin.get("derived", []):
        text = tree.read(d["file"])
        if text is None:
            raise RegistryError("%s: derived file %s does not exist" % (pin["id"], d["file"]))
        hits = list(re.compile(d["regex"], re.M).finditer(text))
        want = d.get("count", 1)
        if (want == "any" and not hits) or (want != "any" and len(hits) != want):
            raise RegistryError("%s: derived %s %s matches %d time(s), want %s"
                                % (pin["id"], d["file"], d["regex"], len(hits), want))
        for m in hits:
            spans.append(Span(d["file"], m.start("v"), m.end("v"), "v", m.group("v"), d, derived=d["from"],
                              match=(m.start(), m.end())))
    return spans


def current(pin, spans):
    """The pin's values as its sites hold them (derived entries and image digests
    excluded: each image reference carries its own digest)."""
    vals = {}
    for s in spans:
        if s.derived is not None or (s.key == "digest" and s.image is not None):
            continue
        if s.key in vals and vals[s.key] != s.text:
            raise RegistryError("%s: sites disagree on %s: %r and %r (%s)" % (pin["id"], s.key, vals[s.key], s.text, s.file))
        vals.setdefault(s.key, s.text)
    return vals


def check_derived(pin, spans, vals):
    problems = []
    for s in spans:
        if s.derived is None:
            continue
        try:
            want = s.derived.format(**helpers(vals))
        except KeyError as e:
            problems.append("%s: derived %s reads %s, which no site holds" % (pin["id"], s.file, e))
            continue
        if s.text != want:
            problems.append("%s: derived %s holds %r, its parent gives %r" % (pin["id"], s.file, s.text, want))
    return problems


# ---- schema ---------------------------------------------------------------

def validate(reg):
    """Problems with the registry's shape (not the tree)."""
    problems, ids, groups = [], set(), set(reg.get("groups", {}))
    for p in reg.get("pin", []):
        pid = p.get("id", "?")
        unknown = set(p) - PIN_KEYS
        if unknown:
            problems.append("%s: unknown keys %s" % (pid, sorted(unknown)))
        for k in ("id", "class", "source", "scheme", "group", "family"):
            if k not in p:
                problems.append("%s: missing %s" % (pid, k))
        if pid in ids:
            problems.append("%s: duplicate id" % pid)
        ids.add(pid)
        if p.get("class") not in ("ours", "eat-stock"):
            problems.append("%s: class must be ours or eat-stock, not %r" % (pid, p.get("class")))
        if p.get("scheme") not in V.SCHEMES:
            problems.append("%s: unknown scheme %r" % (pid, p.get("scheme")))
        if p.get("family") not in FAMILIES:
            problems.append("%s: unknown family %r" % (pid, p.get("family")))
        if (p.get("source") or {}).get("kind") not in KINDS:
            problems.append("%s: unknown source kind %r" % (pid, (p.get("source") or {}).get("kind")))
        if p.get("confirm") and p["confirm"].get("kind") not in KINDS:
            problems.append("%s: unknown confirm kind %r" % (pid, p["confirm"].get("kind")))
        if p.get("group") not in groups:
            problems.append("%s: group %r is not in [groups]" % (pid, p.get("group")))
        if not p.get("sites"):
            problems.append("%s: no sites" % pid)
        for s in p.get("sites", []) + p.get("derived", []):
            if not isinstance(s, dict) or "file" not in s:
                problems.append("%s: a site has no file" % pid)
                continue
            if s["file"].startswith(".github/"):
                problems.append("%s: site %s is under .github/ (bumpers never write a workflow)" % (pid, s["file"]))
            try:
                if "from" in s:
                    if "v" not in re.compile(s["regex"]).groupindex:
                        problems.append("%s: derived %s regex has no group v" % (pid, s["file"]))
                else:
                    site_regex(s)
            except (RegistryError, re.error, KeyError) as e:
                problems.append("%s: %s" % (pid, e))
        hold = p.get("hold")
        if hold is not None and not {"max", "reason", "review_by"} <= set(hold):
            problems.append("%s: hold needs max, reason and review_by" % pid)
        if (p.get("class") == "eat-stock") != (p.get("family") == "eat-stock"):
            problems.append("%s: class eat-stock goes with family eat-stock" % pid)
        if p.get("writer") not in (None, "hook"):
            problems.append("%s: writer is hook or absent" % pid)
    for g, conf in reg.get("groups", {}).items():
        for k in ("tests", "hooks"):
            if not isinstance(conf.get(k, []), list):
                problems.append("group %s: %s must be a list" % (g, k))
    return problems


# ---- images.default ---------------------------------------------------------

def is_test_input(path):
    """tests/ and test_*.py / *_test.* files: strings fed to code under test, which
    golden substitution (hack/substitute-goldens) keeps in step, never a bumper."""
    return path.startswith("tests/") or bool(re.search(r"(^|/)(test_[^/]*|[^/]*_test\.[a-z]+)$", path))


EAT_FROZEN = ("EAT stock: the tag EAT's charts name (a chart default); it moves with an EAT release, "
              "by hand, never to a registry's newest tag")


def _default_pin(conf, written, tag, group, cls):
    pin = {"id": "image:%s:%s:%s" % (group, written, tag), "class": cls, "group": group, "generated": True,
           "family": "eat-stock" if cls == "eat-stock" else conf.get("family", "base-image"),
           "scheme": "docker-suffix", "source": {"kind": "oci", "image": written, "tag": "{v}"},
           "sites": [], "tests": []}
    if cls == "eat-stock":
        pin["frozen"] = EAT_FROZEN
    return pin


def default_pins(tree, reg, owned_spans):
    """Generated pins for image references no explicit pin's sites cover: one per
    image name, tag, class and file group."""
    conf = reg.get("images", {}).get("default", {})
    il = oci.images_lock()
    covered = set()
    for s in owned_spans:
        covered.update((s.file, i) for i in range(s.start, s.end))
    by_key = {}

    def group_of(path):
        return next((g["group"] for g in conf.get("groups", []) if fnmatch.fnmatchcase(path, g["glob"])), "images")

    for path in tree.files():
        if path.startswith(".github/") or is_test_input(path):
            continue
        cls = classes.effective(tree.root, path)
        if cls not in ("ours", "eat-stock"):
            continue
        text = tree.read(path)
        if text is None:
            continue
        for m in il.PINNED_RE.finditer(text):
            _, tag, _ = il.parse_ref(m.group("ref") + "@" + m.group("digest"))
            if tag is None or tag == "latest" or (path, m.start("digest")) in covered:
                continue
            written = m.group("ref")[: -(len(tag) + 1)]
            group = group_of(path)
            pin = by_key.setdefault((written, tag, group, cls), _default_pin(conf, written, tag, group, cls))
            site = {"file": path, "ref": written, "tag": "{v}", "match_tag": tag}
            if site not in pin["sites"]:
                pin["sites"].append(site)
        if not path.endswith((".yaml", ".yml")):
            continue
        for _, ref, end in il.values_refs(text):
            _, tag, digest = il.parse_ref(ref)
            if not digest or tag is None or end is None or (path, end - 1) in covered:
                continue
            if (":" + tag + "@") not in ref:
                continue
            written = ref[: ref.index(":" + tag + "@")]
            group = group_of(path)
            pin = by_key.setdefault((written, tag, group, cls), _default_pin(conf, written, tag, group, cls))
            site = {"file": path, "ref": written, "tag": "{v}", "values": True, "match_tag": tag}
            if site not in pin["sites"]:
                pin["sites"].append(site)
    return list(by_key.values())


def all_pins(tree, reg):
    """(explicit pins, generated default pins, {id: spans})."""
    spans_by, owned = {}, []
    for p in reg.get("pin", []):
        sp = locate(tree, p)
        spans_by[p["id"]] = sp
        owned += sp
    gen = default_pins(tree, reg, owned)
    for p in gen:
        spans_by[p["id"]] = locate(tree, p)
    return reg.get("pin", []), gen, spans_by

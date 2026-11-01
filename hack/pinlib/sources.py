"""Upstream sources: where a pin's newest stable version comes from.

resolve(pin, current) returns a Resolved: values (the primary "v" plus every other
key the pin's sites name: digest, commit, sha256s, ...), published_at and a
release-notes URL. A source never moves a pin backwards: a newest version that
sorts below the current one is an error, and so is a source that cannot find the
tag shape it expects (it never falls back to the current value).

Kinds (the `source` table of a pins/registry.toml entry)
  oci             image, tag ("{v}" template; candidates share the current tag's shape)
  github-release  repo, tag template, select = latest | highest, [image, image_tag,
                  commit, assets = {key = name template}, body = {key = regex}]
  pypi            package
  hf              model (the main revision; files classified by hf_diff)
  apt-index       url, package, also = [packages that must carry the same version]
  eat             what = tag | image; for image: file, repository (Intel EAT's chart
                  values at its latest release)
  llama           repo; releases/latest vX.Y.Z and the bNNNNN tag built from its commit
  habana          image (vault.habana.ai/gaudi-docker/<ver>/<os>/habanalabs/vllm-*)
  json            url, path (dotted), [regex with group v]: kernel.org releases.json,
                  Chrome for Testing last-known-good
  cmd             run = [argv], parse = kv | moved; a repo script that prints pins
                  (hack/ppa-pins, hack/bump-convert-pins --dry-run)
  helm            repo (chart repo URL), chart: the chart's newest version
  github-branch   repo, branch: the branch head commit (flake inputs)
"""

import datetime
import fnmatch
import json
import re
import subprocess

from . import http, oci
from . import versions as V

ROOT = oci.ROOT


class SourceError(Exception):
    pass


class Resolved:
    def __init__(self, values, published_at=None, url=None, note=None):
        self.values, self.published_at, self.url, self.note = values, published_at, url, note

    @property
    def v(self):
        return self.values["v"]

    def __repr__(self):
        return "Resolved(%r)" % (self.values,)


def tag_regex(template, pin=None):
    """A regex that matches tags of template, group v the version (the pin's v pattern)."""
    from .registry import ANY_KEY, key_patterns
    pre, _, post = template.partition("{v}")
    pat = key_patterns(pin).get("v", ANY_KEY) if pin else ANY_KEY
    return re.compile("^" + re.escape(pre) + "(?P<v>" + pat + ")" + re.escape(post) + "$")


def fmt(template, values):
    return template.format(**registry_helpers(values))


def _newest(scheme, candidates, current=None):
    """The highest stable candidate that orders against current (same tag shape)."""
    ok = []
    for c in candidates:
        try:
            if V.word_tag(scheme, c) or not V.is_stable(scheme, c):
                continue
            if current is not None and scheme != "hf-sha" and not V.word_tag(scheme, current) \
                    and not V.same_shape(scheme, current, c):
                continue
        except ValueError:
            continue
        ok.append(c)
    best = None
    for c in ok:
        try:
            if best is None or V.compare(scheme, c, best) > 0:
                best = c
        except ValueError:
            continue  # another flavour than the best so far (a llama build beside a release)
    return best


def _below(scheme, v, cap):
    try:
        return V.compare(scheme, v, cap) < 0
    except ValueError:
        return False


def _not_backwards(pin, current, new):
    if current is None or pin["scheme"] == "hf-sha" or current == new:
        return
    try:
        if V.compare(pin["scheme"], new, current) < 0:
            raise SourceError("%s: upstream newest %s is older than the pin %s" % (pin["id"], new, current))
    except ValueError:
        if pin["scheme"] == "docker-suffix":
            return  # a tag-line change is the policy's to judge, not an error
        raise


def _gh(path):
    return http.get_json("https://api.github.com/" + path.lstrip("/"))


def _gh_releases(repo, pages=3):
    out = []
    for p in range(1, pages + 1):
        page = _gh("repos/%s/releases?per_page=100&page=%d" % (repo, p))
        out += page
        if len(page) < 100:
            break
    return out


def _tag_commit(repo, tag):
    return _gh("repos/%s/commits/%s" % (repo, tag))["sha"]


def _assets(rel, wanted, values):
    """{key: sha256 hex} of release assets named by templates (GitHub asset digests)."""
    by_name = {a["name"]: a for a in rel.get("assets", [])}
    out = {}
    for key, tmpl in (wanted or {}).items():
        name = fmt(tmpl, values)
        a = by_name.get(name)
        if a is None:
            raise SourceError("%s: release %s has no asset %s" % (rel.get("html_url", "?"), rel["tag_name"], name))
        d = a.get("digest") or ""
        if not d.startswith("sha256:"):
            raise SourceError("%s: asset %s carries no sha256 digest" % (rel["tag_name"], name))
        out[key] = d.split(":", 1)[1]
    return out


def _image_values(src, values):
    if src.get("image"):
        tag = fmt(src.get("image_tag", "{v}"), values)
        values["image_tag"] = tag
        values["digest"] = oci.digest(oci.normalize(src["image"] + ":x")[0], tag)
    return values


def _mapped(src, tag):
    for a, b in (src.get("tag_map") or {}).items():
        tag = tag.replace(a, b)
    return tag


def _release_extras(pin, src, rel, values):
    """body / asset_match / commit / assets keys of one release into values."""
    for key, rxs in (src.get("body") or {}).items():
        m = re.search(rxs, rel.get("body") or "")
        if not m:
            raise SourceError("%s: release %s's notes do not match %s" % (pin["id"], rel["tag_name"], rxs))
        values[key] = m.group(1)
    for key, rxs in (src.get("asset_match") or {}).items():
        hits = [m for m in (re.search(rxs, a["name"]) for a in rel.get("assets", [])) if m]
        if not hits:
            raise SourceError("%s: no asset of release %s matches %s" % (pin["id"], rel["tag_name"], rxs))
        values[key] = hits[0].group(1)
    if src.get("commit"):
        values["commit"] = _tag_commit(src["repo"], rel["tag_name"])
    values.update(_assets(rel, src.get("assets"), values))


def github_release(pin, current):
    src, scheme = pin["source"], pin["scheme"]
    tmpl = src.get("tag", "{v}")
    rx = tag_regex(tmpl, pin)
    hold = (pin.get("hold") or {}).get("max")
    if src.get("select", "latest") == "highest" or hold:
        rels = [r for r in _gh_releases(src["repo"]) if not r.get("prerelease") and not r.get("draft")]
        found = {}
        for r in rels:
            m = rx.match(_mapped(src, r["tag_name"]))
            if m and (not hold or V.compare(scheme, m.group("v"), hold) < 0):
                found[m.group("v")] = r
        best = _newest(scheme, found)
        if best is None:
            raise SourceError("%s: no stable %s release matches %s%s" % (
                pin["id"], src["repo"], tmpl, " below the hold %s" % hold if hold else ""))
        rel = found[best]
    else:
        rel = _gh("repos/%s/releases/latest" % src["repo"])
        m = rx.match(_mapped(src, rel["tag_name"]))
        if not m or rel.get("prerelease") or rel.get("draft"):
            raise SourceError("%s: %s's latest release %r does not match %s" % (pin["id"], src["repo"], rel["tag_name"], tmpl))
        best = m.group("v")
        if not V.is_stable(scheme, best):
            raise SourceError("%s: %s's latest release %s is not stable" % (pin["id"], src["repo"], best))
    _not_backwards(pin, current, best)
    values = {"v": best, "tag": rel["tag_name"]}
    _release_extras(pin, src, rel, values)
    for follow in src.get("follow") or []:
        tag = fmt(follow["tag"], values)
        try:
            rel2 = _gh("repos/%s/releases/tags/%s" % (follow["repo"], tag))
        except http.HTTPError as e:
            raise SourceError("%s: %s has no release %s (%s)" % (pin["id"], follow["repo"], tag, e)) from None
        _release_extras(pin, follow, rel2, values)
    _image_values(src, values)
    return Resolved(values, rel.get("published_at"), rel.get("html_url"))


def oci_source(pin, current):
    src, scheme = pin["source"], pin["scheme"]
    name = oci.normalize(src["image"] + ":x")[0]
    if current is not None and V.word_tag(scheme, current):
        # a word tag (trixie-slim) has no newer version, only a newer digest
        tag = fmt(src.get("tag", "{v}"), {"v": current})
        return Resolved({"v": current, "tag": tag, "digest": oci.digest(name, tag)}, None)
    rx = tag_regex(src.get("tag", "{v}"), pin)
    found = {}
    for t in oci.tags(name):
        m = rx.match(t)
        if m:
            found[m.group("v")] = t
    if src.get("exclude"):
        found = {v: t for v, t in found.items() if not any(fnmatch.fnmatchcase(t, g) for g in src["exclude"])}
    hold = (pin.get("hold") or {}).get("max")
    if hold:
        found = {v: t for v, t in found.items() if _below(scheme, v, hold)}
    best = _newest(scheme, found, current)
    if best is None:
        raise SourceError("%s: no stable tag of %s matches %s%s" % (
            pin["id"], src["image"], src.get("tag", "{v}"), " shaped like %s" % current if current else ""))
    _not_backwards(pin, current, best)
    tag = found[best]
    return Resolved({"v": best, "tag": tag, "digest": oci.digest(name, tag)}, oci.published(name, tag))


def pypi(pin, current):
    pkg = pin["source"]["package"]
    doc = http.get_json("https://pypi.org/pypi/%s/json" % pkg)
    found = {}
    for v, files in doc["releases"].items():
        if files and not all(f.get("yanked") for f in files):
            found[v] = files
    best = _newest("pep440", found)
    if best is None:
        raise SourceError("%s: PyPI %s has no stable, unyanked release" % (pin["id"], pkg))
    _not_backwards(pin, current, best)
    up = min((f.get("upload_time_iso_8601") or "") for f in found[best]) or None
    return Resolved({"v": best}, up, "https://pypi.org/project/%s/%s/" % (pkg, best))


def hf(pin, current):
    model = pin["source"]["model"]
    doc = http.get_json("https://huggingface.co/api/models/%s" % model)
    sha = doc.get("sha") or ""
    if not re.match(r"^[0-9a-f]{40}$", sha):
        raise SourceError("%s: Hugging Face returned no main revision for %s" % (pin["id"], model))
    values = {"v": sha}
    lfs = pin["source"].get("lfs") or {}
    if lfs:
        files = hf_files(model, sha)
        for key, fname in lfs.items():
            f = files.get(fname)
            if not f or not f.get("lfs"):
                raise SourceError("%s: %s@%s has no LFS file %s" % (pin["id"], model, sha, fname))
            values[key] = f["lfs"]
    return Resolved(values, doc.get("lastModified"), "https://huggingface.co/%s/commits/main" % model)


def hf_files(model, rev):
    """{filename: {"oid": blob id, "lfs": sha256 or None}} at a revision."""
    doc = http.get_json("https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (model, rev))
    return {s["rfilename"]: {"oid": s.get("blobId"), "lfs": (s.get("lfs") or {}).get("sha256")}
            for s in doc.get("siblings", [])}


README_ONLY = re.compile(r"(^|/)(README(\.[A-Za-z]+)?|\.gitattributes)$", re.I)


def hf_diff(model, old, new):
    """('readme-only' | 'substantive', [changed filenames]) between two revisions."""
    a, b = hf_files(model, old), hf_files(model, new)
    changed = sorted(f for f in set(a) | set(b) if (a.get(f) or {}).get("oid") != (b.get(f) or {}).get("oid"))
    kind = "readme-only" if all(README_ONLY.search(f) for f in changed) else "substantive"
    return kind, changed


def apt_index(pin, current):
    src = pin["source"]
    idx = http.get_text(src["url"])
    versions = {}
    pkg = None
    for line in idx.splitlines():
        if line.startswith("Package: "):
            pkg = line.split(None, 1)[1].strip()
        elif line.startswith("Version: ") and pkg:
            versions.setdefault(pkg, set()).add(line.split(None, 1)[1].strip())
    rx = re.compile(src.get("version", r"^[0-9]+\.[0-9]+\.[0-9]+-[0-9]+$"))
    cands = [v for v in versions.get(src["package"], ()) if rx.match(v)]
    for other in src.get("also", []):
        cands = [v for v in cands if v in versions.get(other, ())]
    best = _newest("apt", cands)
    if best is None:
        raise SourceError("%s: %s has no %s version matching %s%s" % (
            pin["id"], src["url"], src["package"], rx.pattern, " shared with %s" % ", ".join(src.get("also", [])) if src.get("also") else ""))
    _not_backwards(pin, current, best)
    return Resolved({"v": best}, None, src["url"])


EAT_REPO = "intel/enterprise-agent-toolkit"


def eat_release():
    rel = _gh("repos/%s/releases/latest" % EAT_REPO)
    if rel.get("prerelease") or rel.get("draft") or not re.match(r"^release-\d+\.\d+\.\d+(-[a-z0-9]+)?$", rel["tag_name"]):
        raise SourceError("EAT's latest release %r is not a release-X.Y.Z tag" % rel.get("tag_name"))
    return rel


def values_tag(text, repository):
    """The tag a Helm values file names beside `repository: <repository>`."""
    rows = text.splitlines()
    for i, row in enumerate(rows):
        m = re.match(r"""^(\s*)repository:\s*["']?([^"'\s#]+)""", row)
        if not m or m.group(2) != repository:
            continue
        indent = len(m.group(1))
        for other in rows[max(0, i - 6):i] + rows[i + 1:i + 7]:
            t = re.match(r"""^(\s*)tag:\s*["']?([^"'\s#]+)""", other)
            if t and len(t.group(1)) == indent:
                return t.group(2)
    return None


def eat(pin, current):
    src = pin["source"]
    rel = eat_release()
    tag = rel["tag_name"]
    if src.get("what", "image") == "tag":
        if not V.is_stable(pin["scheme"], tag):
            raise SourceError("EAT %s is not stable" % tag)
        _not_backwards(pin, current, tag)
        commit = _tag_commit(EAT_REPO, tag)
        return Resolved({"v": tag, "commit": commit}, rel.get("published_at"), rel.get("html_url"))
    text = http.get_text("https://raw.githubusercontent.com/%s/%s/%s" % (EAT_REPO, tag, src["file"]))
    t = values_tag(text, src["repository"])
    if not t:
        raise SourceError("%s: EAT %s's %s names no tag for %s" % (pin["id"], tag, src["file"], src["repository"]))
    _not_backwards(pin, current, t)
    name = oci.normalize(src["repository"] + ":x")[0]
    return Resolved({"v": t, "digest": oci.digest(name, t), "eat": tag}, rel.get("published_at"), rel.get("html_url"))


def llama(pin, current):
    repo = pin["source"].get("repo", "ggml-org/llama.cpp")
    rel = _gh("repos/%s/releases/latest" % repo)
    if not re.match(r"^v\d+\.\d+\.\d+$", rel["tag_name"]) or rel.get("prerelease"):
        raise SourceError("%s: %s's latest release %r is not vX.Y.Z" % (pin["id"], repo, rel["tag_name"]))
    _not_backwards(pin, current, rel["tag_name"])
    commit = rel["target_commitish"]
    if not re.match(r"^[0-9a-f]{40}$", commit):
        commit = _tag_commit(repo, rel["tag_name"])
    build = None
    for r in _gh_releases(repo, pages=10):
        if re.match(r"^b\d+$", r["tag_name"]) and r.get("target_commitish") == commit:
            build = r["tag_name"]
            break
    if build is None:
        raise SourceError("%s: no bNNNNN release of %s is built from %s (%s)" % (pin["id"], repo, rel["tag_name"], commit))
    values = {"v": rel["tag_name"], "build": build, "commit": commit}
    for key, tmpl in (pin["source"].get("images") or {}).items():
        img = fmt(tmpl, values)
        name, tag, _ = oci.normalize(img)
        values[key] = oci.digest(name, tag)
    return Resolved(values, rel.get("published_at"), rel.get("html_url"))


HABANA = "https://vault.habana.ai/artifactory/api/storage/gaudi-docker"


def habana(pin, current):
    src = pin["source"]
    os_dir = src.get("os", "ubuntu24.04")
    rels = [c["uri"].strip("/") for c in http.get_json(HABANA + "/").get("children", []) if c.get("folder")]
    best = _newest("semver", [r for r in rels if re.match(r"^\d+\.\d+\.\d+$", r)])
    if best is None:
        raise SourceError("%s: no gaudi-docker release folder" % pin["id"])
    _not_backwards(pin, current, best)
    kids = [c["uri"].strip("/") for c in http.get_json("%s/%s/%s/habanalabs/" % (HABANA, best, os_dir)).get("children", [])]
    rx = re.compile(src.get("repo", r"^vllm-(?P<vllm>\d+\.\d+\.\d+)-ptupstream-(?P<pt>\d+\.\d+\.\d+)$"))
    hits = [(k, rx.match(k)) for k in kids if rx.match(k)]
    if not hits:
        raise SourceError("%s: gaudi-docker %s has no %s image" % (pin["id"], best, rx.pattern))
    hits.sort(key=lambda h: V.parse("semver", h[1].group("vllm")).key)
    repo, m = hits[-1]
    name = "vault.habana.ai/gaudi-docker/%s/%s/habanalabs/%s" % (best, os_dir, repo)
    builds = [t for t in oci.tags(name) if re.match(r"^%s-\d+$" % re.escape(best), t)]
    if not builds:
        raise SourceError("%s: %s has no %s-<build> tag" % (pin["id"], name, best))
    tag = max(builds, key=lambda t: int(t.rsplit("-", 1)[1]))
    values = {"v": best, "build": tag.rsplit("-", 1)[1], "vllm": m.group("vllm"), "pt": m.group("pt"),
              "repo": repo, "tag": tag, "digest": oci.digest(name, tag)}
    return Resolved(values, None, "https://vault.habana.ai/ui/repos/tree/General/gaudi-docker/%s" % best)


def _dig(doc, path):
    for part in path.split("."):
        if isinstance(doc, list):
            doc = doc[int(part)]
        else:
            doc = doc[part]
    return doc


def json_source(pin, current):
    src = pin["source"]
    doc = http.get_json(src["url"])
    try:
        val = _dig(doc, src["path"])
    except (KeyError, IndexError, ValueError, TypeError):
        raise SourceError("%s: %s has no %s" % (pin["id"], src["url"], src["path"])) from None
    if isinstance(val, list):
        val = _newest(pin["scheme"], [str(x) for x in val if not src.get("regex") or re.match(src["regex"], str(x))])
    val = str(val)
    if src.get("regex"):
        m = re.match(src["regex"], val)
        if not m:
            raise SourceError("%s: %s's %s is %r, not %s" % (pin["id"], src["url"], src["path"], val, src["regex"]))
        val = m.groupdict().get("v", m.group(0))
    _not_backwards(pin, current, val)
    values = {"v": val}
    for key, path in (src.get("extra") or {}).items():
        values[key] = str(_dig(doc, path))
    return Resolved(values, None, src.get("notes"))


def cmd(pin, current):
    """Run a repo script (read-only) and read the pin's values from its output."""
    src = pin["source"]
    try:
        out = subprocess.run(src["run"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=600).stdout
    except subprocess.CalledProcessError as e:
        raise SourceError("%s: %s failed: %s" % (pin["id"], " ".join(src["run"]), (e.stderr or "").strip()[-400:])) from None
    values = {}
    if src.get("parse", "kv") == "kv":
        for line in out.splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+)=(\S+)$", line.strip())
            if m:
                values[m.group(1)] = m.group(2)
    else:  # moved: "<name> <old> -> <new>" lines; unmoved pins keep their value
        for line in out.splitlines():
            m = re.match(r"^(\S+) (\S+) -> (\S+)$", line.strip())
            if m:
                values[m.group(1)] = m.group(3)
    key = src.get("key")
    if key:
        if key not in values and src.get("parse") == "moved":
            values[key] = current
        if key not in values:
            raise SourceError("%s: %s printed no %s" % (pin["id"], " ".join(src["run"]), key))
        values = {"v": values[key]}
    return Resolved(values, None, src.get("notes"))


def helm(pin, current):
    """The newest stable chart version in a Helm repo's index.yaml (version: lines)."""
    src = pin["source"]
    text = http.get_text(src["repo"].rstrip("/") + "/index.yaml")
    m = re.search(r"^  %s:\n((?:  [- ].*\n|    .*\n)+)" % re.escape(src["chart"]), text, re.M)
    if not m:
        raise SourceError("%s: %s has no chart %s" % (pin["id"], src["repo"], src["chart"]))
    vers = re.findall(r"^    version: [\"']?([^\"'\s]+)", m.group(1), re.M)
    best = _newest(pin["scheme"], vers, current)
    if best is None:
        raise SourceError("%s: chart %s has no stable version" % (pin["id"], src["chart"]))
    _not_backwards(pin, current, best)
    return Resolved({"v": best}, None, src.get("notes"))


def github_branch(pin, current):
    src = pin["source"]
    doc = _gh("repos/%s/commits/%s" % (src["repo"], src["branch"]))
    values = {"v": doc["sha"]}
    date = (doc.get("commit") or {}).get("committer", {}).get("date")
    if date:
        values["epoch"] = str(int(datetime.datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()))
    if src.get("narhash"):
        values["narhash"] = narhash(src["repo"], doc["sha"])
    return Resolved(values, date, doc.get("html_url"))


def narhash(repo, rev):
    """The nix narHash of a GitHub tarball (needs nix on PATH)."""
    try:
        out = subprocess.run(["nix", "flake", "prefetch", "--json", "github:%s/%s" % (repo, rev)],
                             capture_output=True, text=True, check=True, timeout=900).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise SourceError("narHash of github:%s/%s needs nix: %s" % (repo, rev, e)) from None
    return json.loads(out)["hash"]


def _sum(pin, spec, values):
    """A sha256 from a published checksum file: spec {url, name?} (templates over the
    resolved values); the hash on the line naming `name`, or the file's first hash."""
    vals = registry_helpers(values)
    url = fmt(spec["url"], vals)
    text = http.get_text(url)
    name = fmt(spec["name"], vals) if spec.get("name") else None
    for line in text.splitlines():
        m = re.search(r"\b([0-9a-f]{64})\b", line)
        if m and (name is None or re.search(r"(^|[\s*/])%s\s*$" % re.escape(name), line.strip())):
            return m.group(1)
    raise SourceError("%s: %s lists no sha256%s" % (pin["id"], url, " for " + name if name else ""))


def registry_helpers(values):
    from .registry import helpers
    return helpers(values)


KINDS = {
    "oci": oci_source, "github-release": github_release, "pypi": pypi, "hf": hf, "apt-index": apt_index,
    "eat": eat, "llama": llama, "habana": habana, "json": json_source, "cmd": cmd, "helm": helm,
    "github-branch": github_branch,
}


def resolve(pin, current=None):
    if pin.get("frozen"):
        return Resolved({"v": current}, note=pin["frozen"])
    kind = pin["source"]["kind"]
    try:
        fn = KINDS[kind]
    except KeyError:
        raise SourceError("%s: unknown source kind %r" % (pin["id"], kind)) from None
    try:
        r = fn(pin, current)
        for key, spec in (pin["source"].get("derive") or {}).items():
            m = re.search(spec["regex"], str(r.values.get(spec.get("from", "v"), "")))
            if not m:
                raise SourceError("%s: %s=%r does not match %s" % (pin["id"], spec.get("from", "v"),
                                                                    r.values.get(spec.get("from", "v")), spec["regex"]))
            r.values[key] = m.group(1)
        for key, spec in (pin["source"].get("sums") or {}).items():
            r.values[key] = _sum(pin, spec, r.values)
    except http.HTTPError as e:
        raise SourceError("%s: %s" % (pin["id"], e)) from None
    conf = pin.get("confirm")
    if conf:
        other = dict(pin, source=conf, confirm=None)
        try:
            c = KINDS[conf["kind"]](other, current)
        except (SourceError, http.HTTPError, ValueError) as e:
            r.note = "unconfirmed: %s" % e
            r.values["unconfirmed"] = True
        else:
            key = conf.get("compare", "v")
            if c.values.get(key) != r.values.get(key):
                r.note = "unconfirmed: %s says %s=%s, %s says %s" % (
                    pin["source"]["kind"], key, r.values.get(key), conf["kind"], c.values.get(key))
                r.values["unconfirmed"] = True
            else:
                for k, val in c.values.items():
                    r.values.setdefault(k, val)
    return r


def now():
    return datetime.datetime.now(datetime.timezone.utc)

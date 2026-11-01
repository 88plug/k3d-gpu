"""The merge policy of a bump diff: auto or review, and why.

judge() reads only the diff (base..head), the registry, pins/known-issues.toml,
canary/engines.toml at head and hack/ledger-freshness's measured versions;
labels never count. "review" needs a human approval at head. "auto" still needs
green CI at head and, for each engine in `canary`, a canary verdict pass with hard
and exact true (hack/bot-merge checks both).

Matrix (family: what may go auto; everything else is review)
  k3s              patch
  plugin           patch, minor
  runtime-engine   patch, minor
  static-engine    patch, digest
  host-driver      none
  host-ppa         patch
  hf               a README/.gitattributes-only revision diff
  pypi, tools,
  telemetry,
  opt-in           patch, minor
  chrome           anything
  base-image       digest, patch (a minor or a line tag is a tag line change)
  eat-stock        none
Cross-cutting:
  - any major is review;
  - a 0.x minor counts as a major unless the pin has a runtime canary (a runtime
    or node engine of canary/engines.toml);
  - a pin whose canary engines canary/engines.toml does not define is review (no
    evidence can come for it);
  - a minor or major move of a component a SUPPORTED ledger-rules row measured is
    review (ledger-defaults);
  - a changed line that is no pin move (hack/pin-only-diff) is review
    (changes-defaults);
  - a known issue with adopt = "auto" that this diff fixes lets a patch or minor
    through for any family (a major is always review);
  - fixing a known issue makes the change urgent.
"""

import os
import re
import subprocess
import tomllib

from . import classes, ledgerinfo, pindiff, registry, sources
from . import versions as V

SEVERITY = ["none", "digest", "patch", "minor", "revision", "line", "major"]
AUTO = {
    "k3s": {"digest", "patch"},
    "plugin": {"digest", "patch", "minor"},
    "runtime-engine": {"digest", "patch", "minor"},
    "static-engine": {"digest", "patch"},
    "host-driver": set(),
    "host-ppa": {"patch"},
    "hf": set(),  # decided by the revision's file diff
    "pypi": {"digest", "patch", "minor"},
    "tools": {"digest", "patch", "minor"},
    "telemetry": {"digest", "patch", "minor"},
    "opt-in": {"digest", "patch", "minor"},
    "chrome": {"digest", "patch", "minor", "major", "line"},
    "base-image": {"digest", "patch"},
    "eat-stock": set(),
}


def worst(types):
    return max(types, key=SEVERITY.index) if types else "none"


def family_allows(family, t):
    return t in AUTO.get(family, set())


def canary_engines(tree):
    """{engine id: kind} from canary/engines.toml in tree ({} when there is none)."""
    text = tree.read("canary/engines.toml")
    if not text:
        return {}
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    return {e["id"]: e.get("kind", "") for e in doc.get("engine", []) if isinstance(e, dict) and e.get("id")}


def update_type(pin, old, new, digest_only=False, runtime_canary=False):
    if digest_only:
        return "digest"
    if old == new:
        return "none"
    try:
        t = V.update_type(pin["scheme"], old, new)
    except ValueError:
        return "line"
    if t == "minor" and not runtime_canary and V.zero_minor(pin["scheme"], old, new):
        return "major"  # 0.x: a minor may break like a major, and no runtime canary checks it
    return t


def changes(root, base, head, reg=None):
    """[{id, group, old, new, digest_only[, new_id]}] for every pin whose values
    (or image digests) differ between commits base and head."""
    reg = reg or registry.load(os.path.join(root, "pins", "registry.toml"))
    sides = []
    for which, ref in (("base", base), ("head", head)):
        try:
            e, g, sb = registry.all_pins(registry.Tree(root, ref), reg)
        except registry.RegistryError as err:
            raise registry.RegistryError("%s %s: %s" % (which, ref, err)) from None
        sides.append(({p["id"]: p for p in e + g}, sb))
    (bpins, bspans), (hpins, hspans) = sides
    out = []
    for pid in sorted(set(bpins) | set(hpins)):
        p = hpins.get(pid) or bpins.get(pid)
        old = registry.current(p, bspans.get(pid, [])) if pid in bpins else {}
        new = registry.current(p, hspans.get(pid, [])) if pid in hpins else {}
        odig = sorted(s.text for s in bspans.get(pid, []) if s.key == "digest")
        ndig = sorted(s.text for s in hspans.get(pid, []) if s.key == "digest")
        if old != new or odig != ndig:
            # the version stayed: only digests or checksums moved (a same-tag respin)
            same = bool(old) and bool(new) and old.get("v") == new.get("v")
            out.append({"id": pid, "group": p["group"], "old": old, "new": new, "digest_only": same})
    # a generated pin keyed by its tag reads as gone + new when its tag moved: pair them
    return pair_generated(out, bpins, hpins)


def pair_generated(rows, bpins, hpins):
    """Join a generated image pin that vanished (old tag) with the one that appeared
    (new tag) for the same image and group into one change."""
    gone = [r for r in rows if not r["new"] and r["old"]]
    new = [r for r in rows if r["new"] and not r["old"]]
    out = [r for r in rows if r not in gone and r not in new]
    for g in gone:
        gp = bpins[g["id"]]
        mate = next((n for n in new if hpins[n["id"]].get("generated") and gp.get("generated")
                     and hpins[n["id"]]["source"]["image"] == gp["source"]["image"]
                     and hpins[n["id"]]["group"] == gp["group"]), None)
        if mate:
            new.remove(mate)
            out.append({"id": g["id"], "new_id": mate["id"], "group": g["group"], "old": g["old"],
                        "new": mate["new"], "digest_only": False})
        else:
            out.append(g)
    return sorted(out + new, key=lambda r: r["id"])


def known_issues(text):
    """The [[issue]] entries of a pins/known-issues.toml text ([] when absent)."""
    if not text:
        return []
    return tomllib.loads(text).get("issue", [])


def site_values(tree, issue):
    """[(file, start, end, v)] of every v match of an issue's sites in tree."""
    out = []
    for s in issue.get("sites", []):
        text = tree.read(s.get("file", ""))
        if text is None:
            continue
        for m in re.compile(s["regex"], re.M).finditer(text):
            out.append((s["file"], m.start("v"), m.end("v"), m.group("v")))
    return out


def fixed_issues(base_tree, head_tree, issues):
    """{issue id: (issue, [(file, start, end)] at head)} for each issue with fixed_in
    set that this diff fixes: a site inside `affected` at base reads fixed_in or
    later at head."""
    out = {}
    for e in issues:
        fixed, scheme = e.get("fixed_in"), e.get("scheme")
        if not fixed or not scheme:
            continue
        before = {(f, i): v for i, (f, _, _, v) in enumerate(site_values(base_tree, e))}
        after = site_values(head_tree, e)
        spans = []
        for i, (f, a, b, v) in enumerate(after):
            old = before.get((f, i))
            if old is None or old == v:
                continue
            try:
                if V.in_range(scheme, old, e.get("affected") or "*") and V.compare(scheme, v, fixed) >= 0:
                    spans.append((f, a, b))
            except ValueError:
                continue
        if spans:
            out[e["id"]] = (e, spans)
    return out


def _owns(spans, file, a, b):
    return any(s.file == file and s.start < b and a < s.end for s in spans)


def judge(root, base, head, changes, reg=None, hf_diff=None, measured=None):
    reg = reg or registry.load(os.path.join(root, "pins", "registry.toml"))
    pins = {p["id"]: p for p in reg.get("pin", [])}
    hf_diff = hf_diff or sources.hf_diff
    reasons, review, types, canary, urgent, groups = [], False, [], set(), False, set()
    btree, htree = registry.Tree(root, base), registry.Tree(root, head)
    issues = known_issues(htree.read("pins/known-issues.toml"))
    fixed = fixed_issues(btree, htree, issues)
    engines = canary_engines(htree)
    if measured is None:
        measured = ledgerinfo.measured(root)
    try:
        _, _, head_spans = registry.all_pins(htree, reg)
    except registry.RegistryError:
        head_spans = {}

    def flag(msg):
        nonlocal review
        review = True
        reasons.append(msg)

    if fixed:
        urgent = True
        reasons.append("fixes known issue %s" % ", ".join(sorted(fixed)))
    if not changes:
        flag("no registry pin moved")
    for c in changes:
        pin = pins.get(c["id"]) or _generated(c)
        groups.add(c["group"])
        old, new = c["old"].get("v"), c["new"].get("v")
        names = list(pin.get("canary", []))
        missing = [e for e in names if e not in engines]
        runtime = any(engines.get(e) in ("runtime", "node") for e in names)
        t = update_type(pin, old, new, c.get("digest_only"), runtime)
        types.append(t)
        canary.update(e for e in names if e in engines)
        label = "%s %s -> %s (%s)" % (c["id"], old, new, t)
        spans = head_spans.get(c.get("new_id") or c["id"], [])
        mine = [e for e, sp in fixed.values() if any(_owns(spans, f, a, b) for f, a, b in sp)]
        adopt = any(e.get("adopt") == "auto" for e in mine)
        if pin["class"] == "eat-stock":
            flag("eat-stock: %s (always review)" % label)
            continue
        hold = (pin.get("hold") or {}).get("max")
        if hold and new:
            try:
                if V.compare(pin["scheme"], new, hold) >= 0:
                    flag("hold: %s reaches the hold %s (%s)" % (label, hold, pin["hold"]["reason"]))
                    continue
            except ValueError:
                pass
        if t == "major":
            flag("major: %s" % label)
            continue
        if missing:
            flag("no canary: %s names canary engine(s) %s that canary/engines.toml does not define"
                 % (label, ", ".join(missing)))
            continue
        if pin["family"] == "hf":
            try:
                kind, files = hf_diff(pin["source"]["model"], old, new)
            except Exception as e:  # noqa: BLE001  (a network failure reads as substantive)
                kind, files = "substantive", ["(file diff unavailable: %s)" % e]
            if kind != "readme-only" and not adopt:
                flag("hf: %s changes %s" % (label, ", ".join(files[:6]) or "files"))
            else:
                reasons.append("auto: %s, %s" % (label, "README-only diff" if kind == "readme-only" else "adopt=auto"))
            continue
        comp = pin.get("component")
        if comp and t in ("minor", "major"):
            rows = sorted(r for r, comps in measured.items() if comp in comps)
            if rows or measured is ledgerinfo.UNKNOWN:
                flag("ledger-defaults: %s moves %s, which %s measured" % (
                    label, comp, ", ".join(rows) if rows else "a SUPPORTED row may have (hack/ledger-freshness unavailable)"))
                continue
        if family_allows(pin["family"], t) or (adopt and t in ("patch", "minor", "digest")):
            reasons.append("auto: %s, family %s%s" % (label, pin["family"], ", known issue adopt=auto" if adopt else ""))
        else:
            flag("%s: %s is review for family %s" % ("tag line" if t == "line" else t, label, pin["family"]))
    ok, bad = pindiff.check(root, base, head, reg, groups or None)
    if not ok:
        flag("changes-defaults: %d changed line(s) are not pin moves: %s" % (len(bad), "; ".join(bad[:5])))
    # a bot writes only ours and eat-stock files: a ledger arm, a finished run's
    # record, EXPERIMENTS.md or a workflow in the diff is a human's, whatever its lines
    for path in changed_paths(root, base, head):
        cls = classes.effective(root, path)
        if cls not in ("ours", "eat-stock"):
            flag("class: %s is %s; no bot changes it" % (path, cls))
    if len(groups) > 1:
        flag("mixed groups: %s (one group per PR)" % ", ".join(sorted(groups)))
    if canary and not review:
        reasons.append("needs: CI green at head and a canary pass (hard and exact) for %s" % ", ".join(sorted(canary)))
    return {"verdict": "review" if review else "auto", "reasons": reasons,
            "group": sorted(groups)[0] if len(groups) == 1 else ("mixed" if groups else None),
            "update_type": worst(types), "urgent": urgent, "canary": sorted(canary),
            "fixes": sorted(fixed)}


def _generated(c):
    """A generated (images.default) pin, rebuilt from its id image:<group>:<name>:<tag>."""
    _, group, rest = c["id"].split(":", 2)
    name = rest.rsplit(":", 1)[0]
    cls = "eat-stock" if group == "eat-stock" else "ours"
    return {"id": c["id"], "class": cls, "group": group, "family": "eat-stock" if cls == "eat-stock" else "base-image",
            "scheme": "docker-suffix", "source": {"kind": "oci", "image": name}, "generated": True}


def changed_paths(root, base, head):
    """Every path the diff base..head adds, changes or deletes (a rename as both names)."""
    out = subprocess.run(["git", "-C", root, "diff", "--name-only", "--no-renames", base, head],
                         check=True, capture_output=True, text=True).stdout
    return sorted({p for p in out.splitlines() if p})


def labels(verdict):
    """The PR labels a verdict carries (display only: policy never reads them)."""
    out = ["autobump", "dependencies", "verdict:" + verdict["verdict"]]
    if verdict["verdict"] == "review":
        out.append("needs-review")
    for r in verdict["reasons"]:
        for key, lab in (("changes-defaults", "changes-defaults"), ("ledger-defaults", "ledger-defaults"),
                         ("fixes known issue", "fixes-known-issue")):
            if r.startswith(key) and lab not in out:
                out.append(lab)
    return out

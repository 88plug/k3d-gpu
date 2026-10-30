#!/usr/bin/env python3
"""The k3s/k3d patch overlay for an intel/enterprise-ai-solutions (EAS) checkout.

For each row of overlay.json it decides, at one EAS commit, and prints the
decision as `fact -> choice [source]`:

  skip         the row's needed_when rule is false for these facts
  drop-merged  a variant reverse-applies (git apply -R --check): upstream has it
  drop-fixed   a fixed_probe regex matches a file its glob selects, no file the
               glob selects matches the probe's `absent` regex (when it has one),
               and the knob the generator writes appears in the matched file (no
               knob: the match alone). A match whose knob is gone is a STOP:
               upstream fixed it under another name, and the generator's knob
               would do nothing.
  apply        the first variant, in order, that applies (git apply --check)
  STOP         none of the above: the patch has to be regenerated. The line
               names the upstream change that moved the hunk: a `moved` hint of
               the row when one matches and watches a file whose hunks no variant
               applies, else the last commit on those files.

Decisions read the commit (--rev, default HEAD) through a scratch index, never
the working tree, so they do not change once the overlay is applied. --apply
patches the working tree of the checkout, only when --rev is HEAD and no row
STOPs: every chosen patch the working tree lacks, in one `git apply`. A patch
it already carries (a re-run) is left as it is; a patch's files in any other
state STOP the run before anything is written.

Patch files are looked up in the overlay.json patch_dirs in order
(integrations/eas/patches, then the upstream drafts in
integrations/eat/upstream/patches), so a draft is never copied.

Usage:
  overlay.py --eas DIR --policy POLICY --nodes N [--rev REV] [--apply]
             [--json FILE|-] [--explain]
  overlay.py --eas DIR --check-verified   every overlay.json `verified` case at its sha
  overlay.py --eas DIR --watch            decisions at every `watch` ref present in DIR

POLICY is the kubernetes_cpu_policy the generator chose (nri-balloons,
kubelet-static or best-effort); N is the cluster's node count. Exit 0 when
nothing STOPs, 3 on a STOP (nothing applied), 1 when --check-verified finds a
moved decision, 2 on bad input.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
POLICIES = ("nri-balloons", "kubelet-static", "best-effort")
RULES = {
    "always": lambda f: True,
    "eas_owns_nri": lambda f: f["policy"] == "nri-balloons",
    "multi_node": lambda f: f["nodes"] > 1,
    "never": lambda f: False,
}
# The k3d-gpu cluster: one server, its own balloons policy kept (--watch).
K3D_GPU_FACTS = {"policy": "best-effort", "nodes": 1}


class Bad(Exception):
    """Input the overlay cannot work with (exit 2)."""


def glob_re(glob):
    """A path glob as a regex: ** spans directories, * and ? stay within one."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


def first_hunk(path):
    """The first hunk of a patch: (file, {"old"|"new": (stated start, lines)}, leading context)."""
    target, start, sides, ctx, changed = None, None, {"old": [], "new": []}, 0, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if start is None:
                if line.startswith("+++ "):
                    target = line[4:].rstrip("\n")
                    target = target[2:] if target.startswith("b/") else target
                m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?", line)
                if m and target:
                    start = {"old": int(m.group(1)), "new": int(m.group(3))}
                    want = {"old": int(m.group(2) or 1), "new": int(m.group(4) or 1)}
                continue
            if all(len(sides[s]) >= want[s] for s in sides):
                break
            tag, body = line[:1], line[1:]
            if tag == " ":
                sides["old"].append(body)
                sides["new"].append(body)
                ctx += 0 if changed else 1
            elif tag in ("-", "+"):
                sides["old" if tag == "-" else "new"].append(body)
                changed = True
            elif tag != "\\":
                break
    if start is None:
        raise Bad("no hunk in %s" % path)
    return target, {s: (start[s], sides[s]) for s in sides}, ctx


def landing(eas, path, side):
    """(file, line) where a patch's first change sits in the commit: the hunk's `side`
    lines ("old" before applying, "new" once applied) located nearest the line the
    hunk header states, as git apply locates them."""
    target, sides, ctx = first_hunk(path)
    stated, lines = sides[side]
    have = eas.text(target).splitlines(keepends=True)
    hits = [i + 1 for i in range(len(have) - len(lines) + 1) if have[i:i + len(lines)] == lines] if lines else []
    at = min(hits, key=lambda i: abs(i - stated)) if hits else stated
    return target, max(at + ctx, 1)


def touched(paths):
    """Files a set of patches change."""
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.startswith("+++ ") and line[4:].strip() != "/dev/null":
                    t = line[4:].strip()
                    t = t[2:] if t.startswith("b/") else t
                    if t not in out:
                        out.append(t)
    return out


class Eas:
    """Read-only views of one EAS commit, plus the working-tree apply."""

    def __init__(self, path, rev):
        self.path = os.path.abspath(path)
        top = self.git("rev-parse", "--show-toplevel", check=False)
        if top.returncode or os.path.realpath(top.stdout.strip()) != os.path.realpath(self.path):
            raise Bad("%s is not the top of a git checkout" % path)
        self.sha = self.resolve(rev)
        if not self.sha:
            raise Bad("%s: no commit %s" % (path, rev))
        self._files = None
        self._text = {}
        self._tmp = tempfile.TemporaryDirectory(prefix="eas-overlay-")
        self.index = os.path.join(self._tmp.name, "index")
        self.git("read-tree", "--index-output=" + self.index, self.sha)

    def git(self, *args, env=None, check=True):
        e = dict(os.environ, **(env or {}))
        r = subprocess.run(["git", "-C", self.path] + list(args), capture_output=True, text=True, env=e)
        if check and r.returncode:
            raise Bad("git %s: %s" % (" ".join(args), r.stderr.strip()))
        return r

    def resolve(self, rev):
        r = self.git("rev-parse", "--verify", "-q", rev + "^{commit}", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def files(self):
        if self._files is None:
            out = self.git("ls-tree", "-r", "-z", "--name-only", self.sha).stdout
            self._files = [p for p in out.split("\0") if p]
        return self._files

    def text(self, path):
        if path not in self._text:
            r = self.git("cat-file", "blob", "%s:%s" % (self.sha, path), check=False)
            self._text[path] = r.stdout if r.returncode == 0 else ""
        return self._text[path]

    def check(self, patches, reverse=False, include=None):
        """git apply --check at the commit, through the scratch index (only `include`'s hunks when given)."""
        args = (["apply", "--cached", "--check", "--whitespace=nowarn"] + (["-R"] if reverse else [])
                + (["--include=" + include] if include else []) + patches)
        return self.git(*args, env={"GIT_INDEX_FILE": self.index}, check=False).returncode == 0

    def last_commit(self, paths):
        r = self.git("log", "-1", "--format=%h %s", self.sha, "--", *paths, check=False)
        return r.stdout.strip()

    def worktree(self, patches, reverse=False, check=True):
        args = ["apply", "--whitespace=nowarn"] + (["--check"] if check else []) + (["-R"] if reverse else []) + patches
        return self.git(*args, check=False).returncode == 0


def load_spec(path):
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise Bad("%s: %s" % (path, e)) from None
    if spec.get("schema") != "k3d-gpu.eas-overlay/v1":
        raise Bad("%s: schema %r, want k3d-gpu.eas-overlay/v1" % (path, spec.get("schema")))
    for row in spec["rows"]:
        if row["needed_when"] not in RULES:
            raise Bad("row %s: needed_when %r is not one of %s" % (row["id"], row["needed_when"], ", ".join(RULES)))
        for v in row["variants"]:
            v["paths"] = [patch_path(spec, f) for f in v["files"]]
        for p in row["fixed_probe"] + row["moved"]:
            try:
                p["re"], p["glob_re"] = re.compile(p["regex"]), glob_re(p["glob"])
                p["absent_re"] = re.compile(p["absent"]) if p.get("absent") else None
            except re.error as e:
                raise Bad("row %s: regex %r: %s" % (row["id"], e.pattern, e)) from None
    return spec


def patch_path(spec, name):
    for d in spec["patch_dirs"]:
        p = os.path.join(ROOT, d, name)
        if os.path.isfile(p):
            return p
    raise Bad("patch %s is in none of %s" % (name, ", ".join(spec["patch_dirs"])))


def rel(path):
    return os.path.relpath(path, ROOT)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def probe(eas, row):
    """(state, file, line, knob): state "fixed", "knob-missing" or None."""
    missing = None
    for p in row["fixed_probe"]:
        selected = [path for path in eas.files() if p["glob_re"].match(path)]
        if p["absent_re"] and any(p["absent_re"].search(eas.text(path)) for path in selected):
            continue
        for path in selected:
            text = eas.text(path)
            m = p["re"].search(text)
            if not m:
                continue
            at = m.start("at") if "at" in p["re"].groupindex and m.start("at") >= 0 else m.start()
            knob = p.get("knob")
            if knob is None or re.search(r"(?<!\w)%s(?!\w)" % re.escape(knob), text):
                return "fixed", path, line_of(text, at), knob
            missing = missing or ("knob-missing", path, line_of(text, at), knob)
    return missing or (None, None, None, None)


def failing(eas, row):
    """The files of a row's patches whose hunks no variant touching them applies at this commit."""
    out = []
    for path in touched([p for v in row["variants"] for p in v["paths"]]):
        tries = [v["paths"] for v in row["variants"] if path in touched(v["paths"])]
        if not any(eas.check(t, include=path) for t in tries):
            out.append(path)
    return out


def moved_by(eas, row, bad):
    """The `moved` hint that watches one of the files `bad` and whose regex matches at this commit, or None."""
    for h in row["moved"]:
        if not any(h["glob_re"].match(b) for b in bad):
            continue
        for path in eas.files():
            if h["glob_re"].match(path) and h["re"].search(eas.text(path)):
                return h
    return None


def decide(spec, eas, facts):
    repo = spec["repo"]
    sha7 = eas.sha[:7]

    def at(path, line):
        return ("eas@%s %s:%d" % (sha7, path, line),
                "https://github.com/%s/blob/%s/%s#L%d" % (repo, eas.sha, path, line))

    out = []
    for row in spec["rows"]:
        rid, rule = row["id"], row["needed_when"]
        variants = [{"name": v["name"], "files": [rel(p) for p in v["paths"]],
                     "applies": eas.check(v["paths"]), "reverses": eas.check(v["paths"], reverse=True)}
                    for v in row["variants"]]
        d = {"id": rid, "what": row["what"], "needed_when": rule, "variants": variants,
             "variant": None, "files": [], "knob": next((p.get("knob") for p in row["fixed_probe"] if p.get("knob")), None),
             "moved_by": None}
        merged = next((v for v in variants if v["reverses"]), None)
        state, ppath, pline, pknob = probe(eas, row)
        applies = next((v for v in variants if v["applies"]), None)
        if not RULES[rule](facts):
            fact = {"eas_owns_nri": "%s kubernetes_cpu_policy=%s, needed only for nri-balloons" % (rid, facts["policy"]),
                    "multi_node": "%s nodes=%d, needed only on more than one node" % (rid, facts["nodes"]),
                    "never": "%s documentation only" % rid}[rule]
            d.update(decision="skip", fact=fact, source="overlay.json %s needed_when=%s" % (rid, rule),
                     permalink="overlay.json %s needed_when=%s: %s" % (rid, rule, spec["rules"][rule]))
        elif merged:
            path, line = landing(eas, os.path.join(ROOT, merged["files"][0]), "new")
            src, link = at(path, line)
            d.update(decision="drop-merged", variant=merged["name"], files=merged["files"],
                     fact="%s variant %s is already in the tree (it reverse-applies)" % (rid, merged["name"]),
                     source=src, permalink=link)
        elif state == "fixed":
            src, link = at(ppath, pline)
            how = "%s wired at %s:%d" % (pknob, ppath, pline) if pknob else "fixed at %s:%d" % (ppath, pline)
            d.update(decision="drop-fixed", fact="%s fixed upstream another way, %s" % (rid, how), source=src, permalink=link)
        elif state == "knob-missing":
            src, link = at(ppath, pline)
            last = eas.last_commit([ppath])
            d.update(decision="STOP", fact="%s fixed upstream at %s:%d, but the generator's knob %s is not there "
                                           "(last changed by %s)" % (rid, ppath, pline, pknob, last),
                     choice="STOP: regenerate %s and the knob the generator writes" % rid, source=src, permalink=link,
                     moved_by=last)
        elif applies:
            path, line = landing(eas, os.path.join(ROOT, applies["files"][0]), "old")
            src, link = at(path, line)
            if applies is variants[0]:
                fact = "%s variant %s applies" % (rid, applies["name"])
            else:
                fact = "%s variant %s does not apply, variant %s does" % (
                    rid, ", ".join(v["name"] for v in variants[:variants.index(applies)]), applies["name"])
            d.update(decision="apply", variant=applies["name"], files=applies["files"], fact=fact,
                     choice="apply " + " ".join(os.path.basename(f) for f in applies["files"]), source=src, permalink=link)
        else:
            paths = failing(eas, row) or touched([p for v in row["variants"] for p in v["paths"]])
            hint = moved_by(eas, row, paths)
            if hint:
                why, src = "the hunk moved in %s (%s)" % (hint["upstream"], hint["what"]), hint["upstream"]
                d["moved_by"] = hint["upstream"]
                link = src
            else:
                last = eas.last_commit(paths)
                why = "the files where its hunks fail (%s) were last changed by %s" % (", ".join(paths), last)
                src, link = "eas@%s %s" % (sha7, paths[0]), "https://github.com/%s/commits/%s/%s" % (repo, eas.sha, paths[0])
                d["moved_by"] = last
            d.update(decision="STOP", fact="%s no variant applies or reverses and no fix probe matches; %s" % (rid, why),
                     choice="STOP: regenerate %s" % rid, source=src, permalink=link)
        d.setdefault("choice", d["decision"])
        d["brief"] = d["decision"] + (":" + d["variant"] if d["decision"] in ("apply", "drop-merged") else "")
        out.append(d)
    return out


def text_lines(decisions, explain):
    return ["%s -> %s [%s]" % (d["fact"], d["choice"], d["permalink"] if explain else d["source"]) for d in decisions]


def run(args, spec):
    if args.policy not in POLICIES:
        raise Bad("--policy %r: want one of %s" % (args.policy, ", ".join(POLICIES)))
    if args.nodes < 1:
        raise Bad("--nodes %d: a cluster has at least one node" % args.nodes)
    facts = {"policy": args.policy, "nodes": args.nodes}
    eas = Eas(args.eas, args.rev)
    if args.apply and eas.sha != eas.resolve("HEAD"):
        raise Bad("--apply patches the working tree, so --rev must be HEAD (%s is not)" % args.rev)
    decisions = decide(spec, eas, facts)
    stop = any(d["decision"] == "STOP" for d in decisions)
    if args.json:
        doc = {"schema": "k3d-gpu.eas-overlay-decisions/v1", "repo": spec["repo"], "rev": eas.sha,
               "facts": facts, "stop": stop, "decisions": decisions}
        if args.json == "-":
            json.dump(doc, sys.stdout, indent=2)
            print()
        else:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
                f.write("\n")
    if args.json != "-":
        print("# overlay at eas@%s (%s), kubernetes_cpu_policy=%s, nodes=%d"
              % (eas.sha[:7], args.rev, facts["policy"], facts["nodes"]))
        print("\n".join(text_lines(decisions, args.explain)))
    if stop:
        print("STOP: the overlay does not fit eas@%s; regenerate the rows above that say STOP. Nothing was applied."
              % eas.sha[:7], file=sys.stderr)
        return 3
    if args.apply:
        return apply(args, eas, decisions)
    return 0


def apply(args, eas, decisions):
    """Each chosen patch the working tree lacks, in one git apply; one it already
    carries is left alone (a re-run). Any other state of a patch's files: STOP."""
    todo, done = [], []
    for d in decisions:
        if d["decision"] != "apply":
            continue
        patches = [os.path.join(ROOT, f) for f in d["files"]]
        if eas.worktree(patches):
            todo += patches
        elif eas.worktree(patches, reverse=True):
            done += patches
        else:
            todo = None
            break
    if todo is None or (todo and not eas.worktree(todo)):
        bad = touched([os.path.join(ROOT, f) for d in decisions if d["decision"] == "apply" for f in d["files"]])
        print("STOP: the working tree of %s differs from eas@%s in %s; reset it to the commit (git checkout -f %s) "
              "and run again. Nothing was applied." % (args.eas, eas.sha[:7], ", ".join(bad), eas.sha[:7]),
              file=sys.stderr)
        return 3
    if todo and not eas.worktree(todo, check=False):
        raise Bad("git apply failed in %s after its --check passed" % args.eas)
    if todo:
        print("applied %s to %s" % (" ".join(os.path.basename(p) for p in todo), args.eas))
    if done:
        print("already applied in %s: %s" % (args.eas, " ".join(os.path.basename(p) for p in done)))
    if not todo and not done:
        print("nothing to apply to %s" % args.eas)
    return 0


def check_verified(args, spec):
    bad = 0
    for v in spec["verified"]:
        try:
            eas = Eas(args.eas, v["rev"])
        except Bad as e:
            print("FAIL %s %s: %s (fetch it)" % (v["profile"], v["rev"][:7], e))
            bad += 1
            continue
        for c in v["cases"]:
            decisions = decide(spec, eas, {"policy": c["policy"], "nodes": c["nodes"]})
            got = {d["id"]: d["brief"] for d in decisions}
            label = "%s %s %s nodes=%d" % (v["profile"], eas.sha[:7], c["policy"], c["nodes"])
            moved = ["%s want %s, got %s" % (i, c["expect"].get(i), got.get(i))
                     for i in sorted(set(got) | set(c["expect"])) if got.get(i) != c["expect"].get(i)]
            summary = " ".join("%s=%s" % kv for kv in sorted(got.items()))
            if moved:
                bad += 1
                print("FAIL %s: %s" % (label, "; ".join(moved)))
            else:
                print("ok   %s: %s" % (label, summary))
            for line in text_lines(decisions, args.explain):
                print("     " + line)
    return 1 if bad else 0


def watch(args, spec):
    for w in spec["watch"]:
        try:
            eas = Eas(args.eas, w["ref"])
        except Bad:
            print("# %s: not in this clone (%s)" % (w["ref"], w["what"]))
            continue
        print("# %s = eas@%s (%s), kubernetes_cpu_policy=%s, nodes=%d"
              % (w["ref"], eas.sha[:7], w["what"], K3D_GPU_FACTS["policy"], K3D_GPU_FACTS["nodes"]))
        print("\n".join(text_lines(decide(spec, eas, K3D_GPU_FACTS), args.explain)))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--eas", required=True, help="EAS git checkout")
    ap.add_argument("--rev", default="HEAD", help="commit to decide at (default HEAD)")
    ap.add_argument("--policy", help="kubernetes_cpu_policy the generator chose")
    ap.add_argument("--nodes", type=int, help="nodes in the cluster")
    ap.add_argument("--apply", action="store_true", help="apply the chosen patches to the working tree")
    ap.add_argument("--json", metavar="FILE", help="also write the decisions as JSON to FILE (- for stdout only)")
    ap.add_argument("--explain", action="store_true", help="full permalinks instead of short sources")
    ap.add_argument("--check-verified", action="store_true", help="check every overlay.json verified case")
    ap.add_argument("--watch", action="store_true", help="decisions at every overlay.json watch ref")
    ap.add_argument("--spec", default=os.path.join(HERE, "overlay.json"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if args.check_verified:
            return check_verified(args, spec)
        if args.watch:
            return watch(args, spec)
        if args.policy is None or args.nodes is None:
            raise Bad("--policy and --nodes are required")
        return run(args, spec)
    except Bad as e:
        print("overlay: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

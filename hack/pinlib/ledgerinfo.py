"""What the ledger (EXPERIMENTS.md) says about pins: PENDING rows, the paths and
amendment aliases pins/classes gives each row, the engine versions SUPPORTED rows
measured, and the amendment stub for a PENDING row's runner pins.

Row verdicts come from ledger() in tests/ledger_drift_check.py (imported by path).
Measured versions come from `hack/ledger-freshness --json` ({"measured": {row:
{component: version}}}), kept for the rows console/ledger-rules.json drives
optimizer defaults from (SUPPORTED, with classes); when either is unreadable,
measured() returns UNKNOWN and the policy treats every component minor as
possibly measured (review).
"""

import fnmatch
import json
import os
import pathlib
import re
import subprocess


class _Unknown(dict):
    pass


UNKNOWN = _Unknown()


class Refused(Exception):
    pass


def rows(root):
    from .oci import load_path
    mod = load_path("ledger_drift_check", os.path.join(root, "tests", "ledger_drift_check.py"))
    return mod.ledger(pathlib.Path(root))


def rule_rows(root):
    """The rows of console/ledger-rules.json that drive optimizer defaults: SUPPORTED,
    with at least one class."""
    try:
        with open(os.path.join(root, "console", "ledger-rules.json"), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return {r["id"] for r in doc.get("rows", []) if r.get("verdict") == "SUPPORTED" and r.get("classes")}


def measured(root):
    """{row: {component: version}} that the SUPPORTED ledger-rules rows measured
    (hack/ledger-freshness --json); UNKNOWN when either source is unreadable."""
    tool = os.path.join(root, "hack", "ledger-freshness")
    rows = rule_rows(root)
    if not os.access(tool, os.X_OK) or rows is None:
        return UNKNOWN
    try:
        out = subprocess.run([tool, "--json"], cwd=root, capture_output=True, text=True, timeout=300, check=True).stdout
        found = json.loads(out).get("measured") or {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return UNKNOWN
    return {r: comps for r, comps in found.items() if r in rows}


def ledger_lines(root):
    """[(glob, [rows], [aliases])] from pins/classes `<glob> ledger <rows> amend=<aliases>` lines."""
    path = os.path.join(root, "pins", "classes")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 3 and parts[1] == "ledger":
                aliases = next((p[len("amend="):].split(",") for p in parts[3:] if p.startswith("amend=")), [])
                out.append((parts[0], parts[2].split(","), [a for a in aliases if a]))
    return out


def row_scope(root, row):
    """(globs, aliases) of a ledger row; Refused when pins/classes names it nowhere."""
    globs, aliases = [], []
    for glob, rs, al in ledger_lines(root):
        if row in rs:
            globs.append(glob)
            aliases += [a for a in al if a not in aliases]
    if not globs:
        raise Refused("pins/classes has no ledger line naming %s, so its runner paths are unknown" % row)
    return globs, aliases or [row]


def run_heading(text, names):
    rx = re.compile(r"^###\s+(?:%s)\s+run\b" % "|".join(re.escape(n) for n in names), re.M)
    m = rx.search(text)
    return m.group(0) if m else None


def next_u(text, aliases):
    rx = re.compile(r"^###\s+(?:%s)\s+amendment\s+U(\d+)\b" % "|".join(re.escape(a) for a in aliases), re.M)
    return max([int(n) for n in rx.findall(text)] + [0]) + 1


def paths_of(root, globs):
    out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    return [p for p in out if any(fnmatch.fnmatchcase(p, g) for g in globs)]


def refuse_unless_open(root, row, open_prs=()):
    """(globs, aliases, ledger text) of a row an amendment may move: PENDING, no
    `### <row or alias> run` heading, and no open PR (open_prs: [{number, files,
    added_headings}]) touching its paths or adding a run section. Refused otherwise."""
    verdict = rows(root).get(row)
    if verdict != "PENDING":
        raise Refused("%s is %s, not PENDING: a finished row's pins are historical" % (row, verdict or "not in the ledger"))
    globs, aliases = row_scope(root, row)
    with open(os.path.join(root, "EXPERIMENTS.md"), encoding="utf-8") as f:
        text = f.read()
    names = [row] + [a for a in aliases if a != row]
    hit = run_heading(text, names)
    if hit:
        raise Refused("%s has a run section (%r): its arms are fixed" % (row, hit.strip()))
    for pr in open_prs:
        touched = [p for p in pr.get("files", []) if any(fnmatch.fnmatchcase(p, g) for g in globs)]
        if touched:
            raise Refused("open PR #%s touches %s's paths (%s)" % (pr.get("number"), row, ", ".join(touched[:3])))
        if run_heading("\n".join(pr.get("added_headings", [])), names):
            raise Refused("open PR #%s adds a run section for %s" % (pr.get("number"), row))
    return globs, aliases, text


def runner_refs(root, globs):
    """{(path, written name, tag, digest)} of every pinned image reference
    (name:tag@sha256) in the row's paths."""
    from . import oci
    il = oci.images_lock()
    out = set()
    for p in paths_of(root, globs):
        try:
            with open(os.path.join(root, p), encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for m in il.PINNED_RE.finditer(text):
            _, tag, _ = il.parse_ref(m.group("ref") + "@" + m.group("digest"))
            if tag and tag != "latest":
                out.add((p, m.group("ref")[: -(len(tag) + 1)], tag, m.group("digest")))
    return out


def insert_after_family(text, names, stub):
    """text with stub inserted after the last `### <name> ...` section of any name;
    Refused when the ledger has none."""
    rx = re.compile(r"^###\s+(?:%s)\b.*$" % "|".join(re.escape(n) for n in names), re.M)
    hits = list(rx.finditer(text))
    if not hits:
        raise Refused("EXPERIMENTS.md has no ### section for %s to put the amendment after" % ", ".join(names))
    nxt = re.compile(r"^#{2,3}\s", re.M).search(text, hits[-1].end())
    at = nxt.start() if nxt else len(text)
    before = text[:at].rstrip("\n") + "\n\n"
    return before + stub.rstrip("\n") + "\n\n" + text[at:].lstrip("\n")


def propose_amendment(root, row, date, resolve, open_prs=(), write=False):
    """Move a PENDING row's runner image pins to their newest same-shape tags under a
    dated amendment stub. resolve(name, tag) -> (new tag, new digest) or None.
    Returns (stub markdown, [(path, old ref, new ref)]); with write, edits the
    runner files and puts the stub into EXPERIMENTS.md."""
    globs, aliases, text = refuse_unless_open(root, row, open_prs)
    moves = []
    for path, name, tag, digest in sorted(runner_refs(root, globs)):
        got = resolve(name, tag)
        if got and (got[0], got[1]) != (tag, digest):
            moves.append((path, "%s:%s@%s" % (name, tag, digest), "%s:%s@%s" % (name, got[0], got[1])))
    if not moves:
        raise Refused("%s's runner image pins are at their newest tags already" % row)
    alias = aliases[0]
    n = next_u(text, aliases)
    names = sorted({(o.split("@")[0], m.split("@")[0]) for _, o, m in moves})
    title = "; ".join("%s to %s" % (o, m.rsplit(":", 1)[1]) for o, m in names[:3]) + (
        " and %d more" % (len(names) - 3) if len(names) > 3 else "")
    lines = ["### %s amendment U%d: %s (%s)" % (alias, n, title, date), "",
             "Proposed on %s by the Freshness workflow's propose-amendment job (a human dispatch) for %s," % (date, row),
             "which is PENDING with no run section. It must be pushed before any measurement of %s;" % row,
             "the owner confirms it by merging this PR, never a bot. Until then the arms stay as",
             "pre-registered.", "", "Runner image pins moved to the newest tag of the same shape:", ""]
    for path, old, new in moves:
        lines.append("- `%s`: `%s` to `%s`." % (path, old, new))
    stub = "\n".join(lines) + "\n"
    if write:
        for path in sorted({p for p, _, _ in moves}):
            full = os.path.join(root, path)
            with open(full, encoding="utf-8") as f:
                body = f.read()
            for p, old, new in moves:
                if p == path:
                    body = body.replace(old, new)
            with open(full, "w", encoding="utf-8") as f:
                f.write(body)
        with open(os.path.join(root, "EXPERIMENTS.md"), "w", encoding="utf-8") as f:
            f.write(insert_after_family(text, aliases + [row], stub))
    return stub, moves

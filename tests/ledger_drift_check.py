#!/usr/bin/env python3
"""Ledger drift guard: fail when a doc states a hypothesis verdict that differs
from the Hypotheses table in EXPERIMENTS.md.

The ledger verdict of a row is the first word of its last cell (PENDING,
SUPPORTED, REFUTED or INCONCLUSIVE). Scanned: README.md, docs/*.md and
docs/intel/*.md, outside fenced code blocks, inline code and HTML comments;
and, in prose form only, every line of scripts/k3d-gpu (its --help text,
messages and comments) and console/*.py (the predictor's output labels).
A doc states a verdict for row Hn in any of these forms:

  prose      "H5 is PENDING", "(H1) is INCONCLUSIVE", "H28 (PENDING)": the id,
             then the verdict word in the same clause (no '.', ';' or '|' and no
             other id between them, at most 80 characters).
  reference  "PENDING [ledger:H1]": a verdict word right before the reference.
  table row  a table row that names exactly one id and has a cell whose text
             starts with a verdict word, as in "| [ledger:H30] | ... | PENDING |".
  heading    "### REFUTED (...)": every table row below it, up to the next
             heading, whose first cell lists ids.
  list line  "PENDING with a pre-registration: H19 (...), H26 (...)": a line
             that opens with a verdict word and a colon; every id on it.
  count      "Six hypotheses are SUPPORTED", "Eight are PENDING": the number
             must equal the ledger's count for that verdict.

A row id may carry one lowercase letter (H1b, a re-run row of H1).

Usage: tests/ledger_drift_check.py [repo-root]
"""

import pathlib
import re
import sys

VERDICTS = ("PENDING", "SUPPORTED", "REFUTED", "INCONCLUSIVE")
V = "|".join(VERDICTS)
NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}

ID = re.compile(r"(?<![\w:-])H(\d+[a-z]{0,2})(?![\w-])")
REF = re.compile(r"\[ledger:H(\d+[a-z]{0,2})\]")
PROSE = re.compile(r"(?<![\w:-])H(\d+[a-z]?)(?![\w-])([^.;|]{0,80}?)\b(" + V + r")\b")
BEFORE_REF = re.compile(r"\b(" + V + r")\**\s*\[ledger:H(\d+[a-z]?)\]")
HEADING = re.compile(r"^#+\s+\**(" + V + r")\b")
LIST_LINE = re.compile(r"^\s*(?:[-*]\s+)?\**(" + V + r")\b[^:]{0,40}:")
COUNT = re.compile(r"\b(\d+|" + "|".join(NUMBERS) + r")\s+(?:hypotheses\s+|rows\s+)?(?:are|is)\s+(" + V + r")\b",
                   re.IGNORECASE)


def ledger(root):
    """{"H1": "INCONCLUSIVE", ...} from the Hypotheses table."""
    rows, inside = {}, False
    for line in (root / "EXPERIMENTS.md").read_text().splitlines():
        if line.startswith("## "):
            inside = line.startswith("## Hypotheses")
        m = re.match(r"^\|\s*(H\d+[a-z]{0,2})\s*\|", line)
        if not (inside and m):
            continue
        cells = [c for c in re.sub(r"\\\|", "", line).split("|")]
        last = cells[-1] if cells[-1].strip() else cells[-2]
        word = re.match(r"\s*\**\s*([A-Z]+)", last)
        rows[m.group(1)] = word.group(1) if word and word.group(1) in VERDICTS else "INVALID"
    return rows


def visible_lines(text):
    """(line number, line) with fenced code, inline code and HTML comments blanked."""
    fence, comment = None, False
    for n, line in enumerate(text.splitlines(), 1):
        t = line.lstrip()
        if fence:
            if t.startswith(fence) and not t[len(fence):].strip(fence[0]).strip():
                fence = None
            continue
        m = re.match(r"(`{3,}|~{3,})", t)
        if m:
            fence = m.group(1)
            continue
        s = line
        if comment:
            if "-->" not in s:
                continue
            s, comment = s.split("-->", 1)[1], False
        while "<!--" in s:
            head, tail = s.split("<!--", 1)
            if "-->" in tail:
                s = head + tail.split("-->", 1)[1]
            else:
                s, comment = head, True
        yield n, re.sub(r"`[^`]*`", "", s)


def ids_in(text):
    return {"H" + i for i in ID.findall(text)} | {"H" + i for i in REF.findall(text)}


def check_file(path, rel, rows):
    errors, section, prev = [], None, ""

    def expect(n, hid, said, how):
        have = rows.get(hid)
        if have is None:
            # A bare id outside the ledger (an H.264-style token) is not a row;
            # a [ledger:Hn] reference to a missing row is.
            if how == "verdict before reference":
                errors.append(f"{rel}:{n}: {hid} ({how}) is not in the EXPERIMENTS.md hypothesis table")
        elif have != said:
            errors.append(f"{rel}:{n}: {hid} reads {said} ({how}) but EXPERIMENTS.md says {have}")

    for n, line in visible_lines(path.read_text()):
        h = HEADING.match(line)
        if h:
            section, prev = h.group(1), ""
            continue
        if line.startswith("#"):
            section, prev = None, ""
            continue
        seen = set()
        for m in BEFORE_REF.finditer(line):
            seen.add("H" + m.group(2))
            expect(n, "H" + m.group(2), m.group(1), "verdict before reference")
        # Prose on this line, then prose split over the previous line and this one.
        before = prev.replace("[ledger:", "[")
        joined = before + " " + line.replace("[ledger:", "[")
        for m in PROSE.finditer(line.replace("[ledger:", "[")):
            hid = "H" + m.group(1)
            if ID.search(m.group(2)) or hid in seen:
                continue
            seen.add(hid)
            expect(n, hid, m.group(3), "prose")
        for m in PROSE.finditer(joined):
            hid = "H" + m.group(1)
            if m.start() < len(before) < m.end() and not ID.search(m.group(2)) and hid not in seen:
                seen.add(hid)
                expect(n, hid, m.group(3), "prose")
        prev = "" if not line.strip() or line.lstrip().startswith("|") else line
        if line.lstrip().startswith("|"):
            cells = line.strip().strip("|").split("|")
            ids = ids_in(line)
            said = [re.match(r"\s*\**(" + V + r")\b", c) for c in cells]
            said = {m.group(1) for m in said if m}
            if len(ids) == 1 and len(said) == 1:
                hid = next(iter(ids))
                if hid not in seen:
                    expect(n, hid, next(iter(said)), "table cell")
            if section and cells and re.fullmatch(r"(?:[\s,\-\]]|\[ledger:|H\d+[a-z]{0,2})*", cells[0]) and ids_in(cells[0]):
                for hid in sorted(ids_in(cells[0])):
                    expect(n, hid, section, "section heading")
        lm = LIST_LINE.match(line)
        if lm:
            for hid in sorted(ids_in(line)):
                expect(n, hid, lm.group(1), "verdict list")
        for m in COUNT.finditer(line):
            word, verdict = m.group(1).lower(), m.group(2)
            if verdict != m.group(2).upper():
                continue
            said = int(word) if word.isdigit() else NUMBERS[word]
            have = sum(1 for v in rows.values() if v == verdict)
            if said != have:
                errors.append(f"{rel}:{n}: says {said} {verdict} but EXPERIMENTS.md has {have}")
    return errors


def check_script(path, rel, rows):
    """Prose verdicts ("H24 PENDING", "H29, PENDING") on any line of a script."""
    errors = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        for m in PROSE.finditer(line):
            hid = "H" + m.group(1)
            if ID.search(m.group(2)) or hid not in rows or rows[hid] == m.group(3):
                continue
            errors.append(f"{rel}:{n}: {hid} reads {m.group(3)} (prose) but EXPERIMENTS.md says {rows[hid]}")
    return errors


def main(argv):
    root = pathlib.Path(argv[1] if len(argv) > 1 else pathlib.Path(__file__).resolve().parent.parent)
    rows = ledger(root)
    bad = sorted(h for h, v in rows.items() if v == "INVALID")
    files = [root / "README.md"] if (root / "README.md").is_file() else []
    files += sorted((root / "docs").glob("*.md")) + sorted((root / "docs" / "intel").glob("*.md"))
    errors = [f"EXPERIMENTS.md: {h} has no verdict word in its last cell" for h in bad]
    for f in files:
        errors += check_file(f, str(f.relative_to(root)), rows)
    code = [root / "scripts" / "k3d-gpu"] + sorted((root / "console").glob("*.py"))
    for f in (f for f in code if f.is_file()):
        files.append(f)
        errors += check_script(f, str(f.relative_to(root)), rows)
    if errors:
        print("\n".join(errors))
        print(f"ledger_drift_check: {len(errors)} verdict(s) disagree with EXPERIMENTS.md")
        return 1
    print(f"ledger_drift_check: {len(files)} file(s) agree with {len(rows)} ledger rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

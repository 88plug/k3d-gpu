"""Golden substitution: replace each exact old pin string with its new one in tests/**.

A test that names a pinned value (a golden transcript, a recorded image list) moves
with the pin. Only tests/** is written; never a ledger, historical, arm or
eat-stock path, nor .github/. Idempotent: a second run with the same pairs
changes nothing, even when an old string is part of its new one (1.37 -> 1.37.1).
"""

import os
import subprocess

from . import classes

MASK = "\x00{%d}\x00"


def substitute_text(text, pairs):
    """text with every old string of pairs [(old, new)] replaced, longest first."""
    pairs = sorted({(o, n) for o, n in pairs if o and o != n}, key=lambda p: -len(p[0]))
    news = sorted({n for _, n in pairs}, key=len, reverse=True)
    masked = text
    for i, n in enumerate(news):
        masked = masked.replace(n, MASK % i)
    for o, n in pairs:
        masked = masked.replace(o, MASK % news.index(n))
    for i, n in enumerate(news):
        masked = masked.replace(MASK % i, n)
    return masked


def test_files(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "tests"], capture_output=True, check=True).stdout
        paths = [p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
        for d, _, fs in os.walk(os.path.join(root, "tests")):
            paths += [os.path.relpath(os.path.join(d, f), root) for f in fs]
    return sorted(p for p in paths if p.startswith("tests/") and classes.writable(root, p))


def live_pairs(root, pairs):
    """The pairs whose old string no file outside tests/ holds any more (prose and
    recorded data aside). An old string still pinned elsewhere (a ledger arm, a
    historical run) may be what a test checks, so it stays."""
    from . import oci
    texts = []
    for p in oci.images_lock().scanned_files(root):
        if p.startswith("tests/"):
            continue
        try:
            with open(os.path.join(root, p), encoding="utf-8") as f:
                texts.append(f.read())
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
    blob = "\x00".join(texts)
    return [(o, n) for o, n in pairs if o and o != n and o not in blob]


def substitute(root, pairs, paths=None):
    """Apply pairs to tests/** (or the given paths, still only under tests/); the
    paths changed. Only pairs whose old string is gone from the rest of the repo."""
    pairs = live_pairs(root, pairs)
    changed = []
    for p in paths if paths is not None else test_files(root):
        if not p.startswith("tests/") or not classes.writable(root, p):
            continue
        full = os.path.join(root, p)
        try:
            with open(full, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        new = substitute_text(text, pairs)
        if new != text:
            with open(full + ".tmp", "w", encoding="utf-8") as f:
                f.write(new)
            os.chmod(full + ".tmp", os.stat(full).st_mode)
            os.replace(full + ".tmp", full)
            changed.append(p)
    return changed

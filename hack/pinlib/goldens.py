"""Golden substitution: replace each exact old pin string with its new one in tests/**.

A test that names a pinned value (a golden transcript, a recorded image list) moves
with the pin. Only tests/** is written; never a ledger, historical, arm or
eat-stock path, nor .github/. A pair leaves a test alone when
  - another pin's site outside tests/ still holds the old string (two images at
    one digest: the test may check the other one);
  - the test names a file outside tests/ that still holds it (it reads that
    record: a ledger arm's or a historical run's pin);
  - the old string is a bare version (a site's value alone, no image name, no
    key) and the test is code (.py, .sh or a #! script): there it is as likely
    the test's own input, as in a version-compare case. In a recorded file (a
    golden transcript, a fixture) it moves.
Prose and records that no test names hold nothing back. Idempotent: a second run
with the same pairs changes nothing, even when an old string is part of its new
one (1.37 -> 1.37.1).
"""

import os
import re
import subprocess

from . import classes

MASK = "\x00{%d}\x00"
BARE = re.compile(r"[A-Za-z0-9._+~-]+")
HEX = re.compile(r"(sha256:)?[0-9a-f]{40,64}")


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


def _tracked(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
        return sorted(p for p in out.decode().split("\0") if p)
    except (OSError, subprocess.CalledProcessError):
        paths = []
        for d, ds, fs in os.walk(root):
            ds[:] = [x for x in ds if x != ".git"]
            paths += [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for f in fs]
        return sorted(paths)


def _read(root, path):
    try:
        with open(os.path.join(root, path), encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def test_files(root):
    return sorted(p for p in _tracked(root) if p.startswith("tests/") and classes.writable(root, p))


def is_code(path, text):
    """A test script, as opposed to a recorded file (a golden, a fixture)."""
    return path.endswith((".py", ".sh")) or text.startswith("#!")


def is_bare(s):
    """A version or tag on its own (v1.37.1-k3s1, release-1.1.0), not a digest,
    a revision or a string with an image name or key around it."""
    return bool(BARE.fullmatch(s)) and not HEX.fullmatch(s)


def site_texts(root):
    """Every string a registry pin's site outside tests/ holds now: each site's
    whole match and each of its values."""
    from . import registry
    path = os.path.join(root, "pins", "registry.toml")
    if not os.path.exists(path):
        return set()
    tree = registry.Tree(root)
    _, _, spans_by = registry.all_pins(tree, registry.load(path))
    out = set()
    for spans in spans_by.values():
        for s in spans:
            if s.file.startswith("tests/"):
                continue
            out.add(s.text)
            if s.match:
                out.add(tree.read(s.file)[s.match[0]:s.match[1]])
    return out


def live_pairs(root, pairs):
    """The pairs whose old string no pin site outside tests/ holds any more."""
    held = site_texts(root)
    return [(o, n) for o, n in pairs if o and o != n and o not in held]


def substitute(root, pairs, paths=None):
    """Apply pairs to tests/** (or the given paths, still only under tests/); the
    paths changed. See the module docstring for the pairs a test keeps."""
    pairs = live_pairs(root, pairs)
    tests = {}
    for p in paths if paths is not None else test_files(root):
        if p.startswith("tests/") and classes.writable(root, p):
            text = _read(root, p)
            if text is not None:
                tests[p] = text
    # the files outside tests/ that still hold each old string some test holds
    wanted = {o for o, _ in pairs if any(o in t for t in tests.values())}
    holders = {o: [] for o in wanted}
    if wanted:
        for p in _tracked(root):
            if p.startswith("tests/"):
                continue
            text = _read(root, p)
            if text is None:
                continue
            for o in wanted:
                if o in text:
                    holders[o].append(p)
    changed = []
    for p, text in sorted(tests.items()):
        code = is_code(p, text)
        mine = [(o, n) for o, n in pairs if o in text and not (code and is_bare(o))
                and not any(h in text for h in holders.get(o, ()))]
        new = substitute_text(text, mine)
        if new != text:
            full = os.path.join(root, p)
            with open(full + ".tmp", "w", encoding="utf-8") as f:
                f.write(new)
            os.chmod(full + ".tmp", os.stat(full).st_mode)
            os.replace(full + ".tmp", full)
            changed.append(p)
    return changed

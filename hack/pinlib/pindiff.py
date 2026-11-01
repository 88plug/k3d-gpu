"""Is a diff only pin moves? The mechanical meaning of "changes no defaults".

A changed line passes when it pairs with a line on the other side that differs
only in version, digest, revision or ISO-date tokens, or when it lies inside a
declared site (the full match of a registry site or derived regex at head) or in a
group's hook output. Anything else (a new line, a reworded line, a flag) is
reported, and hack/pins policy labels the PR changes-defaults.
"""

import fnmatch
import re
import subprocess

from . import registry

TOKENS = re.compile("|".join((
    r"sha256:[0-9a-f]{64}",
    r"sha256-[A-Za-z0-9+/=]{43,44}",                 # nix narHash
    r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])",       # git / Hugging Face revisions
    r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])",       # bare sha256
    r"(?<![0-9a-f])[0-9a-f]{7,12}(?![0-9a-f])(?=…)",  # a digest cut short in prose
    r"\d{4}-\d{2}-\d{2}",                            # ISO date
    r"(?<!\d)\d{8}(?!\d)",                           # YYYYMMDD
    r"(?<!\d)\d{9,11}(?!\d)",                        # epoch seconds (flake.lock lastModified)
    r"(?<![\w.])b\d{4,}(?!\w)",                      # llama.cpp build
    r"(?<![\w])v?\d+(?:\.\d+)+(?:[-+~](?:[0-9A-Za-z]+[.~+-]?)*[0-9A-Za-z])?",  # versions
    r"(?<![\w.])v\d+(?![\w.])",
)))


def normalize(line):
    return TOKENS.sub("\x00", line)


def _run(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, check=True).stdout


def hunks(root, base, head):
    """{path: [(old_start, [removed lines], new_start, [added lines])]}, plus the
    paths added, deleted or binary."""
    out, odd = {}, []
    for row in _run(root, "diff", "--name-status", "--no-renames", base, head).splitlines():
        status, path = row.split("\t", 1)
        if status[0] in "AD":
            odd.append((path, "added" if status[0] == "A" else "deleted"))
    diff = _run(root, "diff", "--unified=0", "--no-renames", "--no-color", base, head)
    path = None
    cur = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("Binary files"):
            m = re.search(r" and b/(.*) differ$", line)
            if m:
                odd.append((m.group(1), "binary"))
        elif line.startswith("@@") and path:
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            cur = (int(m.group(1)), [], int(m.group(2)), [])
            out.setdefault(path, []).append(cur)
        elif cur is not None and path and line[:1] in "+-" and not line.startswith(("+++", "---")):
            (cur[3] if line[0] == "+" else cur[1]).append(line[1:])
    return out, odd


def site_lines(tree, reg, spans_by):
    """{path: set(line numbers)} that hold a site or derived match at this tree."""
    lines = {}
    for spans in spans_by.values():
        for s in spans:
            text = tree.read(s.file)
            a, b = s.match or (s.start, s.end)
            first = text.count("\n", 0, a) + 1
            last = text.count("\n", 0, max(a, b - 1)) + 1
            lines.setdefault(s.file, set()).update(range(first, last + 1))
    return lines


RELEASE_ROW = re.compile(r"^\| \d{4}-\d{2}-\d{2} \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \|$")


def check(root, base, head, reg=None, groups=None):
    """(ok, [offending "path:line: text" strings]). groups: the groups whose hook
    outputs count (default: every group)."""
    reg = reg or registry.load()
    confs = [c for g, c in reg.get("groups", {}).items() if groups is None or g in groups]
    outputs = [g for conf in confs for g in conf.get("outputs", [])]
    history = any("readme-history" in conf.get("hooks", []) for conf in confs)
    by_path, odd = hunks(root, base, head)
    bad = ["%s: file %s" % (p, how) for p, how in odd if not any(fnmatch.fnmatchcase(p, g) for g in outputs)]
    sites = {}
    for ref, which in ((head, "head"), (base, "base")):
        tree = registry.Tree(root, ref)
        try:
            _, _, spans_by = registry.all_pins(tree, reg)
        except registry.RegistryError:
            spans_by = {}
        sites[which] = site_lines(tree, reg, spans_by)
    for path, hs in sorted(by_path.items()):
        if any(fnmatch.fnmatchcase(path, g) for g in outputs):
            continue
        for ostart, removed, nstart, added in hs:
            if history and path == "README.md" and not removed and all(RELEASE_ROW.match(a) for a in added):
                continue
            for i in range(max(len(removed), len(added))):
                r = removed[i] if i < len(removed) else None
                a = added[i] if i < len(added) else None
                if r is not None and a is not None and normalize(r) == normalize(a):
                    continue
                in_site = ((a is None or nstart + i in sites["head"].get(path, ()))
                           and (r is None or ostart + i in sites["base"].get(path, ())))
                if in_site and r is not None and a is not None:
                    continue
                if a is not None:
                    bad.append("%s:%d: +%s" % (path, nstart + i, a.strip()[:160]))
                else:
                    bad.append("%s:%d: -%s" % (path, ostart + i, r.strip()[:160]))
    return not bad, bad

"""The pin class of a path: ours, eat-stock, arm, historical or unclassified.

pins/classes, read by hack/pin-classes (loaded here as a module, so a whole-tree
scan costs one parse), is the authority: a ledger path is an arm while one of its
rows is PENDING and historical otherwise; a bench/ or oracle/ file that holds a pin
and that no line classifies is unclassified. A bumper writes only ours and
eat-stock paths, and never .github/. In a tree without pins/classes (a scratch test
repo) the built-in table below answers instead: bench/, research/, data/ and the
finished records are not writable there.
"""

import fnmatch
import os

from .oci import load_path

# first match wins
BUILTIN = (
    (".github/*", "bot-never"),
    ("console/matrix.v1.json", "historical"),
    ("oracle/seed/*", "historical"),
    ("EXPERIMENTS.md", "historical"),
    ("integrations/eat/*", "eat-stock"),
    ("integrations/eas/*", "eat-stock"),
    ("examples/kuberay/*", "eat-stock"),
    ("examples/agents/litellm-gateway/*", "eat-stock"),
    ("contrib/eat/*", "eat-stock"),
    ("bench/*", "historical"),
    ("research/*", "historical"),
    ("data/*", "historical"),
)

_cache = {}


def builtin(path):
    for glob, cls in BUILTIN:
        if fnmatch.fnmatchcase(path, glob):
            return cls
    return "ours"


def _table(root):
    """(pin-classes module, lines, ledger) for root, or None without pins/classes."""
    key = ("table", root)
    if key not in _cache:
        tool, conf = os.path.join(root, "hack", "pin-classes"), os.path.join(root, "pins", "classes")
        if os.path.exists(tool) and os.path.exists(conf):
            mod = load_path("pin_classes", tool)
            lines, _ = mod.load(conf)
            _cache[key] = (mod, lines, mod.read_ledger(root))
        else:
            _cache[key] = None
    return _cache[key]


def effective(root, path):
    key = (root, path)
    if key in _cache:
        return _cache[key]
    if path.startswith(".github/"):
        cls = "bot-never"  # bumpers never write a workflow, whatever pins/classes says
    elif path == "EXPERIMENTS.md":
        cls = "historical"  # the ledger changes only by a human's amendment
    else:
        table = _table(root)
        if table is None:
            cls = builtin(path)
        else:
            mod, lines, ledger = table
            text = mod.read_text(root, path) if mod.guarded(path) else None
            cls = mod.effective(lines, ledger, path, text)[0]
    _cache[key] = cls
    return cls


def writable(root, path):
    """Whether a bumper may write path."""
    return effective(root, path) in ("ours", "eat-stock")

"""Version helpers the bumper needs on top of hack/pinver.py.

hack/pinver.py is the one version library (schemes, order, ranges, update
types); this module only adds what a bumper asks beyond it:

  is_stable(scheme, v)     not a prerelease, and no nightly/dev/snapshot word in a
                           docker tag; a word-only tag (trixie-slim) is stable
  word_tag(scheme, v)      a docker tag with no number (trixie-slim): no order,
                           only its digest moves
  same_shape(scheme, a, b) b orders against a, and a docker tag keeps its prefix,
                           suffix and number of core parts
  update_type(scheme, old, new)
                           none | patch | minor | major | line | revision:
                           pinver's type, plus "line" for a docker tag whose
                           core has at most two parts (busybox:1.37, ubuntu:26.04,
                           fedora:44) or whose prefix or suffix changed, and
                           "revision" for an hf-sha
  zero_minor(scheme, old, new)
                           a minor move of a 0.x version (it may break like a major)
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pinver  # noqa: E402  (hack/pinver.py)

SCHEMES = pinver.SCHEMES
parse = pinver.parse
compare = pinver.compare
comparable = pinver.comparable
in_range = pinver.in_range

_UNSTABLE_WORD = re.compile(r"(?:^|[-._])(?:rc|alpha|beta|dev|pre|preview|nightly|snapshot|test|edge)\d*(?:$|[-._])",
                            re.I)


def word_tag(scheme, v):
    return scheme == "docker-suffix" and not re.search(r"\d", str(v))


def is_stable(scheme, v):
    if word_tag(scheme, v):
        return v != "latest"
    if pinver.is_prerelease(scheme, v):
        return False
    if scheme == "docker-suffix":
        p = parse(scheme, v)
        return not any(_UNSTABLE_WORD.search(x) for x in p.flavour)
    return True


def _core_parts(v):
    m = re.match(r"^\D*(\d+(?:\.\d+)*)", v)
    return len(m.group(1).split(".")) if m else 0


def same_shape(scheme, a, b):
    """Whether b is a candidate for a pin at a: they order, and a docker tag keeps
    its prefix, suffix and number of core parts (busybox 1.37 never becomes 1.37.1)."""
    if not comparable(scheme, a, b):
        return False
    return scheme != "docker-suffix" or _core_parts(a) == _core_parts(b)


def update_type(scheme, old, new):
    if old == new:
        return "none"
    if scheme == "hf-sha":
        return "revision"
    if scheme == "docker-suffix":
        if word_tag(scheme, old) or word_tag(scheme, new) or not comparable(scheme, old, new):
            return "line"
        if _core_parts(old) <= 2 or _core_parts(new) <= 2:
            return "line"
    t = pinver.update_type(scheme, old, new)
    return "none" if t == "digest" else t


def zero_minor(scheme, old, new):
    try:
        return update_type(scheme, old, new) == "minor" and parse(scheme, old).release[0] == 0
    except ValueError:
        return False

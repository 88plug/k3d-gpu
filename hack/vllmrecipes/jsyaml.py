"""YAML read the way the recipes site reads it: js-yaml 4.1.1's DEFAULT_SCHEMA.

recipes.vllm.ai parses every recipe and taxonomy.yaml with js-yaml 4.1.1
(vllm-project/recipes pnpm-lock.yaml), whose default schema is the YAML 1.2 core
schema (null, bool, int, float) plus timestamp and merge. PyYAML's SafeLoader is
YAML 1.1 (yes/no/on/off are booleans, 0777 is octal, 1:30 is base 60), so this
loader swaps its implicit resolvers for js-yaml's (lib/type/*.js) and builds maps
the way a JS object holds them: keys are strings, array-index keys come first in
ascending order, and a repeated key keeps its first position with the later value.
Numbers with no fraction become ints (JS has one number type); a timestamp becomes
the string JSON.stringify gives the Date.
"""

import datetime
import math
import re

import yaml


class JsYamlLoader(yaml.SafeLoader):
    pass


JsYamlLoader.yaml_implicit_resolvers = {}
_RESOLVERS = [
    ("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
    ("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", list("tTfF")),
    ("tag:yaml.org,2002:int",
     r"^(?:[-+]?0|[-+]?0b[01_]*[01]|[-+]?0x[0-9a-fA-F_]*[0-9a-fA-F]|[-+]?0o[0-7_]*[0-7]"
     r"|[-+]?(?:[1-9]|0[0-9])(?:[0-9_]*[0-9])?)$", list("-+0123456789")),
    ("tag:yaml.org,2002:float",
     r"^(?:[-+]?(?:[0-9][0-9_]*)(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?|\.[0-9_]+(?:[eE][-+]?[0-9]+)?"
     r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$(?<!_)", list("-+.0123456789")),
    ("tag:yaml.org,2002:timestamp",
     r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}"
     r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$", list("0123456789")),
    ("tag:yaml.org,2002:merge", r"^(?:<<)$", ["<"]),
]
for _tag, _rx, _first in _RESOLVERS:
    JsYamlLoader.add_implicit_resolver(_tag, re.compile(_rx), _first)


def _bool(loader, node):
    return loader.construct_scalar(node) in ("true", "True", "TRUE")


def _int(loader, node):
    v = loader.construct_scalar(node).replace("_", "")
    sign = -1 if v[:1] == "-" else 1
    if v[:1] in "+-":
        v = v[1:]
    for prefix, base in (("0b", 2), ("0x", 16), ("0o", 8)):
        if v[:2] == prefix:
            return sign * int(v[2:], base)
    return sign * int(v, 10)


def _float(loader, node):
    v = loader.construct_scalar(node).replace("_", "").lower()
    sign = -1.0 if v[:1] == "-" else 1.0
    if v[:1] in "+-":
        v = v[1:]
    if v == ".inf":
        return sign * math.inf
    if v == ".nan":
        return math.nan
    f = sign * float(v)
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


_TS = re.compile(r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:(?:[Tt]|[ \t]+)([0-9]{1,2}):([0-9]{2}):([0-9]{2})"
                 r"(?:\.([0-9]*))?(?:[ \t]*(Z|([-+])([0-9]{1,2})(?::([0-9]{2}))?))?)?$")


def _timestamp(loader, node):
    m = _TS.match(loader.construct_scalar(node))
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    if not m[4]:
        dt = datetime.datetime(y, mo, d)
    else:
        frac = int((m[7] or "")[:3].ljust(3, "0"))
        dt = datetime.datetime(y, mo, d, int(m[4]), int(m[5]), int(m[6]), frac * 1000)
        if m[9]:
            delta = datetime.timedelta(hours=int(m[10]), minutes=int(m[11] or 0))
            dt = dt - delta if m[9] == "+" else dt + delta
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


_INDEX_KEY = re.compile(r"^(?:0|[1-9][0-9]*)$")


def _is_index(k):
    return bool(_INDEX_KEY.match(k)) and int(k) < 2 ** 32 - 1


def js_str(v):
    """String(v) for the scalars YAML yields."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        if v != v:
            return "NaN"
        if v in (math.inf, -math.inf):
            return "Infinity" if v > 0 else "-Infinity"
        if isinstance(v, int) and abs(v) < 1e21:
            return str(v)
        return _number_to_string(float(v))
    if isinstance(v, list):
        return ",".join("" if x is None else js_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def _number_to_string(x):
    """Number.prototype.toString() from the shortest round-trip digits (ECMA-262 Number::toString)."""
    if x == 0:
        return "0"
    mant, _, exp = repr(abs(x)).partition("e")
    ip, _, fp = mant.partition(".")
    digits = ip + fp
    point = len(ip) + (int(exp) if exp else 0)
    lead = len(digits) - len(digits.lstrip("0"))
    digits, point = digits.lstrip("0").rstrip("0") or "0", point - lead
    k, n = len(digits), point
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = (digits if k == 1 else digits[0] + "." + digits[1:]) + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return ("-" if x < 0 else "") + out


def _map(loader, node):
    loader.flatten_mapping(node)
    pairs, seen = [], {}
    for kn, vn in node.value:
        k = loader.construct_object(kn, deep=True)
        k = k if isinstance(k, str) else js_str(k)
        v = loader.construct_object(vn, deep=True)
        if k in seen:
            pairs[seen[k]] = (k, v)
        else:
            seen[k] = len(pairs)
            pairs.append((k, v))
    idx = sorted((p for p in pairs if _is_index(p[0])), key=lambda p: int(p[0]))
    return dict(idx + [p for p in pairs if not _is_index(p[0])])


JsYamlLoader.add_constructor("tag:yaml.org,2002:bool", _bool)
JsYamlLoader.add_constructor("tag:yaml.org,2002:int", _int)
JsYamlLoader.add_constructor("tag:yaml.org,2002:float", _float)
JsYamlLoader.add_constructor("tag:yaml.org,2002:timestamp", _timestamp)
JsYamlLoader.add_constructor("tag:yaml.org,2002:map", _map)


def loads(text):
    # js-yaml's loadDocuments adds a final line break when the text has none, so a block
    # scalar that ends the text keeps one (clip and keep chomping)
    if text and text[-1] not in "\n\r":
        text += "\n"
    return yaml.load(text, Loader=JsYamlLoader)


def load(path):
    with open(path, encoding="utf-8") as f:
        return loads(f.read())

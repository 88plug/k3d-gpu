"""Validator for the JSON Schema subset the console schemas use (draft 2020-12).

Python standard library only, so `k3d-gpu facts` and `plan` validate what they
write without a pip install. A keyword outside SUPPORTED is an error, never
ignored: a schema can only say what this file checks. tests/console_test.sh
cross-checks it against the `jsonschema` package when that is installed.
"""

import re

SUPPORTED = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "pattern", "minimum", "maximum", "minItems", "maxItems", "anyOf",
}

TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """The schema itself uses something this validator does not check."""


def resolve(ref, root):
    if not ref.startswith("#/"):
        raise SchemaError("only local $ref is supported: %s" % ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def same(a, b):
    """JSON equality: 1 == 1.0, but true != 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def errors(value, schema, root=None, path="$"):
    """Every way `value` breaks `schema`, as "<path>: <message>" strings."""
    root = schema if root is None else root
    if schema is True:
        return []
    if schema is False:
        return ["%s: not allowed" % path]
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise SchemaError("unsupported keyword(s) at %s: %s" % (path, ", ".join(sorted(unknown))))
    out = []
    if "$ref" in schema:
        out += errors(value, resolve(schema["$ref"], root), root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPES[t](value) for t in types):
            return out + ["%s: expected %s, got %s" % (path, "|".join(types), type(value).__name__)]
    if "const" in schema and not same(value, schema["const"]):
        out.append("%s: expected %r" % (path, schema["const"]))
    if "enum" in schema and not any(same(value, e) for e in schema["enum"]):
        out.append("%s: %r not in %r" % (path, value, schema["enum"]))
    if "anyOf" in schema:
        if all(errors(value, s, root, path) for s in schema["anyOf"]):
            out.append("%s: matches none of anyOf" % path)
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        out.append("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            out.append("%s: %r below minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            out.append("%s: %r above maximum %r" % (path, value, schema["maximum"]))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append("%s: fewer than %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append("%s: more than %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                out += errors(item, schema["items"], root, "%s[%d]" % (path, i))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append("%s: missing %r" % (path, key))
        extra = schema.get("additionalProperties", True)
        for key in sorted(value):
            sub = "%s.%s" % (path, key)
            if key in props:
                out += errors(value[key], props[key], root, sub)
            else:
                out += errors(value[key], extra, root, sub)
    return out


def check(value, root, ref=None):
    """Raise ValueError listing every error of `value` against root (or root's `ref`)."""
    schema = resolve(ref, root) if ref else root
    found = errors(value, schema, root)
    if found:
        raise ValueError("schema violations:\n  " + "\n  ".join(found))

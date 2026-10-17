"""Shared helpers for capacity-oracle bundles (stdlib only).

A bundle is the JSON document `k3d-gpu share` builds and the oracle ingest
validates: schema `k3d-gpu.oracle.bundle/1` (oracle/schema/bundle-v1.json).
This module holds what both sides need to agree on byte for byte: canonical
JSON, the content hash, the comparison key, the closed lever list, and a
small JSON Schema checker for the keywords bundle-v1.json uses.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema", "bundle-v1.json")
SCHEMA_ID = "k3d-gpu.oracle.bundle/1"

# Bundle size cap (V1). Raw per-request samples dominate the size.
MAX_BYTES = 1024 * 1024

# Intel Advantage levers (docs/moat-design.md section 5). Closed list: a bundle
# can only name these.
LEVERS = ("pe_pinning", "prefill_guard", "xmx_gate", "amx", "numa_replica", "openvino_int4",
          "npu_offload", "media_engines", "qat", "iaa_zswap", "dsa_dto", "rdt", "kv_u4",
          "spec_decode", "itd")


def canonical(obj) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, ASCII only."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(bundle: dict) -> str:
    """sha256 of the canonical bundle with content_sha256 removed."""
    body = {k: v for k, v in bundle.items() if k != "content_sha256"}
    return sha256_hex(canonical(body))


def comparison_key(bundle: dict) -> str:
    """Results are only ever compared inside one key: the pinned software, the
    workload and the engine. Hardware is what varies between keys' rows."""
    t, w = bundle["tool"], bundle["workload"]
    return sha256_hex(canonical({
        "tool.version": t["version"], "tool.k3s": t["k3s"], "tool.server_image": t["server_image"],
        "workload": w, "results.engine": bundle["results"]["engine"],
    }))


def seal(bundle: dict) -> dict:
    """Fill comparison_key and content_sha256 (in that order: the key is part of the content)."""
    bundle["comparison_key"] = comparison_key(bundle)
    bundle["content_sha256"] = content_hash(bundle)
    return bundle


def iso_week(ts: float | None = None) -> str:
    """ISO week of a UNIX time (now when None): '2026-W42'."""
    d = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) if ts is not None
         else datetime.datetime.now(datetime.timezone.utc))
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def load_schema(path: str = SCHEMA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------- schema check
# Supports the JSON Schema 2020-12 keywords bundle-v1.json uses: $ref (local
# #/$defs/...), type, const, enum, properties, required, additionalProperties
# (false), items, minItems, maxItems, pattern, minLength, maxLength, minimum,
# maximum. Any other keyword in the schema is an error, so the checker cannot
# silently ignore a constraint.
KNOWN = {"$schema", "$id", "$defs", "$ref", "$comment", "title", "description", "type", "const",
         "enum", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
         "pattern", "minLength", "maxLength", "minimum", "maximum"}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    pass


def check_schema(instance, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """Errors of instance against schema, as 'path: message' strings."""
    root = root if root is not None else schema
    unknown = set(schema) - KNOWN
    if unknown:
        raise SchemaError(f"{path}: unsupported schema keyword(s) {sorted(unknown)}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise SchemaError(f"{path}: only local #/$defs refs are supported, got {ref}")
        return check_schema(instance, root["$defs"][ref[len("#/$defs/"):]], root, path)
    errs: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](instance) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in {schema['enum']}")
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errs.append(f"{path}: does not match {schema['pattern']}")
        if len(instance) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if _TYPES["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(instance):
                errs += check_schema(v, schema["items"], root, f"{path}[{i}]")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in instance:
                errs.append(f"{path}: missing {k}")
        for k, v in instance.items():
            if k in props:
                errs += check_schema(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key {k!r}")
    return errs

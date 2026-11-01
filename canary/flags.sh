#!/usr/bin/env bash
# The engine flags and environment names k3d-gpu emits, per engine family, for the
# canary's surface check (canary/surface.py asserts each one exists in the image).
#
#   canary/flags.sh [--root DIR] [FAMILY...]
#
# Prints one tab-separated line per name, sorted and unique:
#   <family> <source> <kind> <name>
# family  vllm | ovms | llama.cpp | llama-convert | dlstreamer | sglang
# source  where the name is emitted, <file>:<function or array> (console/*.py: <file>:<function>)
# kind    flag | env | element | property
# name    --flag or -f (no value), VLLM_* env name, GStreamer element, element.property
#
# Sources:
#   scripts/k3d-gpu   vllm_manifest (and VLLM_XPU_ENV, VLLM_HPU_ENV), agent_manifest (OVMS),
#                     llamacpp_manifest, gguf_convert, llamacpp_convert_job callers,
#                     agent_video_script (DL Streamer pipeline)
#   console/model_opt.py, console/fit.py
#                     the optimizer's and the fit's engine arguments (agent_opt_items and
#                     AGENT_LLAMA_FLAGS feed them to the manifests): each string literal
#                     that starts with - in a list or an .append() argument, attributed to
#                     the engine of the innermost `engine == "X"` / `eng == "X"` test, or of
#                     the one engine left after earlier `if engine == "X": ... return`
#                     branches; FUNCTION_FAMILY names the functions with no engine test
#   bench/h54/h54.py  sglang_args, the only sglang command line in the repo (read only)
# A flag literal no rule attributes is an error, so a new emitter cannot go unchecked.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = --root ]; then
    [ $# -ge 2 ] || { echo "flags.sh: --root needs a directory" >&2; exit 2; }
    ROOT=$2
    shift 2
fi
LAUNCHER=${ROOT}/scripts/k3d-gpu
[ -f "${LAUNCHER}" ] || { echo "flags.sh: no ${LAUNCHER}" >&2; exit 1; }

# fn_body <name>: the launcher function's lines, comments dropped.
fn_body() {
    awk -v f="$1" '$0 ~ "^"f"\\(\\) \\{" {p = 1} p {print} p && /^}/ {exit}' "${LAUNCHER}" \
        | grep -vE '^[[:space:]]*#' || true
}

# array_body <name>: the items of a launcher array NAME=( ... ).
array_body() {
    awk -v a="$1" '$0 ~ "^"a"=\\($" {p = 1; next} p && /^\)/ {exit} p {print}' "${LAUNCHER}" \
        | grep -vE '^[[:space:]]*#' || true
}

FLAG_RE='--?[A-Za-z][A-Za-z0-9_-]*'

# shell_flags: flag words a manifest function puts in the server's argument list:
# "${item}--flag", a YAML list line "- --flag", llama_items arguments and the fit's
# default flags="..." string.
shell_flags() {
    local body
    body=$(cat)
    {
        grep -oE "\\\$\\{item\\}'?${FLAG_RE}" <<< "${body}" | sed -E "s/^\\\$\\{item\\}'?//"
        grep -E '^[[:space:]]+- -' <<< "${body}" | sed -E "s/^[[:space:]]+- (${FLAG_RE}).*/\\1/"
        grep -oE "\\\\n[[:space:]]+- ${FLAG_RE}" <<< "${body}" | sed -E 's/^\\n[[:space:]]+- //'
        grep -oE 'llama_items [^)]*' <<< "${body}" | grep -oE "(^|[[:space:]\"])${FLAG_RE}"
        grep -oE 'flags="-[^"]*"' <<< "${body}" | grep -oE "(^|[[:space:]\"])${FLAG_RE}"
    } | tr -d ' "' | grep -xE -- "${FLAG_RE}" || true
}

emit() { # emit <family> <source> <kind>: one line per name on stdin
    local n
    while IFS= read -r n; do
        [ -z "${n}" ] || printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${n}"
    done
}

{
    fn_body vllm_manifest | shell_flags | emit vllm scripts/k3d-gpu:vllm_manifest flag
    for a in VLLM_XPU_ENV VLLM_HPU_ENV; do
        array_body "${a}" | sed -nE 's/^[[:space:]]*"?(VLLM_[A-Z0-9_]+)=.*/\1/p' | emit vllm "scripts/k3d-gpu:${a}" env
    done
    fn_body agent_manifest | shell_flags | emit ovms scripts/k3d-gpu:agent_manifest flag
    fn_body agent_ovms_addon_manifest | shell_flags | emit ovms scripts/k3d-gpu:agent_ovms_addon_manifest flag
    fn_body llamacpp_manifest | shell_flags | emit llama.cpp scripts/k3d-gpu:llamacpp_manifest flag
    # The conversion's own flags: gguf_convert's convert_hf_to_gguf.py call, and the
    # arguments llamacpp_manifest passes to llamacpp_convert_job.
    fn_body gguf_convert | sed -nE '/convert_hf_to_gguf\.py/{s/(^|[[:space:]"])(--[A-Za-z][A-Za-z0-9_-]*)/\n\2\n/g; p}' \
        | grep -xE -- "${FLAG_RE}" | emit llama-convert scripts/k3d-gpu:gguf_convert flag
    fn_body llamacpp_manifest | sed -nE '/llamacpp_convert_job /{s/.*"(--[^"]*)"\).*/\1/p}' \
        | tr ' ' '\n' | grep -xE -- "${FLAG_RE}" | emit llama-convert scripts/k3d-gpu:llamacpp_manifest flag
    python3 - "${ROOT}" <<'PY'
import ast
import re
import sys

root = sys.argv[1]
FLAG = re.compile(r"^(--?[A-Za-z][A-Za-z0-9_-]*)")
ENVV = re.compile(r"^(VLLM_[A-Z0-9_]+)=")
ENGINES = {"vllm", "ovms", "llama.cpp"}
# Functions that emit engine arguments with no engine test of their own.
FUNCTION_FAMILY = {
    ("console/model_opt.py", "eat_patch"): "vllm",      # EAT's vLLM modelConfigs entry
    ("console/fit.py", "convert_plan"): "llama-convert",  # convert_hf_to_gguf.py arguments
    ("console/fit.py", "fit_groups"): "llama.cpp",     # -hf for a GGUF repo with several models
}
bad = []


def eng_test(t):
    if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.And):
        for v in t.values:
            e = eng_test(v)
            if e:
                return e
        return None
    if (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)
            and isinstance(t.left, ast.Name) and t.left.id in ("engine", "eng")
            and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value in ENGINES):
        return t.comparators[0].value
    return None


def lead(e):
    """The literal text an argument starts with: "x", "x" % y, f"x{y}"."""
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Mod):
        return lead(e.left)
    if isinstance(e, ast.JoinedStr) and e.values:
        return lead(e.values[0])
    return None


def literals(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.List):
            out += [(lead(e), e.lineno) for e in n.elts]
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
              and len(n.args) == 1):
            out.append((lead(n.args[0]), n.args[0].lineno))
    return [(v, ln) for v, ln in out if v is not None]


def scan(rel):
    found = []

    def expr(node, fn, eng, excluded):
        for v, ln in literals(node):
            m, env = FLAG.match(v), ENVV.match(v)
            if not m and not env:
                continue
            who = eng or FUNCTION_FAMILY.get((rel, fn))
            if who is None and len(ENGINES - excluded) == 1:
                who = next(iter(ENGINES - excluded))
            if who is None:
                bad.append("%s:%d (%s) %s" % (rel, ln, fn, v))
                continue
            found.append((who, "%s:%s" % (rel, fn), "env" if env else "flag", (env or m).group(1)))

    def block(stmts, fn, eng, excluded):
        excluded = set(excluded)
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                block(s.body, s.name, None, set())
            elif isinstance(s, ast.ClassDef):
                block(s.body, fn, eng, excluded)
            elif isinstance(s, ast.If):
                e = eng_test(s.test)
                pure = e is not None and isinstance(s.test, ast.Compare)
                block(s.body, fn, e or eng, excluded)
                block(s.orelse, fn, eng, excluded | ({e} if pure else set()))
                if pure and s.body and isinstance(s.body[-1], ast.Return):
                    excluded.add(e)
            elif isinstance(s, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
                for f in ("body", "orelse", "finalbody"):
                    block(getattr(s, f, None) or [], fn, eng, excluded)
                for h in getattr(s, "handlers", None) or []:
                    block(h.body, fn, eng, excluded)
            else:
                expr(s, fn, eng, excluded)

    with open("%s/%s" % (root, rel), encoding="utf-8") as f:
        block(ast.parse(f.read()).body, "<module>", None, set())
    return found


rows = scan("console/model_opt.py") + scan("console/fit.py")

# sglang: bench/h54/h54.py sglang_args() is the repo's sglang command line; its flags
# are the words after the module name (python3 -m sglang.launch_server ...).
try:
    tree = ast.parse(open("%s/bench/h54/h54.py" % root, encoding="utf-8").read())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "sglang_args":
            words = [v for v, _ in literals(fn)]
            mods = [i for i, v in enumerate(words) if v.endswith("launch_server")]
            for v in words[mods[0] + 1:] if mods else []:
                m = FLAG.match(v)
                if m:
                    rows.append(("sglang", "bench/h54/h54.py:sglang_args", "flag", m.group(1)))
except FileNotFoundError:
    pass

# DL Streamer: the elements and properties of agent_video_script's gst-launch pipeline,
# with ${src}, ${decode} and ${pre} expanded to every value the script assigns them.
text = open("%s/scripts/k3d-gpu" % root, encoding="utf-8").read()
m = re.search(r"^agent_video_script\(\) \{\n(.*?)^\}", text, re.M | re.S)
if m:
    body = m.group(1)
    assigned = {}
    for k, v in re.findall(r"\b(\w+)=\"([^\"]*)\"", body):
        assigned.setdefault(k, []).append(v)
    pipe = re.search(r"gst-launch-1\.0 -e (.*?[^\\])\n", re.sub(r"\\\n\s*", " ", body))
    if pipe:
        segments, props = [], []
        for seg in pipe.group(1).split("!"):
            words = seg.split()
            if not words:
                continue
            ref = re.fullmatch(r"\$\{(\w+)\}", words[0])
            if ref:
                for alt in assigned.get(ref.group(1), []):
                    segments += [s.split() for s in alt.split("!") if s.split()]
                continue
            segments.append(words)
        for words in segments:
            el = words[0]
            if "/" in el or not re.fullmatch(r"[a-z][a-z0-9_-]*", el):
                continue  # caps, not an element
            rows.append(("dlstreamer", "scripts/k3d-gpu:agent_video_script", "element", el))
            for w in words[1:]:
                ref = re.fullmatch(r"\$\{(\w+)\}", w)
                for kv in (assigned.get(ref.group(1), []) if ref else [w]):
                    if "=" in kv:
                        rows.append(("dlstreamer", "scripts/k3d-gpu:agent_video_script", "property",
                                     "%s.%s" % (el, kv.split("=", 1)[0])))

for r in rows:
    print("\t".join(r))
if bad:
    sys.stderr.write("flags.sh: engine flag literals no rule attributes to an engine (name the function in FUNCTION_FAMILY):\n  "
                     + "\n  ".join(bad) + "\n")
    sys.exit(1)
PY
} | if [ $# -gt 0 ]; then awk -F'\t' -v fams=" $* " 'index(fams, " " $1 " ")'; else cat; fi | LC_ALL=C sort -u

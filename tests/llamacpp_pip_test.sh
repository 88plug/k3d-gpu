#!/usr/bin/env bash
# tests/llamacpp_test.sh in a scratch copy of the tree whose launcher carries another
# GGUF_CONVERT_PIP. A gguf-convert-pip bump (pins/registry.toml) moves that one line of
# scripts/k3d-gpu, so the suite reads the conversion Job's pip set from the launcher:
#   - another fully pinned set: the suite passes, nothing in it names the old versions;
#   - a set with a requirement that is not name==version (no version, a range, a
#     wildcard), one without transformers, or an empty one: the suite stops at the
#     read with exit 1 and names it, before any render (an empty read cannot match).
#
# Usage: tests/llamacpp_pip_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || sed 's/^/     | /' <<< "$2"; }

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
mkdir "${WORK}/tree"
tar -C "${ROOT}" --exclude=./.git -cf - . | tar -C "${WORK}/tree" -xf -
LAUNCHER="${WORK}/tree/scripts/k3d-gpu"

# set_pip <value>: the copy's launcher with GGUF_CONVERT_PIP="<value>" on its one such line.
set_pip() {
    python3 - "${LAUNCHER}" "$1" <<'PY'
import re
import sys

path, value = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    text = f.read()
text, n = re.subn(r'^GGUF_CONVERT_PIP="[^"]*"$', lambda _m: f'GGUF_CONVERT_PIP="{value}"', text, flags=re.M)
if n != 1:
    sys.exit(f"{path}: {n} GGUF_CONVERT_PIP lines, not 1")
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PY
}
# suite <name>: the copy's tests/llamacpp_test.sh, output in ${WORK}/<name>.out, its exit code.
suite() { bash "${WORK}/tree/tests/llamacpp_test.sh" > "${WORK}/$1.out" 2>&1; }

MOVED="transformers==9.9.1 huggingface-hub==9.9.2 tokenizers==9.9.3 safetensors==9.9.4"
if grep -qxF "GGUF_CONVERT_PIP=\"${MOVED}\"" "${ROOT}/scripts/k3d-gpu"; then
    echo "FAIL the launcher already pins ${MOVED}: pick another moved set"
    exit 1
fi
if set_pip "${MOVED}" && grep -qxF "GGUF_CONVERT_PIP=\"${MOVED}\"" "${LAUNCHER}" && suite moved \
    && tail -1 "${WORK}/moved.out" | grep -qxE '[1-9][0-9]* passed, 0 failed'; then
    ok "GGUF_CONVERT_PIP moved to ${MOVED}: tests/llamacpp_test.sh passes ($(tail -1 "${WORK}/moved.out"))"
else fail "tests/llamacpp_test.sh on a launcher with another pinned pip set" "$(grep -A3 '^FAIL' "${WORK}/moved.out"; tail -1 "${WORK}/moved.out")"; fi

# Each set the read refuses: exit 1 on its first line, which names the set, and no case ran.
refused() {
    local name=$1 value=$2
    set_pip "${value}" || return 1
    suite "${name}" && return 1
    [ "$(wc -l < "${WORK}/${name}.out")" -eq 1 ] \
        && grep -qxF "FAIL scripts/k3d-gpu has no GGUF_CONVERT_PIP of name==version requirements, transformers among them (read '${value}')" \
            "${WORK}/${name}.out"
}
for c in "unversioned|transformers huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0" \
         "range|transformers>=5.21 huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0" \
         "wildcard|transformers==5.* huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0" \
         "no-transformers|huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0" \
         "empty|"; do
    if refused "${c%%|*}" "${c#*|}"; then
        ok "GGUF_CONVERT_PIP='${c#*|}' (${c%%|*}): tests/llamacpp_test.sh exits 1 at the read"
    else fail "tests/llamacpp_test.sh refuses GGUF_CONVERT_PIP='${c#*|}' (${c%%|*})" "$(head -5 "${WORK}/${c%%|*}.out"; tail -1 "${WORK}/${c%%|*}.out")"; fi
done

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

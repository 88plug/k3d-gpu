#!/usr/bin/env bash
# Runs each examples/agents quickstart against the stub OpenAI endpoint
# (examples/agents/stub_openai.py) inside a pinned python container. The
# framework packages are installed in the container only. No cluster, no model.
# Each example installs requirements.txt under constraints.txt (the full pinned
# set, transitive dependencies included); the installed set must equal it.
#
# Usage: tests/agent_examples_test.sh [example ...]   (default: all)

set -uo pipefail

EXAMPLES_DIR="$(cd "$(dirname "$0")/../examples/agents" && pwd)"
# python:3.13.18-slim-trixie (multi-arch index digest)
IMAGE="${K3D_GPU_EXAMPLES_IMAGE:-python:3.13.18-slim-trixie@sha256:76baad6037690d6e8c4397fbcaef17bca6f19eecef822b708fe8fa3417d30bb5}"

if [ "$#" -gt 0 ]; then
    examples=("$@")
else
    examples=()
    for req in "${EXAMPLES_DIR}"/*/requirements.txt; do
        examples+=("$(basename "$(dirname "${req}")")")
    done
fi

FAILS=()
for ex in "${examples[@]}"; do
    [ -f "${EXAMPLES_DIR}/${ex}/requirements.txt" ] || { echo "no such example: ${ex}" >&2; FAILS+=("${ex}"); continue; }
    [ -f "${EXAMPLES_DIR}/${ex}/constraints.txt" ] || { echo "FAIL ${ex}: no constraints.txt" >&2; FAILS+=("${ex}"); continue; }
    # README.md's table row names the example's direct pins: they must equal requirements.txt.
    want=$(grep -v '^#' "${EXAMPLES_DIR}/${ex}/requirements.txt" | sed '/^[[:space:]]*$/d' | sort)
    got=$(grep -F "| [\`${ex}/\`]" "${EXAMPLES_DIR}/README.md" | grep -o "\`[A-Za-z0-9._-]*==[^\`]*\`" | tr -d "\`" | sort)
    if [ "${got}" != "${want}" ]; then
        echo "FAIL ${ex}: README.md table names '${got//$'\n'/ }', requirements.txt pins '${want//$'\n'/ }'" >&2
        FAILS+=("${ex}"); continue
    fi
    echo "=== ${ex}"
    if docker run --rm \
        -v "${EXAMPLES_DIR}:/examples:ro" \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -e EXAMPLE="${ex}" \
        "${IMAGE}" \
        sh -ec 'python -m venv /tmp/venv
                /tmp/venv/bin/pip install --quiet --disable-pip-version-check \
                    -r "/examples/${EXAMPLE}/requirements.txt" -c "/examples/${EXAMPLE}/constraints.txt"
                /tmp/venv/bin/pip check
                /tmp/venv/bin/pip freeze --all --exclude pip | sort -f > /tmp/installed.txt
                grep -v "^#" "/examples/${EXAMPLE}/constraints.txt" | sort -f | diff -u - /tmp/installed.txt
                cd /examples && /tmp/venv/bin/python check.py "${EXAMPLE}"'; then
        echo "PASS ${ex}"
    else
        echo "FAIL ${ex}"
        FAILS+=("${ex}")
    fi
done

if [ "${#FAILS[@]}" -gt 0 ]; then
    echo "failed: ${FAILS[*]}"
    exit 1
fi
echo "all ${#examples[@]} example(s) passed"

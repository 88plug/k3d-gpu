#!/usr/bin/env bash
# Runs each examples/agents quickstart against the stub OpenAI endpoint
# (examples/agents/stub_openai.py) inside a pinned python container. The
# framework packages are installed in the container only. No cluster, no model.
#
# Usage: tests/agent_examples_test.sh [example ...]   (default: all)

set -uo pipefail

EXAMPLES_DIR="$(cd "$(dirname "$0")/../examples/agents" && pwd)"
# python:3.13.16-slim-trixie (multi-arch index digest)
IMAGE="${K3D_GPU_EXAMPLES_IMAGE:-python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f}"

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
    echo "=== ${ex}"
    if docker run --rm \
        -v "${EXAMPLES_DIR}:/examples:ro" \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -e EXAMPLE="${ex}" \
        "${IMAGE}" \
        sh -ec 'python -m venv /tmp/venv
                /tmp/venv/bin/pip install --quiet --disable-pip-version-check -r "/examples/${EXAMPLE}/requirements.txt"
                /tmp/venv/bin/pip check
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

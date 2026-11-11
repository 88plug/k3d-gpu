#!/usr/bin/env bash
# The vendored vLLM recipes (console/vllm-recipes.json) and their generator
# (hack/vllm-recipes): tests/vllm_recipes_gen_test.py, under the Python that holds
# PyYAML. CI and the Freshness bump point VLLM_RECIPES_PYTHON at a venv with the
# pinned PyYAML (hack/ci-tools.env PYYAML_VERSION); with it set, a missing PyYAML is a
# failure. Unset, python3 runs it, and a python3 without PyYAML skips with a message.
#
# Usage: tests/vllm_recipes_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
py=${VLLM_RECIPES_PYTHON:-python3}
if ! "${py}" -c 'import yaml' 2>/dev/null; then
    if [ -n "${VLLM_RECIPES_PYTHON:-}" ]; then
        echo "FAIL VLLM_RECIPES_PYTHON=${py} cannot import yaml (PyYAML)"
        exit 1
    fi
    echo "skip tests/vllm_recipes_gen_test.py (python3 has no PyYAML; set VLLM_RECIPES_PYTHON)"
    exit 0
fi
exec "${py}" "${ROOT}/tests/vllm_recipes_gen_test.py"

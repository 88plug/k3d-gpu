#!/usr/bin/env bash
# The vendored vLLM recipes (console/vllm-recipes.json), their generator and the drift
# report (hack/vllm-recipes): tests/vllm_recipes_gen_test.py and
# tests/vllm_recipes_drift_test.py, under the Python that holds PyYAML. CI and the
# Freshness bump point VLLM_RECIPES_PYTHON at a venv with the pinned PyYAML
# (hack/ci-tools.env PYYAML_VERSION); with it set, a missing PyYAML is a failure.
# Unset, python3 runs them, and a python3 without PyYAML skips with a message.
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
    echo "skip tests/vllm_recipes_gen_test.py and tests/vllm_recipes_drift_test.py (python3 has no PyYAML; set VLLM_RECIPES_PYTHON)"
    exit 0
fi
rc=0
"${py}" "${ROOT}/tests/vllm_recipes_gen_test.py" || rc=1
"${py}" "${ROOT}/tests/vllm_recipes_drift_test.py" || rc=1
exit "${rc}"

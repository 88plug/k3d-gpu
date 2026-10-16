#!/usr/bin/env bash
# Regenerate docs-build/requirements.txt (hashed lock) from requirements.in in the
# same pinned Python image the docs workflow builds with (docs-build/Dockerfile).
#
# Usage: docs-build/lock.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${DOCS_PYTHON_IMAGE:-$(sed -n 's/^FROM \(python:[^ ]*\).*$/\1/p' "${ROOT}/docs-build/Dockerfile")}"
PIP_TOOLS="pip-tools==7.6.2"

docker run --rm -v "${ROOT}/docs-build:/w" -w /w "${IMAGE}" sh -c \
    "pip install -q --root-user-action=ignore '${PIP_TOOLS}' && \
     pip-compile -q --generate-hashes --strip-extras --allow-unsafe \
       --output-file requirements.txt requirements.in && \
     chown $(id -u):$(id -g) requirements.txt"

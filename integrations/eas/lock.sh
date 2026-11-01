#!/usr/bin/env bash
# Regenerate integrations/eas/requirements.lock (hashed) from requirements.in, in the
# bastion's own base image (bastion.Dockerfile FROM: Ubuntu 24.04, Python 3.12), so the
# lock resolves for the interpreter that installs it.
#
# requirements.in is enterprise-ai-solutions' requirements.txt at the commit its first
# line names; copy the new file over it (keeping that line, with the new commit) when the
# EAS pin moves. pip-compile picks the newest release inside those ranges.
#
# Usage: integrations/eas/lock.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="$(sed -n 's/^FROM \(ubuntu:[^ ]*\).*$/\1/p' "${HERE}/bastion.Dockerfile")"
PIP_TOOLS="pip-tools==7.6.2"
header=$(head -1 "${HERE}/requirements.in")
[[ "${header}" =~ ^#\ enterprise-ai-solutions@[0-9a-f]{40}\ requirements\.txt$ ]] \
    || { echo "requirements.in line 1 must be '# enterprise-ai-solutions@<sha> requirements.txt'" >&2; exit 1; }

docker run --rm -v "${HERE}:/w" -w /w "${IMAGE}" sh -c \
    "apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends python3-venv >/dev/null && \
     python3 -m venv /tmp/v && /tmp/v/bin/pip install -q '${PIP_TOOLS}' && \
     /tmp/v/bin/pip-compile -q --upgrade --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url \
       --output-file requirements.lock requirements.in && \
     { echo '${header}'; cat requirements.lock; } > /tmp/lock && cat /tmp/lock > requirements.lock && \
     chown $(id -u):$(id -g) requirements.lock"

#!/usr/bin/env bash
# k3d-shim.sh: installed as ${G}/bin/k3d in front of the pinned k3d (k3d.real). `k3d cluster
# create` of a k3dgpu-g-* cluster gets one more volume, the campaign's model directory at
# /var/apb-models in every node (stage.py's hostPath), so models are read in place, not copied.
# Every other call passes through unchanged.
set -euo pipefail
REAL=$(dirname "$0")/k3d.real
if [ "${1:-}" = cluster ] && [ "${2:-}" = create ] && [[ "${3:-}" == k3dgpu-g-* ]]; then
    exec "${REAL}" "$@" --volume="${HOME}/k3dgpu-gemma/models:/var/apb-models@server:*;agent:*"
fi
exec "${REAL}" "$@"

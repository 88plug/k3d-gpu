#!/usr/bin/env bash
# k3d-shim.sh: installed as ${G}/bin/k3d in front of the host's k3d (K3D_REAL, default
# /usr/bin/k3d). `k3d cluster create` of a k3dgpu-d-* cluster gets one more volume, the
# campaign's model directory at /var/apb-models in the server node (stage.py's hostPath),
# read-only, so models are read in place, never copied. Every other call passes through unchanged.
set -euo pipefail
REAL=${K3D_REAL:-/usr/bin/k3d}
G=${H57D_G:-${HOME}/k3dgpu-h57d}
if [ "${1:-}" = cluster ] && [ "${2:-}" = create ] && [[ "${3:-}" == "${H57D_PREFIX:-k3dgpu-d-}"* ]]; then
    exec "${REAL}" "$@" --volume="${G}/models:/var/apb-models:ro@server:*"
fi
exec "${REAL}" "$@"

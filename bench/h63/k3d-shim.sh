#!/usr/bin/env bash
# k3d-shim.sh: installed as ${G}/bin/k3d in front of the host's k3d (K3D_REAL, default
# /usr/bin/k3d). `k3d cluster create` of one of this campaign's clusters (H63_PREFIX) gets one more
# volume, the campaign's model directory at /var/apb-models in the server node, read-only: no H63
# arm writes there (Od serves the graph.pbtxt bench/h57d/convert.sh wrote, Ls and Ld-mtp mount it
# read-only), so models are read in place, never copied. Every other call passes through unchanged.
set -euo pipefail
REAL=${K3D_REAL:-/usr/bin/k3d}
G=${H63_G:-${HOME}/k3dgpu-h63}
if [ "${1:-}" = cluster ] && [ "${2:-}" = create ] && [ -n "${H63_PREFIX:-}" ] && [[ "${3:-}" == "${H63_PREFIX}"* ]]; then
    exec "${REAL}" "$@" --volume="${G}/models:/var/apb-models:ro@server:*"
fi
exec "${REAL}" "$@"

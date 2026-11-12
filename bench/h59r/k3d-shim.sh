#!/usr/bin/env bash
# H59r's copy of bench/h59/k3d-shim.sh (EXPERIMENTS.md "H59r pre-registration"): the same steps,
# with bench/h59r/run.py's tables (crescent only, row H59r, llama.cpp b12110). test_h59r.py
# checks that every other line equals bench/h59's.
# k3d-shim.sh: installed as ${G}/bin/k3d in front of the host's k3d (K3D_REAL, default
# /usr/bin/k3d). `k3d cluster create` of one of this campaign's clusters (H59_PREFIX) gets one more
# volume, the campaign's model directory at /var/apb-models in the server node, so models are read
# in place, never copied. It is writable: OVMS's --pull init container (arm O) writes graph.pbtxt
# beside the weights; arm L's pod mounts it read-only. Every other call passes through unchanged.
set -euo pipefail
REAL=${K3D_REAL:-/usr/bin/k3d}
G=${H59_G:-${HOME}/k3dgpu-h59r}
if [ "${1:-}" = cluster ] && [ "${2:-}" = create ] && [ -n "${H59_PREFIX:-}" ] && [[ "${3:-}" == "${H59_PREFIX}"* ]]; then
    exec "${REAL}" "$@" --volume="${G}/models:/var/apb-models@server:*"
fi
exec "${REAL}" "$@"

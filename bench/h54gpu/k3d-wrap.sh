#!/usr/bin/env bash
# k3d-wrap.sh: installed as ~/k3dgpu-gemma/bin/k3d for H54b holds (amendment G1). `k3d-gpu up`
# has no volume knob, so on `k3d cluster create` this adds the campaign's model tree to the
# server node: H54_MODELS read-only at /models, and its hf/ cache read-write at /models/hf (EAT's
# chart mounts it as HF_HOME). Every other call goes to the real k3d (K3D_REAL) unchanged.
real=${K3D_REAL:-$(dirname "$0")/k3d-5.9.0}
if [ "${1:-}" = cluster ] && [ "${2:-}" = create ] && [ -n "${H54_MODELS:-}" ]; then
    exec "${real}" "$@" --volume="${H54_MODELS}:/models:ro@server:0" \
        --volume="${H54_MODELS}/hf:/models/hf@server:0"
fi
exec "${real}" "$@"

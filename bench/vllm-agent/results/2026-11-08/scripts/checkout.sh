#!/bin/bash
# checkout.sh <sha> <dir>: a clean checkout of one commit of 88plug/k3d-gpu, unmodified.
set -eu
SHA=$1 D=$2
rm -rf "$D"
git init -q "$D"
git -C "$D" fetch -q --depth 1 https://github.com/88plug/k3d-gpu.git "$SHA"
git -C "$D" checkout -q FETCH_HEAD
git -C "$D" log -1 --format='%H %s'
git -C "$D" status --porcelain | wc -l
grep -m1 '^VERSION=' "$D/scripts/k3d-gpu"
grep -m1 'vllm/vllm-openai-xpu:v' "$D/scripts/k3d-gpu"
sha256sum "$D/scripts/k3d-gpu"

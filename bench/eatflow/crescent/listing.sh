#!/usr/bin/env bash
# listing.sh DIR: a cheap fingerprint of a model directory that a hold mounts read-only (crescent
# amendment M1, LIVE_MOUNT_B): every file's relative path, size and mtime, sorted, then the
# sha256 of its small JSON indexes (model.safetensors.index.json, config.json). models.sh writes
# it once, after the full sha256 check against the Hub; live.sh compares it before every hold.
set -euo pipefail
D=$(readlink -f "${1:?usage: listing.sh DIR}")
cd "${D}"
find . -type f -not -path './.cache/*' -printf '%P %s %T@\n' | LC_ALL=C sort
for f in model.safetensors.index.json config.json; do
    [ -f "${f}" ] && sha256sum "${f}"
done
exit 0

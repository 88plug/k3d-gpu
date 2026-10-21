#!/usr/bin/env bash
# fetch.sh REPO REVISION GB [cache]: download one H54b model on crescent at a pinned revision
# (amendment G1), under the shared lock, only if `/` stays at or below 96% after GB more (the
# model plus the disk its arm then adds).
# Default: `hf download --local-dir ~/k3dgpu-gemma/models/<org>_<name>`; `cache`: into the HF
# cache layout under ~/k3dgpu-gemma/models/hf/hub (EAT's chart reads it as HF_HOME=/data).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "${HERE}/lib.sh"
repo=$1 rev=$2 gb=$3 mode=${4:-dir}
G=${HOME}/k3dgpu-gemma
OUT=${G}/runs
mkdir -p "${OUT}"
HF=${G}/.venv/bin/hf
if [ -z "${H54_LOCKED:-}" ]; then   # H54_LOCKED=1: campaign.sh holds the lock already
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 21600 9 || { say "fetch ${repo}: lock not taken"; exit 4; }
fi
disk_ok "${gb}" || { say "fetch ${repo}: ${gb} GB would take / above 96%: not fetched"; exit 5; }
say "fetch ${repo}@${rev} (${gb} GB, ${mode})"
if [ "${mode}" = cache ]; then
    "${HF}" download "${repo}" --revision "${rev}" --cache-dir "${G}/models/hf/hub" > /dev/null
else
    d=${G}/models/${repo/\//_}
    "${HF}" download "${repo}" --revision "${rev}" --local-dir "${d}" > /dev/null &&
        echo "${repo} ${rev}" > "${d}/.pinned-revision"
fi
rc=$?
say "fetch ${repo} rc=${rc} df $(df -P / | awk 'NR==2 {print $5}')"
exit "${rc}"

#!/usr/bin/env bash
# prep.sh HOST: the H59-family model files on this host (EXPERIMENTS.md "H59 pre-registration",
# Model; "H59 amendment R1"). Preparations are not holds.
#   1. Downloads at the pinned commits, outside the lock, into ${G}/models: Google's QAT GGUF
#      (gemma-4-26B_q4_0-it.gguf only) and OpenVINO's int4 IR (OVB); each file is checked against
#      the Hub's LFS sha256 (bench/wildct/verify_models.py). The GGUF's directory is also reachable
#      as gguf/<repo>@<commit>, the path L's render serves it from. OVB's directory and the OVMS
#      compiled-model cache directory are mode 777: OVMS's --pull init container runs as uid 5000
#      and writes graph.pbtxt there at each start (H54 amendment R2's directory mode).
#   2. The QAT drafter, inside the host lock (flock -w 21600; crescent ~/k3dgpu-bench.lock, else
#      LOCK) under `timeout -k 30 3600`: arm L rendered with no cluster (run.py prep-render), and
#      its conversion Job's container (PR #182's path: full-b11960, its pip pins, convert_hf_to_gguf
#      --outtype bf16, llama-quantize Q4_0) run once with docker against ${G}/models; the Job's
#      spec is kept beside the GGUF as prep-job.json, which every hold's render must equal.
#   3. ${G}/weights.sha256: the sha256 of every file the arms read.
#   G  campaign dir (default from the profile);  PY_HF  a python with huggingface_hub
set -euo pipefail
HOST=${1:?usage: prep.sh HOST}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
RUN=${HERE}/run.py
pfield() { python3 "${RUN}" profile "${HOST}" | sed -n "s/^$1=//p"; }
G=${G:-$(pfield g)}
G=${G/#\~/${HOME}}
M=${G}/models
PY_HF=${PY_HF:-python3}
export HF_HUB_DISABLE_TELEMETRY=1 REPO G
read -r GREPO GREV GFILE OVB OREV DDIR <<< "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import run
print(run.GGUF_REPO, run.GGUF_REV, run.GGUF_FILE, run.OVB, run.OVB_REV, run.DRAFT_DIR)' "${HERE}")"
mkdir -p "${M}/gguf" "${G}/prep"
echo "downloads $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"
nice -n 10 "${PY_HF}" - "${M}" "${GREPO}" "${GREV}" "${GFILE}" "${OVB}" "${OREV}" <<'EOF'
import sys
from huggingface_hub import hf_hub_download, snapshot_download
m, grepo, grev, gfile, ovb, orev = sys.argv[1:]
print(hf_hub_download(grepo, gfile, revision=grev, local_dir=f"{m}/{grepo}"), flush=True)
print(snapshot_download(ovb, revision=orev, local_dir=f"{m}/{ovb}"), flush=True)
EOF
echo "${GREV}" > "${M}/${GREPO}/.pinned-revision"
echo "${OREV}" > "${M}/${OVB}/.pinned-revision"
"${PY_HF}" "${REPO}/bench/wildct/verify_models.py" "${M}" --skip gemma-4-26B-it-mmproj.gguf "${GREPO}@${GREV}" "${OVB}@${OREV}"
ln -sfn "../${GREPO}" "${M}/gguf/${GREPO//\//__}@${GREV}"
mkdir -p "${M}/.ov_cache"
chmod 777 "${M}/${OVB}" "${M}/.ov_cache"

case ${HOST} in crescent) LOCKF=${HOME}/k3dgpu-bench.lock ;; *) LOCKF=${LOCK:?set LOCK to this host\'s lock file} ;; esac
if [ ! -f "${M}/${DDIR}/manifest.json" ]; then
    J=${G}/prep/drafter
    rm -rf "${J}"
    python3 "${RUN}" prep-render "${J}"
    mapfile -t ARGV < <(python3 "${RUN}" docker-argv "${J}/job.json" "${M}" "${J}")
    exec 9> "${LOCKF}"
    echo "waiting for the lock $(date -u +%FT%TZ)"
    flock -w 21600 9
    echo "lock taken $(date -u +%FT%TZ); converting the drafter ($(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "${J}/job.json"))"
    timeout -k 30 3600 "${ARGV[@]}"
    cp "${J}/job.json" "${M}/${DDIR}/prep-job.json"
    exec 9>&-
fi
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("drafter:", m["source"], m["quantize"], m["sha256"], m["bytes"])' "${M}/${DDIR}/manifest.json"
( cd "${M}" && find "${GREPO}/${GFILE}" "${OVB}" "${DDIR}/model.gguf" -type f ! -path '*/.cache/*' ! -name graph.pbtxt ! -name .pinned-revision -print0 \
    | sort -z | xargs -0 sha256sum ) > "${G}/weights.sha256"
python3 "${RUN}" models "${G}"
echo "done $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"

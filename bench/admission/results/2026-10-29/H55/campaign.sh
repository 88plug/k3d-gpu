#!/usr/bin/env bash
# campaign.sh: the five H55 rounds back to back, one lock hold per round
# (bench/admission/run.sh takes ~/k3dgpu-bench.lock itself). Stops at the first
# round that does not exit 0. Writes DONE with the last exit code at the end.
set -u
C=/home/andrew/k3dgpu-h55
SRC=${C}/src
OUT=${C}/results/H55
export K3DGPU_LIVE_LOCK=/home/andrew/k3dgpu-bench.lock
export AD_SUDO_PASS=/tmp/claude-1000/-home-andrew-k3d-gpu/86e13552-98b8-48e1-b1aa-9fbfea1a0978/scratchpad/.wpass
export AD_MODEL_DIR=/home/andrew/h22-models/OpenVINO/Qwen3-4B-int4-ov
export AD_SNAPSHOT=${OUT}/cpufreq-snapshot.txt
unset AD_EXTRA_LOCK
cd "${SRC}" || exit 1
{
    echo "campaign start $(date -u +%FT%TZ)"
    echo "source: git archive of 890d1d16790ed656ae5aa86a8319e578310b0f80 at ${SRC}"
    echo "lock: ${K3DGPU_LIVE_LOCK}; no extra lock"
} > "${OUT}/campaign_start.txt"
bash "${C}/cotenants.sh" "campaign start" >> "${OUT}/cotenants.txt" 2>&1
rc=0
for r in 1 2 3 4 5; do
    echo "$(date -u +%FT%TZ) round ${r} launch" >> "${OUT}/campaign.out"
    bash "${SRC}/bench/admission/run.sh" "${OUT}" "${r}" >> "${OUT}/campaign.out" 2>&1
    rc=$?
    echo "$(date -u +%FT%TZ) round ${r} exit ${rc}" >> "${OUT}/campaign.out"
    bash "${C}/cotenants.sh" "after round ${r}" >> "${OUT}/cotenants.txt" 2>&1
    [ "${rc}" = 0 ] || break
done
echo "campaign end $(date -u +%FT%TZ) last_rc ${rc}" > "${OUT}/campaign_end.txt"
echo "${rc}" > "${C}/DONE"

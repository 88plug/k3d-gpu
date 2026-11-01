#!/usr/bin/env bash
# setup.sh: H62's setup phase on crescent (EXPERIMENTS.md "H62 pre-registration", "Setup"), not a
# hold, inside the bench lock. Run from the H62 checkout at the deploy commit.
#   1. host gate: MemAvailable for both guests plus 48 GiB, / at most 90% used, Hugging Face and the
#      registries reachable; re-read every 10 min for up to 60 min
#   2. A1's IR against its manifest.json; the Hub's file list of B5's weights (the QAT checkpoint
#      at f1e06dc5) and the script that checks them
#   3. both guests (vm.sh build, fetch, up, wait), then the guest gate: amx_tile on all 96 vCPUs
#   4. VM a's A1 (guest-a.sh, 45 min ceiling) and VM b's EAS install (guest-b.sh, 120 min ceiling),
#      side by side; their records copied back
#   5. the NFD gate (VM b's node carries cpu-cpuid.AMXBF16=true) and the weight gate (B5's files
#      equal the QAT checkpoint's)
# Exit 0: both arms serve. 4: lock not taken. 6: the host gate still failed after 60 min. 7: not
# deliverable (a ceiling passed, or no AMX in a guest). 8: the NFD or weight gate failed (no hold
# until a dated amendment). 1: anything else. Records: ~/k3dgpu-bench/results/H62/setup.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
P() { python3 "${HERE}/h62.py" "$@"; }
B=${HOME}/k3dgpu-bench
export H62_STATE=${H62_STATE:-${B}/h62}
R=${H62_RESULTS:-${B}/results/H62}/setup
A_CAP=2700 B_CAP=7200
mkdir -p "${R}"
log() { echo "== setup $* $(date -u +%FT%TZ)" | tee -a "${R}/setup.log"; }
# Every ssh, scp and vm.sh below runs with fd 9 closed: an ssh ControlMaster it starts outlives
# this script, and must not keep the bench lock.
G() { ssh -F "${H62_STATE}/ssh_config" "$(P vm "$1" host)" "${@:2}" 9>&-; }
put() { scp -q -F "${H62_STATE}/ssh_config" "${@:2}" "$(P vm "$1" host):h62/" 9>&-; }
vm() { bash "${HERE}/vm.sh" "$@" 9>&-; }
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 7200 9 || { log "lock not taken in 7200 s"; exit 4; }
log "lock taken"
git -C "${REPO}" rev-parse HEAD > "${R}/commit.txt"
git -C "${REPO}" status --porcelain > "${R}/commit-status.txt"

# 1. Host gate.
for i in 0 1 2 3 4 5 6; do
    P host-facts > "${R}/host-facts.json"
    P gates host "${R}/host-facts.json" > "${R}/gate-host.txt" && break
    [ "${i}" -lt 6 ] || { log "host gate failed after 60 min"; cat "${R}/gate-host.txt"; exit 6; }
    log "host gate not passed: $(grep -c FAIL "${R}/gate-host.txt") item(s); re-read in 10 min"
    sleep 600
done
log "host gate passed"

# 2. A1's IR and B5's reference list.
IR=$(readlink -f "${H62_IR:-${B}/models/gemma-4-26B-A4B-it-qat-int4-ov}")
P ir-verify "${IR}" > "${R}/ir-verify.txt" || { log "A1's IR does not match its manifest"; exit 1; }
curl -fsSL "https://huggingface.co/api/models/$(P pin qat_repo)/revision/$(P pin qat_rev)?blobs=true" \
    > "${R}/qat-siblings.json" || { log "no Hub file list"; exit 1; }
P verify-script "${R}/qat-siblings.json" > "${R}/verify-weights.py"

# 3. The guests.
log "guests"
vm build > "${R}/vm-build.log" 2>&1 || { log "QEMU image build failed"; exit 1; }
vm fetch > "${R}/vm-fetch.log" 2>&1 || { log "cloud image failed its check"; exit 1; }
for vm in a b; do
    H62_IR=${IR} vm up "${vm}" > "${R}/vm-up-${vm}.log" 2>&1 || { log "VM ${vm} did not start"; exit 1; }
done
for vm in a b; do
    vm wait "${vm}" 900 >> "${R}/vm-up-${vm}.log" 2>&1 || { log "VM ${vm} did not boot"; exit 1; }
done
fa=$(vm facts a) && fb=$(vm facts b) || { log "no guest facts"; exit 1; }
python3 -c 'import json, sys; a, b = (json.loads(x) for x in sys.argv[1:3])
print(json.dumps({"amx_tile": {"a": a["amx_tile"], "b": b["amx_tile"]}, "nproc": {"a": a["nproc"], "b": b["nproc"]},
                  "a": a, "b": b}))' "${fa}" "${fb}" > "${R}/guest-facts.json"
P gates guest "${R}/guest-facts.json" > "${R}/gate-guest.txt" || { log "guest gate failed: not deliverable"; exit 7; }

# 4. Each guest's setup, side by side under its ceiling.
for vm in a b; do
    G "${vm}" mkdir -p h62/bin
    put "${vm}" "${HERE}/h62.py" "${HERE}/guest-${vm}.sh"
done
scp -q -F "${H62_STATE}/ssh_config" "${B}/bin/k3d" "${B}/bin/kubectl" h62-a:h62/bin/ 9>&-
git -C "${REPO}" archive --format=tar HEAD | G a "mkdir -p h62/k3d-gpu && tar -x -C h62/k3d-gpu"
log "guest-a (cap ${A_CAP} s) and guest-b (cap ${B_CAP} s)"
timeout -k 60 "${A_CAP}" ssh -F "${H62_STATE}/ssh_config" h62-a 'bash h62/guest-a.sh' > "${R}/guest-a.log" 2>&1 9>&- &
pa=$!
timeout -k 60 "${B_CAP}" ssh -F "${H62_STATE}/ssh_config" h62-b 'bash h62/guest-b.sh' > "${R}/guest-b.log" 2>&1 9>&- &
pb=$!
wait "${pa}"; xa=$?
wait "${pb}"; xb=$?
for vm in a b; do
    scp -q -r -F "${H62_STATE}/ssh_config" "$(P vm "${vm}" host):h62/records" "${R}/guest-${vm}" 2>/dev/null 9>&- || true
done
log "guest-a exit ${xa}, guest-b exit ${xb}"
if [ "${xa}" = 124 ] || [ "${xb}" = 124 ]; then log "a ceiling passed: not deliverable"; exit 7; fi
[ "${xa}" = 0 ] && [ "${xb}" = 0 ] || exit 1

# 5. NFD and weight gates on VM b.
amx=$(G b ". ~/h62/env && kubectl get nodes -o jsonpath='{.items[0].metadata.labels.feature\\.node\\.kubernetes\\.io/cpu-cpuid\\.AMXBF16}'")
python3 -c 'import json, sys; print(json.dumps({"amxbf16": sys.argv[1] or None}))' "${amx}" > "${R}/nfd-facts.json"
P gates nfd "${R}/nfd-facts.json" > "${R}/gate-nfd.txt" || { log "NFD gate failed"; exit 8; }
sel=$(P arm B5 selector)
read -r pod _ <<< "$(G b ". ~/h62/env && kubectl -n llm-inference get pods -l ${sel} -o json" | P pick-pod "")"
[ -n "${pod:-}" ] || { log "no Ready B5 pod"; exit 1; }
mnt=$(G b ". ~/h62/env && kubectl -n llm-inference get pod ${pod} -o json" | P model-mount main) || { log "no model mount"; exit 1; }
G b ". ~/h62/env && kubectl -n llm-inference exec -i ${pod} -c main -- python3 - ${mnt}" \
    < "${R}/verify-weights.py" > "${R}/weights-verify.txt" 2>&1
tail -1 "${R}/weights-verify.txt" > "${R}/weights-facts.json"
P gates weights "${R}/weights-facts.json" > "${R}/gate-weights.txt" || { log "weight gate failed"; exit 8; }
log "setup done: both arms serve"

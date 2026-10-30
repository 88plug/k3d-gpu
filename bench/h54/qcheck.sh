#!/usr/bin/env bash
# qcheck.sh STEP: H54q's checks on crescent around its holds. Neither step is part of the
# protocol or the score; both are records.
#   verify      under the bench lock: the sha256 of every weight file H54q's arms load, against
#               ~/k3dgpu-gemma/weights*.sha256 (the H54 pre-registration's list) and OVQ's
#               manifest.json; writes results/H54q/weights-verify.txt
#   frontdoor L a 16-token chat through Dynamo :8100 for each serving pair, passing when it
#               answers with content and that pair's SGLang num_requests_total rises (180 s each,
#               E2 item 7's check); writes results/H54q/frontdoor-L.txt. H54q uses no card and
#               never stops a serving pair; this records that both still serve.
set -uo pipefail
G=${HOME}/k3dgpu-gemma
M=${G}/models
O=${G}/results/H54q
mkdir -p "${O}"
case ${1:?usage: qcheck.sh verify|frontdoor LABEL} in
verify)
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 21600 9 || { echo "lock not taken in 21600 s"; exit 4; }
    f=${O}/weights-verify.txt
    {
        date -u +%FT%TZ
        # weights.sha256 names OVB's download directory; R1 renamed it to the Hub id's path.
        sed 's#  OpenVINO_gemma-4-26b-a4b-it-int4-ov/#  OpenVINO/gemma-4-26b-a4b-it-int4-ov/#' "${G}/weights.sha256" \
            | (cd "${M}" && sha256sum -c -)
        echo "weights.sha256 rc=$?"
        (cd "${M}/Intel_gemma-4-26B-A4B-it-int4-AutoRound" && sha256sum -c "${G}/weights-autoround.sha256")
        echo "weights-autoround.sha256 rc=$?"
        python3 - "${M}/k3dgpu/gemma-4-26b-a4b-it-qat-int4-sym-g32-ov" <<'PY'
import hashlib, json, os, sys
d = sys.argv[1]
files = json.load(open(os.path.join(d, "manifest.json")))["files"]
bad = 0
for n, want in sorted(files.items()):
    h = hashlib.sha256()
    with open(os.path.join(d, n), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    ok = h.hexdigest() == want["sha256"] and os.path.getsize(os.path.join(d, n)) == want["bytes"]
    bad += not ok
    print(f"{'OK ' if ok else 'BAD'} OVQ {n} {h.hexdigest()[:16]} {want['bytes']}")
print(f"OVQ {d}: {len(files)} files, {bad} bad; not in the manifest: {sorted(set(os.listdir(d)) - set(files) - {'manifest.json'})}")
sys.exit(1 if bad else 0)
PY
        echo "OVQ manifest rc=$?"
        for d in google_gemma-4-26B-A4B-it-qat-q4_0-unquantized google_gemma-4-26B-A4B-it-qat-q4_0-gguf \
                 OpenVINO/gemma-4-26b-a4b-it-int4-ov Intel_gemma-4-26B-A4B-it-int4-AutoRound; do
            echo "${d} revision $(head -1 "${M}/${d}/.pinned-revision" 2>/dev/null || echo none)"
        done
        date -u +%FT%TZ
    } > "${f}" 2>&1
    grep -E "rc=|FAILED|BAD|files," "${f}" ;;
frontdoor)
    f=${O}/frontdoor-${2:?usage: qcheck.sh frontdoor LABEL}.txt
    : > "${f}"
    n() { curl -s -m 8 "http://127.0.0.1:$1/metrics" | awk '/^sglang:num_requests_total/{s+=$2} END{print s+0}'; }
    for P in 30000 30001; do
        b=$(n "${P}") end=$(( SECONDS + 180 )) ok=0 c=""
        until [ "${SECONDS}" -ge "${end}" ]; do
            c=$(curl -s -m 60 http://127.0.0.1:8100/v1/chat/completions -H 'Content-Type: application/json' \
                -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
                | python3 -c 'import json,sys;print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())' 2>/dev/null)
            if [ -n "${c}" ] && [ "$(n "${P}")" -gt "${b}" ]; then ok=1; break; fi
            sleep 5
        done
        echo "$(date -u +%FT%TZ) :${P} pass=${ok} before=${b} after=$(n "${P}") content=${c:-none}" | tee -a "${f}"
    done ;;
*) echo "usage: qcheck.sh verify|frontdoor LABEL"; exit 2 ;;
esac

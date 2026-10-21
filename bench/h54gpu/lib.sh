# shellcheck shell=bash
# lib.sh: shared pieces of the H54b/H54c arm scripts (sourced). EXPERIMENTS.md "H54
# pre-registration", measurement protocol, and amendment G1.
# Needs: KUBECONFIG, OUT (this hold's run dir), ROOT (repo checkout), PATH with k3d/kubectl.
C1_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%"
NODE_IMAGE=${NODE_IMAGE:-cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c}
MODELS=${MODELS:-${HOME}/k3dgpu-gemma/models}

say() { echo "== $* $(date -u +%FT%TZ)" | tee -a "${OUT}/arms.log"; }

# cluster_up NAME: one fresh cluster, C1's kubelet thresholds, the model tree read-only at /models.
cluster_up() {
    k3d cluster create "$1" --image "${NODE_IMAGE}" --no-lb --wait --timeout 300s \
        --k3s-arg="--disable=traefik@server:0" --k3s-arg="--disable=intel-gpu-dra@server:0" \
        --k3s-arg="${C1_ARGS}@server:*" \
        --k3s-node-label="nvidia.com/gpu.present=false@server:*" \
        --volume="${MODELS}:/models:ro@server:0" >> "${OUT}/cluster.log" 2>&1 || return 1
    k3d kubeconfig get "$1" > "${KUBECONFIG}"
    kubectl wait --for=condition=Ready node --all --timeout=300s >> "${OUT}/cluster.log" 2>&1
    wait_sa
}

# wait_sa: the default ServiceAccount exists (a pod is refused until the controller makes it).
wait_sa() {
    for _ in $(seq 1 60); do
        kubectl get serviceaccount default >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

# card_free N: renderD(128+N) has no process open (fuser) and no xpu-smi process.
card_free() {
    local rd="/dev/dri/renderD$((128 + $1))"
    [ -e "${rd}" ] || return 1
    ! sudo -n fuser "${rd}" >/dev/null 2>&1
}

# gpu_energy N: the xe hwmon energy counters of card N (µJ, "label=value ..."), or "none".
gpu_energy() {
    local bdf h out=""
    bdf=$(basename "$(readlink -f "/sys/class/drm/renderD$((128 + $1))/device")")
    for h in /sys/bus/pci/devices/"${bdf}"/hwmon/hwmon*; do
        for e in "${h}"/energy*_input; do
            [ -e "${e}" ] || continue
            out+="$(cat "${e%_input}_label" 2>/dev/null || basename "${e}")=$(sudo -n cat "${e}") "
        done
    done
    echo "${out:-none}"
}

# serve_pod NAME YAML_FILE PORT: apply, wait for /v1/models and a 16-token chat, port-forward
# to localhost:PORT. Not answering within BOOT_S (default 900) = not deliverable (rc 1).
serve_pod() {
    local name=$1 yaml=$2 port=$3 t0 boot=${BOOT_S:-900}
    kubectl apply -f "${yaml}" >> "${OUT}/arms.log" 2>&1 || return 1
    t0=$(date +%s)
    kubectl port-forward "pod/${name}" "${port}:8080" > "${OUT}/${name}.pf.log" 2>&1 &
    PF=$!
    while [ $(( $(date +%s) - t0 )) -lt "${boot}" ]; do
        if ! kill -0 "${PF}" 2>/dev/null; then
            kubectl port-forward "pod/${name}" "${port}:8080" >> "${OUT}/${name}.pf.log" 2>&1 &
            PF=$!
        fi
        if curl -sf "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1 &&
           curl -sf -m 120 "http://127.0.0.1:${port}/v1/chat/completions" -H 'Content-Type: application/json' \
               -d "{\"model\":\"m\",\"max_tokens\":16,\"temperature\":0,\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}]}" \
               > "${OUT}/${name}.smoke.json" 2>/dev/null; then
            echo $(( $(date +%s) - t0 )) > "${OUT}/${name}.boot_s"
            return 0
        fi
        phase=$(kubectl get pod "${name}" -o jsonpath='{.status.phase}' 2>/dev/null)
        [ "${phase}" = Failed ] && break
        sleep 10
    done
    kubectl logs "${name}" > "${OUT}/${name}.log" 2>&1
    echo "not deliverable after $(( $(date +%s) - t0 )) s" > "${OUT}/${name}.not-deliverable"
    return 1
}

# stop_pod NAME: logs, delete, wait until gone.
stop_pod() {
    kubectl logs "$1" > "${OUT}/$1.log" 2>&1
    [ -z "${PF:-}" ] || kill "${PF}" 2>/dev/null
    PF=""
    kubectl delete pod "$1" --wait=true --timeout=180s >> "${OUT}/arms.log" 2>&1
}

# n1_phase NAME PORT CARD: 60 s idle (RAPL), then 3 warmup + 8 sequential requests, 256 tokens,
# ignore_eos, temperature 0, unique prompts; RAPL package-0 and the card's xe energy beside it.
n1_phase() {
    local name=$1 port=$2 card=$3
    "${ROOT}/bench/energy.sh" --json "${OUT}/${name}.idle-energy.json" -- sleep 60 2>>"${OUT}/arms.log"
    echo "before $(date -u +%FT%TZ) $(gpu_energy "${card}")" > "${OUT}/${name}.gpu-energy"
    "${ROOT}/bench/energy.sh" --json "${OUT}/${name}.n1-energy.json" -- \
        python3 "${ROOT}/bench/llm_load.py" --base-url "http://127.0.0.1:${port}/v1" --model m \
        --requests 8 --concurrency 1 --warmup 3 --max-tokens 256 --temperature 0 --ignore-eos \
        --unique-prompts --timeout 600 --label "${name}" --out "${OUT}/${name}.n1.json" \
        >> "${OUT}/arms.log" 2>&1
    echo "after $(date -u +%FT%TZ) $(gpu_energy "${card}")" >> "${OUT}/${name}.gpu-energy"
}

# full_phases NAME BASE_URL CARDS EXTRA_BODY: H54b's protocol for one arm (steps 2-4): 60 s idle,
# N=1 (8 requests, 3 warmup), N=4 (16), N=16 (32), each 256 tokens, ignore_eos, temperature 0,
# unique prompts; TTFT: 4 requests at N=1 on ttft_prompt.txt, max_tokens 16; GSM8K items 0-49.
# RAPL package-0 per phase and the cards' xe energy counters around the whole arm. MODEL_ID: the
# served model name (default m).
full_phases() {
    local name=$1 url=$2 cards=$3 extra=${4:-} c n ex=()
    [ -z "${extra}" ] || ex=(--extra-body "${extra}")
    "${ROOT}/bench/energy.sh" --json "${OUT}/${name}.idle-energy.json" -- sleep 60 2>>"${OUT}/arms.log"
    for c in ${cards//,/ }; do echo "before card${c} $(date -u +%FT%TZ) $(gpu_energy "${c}")"; done > "${OUT}/${name}.gpu-energy"
    for n in 1:8 4:16 16:32; do
        "${ROOT}/bench/energy.sh" --json "${OUT}/${name}.n${n%%:*}-energy.json" -- \
            python3 "${ROOT}/bench/llm_load.py" --base-url "${url}" --model "${MODEL_ID:-m}" --requests "${n##*:}" \
            --concurrency "${n%%:*}" --warmup 3 --max-tokens 256 --temperature 0 --ignore-eos \
            --unique-prompts --timeout 900 --label "${name}" "${ex[@]}" --out "${OUT}/${name}.n${n%%:*}.json" \
            >> "${OUT}/arms.log" 2>&1
    done
    python3 "${ROOT}/bench/llm_load.py" --base-url "${url}" --model "${MODEL_ID:-m}" --requests 4 --concurrency 1 \
        --warmup 0 --max-tokens 16 --temperature 0 --unique-prompts --timeout 900 --label "${name}" \
        --prompt "$(cat "${ROOT}/bench/h54gpu/ttft_prompt.txt")" "${ex[@]}" --out "${OUT}/${name}.ttft.json" \
        >> "${OUT}/arms.log" 2>&1
    local nt=()
    [ -z "${extra}" ] || nt=(--no-think)
    python3 "${ROOT}/bench/h54gpu/gsm8k.py" run --base-url "${url}" --model "${MODEL_ID:-m}" \
        --items "${HOME}/k3dgpu-gemma/data/gsm8k-test.jsonl" --first 0 --last 49 "${nt[@]}" \
        --out "${OUT}/${name}.gsm8k.json" >> "${OUT}/arms.log" 2>&1
    for c in ${cards//,/ }; do echo "after card${c} $(date -u +%FT%TZ) $(gpu_energy "${c}")"; done >> "${OUT}/${name}.gpu-energy"
}

# disk_ok GB: true when `/` stays at or below 96% used after GB more is written (amendment G1).
disk_ok() {
    df -P -B1 / | awk -v need="$1" 'NR==2 {exit !((($3 + need * 1e9) / ($3 + $4)) <= 0.96)}'
}

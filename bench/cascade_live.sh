#!/usr/bin/env bash
# Live protocol for idea M3, cascade (shared-prefix) decode attention.
# Run each stage serially on an otherwise idle host. See bench/CASCADE_ATTENTION.md.
#
#   bench/cascade_live.sh s0     OUT URL MODEL             # stage 0, stock OVMS already serving
#   bench/cascade_live.sh kernel OUT                       # stage 1-lite microbench, no server
#   bench/cascade_live.sh aslo   OUT URL MODEL AGENT_TRACE # A@SLO of the tuned stock baseline
#
# s0 reads the IMC free-running counters with `sudo -n perf`: refresh the sudo
# timestamp first (sudo -v). REPS (default 5) sets repeats; orders alternate
# between repeats (ABBA) so drift does not line up with one arm.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPS=${REPS:-5}
IMC="uncore_imc_free_running_0/data_read/,uncore_imc_free_running_1/data_read/"
PCORES="0,2,4,6,8,10,12,14" # one logical CPU per P-core (lscpu -e: CPU 2k and 2k+1 share core k)

usage() {
    sed -n '2,12p' "$0"
    exit 2
}

# order <rep> <words...>: the words, reversed on even repeats (ABBA).
order() {
    local rep=$1 w rev=()
    shift
    if ((rep % 2)); then
        echo "$@"
        return
    fi
    for w in "$@"; do rev=("$w" "${rev[@]}"); done
    echo "${rev[@]}"
}

stage_s0() {
    local out=$1 url=$2 model=$3
    mkdir -p "$out"
    sudo -n true || { echo "cascade_live: run 'sudo -v' first (perf needs root, perf_event_paranoid=2)"; exit 1; }
    local rep mode tag pid
    for rep in $(seq 1 "$REPS"); do
        for mode in $(order "$rep" shared distinct short); do
            tag="$mode-b8-r$rep"
            sudo -n perf stat -a -I 500 -x, -e "$IMC" -o "$out/idle-$tag.csv" -- sleep 20
            date +%s.%N >"$out/imc-$tag.start"
            sudo -n perf stat -a -I 500 -x, -e "$IMC" -o "$out/imc-$tag.csv" &
            pid=$!
            python3 "$HERE/cascade_probe.py" run --url "$url" --model "$model" --mode "$mode" \
                --batch 8 --out "$out/$tag.json" || echo "cascade_live: $tag marked invalid"
            sudo -n kill -INT "$pid" 2>/dev/null || true
            wait "$pid" || true
            python3 "$HERE/cascade_probe.py" imc --perf "$out/imc-$tag.csv" \
                --perf-start "$(cat "$out/imc-$tag.start")" --run "$out/$tag.json" \
                --idle "$out/idle-$tag.csv" >"$out/$tag.imc.json" || true
        done
    done
    # S0d: attention-added step time vs batch, shared prefix vs short context.
    local b
    for rep in 1 2 3; do
        for b in $(order "$rep" 1 2 4 8); do
            for mode in shared short; do
                python3 "$HERE/cascade_probe.py" run --url "$url" --model "$model" --mode "$mode" \
                    --batch "$b" --out "$out/sweep-$mode-b$b-r$rep.json" || true
            done
        done
    done
}

stage_kernel() {
    local out=$1
    mkdir -p "$out"
    cc -O3 -mavx2 -mfma -mavxvnni -fopenmp -o "$out/cascade_kernel" "$HERE/cascade_kernel.c" -lm
    "$out/cascade_kernel" check >"$out/kernel-check.jsonl"
    export OMP_PROC_BIND=true OMP_PLACES=cores
    local rep arm shape
    for rep in $(seq 1 "$REPS"); do
        for shape in "8 3400 64" "4 3400 64" "16 3400 64" "8 8192 64" "8 16384 64" "8 3400 512"; do
            for arm in $(order "$rep" stock cascade vnni_gemv cascade_vnni); do
                # shellcheck disable=SC2086 # shape is three words on purpose
                taskset -c "$PCORES" "$out/cascade_kernel" bench $shape 8 30 "$arm" \
                    >>"$out/kernel-${shape// /-}.jsonl"
            done
        done
    done
}

stage_aslo() {
    local out=$1 url=$2 model=$3 at=$4
    mkdir -p "$out"
    local rep n scale
    for rep in 1 2; do
        for n in $(order "$rep" 4 6 8 10 12 16); do
            scale=$(awk -v n="$n" 'BEGIN { print n * 1.25 }') # ~20 sessions per concurrency slot
            python3 "$at" gen support --scale "$scale" --seed "$rep" -o "$out/support-n$n-r$rep.json"
            python3 "$at" run "$out/support-n$n-r$rep.json" --url "$url" --model "$model" \
                --max-concurrency "$n" --time-scale 1 --out "$out/aslo-n$n-r$rep"
        done
    done
    local args=()
    for f in "$out"/aslo-n*-r*.jsonl; do
        n=${f##*/aslo-n}
        args+=("${n%%-*}=$f")
    done
    for slo in 20 30; do
        python3 "$HERE/cascade_model.py" aslo --slo "$slo" "${args[@]}" >"$out/aslo-slo$slo.json"
    done
}

case "${1:-}" in
s0) [ $# -eq 4 ] || usage; stage_s0 "$2" "$3" "$4" ;;
kernel) [ $# -eq 2 ] || usage; stage_kernel "$2" ;;
aslo) [ $# -eq 5 ] || usage; stage_aslo "$2" "$3" "$4" "$5" ;;
*) usage ;;
esac

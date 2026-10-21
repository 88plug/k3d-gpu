#!/usr/bin/env bash
# reproduce.sh: the Reproduce header of an H54 hold (host, kernel, microcode, cpuset, tools,
# repository commit, image digests, model revisions and weight sha256, engine flags). Read-only.
set -uo pipefail
G=${G:-${HOME}/k3dgpu-gemma}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
echo "# H54 Reproduce header, $(date -u +%FT%TZ)"
echo "host: $(hostname)"
echo "cpu: $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo); $(nproc) CPUs; sockets $(lscpu | awk -F': *' '/^Socket/{print $2}'); NUMA nodes $(lscpu | awk -F': *' '/^NUMA node\(s\)/{print $2}')"
echo "microcode: $(awk -F': ' '/^microcode/{print $2; exit}' /proc/cpuinfo)"
echo "kernel: $(uname -r); cmdline: $(cat /proc/cmdline)"
echo "memory: $(awk '/MemTotal/{print $2" kB"}' /proc/meminfo); swap: $(awk '/SwapTotal/{print $2" kB"}' /proc/meminfo)"
echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo n/a); smt: $(cat /sys/devices/system/cpu/smt/active 2>/dev/null)"
echo "amx: $(grep -o 'amx_[a-z0-9]*' /proc/cpuinfo | sort -u | tr '\n' ' ')"
echo "cpuset: none (no node cpuset; the k3d node container sees every CPU; optimized arms bind threads to 0-47 with the engine's own knob)"
echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
for t in k3d.real kubectl helm uv; do echo "${t}: $("${G}/bin/${t}" version 2>/dev/null | head -1 || "${G}/bin/${t}" --version 2>/dev/null | head -1) sha256 $(sha256sum "${G}/bin/${t}" | cut -c1-16)"; done
echo "repo: $(git -C "${REPO}" rev-parse HEAD) $(git -C "${REPO}" status --porcelain | wc -l) uncommitted"
echo "eat: $(git -C "${G}/eat-src" rev-parse HEAD 2>/dev/null)"
echo "images:"
python3 "${HERE}/h54.py" arms | python3 -c 'import json,sys; [print(f"  {k}: {v}") for k, v in json.load(sys.stdin)["images"].items()]'
echo "  node: cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f60a47948eb1c4e6c5e80059c7f581427429c35a2627c58fc2307e4b76"
echo "models (revision, from .pinned-revision):"
for d in "${G}"/models/*/ "${G}"/models/*/*/; do
    [ -f "${d}.pinned-revision" ] && echo "  ${d#"${G}/models/"} $(head -1 "${d}.pinned-revision")"
    [ -f "${d}manifest.json" ] && echo "  ${d#"${G}/models/"} built here: $(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("source"), m.get("weights"))' "${d}manifest.json")"
done
echo "weights sha256:"
cat "${G}"/weights*.sha256 2>/dev/null | sed 's/^/  /'
echo "data: gsm8k-test-0-199.jsonl $(sha256sum "${HERE}/gsm8k-test-0-199.jsonl" | cut -c1-16) (openai/gsm8k main test @ 740312ad); ttft-prompt.txt $(sha256sum "${HERE}/ttft-prompt.txt" | cut -c1-16)"
echo "engine flags (Lg, Lg-mtp, Sg as rendered; B1g/Vq/Vi/A1o/A1g/B3g in each arm's deploy.yaml):"
python3 - "${HERE}" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import h54
print("  Lg:", " ".join(h54.llamacpp_args(False)))
print("  Lg-mtp:", " ".join(h54.llamacpp_args(True)))
print("  Sg:", " ".join(h54.sglang_args()), "SGLANG_CPU_OMP_THREADS_BIND=" + h54.THREADS)
EOF

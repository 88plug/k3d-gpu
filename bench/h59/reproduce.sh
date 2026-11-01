#!/usr/bin/env bash
# reproduce.sh: the Reproduce header of an H59-family hold (host, CPU, kernel, microcode, tools,
# repository commit, engine and node images, model revisions and file sha256). Each arm's
# rendered and staged manifests, server command line, CPUs and fit are in its own directory
# (agent.yaml, deploy.yaml, cpus.txt, fit.json). Read-only.
set -uo pipefail
G=${G:-${HOME}/k3dgpu-h59}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
echo "# H59 Reproduce header (${H59_PROFILE:-?}), $(date -u +%FT%TZ)"
echo "host: $(hostname)"
echo "cpu: $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo); $(nproc) CPUs; P-cores $(cat /sys/devices/cpu_core/cpus 2>/dev/null || echo none), E-cores $(cat /sys/devices/cpu_atom/cpus 2>/dev/null || echo none)"
echo "isa: amx $(grep -c amx_tile /proc/cpuinfo) avx512f $(grep -c avx512f /proc/cpuinfo) avx_vnni $(grep -c avx_vnni /proc/cpuinfo) (CPUs listing each)"
echo "microcode: $(awk -F': ' '/^microcode/{print $2; exit}' /proc/cpuinfo)"
echo "kernel: $(uname -r)"
echo "memory: $(awk '/MemTotal|MemAvailable|SwapTotal|SwapFree/{printf "%s %s kB; ", $1, $2}' /proc/meminfo)"
echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null); epp: $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null); k3d: $("${K3D_REAL:-/usr/bin/k3d}" version | head -1); kubectl: $(kubectl version --client 2>/dev/null | head -1)"
if [ -f "${REPO}/COMMIT" ]; then echo "repo: $(cat "${REPO}/COMMIT") (archive)"; else
    echo "repo: $(git -C "${REPO}" rev-parse HEAD) $(git -C "${REPO}" status --porcelain | wc -l) uncommitted"; fi
echo "k3d-gpu: $("${REPO}/scripts/k3d-gpu" version 2>/dev/null | head -1)"
echo "images:"
python3 - "${HERE}" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import run
print(f"  ovms: {run.OVMS_IMAGE}\n  llama.cpp: {run.LLAMA_CPU_IMAGE}\n  llama.cpp (drafter conversion): {run.LLAMA_FULL_IMAGE}")
print(f"models: {run.GGUF_REPO}@{run.GGUF_REV} ({run.GGUF_FILE}), {run.OVB}@{run.OVB_REV}, "
      f"{run.DRAFT_REPO}@{run.DRAFT_REV} (converted by prep.sh with the render's conversion Job)")
EOF
echo "  node: ${H59_IMAGE:-unset}"
echo "drafter: $(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("source"), m.get("convert"), m.get("quantize"), m.get("sha256"), m.get("bytes"))' \
    "${G}/models/gguf/google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant@9537141506fe8875b3ed45b264af13580cb29166/manifest.json" 2>/dev/null)"
echo "file sha256:"
sed 's/^/  /' "${G}/weights.sha256" 2>/dev/null
echo "data: gsm8k-test-0-199.jsonl $(sha256sum "${REPO}/bench/h54/gsm8k-test-0-199.jsonl" | cut -c1-16); ttft-prompt.txt $(sha256sum "${REPO}/bench/h54/ttft-prompt.txt" | cut -c1-16)"

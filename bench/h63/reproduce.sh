#!/usr/bin/env bash
# reproduce.sh: the Reproduce header of an H63 hold (host, CPU, kernel, microcode, RAPL limits,
# tools, repository commit, engine and node images, model revisions, the drafters' builds and
# file sha256). Each arm's rendered and staged manifests, server command line, CPUs and fit are in
# its own directory (agent.yaml, deploy.yaml, cpus.txt, fit.json). Read-only.
set -uo pipefail
G=${G:-${HOME}/k3dgpu-h63}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
echo "# H63 Reproduce header (${H63_PROFILE:-?}), $(date -u +%FT%TZ)"
echo "host: $(hostname)"
echo "cpu: $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo); $(nproc) CPUs; P-cores $(cat /sys/devices/cpu_core/cpus 2>/dev/null || echo none), E-cores $(cat /sys/devices/cpu_atom/cpus 2>/dev/null || echo none)"
echo "isa: amx $(grep -c amx_tile /proc/cpuinfo) avx512f $(grep -c avx512f /proc/cpuinfo) avx_vnni $(grep -c avx_vnni /proc/cpuinfo) (CPUs listing each)"
echo "microcode: $(awk -F': ' '/^microcode/{print $2; exit}' /proc/cpuinfo)"
echo "kernel: $(uname -r)"
echo "memory: $(awk '/MemTotal|MemAvailable|SwapTotal|SwapFree/{printf "%s %s kB; ", $1, $2}' /proc/meminfo)"
echo "rapl: PL1 $(cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw 2>/dev/null) uW, PL2 $(cat /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw 2>/dev/null) uW"
echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null); epp: $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null); k3d: $("${K3D_REAL:-/usr/bin/k3d}" version | head -1); kubectl: $(kubectl version --client 2>/dev/null | head -1)"
if [ -f "${REPO}/COMMIT" ]; then echo "repo: $(cat "${REPO}/COMMIT") (archive)"; else
    echo "repo: $(git -C "${REPO}" rev-parse HEAD) $(git -C "${REPO}" status --porcelain | wc -l) uncommitted"; fi
echo "k3d-gpu: $("${REPO}/scripts/k3d-gpu" version 2>/dev/null | head -1)"
echo "images:"
python3 "${HERE}/run.py" header
echo "  node: ${H63_IMAGE:-unset}"
echo "pinned revisions:"
for d in "${G}"/models/*/ "${G}"/models/*/*/; do
    [ -f "${d}.pinned-revision" ] && echo "  ${d#"${G}/models/"} $(head -1 "${d}.pinned-revision")"
done
echo "drafter check: $(tr -d '\n' < "${G}/prep/drafter-check.json" 2>/dev/null)"
echo "file sha256:"
sed 's/^/  /' "${G}/weights.sha256" 2>/dev/null
echo "data: gsm8k-test-0-199.jsonl $(sha256sum "${REPO}/bench/h54/gsm8k-test-0-199.jsonl" | cut -c1-16); ttft-prompt.txt $(sha256sum "${REPO}/bench/h54/ttft-prompt.txt" | cut -c1-16)"

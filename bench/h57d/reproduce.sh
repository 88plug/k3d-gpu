#!/usr/bin/env bash
# reproduce.sh: the Reproduce header of an H57d hold (host, kernel, microcode, tools, repository
# commit, image digests, model revisions and file sha256, engine flags). Read-only.
set -uo pipefail
G=${G:-${HOME}/k3dgpu-h57d}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
echo "# H57d Reproduce header, $(date -u +%FT%TZ)"
echo "host: $(hostname)"
echo "cpu: $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo); $(nproc) CPUs; P = 0-15, E = 16-31"
echo "isa: amx $(grep -c amx_tile /proc/cpuinfo) avx512f $(grep -c avx512f /proc/cpuinfo) avx_vnni $(grep -c avx_vnni /proc/cpuinfo) (CPUs listing each)"
echo "microcode: $(awk -F': ' '/^microcode/{print $2; exit}' /proc/cpuinfo)"
echo "kernel: $(uname -r)"
echo "memory: $(awk '/MemTotal|MemAvailable|SwapTotal|SwapFree/{printf "%s %s kB; ", $1, $2}' /proc/meminfo)"
echo "rapl: PL1 $(cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw) uW, PL2 $(cat /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw) uW"
echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null); epp: $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null); k3d: $(/usr/bin/k3d version | head -1); kubectl: $(kubectl version --client 2>/dev/null | head -1)"
echo "repo: $(git -C "${REPO}" rev-parse HEAD) $(git -C "${REPO}" status --porcelain | wc -l) uncommitted"
echo "images:"
echo "  llamacpp: $(python3 "${HERE}/h57d.py" arms | python3 -c 'import json,sys; print(json.load(sys.stdin)["llamacpp"])')"
echo "  ovms: openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 (k3d-gpu agent manifest's pin)"
echo "  node: cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f60a47948eb1c4e6c5e80059c7f581427429c35a2627c58fc2307e4b76"
echo "models (revision, from .pinned-revision; built here, from manifest.json):"
for d in "${G}"/models/*/ "${G}"/models/*/*/; do
    [ -f "${d}.pinned-revision" ] && echo "  ${d#"${G}/models/"} $(head -1 "${d}.pinned-revision")"
    [ -f "${d}manifest.json" ] && echo "  ${d#"${G}/models/"} built here: $(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("source"), {k: v["sha256"][:16] for k, v in m["files"].items()})' "${d}manifest.json")"
done
echo "file sha256:"
sed 's/^/  /' "${G}/weights.sha256" 2>/dev/null
echo "data: gsm8k-test-0-199.jsonl $(sha256sum "${REPO}/bench/h54/gsm8k-test-0-199.jsonl" | cut -c1-16); ttft-prompt.txt $(sha256sum "${REPO}/bench/h54/ttft-prompt.txt" | cut -c1-16)"
echo "engine flags (Od in each arm's deploy.yaml):"
python3 "${HERE}/h57d.py" arms | python3 -c 'import json,sys; [print(f"  {k}: {\" \".join(v)}") for k, v in json.load(sys.stdin)["args"].items()]'

#!/usr/bin/env bash
# export_qat_ov.sh [--dry-run] SRC_DIR SRC_REPO SRC_REV OUT_DIR [TASK]: export a QAT checkpoint to
# an OpenVINO IR whose weights keep the QAT scheme (Q4_0: symmetric INT4, 32-weight groups, every
# layer), with the pinned export venv (bench/eatflow/crescent/ov-export-venv.txt: optimum-intel
# 2.2.0, openvino 2026.4.1, nncf 3.4.0, transformers 5.5.4). Crescent amendment M1.
#
# The IR is written to OUT_DIR.partial and renamed to OUT_DIR only when the export succeeded and
# its manifest.json (pins, command, source repo and revision, the sha256 of every IR file) is
# written: the rename is the single step at which readers can see the IR, so none sees half of
# one. An existing OUT_DIR is left alone. TASK defaults to text-generation-with-past.
#   OV_VENV      the export venv (default ~/k3dgpu-bench/ovenv; created from the pins if missing)
#   EXPORT_NOTE  free text kept in manifest.json (e.g. why a pin is below the latest release)
set -euo pipefail
DRY=""
[ "${1:-}" = --dry-run ] && { DRY=1; shift; }
SRC=${1:?usage: export_qat_ov.sh [--dry-run] SRC_DIR SRC_REPO SRC_REV OUT_DIR [TASK]}
REPO=${2:?SRC_REPO} REV=${3:?SRC_REV} OUT=${4:?OUT_DIR} TASK=${5:-text-generation-with-past}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PINS=${HERE}/../eatflow/crescent/ov-export-venv.txt
V=${OV_VENV:-${HOME}/k3dgpu-bench/ovenv}
[[ "${REV}" =~ ^[0-9a-f]{40}$ ]] || { echo "SRC_REV must be a full 40-hex commit, got '${REV}'"; exit 2; }
cmd=("${V}/bin/optimum-cli" export openvino --model "${SRC}" --task "${TASK}" --weight-format int4 --sym
     --group-size 32 --ratio 1.0 --backup-precision none "${OUT}.partial")
if [ -n "${DRY}" ]; then printf '%s\n' "${cmd[@]}"; exit 0; fi
[ ! -e "${OUT}" ] || { echo "${OUT} exists: left alone"; exit 0; }
if [ ! -x "${V}/bin/optimum-cli" ]; then
    uv venv -q --python 3.12.11 "${V}"
    uv pip install -q --python "${V}/bin/python" --index-strategy unsafe-best-match \
        --extra-index-url https://download.pytorch.org/whl/cpu -r "${PINS}"
    uv pip check --python "${V}/bin/python"
fi
rm -rf "${OUT}.partial"
t0=$(date +%s)
"${cmd[@]}"
secs=$(( $(date +%s) - t0 ))
# The QAT grid (bench/models/qat_grid.py): each INT4 model file's error against the checkpoint
# as exported, then its int4 values and scales rewritten with scale = the block's grid step,
# then the error again; both reports stay beside the IR (grid-*.json).
for xml in "${OUT}.partial"/openvino*model.xml; do
    [ -e "${xml}" ] || continue
    b=$(basename "${xml}" .xml)
    "${V}/bin/python" "${HERE}/qat_grid.py" check "${xml}" "${SRC}" --out "${OUT}.partial/grid-${b}-export.json"
    if [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["matched"])' "${OUT}.partial/grid-${b}-export.json")" -gt 0 ]; then
        "${V}/bin/python" "${HERE}/qat_grid.py" snap "${xml}" "${SRC}" "${OUT}.partial/${b}.snap.xml"
        mv -f "${OUT}.partial/${b}.snap.xml" "${xml}"
        mv -f "${OUT}.partial/${b}.snap.bin" "${xml%.xml}.bin"
        "${V}/bin/python" "${HERE}/qat_grid.py" check "${xml}" "${SRC}" --out "${OUT}.partial/grid-${b}-snapped.json"
    fi
done
python3 - "${OUT}.partial" "${REPO}" "${REV}" "${TASK}" "${secs}" "${V}" "${cmd[@]}" <<'EOF'
import hashlib, json, os, subprocess, sys
out, repo, rev, task, secs, venv, *cmd = sys.argv[1:]
files = {}
for name in sorted(os.listdir(out)):
    p = os.path.join(out, name)
    if os.path.isfile(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        files[name] = {"sha256": h.hexdigest(), "bytes": os.path.getsize(p)}
freeze = subprocess.run(["uv", "pip", "freeze", "--python", os.path.join(venv, "bin", "python")],
                        capture_output=True, text=True).stdout.split()
pins = [x for x in freeze if x.split("==")[0].lower() in ("optimum-intel", "optimum", "openvino", "nncf",
                                                          "transformers", "torch", "openvino-tokenizers")]
grid = {n[len("grid-"):-len(".json")]: {k: v for k, v in json.load(open(os.path.join(out, n))).items()
                                         if k in ("matched", "skipped", "summary")}
        for n in sorted(os.listdir(out)) if n.startswith("grid-") and n.endswith(".json")}
json.dump({"source": {"repo": repo, "revision": rev}, "task": task, "weights": "int4 sym, group 32, ratio 1.0",
           "command": [c.replace(".partial", "") if c.endswith(".partial") else c for c in cmd],
           "qat_grid": grid, "export_seconds": int(secs), "pins": pins, "note": os.environ.get("EXPORT_NOTE", ""),
           "files": files},
          open(os.path.join(out, "manifest.json"), "w"), indent=1)
EOF
mv -T "${OUT}.partial" "${OUT}"
echo "exported ${OUT} in ${secs} s"

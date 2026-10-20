#!/usr/bin/env bash
# models.sh gemma4|qwen38|verify DIR REPO REV: the crescent rows' models under
# ~/k3dgpu-bench/models (LIVE_MODELS), each pinned to a Hub revision (crescent amendment M1, H53).
#   gemma4  link the shared Gemma downloads and QAT IR (~/k3dgpu-gemma/models) under the names live.sh
#           reads, after their files verify against the Hub; no second copy
#   qwen38  download Qwen/Qwen3.8-27B (bf16, B2) and OpenVINO/Qwen3.8-27B-int4-ov (AG) at their
#           pinned revisions, then verify them
#   verify DIR REPO REV
#           every LFS file of REPO@REV against the sha256 the Hub lists, and every other file's
#           size; prints one line per file and exits non-zero on any mismatch
set -euo pipefail
B=${HOME}/k3dgpu-bench
M=${B}/models
G=${HOME}/k3dgpu-gemma/models
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "${M}"
verify() {  # verify DIR REPO REV
    python3 - "$1" "$2" "$3" <<'EOF'
import hashlib, json, os, sys, urllib.request
d, repo, rev = sys.argv[1:4]
with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true") as r:
    sib = json.load(r)["siblings"]
bad = 0
for s in sib:
    p = os.path.join(d, s["rfilename"])
    lfs = s.get("lfs")
    if not os.path.exists(p):
        # every weight (LFS) file must be there; a skipped README or .gitattributes is noted
        print(f"{'MISSING' if lfs else 'absent '} {s['rfilename']}"); bad += bool(lfs); continue
    if lfs:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        ok = h.hexdigest() == lfs["sha256"]
        print(f"{'ok ' if ok else 'BAD'} {h.hexdigest()} {s['rfilename']}")
    else:
        ok = os.path.getsize(p) == s.get("size", os.path.getsize(p))
        print(f"{'ok ' if ok else 'BAD'} size {os.path.getsize(p)} {s['rfilename']}")
    bad += not ok
sys.exit(1 if bad else 0)
EOF
}
fetch() {  # fetch REPO REV DIR
    [ -x "${B}/hfvenv/bin/hf" ] || { "${B}/bin/uv" venv -q --python 3.12.11 "${B}/hfvenv"; \
        "${B}/bin/uv" pip install -q --python "${B}/hfvenv/bin/python" "huggingface_hub==2.2.0"; }
    HF_HUB_DISABLE_XET=1 "${B}/hfvenv/bin/hf" download "$1" --revision "$2" --local-dir "$3"
    rm -rf "$3/.cache"
}
case ${1:?usage: models.sh gemma4|qwen38|verify DIR REPO REV} in
    gemma4)
        verify "${G}/google_gemma-4-26B-A4B-it-qat-q4_0-unquantized" google/gemma-4-26B-A4B-it-qat-q4_0-unquantized \
            f1e06dc520982d9b9edd76859fdb7ab209449949
        ln -sfn "${G}/google_gemma-4-26B-A4B-it-qat-q4_0-unquantized" "${M}/gemma-4-26B-A4B-it-qat-q4_0-unquantized"
        # The fingerprint live.sh checks before every hold that mounts these weights (LIVE_MOUNT_B).
        "${HERE}/listing.sh" "${M}/gemma-4-26B-A4B-it-qat-q4_0-unquantized" > "${M}/gemma-4-26B-A4B-it-qat-q4_0-unquantized.listing"
        ir=${GEMMA_QAT_IR:-${G}/k3dgpu_gemma-4-26B-A4B-it-qat-int4-sym-g32-ov}   # bench/models/export_qat_ov.sh
        ln -sfn "${ir}" "${M}/gemma-4-26B-A4B-it-qat-int4-ov"
        (cd "${ir}" && sha256sum ./*.xml ./*.bin) ;;
    qwen38)
        fetch Qwen/Qwen3.8-27B 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 "${M}/Qwen3.8-27B"
        fetch OpenVINO/Qwen3.8-27B-int4-ov 7b20ea8c7df7223a85ff27bab5c473f0cb038943 "${M}/Qwen3.8-27B-int4-ov"
        verify "${M}/Qwen3.8-27B" Qwen/Qwen3.8-27B 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
        "${HERE}/listing.sh" "${M}/Qwen3.8-27B" > "${M}/Qwen3.8-27B.listing"
        verify "${M}/Qwen3.8-27B-int4-ov" OpenVINO/Qwen3.8-27B-int4-ov 7b20ea8c7df7223a85ff27bab5c473f0cb038943 ;;
    verify) verify "$2" "$3" "$4" ;;
    *) echo "usage: models.sh gemma4|qwen38|verify DIR REPO REV"; exit 2 ;;
esac

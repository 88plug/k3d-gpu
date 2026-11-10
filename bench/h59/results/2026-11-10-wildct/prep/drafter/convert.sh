gguf_convert () 
{ 
    set -euo pipefail;
    local repo=$1 rev=$2 out=$3 need=$4 quant=$5 src tmp have free;
    shift 5;
    if [ -f "${out}/manifest.json" ]; then
        echo "gguf-convert: ${out} is already built";
        return 0;
    fi;
    src="${out}.src" tmp="${out}.partial";
    rm -rf "${tmp}";
    mkdir -p "${src}" "${tmp}";
    have=$(du -sb "${src}" | cut -f1);
    free=$(df -P -B1 "${tmp}" | awk 'NR == 2 { print $4 }');
    if [ "$((need - have))" -gt "${free}" ]; then
        echo "gguf-convert: needs $(((need - have) >> 30)) GiB more under $(dirname "${out}"), $((free >> 30)) GiB free";
        return 1;
    fi;
    pip install -q --target /tmp/pydeps ${GGUF_CONVERT_PIP};
    PYTHONPATH=/tmp/pydeps python3 -c 'import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3],
                  allow_patterns=["*.json", "*.safetensors", "*.jinja", "*.txt", "*.model", "tokenizer*"])' "${repo}" "${rev}" "${src}";
    PYTHONPATH=/tmp/pydeps python3 /app/convert_hf_to_gguf.py "$@" --outfile "${tmp}/model.gguf" "${src}";
    if [ "${quant}" != - ]; then
        /app/llama-quantize "${tmp}/model.gguf" "${tmp}/model-${quant}.gguf" "${quant}";
        mv -f "${tmp}/model-${quant}.gguf" "${tmp}/model.gguf";
    fi;
    pip freeze --path /tmp/pydeps > "${tmp}/pip-freeze.txt";
    python3 -c 'import hashlib, json, os, sys
d, f = sys.argv[1], os.path.join(sys.argv[1], "model.gguf")
h = hashlib.sha256()
with open(f, "rb") as fh:
    for c in iter(lambda: fh.read(1 << 24), b""):
        h.update(c)
json.dump({"source": sys.argv[2] + "@" + sys.argv[3], "convert": sys.argv[4], "quantize": sys.argv[5],
           "sha256": h.hexdigest(), "bytes": os.path.getsize(f)}, open(os.path.join(d, "manifest.json"), "w"), indent=1)' "${tmp}" "${repo}" "${rev}" "$*" "${quant}";
    rm -rf "${out}";
    mv -T "${tmp}" "${out}";
    rm -rf "${src}";
    echo "gguf-convert: built ${out}/model.gguf"
}
gguf_convert "$@"

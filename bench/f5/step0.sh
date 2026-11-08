#!/usr/bin/env bash
# F5 step 0 on crescent, before the lock: the three release tags (git ls-remote, peeled),
# the node image the launcher names (pulled, its digest recorded) and the checkout's commit.
#   step0.sh <k3d-gpu checkout> <out>
set -uo pipefail
WT=$1 OUT=$2
mkdir -p "${OUT}"
{
    t0=$(date +%s)
    echo "[$(date -Is)] step 0: k3d-gpu $(git -C "${WT}" rev-parse HEAD) ($(git -C "${WT}" status --porcelain | wc -l) local changes)"
    for spec in "eas intel/enterprise-ai-solutions v1.2.0 a051f00fcb2dd473fe763610471cd509db82c433" \
                "ei intel/enterprise-inference v1.1.0 d035b1ef1981fbf5084ff05c9a337e620478bb87" \
                "eat intel/enterprise-agent-toolkit release-2.0.0 7b827e2d95cec826a20c7e3f5a30eb4818ed2992"; do
        set -- ${spec}
        peeled=$(git ls-remote "https://github.com/$2" "refs/tags/$3^{}" | cut -f1)
        [ -n "${peeled}" ] || peeled=$(git ls-remote "https://github.com/$2" "refs/tags/$3" | cut -f1)
        echo "ls-remote $1 $2 $3: ${peeled:-?} pin $4 $([ "${peeled}" = "$4" ] && echo '(tag = pin)' || echo '(TAG MOVED)')"
    done
    image="cryptoandcoffee/k3d-gpu:$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "${WT}/scripts/k3d-gpu")"
    docker pull -q "${image}" > /dev/null 2>&1
    echo "node image ${image}: $(docker image inspect -f '{{.Id}} {{index .RepoDigests 0}}' "${image}" 2>/dev/null)"
    echo "[$(date -Is)] step 0 wall=$(( $(date +%s) - t0 ))s"
} >> "${OUT}/step0.txt" 2>&1
cat "${OUT}/step0.txt"

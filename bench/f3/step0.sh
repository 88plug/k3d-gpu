#!/usr/bin/env bash
# F3 step 0 (before the lock): the node image at the deploy commit, and git ls-remote of the
# three pins. Both timed and recorded.
set -uo pipefail
R=/home/andrew/.cache/k3dgpu-f3/f3-scored
OUT="${R}/out"
TAG=k3d-gpu-f3node:48e1da90
mkdir -p "${OUT}"
{
    t0=$(date +%s)
    echo "[$(date -Is)] step 0 docker build start: docker build -t ${TAG} . (in ${R}/src, k3d-gpu $(cat "${R}/src/.commit"))"
    ( cd "${R}/src" && docker build -t "${TAG}" . ) > "${OUT}/step0-build.log" 2>&1
    rc=$?
    echo "[$(date -Is)] step 0 docker build rc=${rc} wall=$(( $(date +%s) - t0 ))s image ID $(docker image inspect -f '{{.Id}}' "${TAG}" 2>/dev/null)"
    t0=$(date +%s)
    for spec in "eas intel/enterprise-ai-solutions main 97f6157d6a72f7173c698c474d4e51d2c79f1fd6" \
                "ei intel/enterprise-inference main 3e8a24486b6ee812adf2bf452f8ba5751c16c8bd" \
                "eat intel/enterprise-agent-toolkit release-2.0.0 036b66234801e9bd950afd90830821859780d668"; do
        set -- ${spec}
        head=$(git ls-remote "https://github.com/$2" "refs/heads/$3" | cut -f1)
        echo "ls-remote $1 $2 $3: head ${head:-?} pin $4 $([ "${head}" = "$4" ] && echo '(head = pin)' || echo '(head moved; the pin is a SHA)')"
    done
    echo "[$(date -Is)] step 0 ls-remote wall=$(( $(date +%s) - t0 ))s"
} >> "${OUT}/step0.txt" 2>&1
cat "${OUT}/step0.txt"
tail -3 "${OUT}/step0-build.log"

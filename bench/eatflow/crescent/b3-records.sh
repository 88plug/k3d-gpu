#!/usr/bin/env bash
# b3-records.sh DIR NODE MODEL: H50o's records of arm B3's pulled model (EXPERIMENTS.md "H50-H52
# pre-registration", H50o: "the revision it pulls is read from its log and its weights' sha256
# recorded", "the graph each hold serves is saved"; H50o amendment O1 item 2). EAT's OVMS chart
# pulls MODEL with `ovms --pull` in its init container into its PVC, which k3s's local-path
# provisioner keeps in NODE under B3_STORAGE (default /var/lib/rancher/k3s/storage) as
# <pv>_default_ovms-model-server. live.sh runs this once B3 is ready, or when it is not. Into DIR:
#   init.log, ovms.log, ovms-previous.log  the init container's log (what ovms --pull reported),
#                  the server's log and its last crashed run's
#   describe.txt, objects.txt  the pod's description (events, restarts, kills) and the PVC and pod
#   volume.txt     the volume's path in the node ("none" when the PVC never bound)
#   files.txt      size and path of every file in the volume, .git excluded
#   sha256.txt     sha256 of every such file
#   git.txt        the clone's HEAD and refs and the .git size, when the pull left a .git
#   graph.pbtxt, config.json  the graph and the server config the hold serves
# Records only: nothing here gates a pair or the hold, and it always exits 0.
set -uo pipefail
D=${1:?usage: b3-records.sh DIR NODE MODEL} N=${2:?usage: b3-records.sh DIR NODE MODEL} M=${3:?usage: b3-records.sh DIR NODE MODEL}
S=${B3_STORAGE:-/var/lib/rancher/k3s/storage}
mkdir -p "${D}"
k() { kubectl -n default "$@"; }
k logs deploy/ovms-model-server -c model-downloader > "${D}/init.log" 2>&1
k logs deploy/ovms-model-server -c ovms > "${D}/ovms.log" 2>&1
k logs deploy/ovms-model-server -c ovms --previous > "${D}/ovms-previous.log" 2>&1
k describe pod -l app.kubernetes.io/name=ovms-model-server > "${D}/describe.txt" 2>&1
k get pvc,pod -o wide > "${D}/objects.txt" 2>&1
# shellcheck disable=SC2016 # expanded by the node's shell, with the paths as its arguments
v=$(docker exec "${N}" sh -c 'ls -d "$1"/*_default_ovms-model-server 2>/dev/null | head -1' _ "${S}")
echo "volume: ${v:-none}" > "${D}/volume.txt"
[ -n "${v}" ] || exit 0
# shellcheck disable=SC2016
docker exec "${N}" sh -c 'cd "$1" && find . -path "*/.git" -prune -o -type f -printf "%s %p\n" | sort -k2' _ "${v}" \
    > "${D}/files.txt" 2>&1
# shellcheck disable=SC2016
docker exec "${N}" sh -c 'cd "$1" && find . -path "*/.git" -prune -o -type f -print0 | sort -z | xargs -0 -r sha256sum' _ "${v}" \
    > "${D}/sha256.txt" 2>&1
# shellcheck disable=SC2016
docker exec "${N}" sh -c 'g=$1/.git; [ -d "$g" ] || { echo "no .git"; exit 0; }; cd "$g" || exit 0
    echo "HEAD $(cat HEAD)"
    find refs -type f | sort | while read -r r; do echo "$r $(cat "$r")"; done
    [ ! -f packed-refs ] || cat packed-refs
    echo "git_bytes $(du -sb . | cut -f1)"' _ "${v}/${M}" > "${D}/git.txt" 2>&1
docker exec "${N}" cat "${v}/${M}/graph.pbtxt" > "${D}/graph.pbtxt" 2>&1
docker exec "${N}" cat "${v}/config.json" > "${D}/config.json" 2>&1
exit 0

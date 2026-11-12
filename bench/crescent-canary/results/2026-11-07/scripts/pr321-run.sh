set -u
V=$1
case "$V" in
  head) R=$HOME/k3dgpu-bench/canary-321 ;;
  main) R=$HOME/k3dgpu-bench/canary-321-main ;;
  r2) R=$HOME/k3dgpu-bench/canary-321-r2 ;;
  *) echo "usage: head|main"; exit 2 ;;
esac
B=$HOME/k3dgpu-bench/runs/canary-321
O=$B/$V
test -s "$B/lock-taken" || { echo "no lock-taken file"; exit 3; }
kill -0 "$(cat "$B/holder.pid")" 2>/dev/null || { echo "lock holder not alive"; exit 3; }
rm -rf "$O"; mkdir -p "$O"
export PATH=$HOME/k3dgpu-bench/bin:$PATH
export K3D_GPU_CLUSTER=k3dgpu-canary-images
export K3D_GPU_STATE_DIR=$O/state
export K3D_GPU_SHARE=$R/share
L=$R/scripts/k3d-gpu
KC=$O/state/kubeconfig-k3dgpu-canary-images
git -C "$R" rev-parse HEAD > "$O/sha"
grep -n '^DEVICE_TEST_IMAGE=' "$L" > "$O/device-test-image.txt"
df --output=pcent / | tail -1 > "$O/df.txt"
k3d version > "$O/k3d-version.txt" 2>&1
cleanup() {
  date -u +%FT%TZ > "$O/down.start"
  "$L" down > "$O/down.log" 2>&1; echo "down rc $?" >> "$O/rc.txt"
  k3d cluster list > "$O/clusters-after.txt" 2>&1
}
trap cleanup EXIT
date -u +%FT%TZ > "$O/up.start"
"$L" up > "$O/up.log" 2>&1; echo "up rc $?" >> "$O/rc.txt"
date -u +%FT%TZ > "$O/test.start"
"$L" test > "$O/test.log" 2>&1; echo "test rc $?" >> "$O/rc.txt"
date -u +%FT%TZ > "$O/test.end"
kubectl --kubeconfig "$KC" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" allocatable xe="}{.status.allocatable.gpu\.intel\.com/xe}{" capacity xe="}{.status.capacity.gpu\.intel\.com/xe}{"\n"}{end}' > "$O/node-xe.txt" 2>&1
kubectl --kubeconfig "$KC" get events -A --sort-by=.lastTimestamp -o custom-columns=NS:.metadata.namespace,OBJ:.involvedObject.name,REASON:.reason,MSG:.message > "$O/events.txt" 2>&1
kubectl --kubeconfig "$KC" get pods -A -o wide > "$O/pods.txt" 2>&1
kubectl --kubeconfig "$KC" -n kube-system get ds -o wide > "$O/ds.txt" 2>&1
kubectl --kubeconfig "$KC" get ds -A -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].args}{"\n"}{end}' > "$O/ds-args.txt" 2>&1
cat "$O/rc.txt"

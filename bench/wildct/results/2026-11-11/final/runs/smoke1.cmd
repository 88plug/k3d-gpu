set -u
W=~/k3dgpu-bench-wildct; O=$W/runs/smoke1
export KUBECONFIG=$O/kubeconfig K3D_GPU_SHARE=$W/k3d-gpu/usr/share/k3d-gpu K3D_GPU_CLUSTER=k3dgpu-w-smoke K3D_GPU_VENDORS=auto,-nvidia,-intel
export K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<2Gi,imagefs.available<2Gi,nodefs.inodesFree<1%"
t0=$SECONDS
k3d-gpu up > $O/up.log 2>&1; echo "up rc=$? $((SECONDS-t0)) s"
grep -E 'opted out|Accelerators' $O/up.log
docker inspect -f 'priv={{.HostConfig.Privileged}} req={{.HostConfig.DeviceRequests}} rt={{.HostConfig.Runtime}}' k3d-k3dgpu-w-smoke-server-0
for i in $(seq 40); do kubectl get pods -A --no-headers 2>/dev/null | awk '$4!="Running"&&$4!="Completed"{b++} END{exit b>0}' && break; sleep 5; done
kubectl get pods -A --no-headers | awk '{print $1"/"$2, $4}'
kubectl get node -o jsonpath='{.items[0].spec.taints}{"\n"}{.items[0].status.allocatable}{"\n"}'
kubectl get pods -A -o json | python3 -c '
import json,sys
bad=[]
for p in json.load(sys.stdin)["items"]:
    for v in p["spec"].get("volumes",[]):
        hp=(v.get("hostPath") or {}).get("path","")
        if hp.startswith(("/dev/dri","/dev/nvidia")) or hp=="/dev": bad.append(p["metadata"]["name"]+" "+hp)
print("GPU-SCAN", "clean" if not bad else bad)'
k3d-gpu down > $O/down.log 2>&1; echo "down rc=$?"

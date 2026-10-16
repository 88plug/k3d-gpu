# KubeRay on k3d-gpu with hybrid P/E placement

A [KubeRay](https://github.com/ray-project/kuberay) `RayCluster` laid out across an
Intel hybrid CPU and its iGPU, using the `k3d-gpu.io/cores` pod label that k3d-gpu's
NRI balloons policy reads (see "Hybrid P-core / E-core placement" in the top-level
README).

| Ray group | Pod label `k3d-gpu.io/cores` | Resources (requests = limits) | Ray resources |
|-----------|------------------------------|-------------------------------|---------------|
| head      | `efficient` (E-cores)        | 1 CPU, 2Gi                    | `num-cpus=0`: runs no tasks |
| `cpu-p`   | `performance` (P-cores)      | 4 CPU, 8Gi                    | `CPU: 4`, `p_core: 4` |
| `cpu-e`   | `efficient` (E-cores)        | 2 CPU, 4Gi                    | `CPU: 2`, `e_core: 2` |
| `igpu`    | none (shared balloon)        | 2 CPU, 8Gi, `gpu.intel.com/i915: 1` | `CPU: 2`, `intel_gpu: 1` |

The balloons policy sizes a balloon from the pod's CPU requests, so every pinned
group requests whole CPUs equal to its limits. The head and both CPU groups carry
`nodeSelector: k3d-gpu.io/hybrid-cpu: "true"`, the label `k3d-gpu up` puts on nodes
of a hybrid host, so they stay Pending on a host where pinning cannot happen.
KubeRay sets `num-cpus` from the container CPU limit and adds the `/dev/shm`
volume and head Service ports itself.

## Versions

Pinned to what Intel's Enterprise Agent Toolkit (EAT) deploys, so the two can share a
cluster (one KubeRay operator per cluster):

| Component | Version | Source |
|-----------|---------|--------|
| KubeRay operator | helm chart `kuberay-operator` 1.3.0 (`quay.io/kuberay/operator:v1.3.0`, CRD `ray.io/v1`) | EAT `core/inventory/metadata/agentic-metadata.cfg` |
| Ray image | `rayproject/ray:2.40.0-py312@sha256:ad8df8a5…abbad` (linux/amd64, user 1000) | EAT `core/inventory/kuberay-config.yaml` |

Upstream KubeRay is at 1.7.1 (2026-09-21). Bump the operator, the Ray tag and digest,
and the pins in `tests/kuberay_test.sh` together.

## Install

On a host where `k3d-gpu detect` lists `hybrid` and `intel`:

```sh
k3d-gpu up

# Skip if EAT already installed the operator.
helm install kuberay-operator kuberay-operator \
  --repo https://ray-project.github.io/kuberay-helm/ --version 1.3.0 \
  --namespace ray-system --create-namespace --wait

kubectl create namespace ray
kubectl apply -f examples/kuberay/raycluster-hybrid.yaml
kubectl -n ray get raycluster hybrid
kubectl -n ray get pods -L ray.io/group,k3d-gpu.io/cores
```

The operator watches every namespace by default (`singleNamespaceInstall: false`).

### iGPU worker

- `gpu.intel.com/i915` is the resource for Meteor Lake and Arrow Lake iGPUs. On
  Lunar Lake or Panther Lake (driver `xe`) change both the request and limit to
  `gpu.intel.com/xe`. `k3d-gpu detect` prints the resource your host exposes.
- The render node keeps the host's group (`render`), and the Ray image runs as
  uid 1000. If the worker cannot open `/dev/dri/renderD*`, add the host's render gid
  to the igpu pod: `securityContext: {supplementalGroups: [<gid>]}` with the gid from
  `stat -c %g /dev/dri/renderD128`.
- `rayproject/ray` carries no Intel GPU user-space runtime (Level Zero / OpenCL).
  The worker holds the device; GPU compute needs an image with that runtime, such as
  one built on OpenVINO. Keep the Ray version equal to `rayVersion`.

## Use from Ray

```python
import ray

@ray.remote(num_cpus=1, resources={"p_core": 1})
def hot(): ...            # P-core worker

@ray.remote(num_cpus=1, resources={"e_core": 1})
def background(): ...     # E-core worker

@ray.remote(resources={"intel_gpu": 1})
def infer(): ...          # iGPU worker
```

## Check placement

Each pinned pod's allowed CPUs must sit inside its core type:

```sh
cat /sys/devices/cpu_core/cpus /sys/devices/cpu_atom/cpus     # host P / E CPUs
for p in $(kubectl -n ray get pods -o name); do
  echo "$p $(kubectl -n ray get "$p" -o jsonpath='{.metadata.labels.k3d-gpu\.io/cores}')"
  kubectl -n ray exec "$p" -- grep Cpus_allowed_list /proc/1/status
done
```

## Static test

`tests/kuberay_test.sh` validates the manifest with kubeconform v0.8.0 in strict mode
against the RayCluster CRD from the pinned chart, checks that a misspelled field is
rejected, and asserts the placement table above. It needs no cluster.

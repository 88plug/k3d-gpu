# Intel Enterprise Agent Toolkit on k3d-gpu

Runs Intel's [Enterprise Agent Toolkit](https://github.com/intel/enterprise-agent-toolkit)
(EAT) `release-1.0.3-hotfix` (commit `0d3438d`) on one k3d-gpu node, on a client
CPU with no AVX-512 and no AMX. EAT's docs target Xeon clusters built by
Kubespray. Here it goes through EAT's own **brownfield** path ("Vanilla
Kubernetes", an existing kubeconfig). No EAT file is edited.

Verified on a Lenovo P360 Ultra: i9-13900T (8 P-cores = CPUs 0-15, 16 E-cores =
CPUs 16-31, AVX2 + AVX-VNNI only), 125 GB RAM, Docker 29.3, k3d 5.9.0, k3s
v1.34.1+k3s1 (2026-10-16). Re-run end to end on a fresh cluster with the node
image built from this repo on 2026-10-16: 5 PLAY RECAPs `failed=0`, the model
Ready 4 min after deploy, `eat-check.sh` passed.

What comes up: EAT's LiteLLM gateway (GenAI Gateway, with its Langfuse trace
stack), ingress-nginx, Redis Stack, pgvector, agent-sandbox v0.5.0 with a warm
pool of Python sandboxes, and EAT's CPU vLLM serving `Qwen/Qwen3-1.7B`,
registered in LiteLLM by EAT's own registration job.

## Reproduce

Needs docker, k3d, kubectl, curl and jq on the host. Until `hybrid` ships in a
published image, build the node image from this repo:
`docker build -t "$IMAGE" .` and add `K3D_GPU_IMAGE="$IMAGE"` to step 1.

```bash
# 1. A k3d-gpu cluster with EAT's node labels and k3s ServiceLB off.
export KUBECONFIG=$PWD/eat.kubeconfig
K3D_GPU_CLUSTER=eat \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb \
  k3d-gpu up

# 2. EAT's base agent stack (components: agentic-config.cfg). ~10 min.
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh

# 3. One model from EAT's vLLM catalog, registered in LiteLLM. ~6 min to Ready.
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait -n default --for=condition=Ready pod \
  -l app.kubernetes.io/instance=vllm-qwen-3-1-7b-cpu --timeout=1200s

# 4. End-to-end check: chat and a tool call through LiteLLM, then model-written
#    Python run in a sandbox.
EAT_WORK=$PWD/eat-work integrations/eat/eat-check.sh

# Tear down.
K3D_GPU_CLUSTER=eat k3d-gpu down
```

`eat-up.sh` clones EAT at the pinned tag and commit and builds a throwaway
Ubuntu 24.04 bastion container (`bastion.Dockerfile`). Then it runs
`brownfield.sh` in that container on the host network. EAT's deployer
apt-installs tools, writes `/etc/hosts`, `/usr/local/bin` and `~/.kube/config`,
and refuses to run on anything but Ubuntu 22.04+. The container keeps all of
that off the host.

| File | Role |
|------|------|
| `eat-up.sh` | clone + bastion build + run; `model <id>` deploys one catalog model |
| `brownfield.sh` | runs in the bastion; answers EAT's prompts and calls its brownfield functions |
| `bastion.Dockerfile` | Ubuntu 24.04 (digest-pinned) plus what EAT's one-click installer installs |
| `agentic-config.cfg` | EAT's own component toggles: gateway, ingress, Redis, pgvector, agent sandbox on; Kubespray, observability, models, KubeRay, CPU balloons off |
| `vllm-values.yaml` | k3d-gpu values merged over EAT's `vllm/xeon-values.yaml` (memory limit, P-core balloon, image digest) |
| `gateway-charts.py` | writes pinned copies of EAT's GenAI Gateway chart directories; `brownfield.sh` passes them to `deploy-genai-gateway.yml` |
| `gateway-values.yaml` | the gateway chart's pins: postgresql and redis chart versions, LiteLLM, PostgreSQL, Redis and init images by digest |
| `gateway-trace-values.yaml` | the Langfuse trace stack's pins: Langfuse web and worker, PostgreSQL, Valkey, ClickHouse, ZooKeeper, MinIO by digest |
| `eat-check.sh` | the end-state check below |

`tests/eat_test.sh` runs `eat-check.sh` against stub `kubectl` and `curl`
(no cluster) and checks that every script here is committed executable.

The gateway is reached on the host IP of the running ingress-nginx controller
pod (the k3d node IP). ingress-nginx binds hostPort 80/443 there, with `Host: api.example.com` (`cluster_url` in `agentic-config.cfg`) over
TLS with a self-signed cert. The host's own ports 80/443 are not used.

## Observed end state

`eat-check.sh` on the live cluster:

```
==> Chat through LiteLLM at https://api.example.com (ingress on 172.20.0.2)...
    Kubernetes is an open-source platform for automating the deployment, scaling, and management of containerized applications.
==> Tool call through LiteLLM...
    get_weather({"city": "Portland"})
==> Code run: model writes Python through LiteLLM, agent sandbox runs it...
    model wrote:
      sum_of_squares = sum(i**2 for i in range(1, 11))
      print(sum_of_squares)
    sandbox python-pool-hv9sd: {"stdout":"385\n","stderr":"","exit_code":0}
==> EAT check passed.
```

The check fails unless the chat reply is non-empty, the tool request comes back
as `finish_reason: tool_calls` naming `get_weather` (vLLM's `hermes` parser, set
by EAT's catalog), and the sandbox prints exactly `385`. The code run is a
scripted pipeline: bash hands the model's text to the sandbox router. It is not
an agent loop and not a tool call.

The code goes to the router as `python3 -c '<code>'`, single-quoted by jq's
`@sh`. agent-sandbox v0.5.0's `python-runtime-sandbox` splits the command with
`shlex.split` (newer releases run it under `/bin/sh -c`; both read `@sh` quoting
back unchanged). Bash's `printf %q` does not survive `shlex.split` for
multi-line code. Sent to the live v0.5.0 router:

```
python3 -c s\ =\ sum\(i\*\*2\ for\ i\ in\ range\(1,\ 11\)\)$'\n'print\(s\)
  -> SyntaxError: invalid syntax, exit_code 1
python3 -c 's = sum(i**2 for i in range(1, 11))
print(s)'
  -> {"stdout":"385\n","stderr":"","exit_code":0}
```

Raw gateway response from the first run (`POST /v1/chat/completions`,
`model: Qwen/Qwen3-1.7B`):

```
"system_fingerprint":"vllm-0.24.0-5616035f","choices":[{"finish_reason":"stop",...
"content":"<think>\n\n</think>\n\nKubernetes is an open-source platform for automating the deployment, ..."
"usage":{"completion_tokens":27,"prompt_tokens":20,"total_tokens":47}   HTTP 200
```

vLLM lands on P-cores through k3d-gpu's balloons policy:

```
nri-resource-policy-balloons: assigning container default/vllm-qwen-3-1-7b-cpu-.../vllm to balloon performance[0]{cpus:"0-7", mems:"0"}
vllm pod: Cpus_allowed_list: 0-7          (host /sys/devices/cpu_core/cpus: 0-15)
vllm:     OpenMP core ids=[1, 3, 5, 7]; Explicitly set (6.0/16.0) GiB for KV cache
vllm pod: image vllm/vllm-openai-cpu@sha256:b0e6f860...981a1   (from vllm-values.yaml)
```

`cpu: "8"` is 8 logical CPUs: CPUs 0-7 are 4 P-cores with both hyperthreads.
vLLM's CPU backend runs one OpenMP thread per physical core, so it computes on
4 threads (`core ids=[1, 3, 5, 7]`); the sibling threads stay in the balloon,
idle. Raise `cpu` to give vLLM more P-cores.

## Blockers hit, and the fix for each

1. **No EAT menu reaches brownfield.** `brownfield_deployment()` is defined in
   `core/lib/brownfield/brownfield_deployment.sh`, but `deploy-agentic-stack.sh`
   only sources it (line 629) and never calls it. *Fix:* `brownfield.sh` sources
   the script with `DEPLOY_SOURCED=1` (its own hook, line 1137) and calls the
   function, piping the menu answers `5` (Vanilla), `1` (Deploy Inference
   Stack), `yes`.
2. **Sourcing passes our arguments to EAT's parser.** `deploy-agentic-stack.sh`
   loops over `"$@"` at top level (line 58) even when sourced, so
   `brownfield.sh model cpu-qwen3-1-7b` died with
   `ERROR: Unknown argument 'model'`. *Fix:* `set --` before `source`.
3. **k3s is not detected as vanilla.** The detection regex
   `^v[0-9]+\.[0-9]+\.[0-9]+$` (brownfield_deployment.sh:124) rejects
   `v1.34.1+k3s1`. Detection also runs before the bastion playbook installs
   kubectl, so a fresh bastion always gets the menu. *Fix:* answer `5`.
4. **Ubuntu-only deployer.** setup-bastion.yml:499-500 asserts Ubuntu >= 22.04.
   *Fix:* the bastion container. Its `pip3 install kubernetes pyyaml ...` as root
   against system Python (setup-bastion.yml:59-67) fails on 24.04 with
   `error: externally-managed-environment` (PEP 668). `sudo` drops EAT's
   `PIP_BREAK_SYSTEM_PACKAGES`, so the image sets `break-system-packages` in
   `/etc/pip.conf`.
5. **Root kubectl had no kubeconfig.** `deploy-cluster-config.yml` runs with
   `--become` (cluster-config-init.sh:11). Brownfield copies the kubeconfig only
   to the user's `~/.kube/config`, so root's kubectl hit `localhost:8080`:
   `kubectl patch storageclass local-path ... dial tcp [::1]:8080: connect: connection refused`.
   *Fix:* `brownfield.sh` installs the same kubeconfig at `/root/.kube/config`
   in the bastion.
6. **ingress-nginx Pending on k3s.** EAT installs ingress-nginx 4.12.2 with
   `controller.hostPort` 80/443 and the chart's default `LoadBalancer` Service
   (deploy-ingress-controller.yml:54-72). k3s ServiceLB answers that Service
   with an svclb pod binding the same host ports on the same node. On a first
   cluster with ServiceLB on, EAT's readiness loop sat for 509.6 s and passed
   only after the Service was hand-patched to NodePort. *Fix:*
   `K3D_GPU_K3S_ARGS=--disable=servicelb`. On a fresh cluster with it, the
   controller was `Running` 28 s after install with no patch. Its Service stays
   `<pending>`, and nothing in EAT waits for that address (the bastion precheck
   that does is `ignore_errors`).
7. **Brownfield never labels nodes.** EAT's charts require
   `ei-inference-eligible=true` / `ei-infra-eligible=true` node affinity
   (vllm/values.yaml:141-146), but `label-nodes.yml` runs only from
   `install_kubernetes()` (kubernetes-setup.sh:15), the Kubespray path.
   *Fix:* `K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true`.
8. **The smallest catalog LLMs are not selectable.** `get_model_names`
   (model-selection.sh:103) accepts only 8B+ ids, while
   `deploy-inference-models.yml` also serves `cpu-qwen3-1-7b`, `cpu-qwen3-4b`,
   `cpu-llama-3-2-3b`, `cpu-qwen2-5-coder-7b`. *Fix:* `eat-up.sh model <id>`
   makes the same calls as brownfield "Manage Models"
   (`setup_bastion`, `deploy_inference_llm_models_playbook`) without that
   check. The model is still registered in LiteLLM by EAT's own job.
9. **40 GiB KV cache per model, no knob.** Every catalog entry in
   `vllm/xeon-values.yaml` sets `VLLM_CPU_KVCACHE_SPACE: "40"` (Qwen3-1.7B at
   line 181), and with EAT's balloons off the pod has no memory request or
   limit. Probed with EAT's v0.24.0 image under a 12 GiB limit, vLLM refuses
   cleanly rather than being OOM-killed:
   `Available memory on node 0 (10.13/12.0 GiB) on kv cache allocation is less than requested memory for kv (40.0 GiB)`.
   With 4 GiB it refused differently, because Qwen3-1.7B's default 40960-token
   context needs more:
   `To serve at least one request with the model's max seq len (40960), (4.38 GiB KV cache is needed...`.
   *Fix:* `brownfield.sh` passes EAT's playbook a copy of `xeon-values.yaml`
   (through its own `xeon_values_file` variable) with `vllm-values.yaml` merged
   on top and every KV cache set to `EAT_VLLM_KV_GIB` (default 6). The pod gets
   `memory: 16Gi`. The chart reads both values already.
10. **Two NRI policies cannot share a node.** EAT's `nri_cpu_balloons` role
    runs `helm upgrade --install nri-resource-policy-balloons` into
    `kube-system`, unpinned (install_nri.yaml:79,94). The DaemonSet name is the
    one k3d-gpu's hybrid policy already uses. Probed read-only:
    ```
    $ helm upgrade --install nri-resource-policy-balloons nri-plugins/nri-resource-policy-balloons -n kube-system --dry-run=server
    Error: unable to continue with install: ServiceAccount "nri-resource-policy-balloons" in namespace "kube-system" exists and cannot be imported into the current release: invalid ownership metadata
    ```
    *Fix:* `deploy_nri_balloon_policy=off`, and vLLM joins k3d-gpu's policy
    instead. EAT's vLLM chart already writes the pod annotation
    `balloon.balloons.resource-policy.nri.io: <cpu_balloon_annotation>`.
    nri-plugins v0.14.0 resolves that annotation to a balloon type by name before
    any match expression (`chooseBalloonDef`, cmd/plugins/balloons/policy/balloons-policy.go:747).
    So `cpu_balloon_annotation: performance` with `cpu: "8"` puts vLLM in
    k3d-gpu's P-core balloon, sized to 8 logical CPUs (4 P-cores), as observed
    above.

Not a blocker: **AMX/AVX-512 are not required.** The CPU vLLM image with EAT's
exact Qwen3 settings (`VLLM_CPU_SGL_KERNEL=1`, bf16, block size 128, chunked
prefill, prefix caching, hermes tool parser) served on this AVX2-only CPU, both
in a plain `docker run` probe and in the cluster. vLLM logs one fallback:
`Failed to create oneDNN linear, fallback to torch linear`. bf16 oneDNN matmul
needs ISA this CPU lacks. That costs speed, not correctness. EAT's AMX check
(`lscpu | grep -i amx`, get_optimized_cpu_topology.yaml:597-603) runs only in
the balloon path, which is off here.

## Versions: pinned and floating

Pinned here: EAT by tag and commit (`eat-up.sh` refuses any other HEAD), the
bastion base image by digest, Helm v3.14.0 baked into the bastion by sha256,
the vLLM image by digest (`vllm-values.yaml`), and the GenAI Gateway's charts
and images. EAT itself pins ingress-nginx 4.12.2 (`ingress_controller` in
`agentic-metadata.cfg`), kubectl v1.29.0, agent-sandbox v0.5.0 and the
Langfuse chart 1.5.30. `images.lock` lists every image pinned here, and
`scripts/images-lock pins` fails CI on any image in these files without a digest.

The GenAI Gateway needed its own fix. `deploy-genai-gateway.yml` runs
`helm dependency update` on `genai-gateway`, whose `Chart.yaml` asks for
postgresql `>=13.3.0` and redis `>=18.0.0`. On 2026-10-18 that resolved
postgresql 18.12.4 and redis 28.3.1, whose default tag is `latest`: with
EAT's repository overrides the gateway pulled `bitnamilegacy/postgresql:latest`
and `bitnamilegacy/redis:latest`. Bitnami keeps `bitnamilegacy` only as a
temporary archive ([bitnami/containers#83267](https://github.com/bitnami/containers/issues/83267)).
In stack mode `brownfield.sh` runs `gateway-charts.py`, which copies EAT's
`genai-gateway` and `genai-gateway-trace` chart directories to `~/k3d-gpu-charts`
in the bastion and pins them:

- `Chart.yaml`: postgresql 18.12.4 and redis 28.3.1 exactly.
- `values.yaml`: `gateway-values.yaml` merged in. LiteLLM
  `v1.83.14-stable.patch.2`, PostgreSQL `17.5.0-debian-12-r20` and Redis
  `8.0.3-debian-12-r2`, each by digest. The last two are the tags that carry
  `latest`'s digest, so the same images run.
- `templates/deployment.yaml`: the literal `busybox:1.36` init image, by digest.
- `charts/langfuse/values.yaml`: `gateway-trace-values.yaml` merged in. Langfuse
  web and worker `3.173.0` (the 1.5.30 chart's appVersion) and the five
  `bitnamilegacy` subchart images at the tags they already rendered, each by digest.

`brownfield.sh` passes the copies to that playbook only, as extra vars
`helm_charts_base_genai` and `helm_charts_base_genai_trace`. Extra vars take
precedence over the playbook's `vars_files`. `gateway-charts.py` stops the
deploy if EAT's files no longer match: an overlay repository or tag EAT's
values do not name, a missing dependency, or no `busybox:1.36` line. Rendered
with Helm v3.14.0 on 2026-10-18, the copies differ from EAT's charts only in
the 11 `image:` lines, each now `<tag>@sha256:...`.

Helm needed care: `setup-bastion.yml` installs it by running `get-helm-3` from
helm's `main` branch, unless `/usr/local/bin/helm` exists (`creates:`). With
Helm baked in, that task reports `ok` and the script it downloads is never run.

Still floating, all inside EAT's own playbooks and not reachable without
editing EAT: the bastion's apt packages (from the current Ubuntu 24.04
archive), the pip packages `setup-bastion.yml` installs (`ansible`,
`ansible-core`, `kubernetes`, `pyyaml`, `jsonpatch`, `requests`, `urllib3`, no
versions), its `ansible-galaxy collection install --force` of `kubernetes.core`,
`community.kubernetes`, `community.general` and `ansible.posix`, and the
registration job's `curlimages/curl:latest`. Outside the gateway, EAT's
bundled charts name images by tag only: Redis Stack
`redis/redis-stack-server:7.4.0-v8` and pgvector `pgvector/pgvector:pg16`, and
agent-sandbox builds its router and runtime images in the cluster from its
source. A later run can resolve any of these to a newer release than the one
observed.

## What stays unsupported, and why

- **Rook-Ceph.** EAT's brownfield menu hard-sets `deploy_ceph="no"`
  (brownfield_deployment.sh:193). k3s's `local-path` class serves every PVC
  instead. The vLLM chart pins `storageClassName: local-path`.
- **EAT's CPU balloons policy** (`deploy_nri_balloon_policy`), see blocker 10.
  On a k3d-gpu node without `hybrid` it could install. That case is
  UNVERIFIED (probe: `K3D_GPU_VENDORS=nvidia,intel k3d-gpu up`, then
  `deploy_nri_balloon_policy=on`).
- **Catalog models above the host's RAM.** EAT's default
  `cpu-qwen2-5-coder-14b` needs about 28 GiB of bf16 weights plus KV cache.
  Not tried here.

Not tried, so UNVERIFIED on k3d: observability (`deploy_observability=on`),
Istio ambient (`deploy_istio`, not set by the default config), KubeRay
(`deploy_kuberay=on`), the AgenticAI plugin (`deploy_agenticai_plugin=on`),
OVMS models. Probe for each: set the toggle in `agentic-config.cfg`, rerun
`eat-up.sh`, and check the component's own endpoint.

## Upstream notes for EAT (at `0d3438d`)

- `deploy-agentic-stack.sh:58` parses `"$@"` even under `DEPLOY_SOURCED=1`
  (`:1137`). The parse loop belongs inside `main`.
- `deploy-agentic-stack.sh:629` sources `brownfield_deployment.sh`, but no
  menu calls `brownfield_deployment`. Brownfield is documented and unreachable.
- `core/lib/brownfield/brownfield_deployment.sh:124`: vanilla detection
  rejects distro suffixes (`+k3s1`, `+rke2r1`). k3s/k3d/RKE2 deserve a platform
  of their own, or the regex should allow `+<suffix>`.
- `core/lib/components/kubernetes-setup.sh:15`: `label-nodes.yml` runs only
  for Kubespray. Brownfield should label nodes, or check the labels and say so,
  since every chart requires them.
- `core/lib/models/model-selection.sh:103`: the allowed id list is shorter than
  `playbooks/deploy-inference-models.yml` serves (`cpu-qwen3-1-7b`,
  `cpu-qwen3-4b`, `cpu-llama-3-2-3b`, `cpu-qwen2-5-coder-7b`, deepseek
  distills).
- `core/helm-charts/vllm/xeon-values.yaml:181` (and every catalog entry):
  `VLLM_CPU_KVCACHE_SPACE` is fixed at 10-60 GiB with no config knob, and the
  pod has no memory limit unless balloons are on.
- `core/helm-charts/vllm/templates/deployment.yaml`: no config checksum
  annotation, so a rerun that changes only the ConfigMap (the KV cache) leaves
  the running pod on the old value. Here the release was uninstalled before
  redeploying with a new KV size.
- `core/playbooks/deploy-ingress-controller.yml:54-72`: hostPort 80/443 plus
  the default `LoadBalancer` Service collides with k3s ServiceLB on the same
  node. `controller.service.type: NodePort` (or ClusterIP) would suffice with
  hostPort on.
- `core/roles/nri_cpu_balloons/tasks/install_nri.yaml:79,94`: chart unpinned
  (floats to whatever nri-plugins publishes). It also installs over a fixed
  release name without checking for an NRI policy already on the node; `:40`
  checks only that the NRI socket exists.
- `core/playbooks/register-model-genai-gateway.yml:23`: `curlimages/curl:latest`.
  `core/helm-charts/vllm/values.yaml:213`: `alpine` `tag: "latest"`. Both
  float.
- `core/playbooks/setup-bastion.yml:59-67`: system-wide `pip3 install` as
  root fails on Ubuntu 24.04 (PEP 668), which `:499-500` accepts as supported.
- `core/lib/cluster/config/cluster-config-init.sh:11`: `--become` playbooks
  call kubectl as root, but brownfield sets up only the invoking user's kubeconfig.
- `core/lib/system/setup-env.sh:30`: full `git clone` of Kubespray even for
  brownfield, which never runs it.

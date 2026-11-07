# Intel Enterprise AI Solutions on k3d-gpu

`k3d-gpu eas` installs Intel's [Enterprise AI Solutions](https://github.com/intel/enterprise-ai-solutions)
(EAS) on a k3d-gpu cluster with EAS's own installer, `es_auto_installer.sh`, in its
bring-your-own-cluster mode (`existing_kubernetes`). The layers are platform, inference
([enterprise-inference](https://github.com/intel/enterprise-inference), EI) and agent-toolkit
([enterprise-agent-toolkit](https://github.com/intel/enterprise-agent-toolkit) 2.0, EAT).
They run on the Xeon CPU path: `kubernetes_accelerator: cpu` is the only value EAS acts on at
the pinned commit. Gaudi and Arc stay with `k3d-gpu agent up`.

The default pins are Intel's GA releases of 2026-11-06 (`pins.json` profile `release`): EAS
[`v1.2.0`](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0) (`a051f00`) and
the commits its `configs/repos/repos.agent-toolkit.yaml` pins, enterprise-inference
[`v1.1.0`](https://github.com/intel/enterprise-inference/releases/tag/v1.1.0) (`d035b1e`) and EAT
[`release-2.0.0`](https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-2.0.0)
(`7b827e2`). Profiles `main` and `v1.2.0-rc.1` keep the commits F3 and H62 ran.

EAT 2.0 has no installer of its own: it is EAS's agent-toolkit layer, so `k3d-gpu eat` is
`k3d-gpu eas` with `--agent-toolkit`:

```bash
k3d-gpu eat up --dry-run    # facts, choices and overlay decisions; changes nothing
k3d-gpu eat up              # = eas up --agent-toolkit; creates the cluster first if it is absent
k3d-gpu eat check           # Intel's validate, stack readiness, then chat, tool call, guard and sandbox through EAS's LiteLLM
k3d-gpu eat status
k3d-gpu eat down            # teardown platform; the cluster stays
k3d-gpu eat up --legacy     # EAT 1.x (release-1.1.0) through its own installer, integrations/eat/eat-up.sh (from a checkout; not packaged)
```

`eas up` without `--agent-toolkit` installs platform and inference. `--layer erag` reaches
`eas-config.py`, which refuses it with the reason.

## What `eas up` does

1. Reads facts, read-only, into `facts.json` (schema `k3d-gpu.eas-facts/v1`, the one
   `eas-config.py` documents): nodes, namespaces, DaemonSets, the agent-sandbox CRD and its
   controller image, the server version, rootless docker, the node container's NRI socket
   and containerd CNI `bin_dirs` (`docker exec ... test -S` and `crictl info`), MemAvailable
   and inotify limits, the host's CPU topology read from `/sys` as EAS's
   `probe-node-topology.sh` reads it, and `k3d-gpu fit` of each of `eas-config.py`'s model
   candidates (vLLM on the CPU).
2. Resolves refs: a profile of `pins.json` (`--eas-ref v1.2.0-rc.1`) and a full SHA go to
   `eas-config.py` as given; any other `--eas-ref`, `--ei-ref` or `--eat-ref` becomes a
   commit through `git ls-remote` here, once, and is printed. An EAS ref that is no profile
   takes the EI and EAT commits from `configs/repos/repos.<layer>.yaml` at that EAS commit,
   as EAS's own `init` would, unless `--ei-ref` or `--eat-ref` names them.
3. `eas-config.py` writes the overrides, `global_config.edit.yaml`, `models.edit.yaml`,
   `repos.<layer>.yaml` and `choices.txt`, one `fact -> choice [source]` line per decision.
   Exit 3 is a refusal: nothing is changed. The commits it resolved (the `# eas|ei|eat`
   lines of `choices.txt`) go to `pins.resolved.json`. Under `kubernetes_cpu_policy:
   nri-balloons` it estimates EI's `cpu: auto` for the model (one NUMA node's logical CPUs,
   less what EAS's balloons policy reserves there, its SMT siblings or 12 CPUs, and
   Qwen3Guard's 8) and writes an explicit `cpu` (EAT's documented remedy) only when the pods
   outside the balloons, EAT's sandbox router among them, would not fit beside it (H62).
4. Stops before any change when `env/k3d-gpu` was set up for another layer or other commits
   (`env.json`): EAS keeps one layer per checkout and `init` never reseeds `models.yaml` or
   `config.<layer>.yaml`. `eas down`, then `eas up --reset-env`, moves the env aside.
5. Builds the bastion (`bastion.Dockerfile`), tagged by the sha256 of the Dockerfile and
   `requirements.lock`, or reuses it.
6. Runs `eas.sh up <layer>` in the bastion: clone EAS and check out the commit, apply the
   patch overlay (`overlay.py --apply`, with `kubernetes_cpu_policy` from the overrides and
   the node count), set each manifest rev from `repos.<layer>.yaml`,
   `init <layer> --env k3d-gpu`, set the manifest's `global_config:` keys in
   `env/k3d-gpu/global_config.yaml` (`auth_provider: litellm` for the agent toolkit: `init`
   writes them only into a file it creates), merge the two edit files into `env/k3d-gpu/`,
   then `install <layer> --env k3d-gpu -- -e @k3d-gpu-images.yaml -e @k3d-gpu-overrides.yaml
   [your args]`. Extra vars load last in `es_auto_installer.sh` `main()`, so the overrides
   win over every config file. `configure` is never run (at v1.2.0 it takes no `--force` and
   needs a terminal).
7. Prints the gateway: the LoadBalancer IP of eg-gateway's Service (k3s ServiceLB, the node
   IP), and with the agent toolkit a `curl --resolve litellm.<base_domain_name>:443:<ip>`
   line. `/etc/hosts` is not edited.

The bastion runs on the host network with the state dir read-write at `/eas-state`, a
kubeconfig holding this cluster's context alone read-only at `/kube/config`, and
`integrations/` read-only at `/k3d-gpu/integrations` (overlay.py finds its patch drafts
relative to the dir above `integrations/`). It runs as root: `es_auto_installer.sh`
`ensure_sudo` runs `sudo -k` under `set -e` unless EUID is 0, and the image has no sudo.
`eas.sh` hands the state dir back to your uid:gid when it exits. `HF_TOKEN` and the proxy
variables reach it by name only, when set.

State per cluster: `~/.local/state/k3d-gpu/eas-<cluster>/` (mode 0700): the EAS checkout with
its `env/k3d-gpu/` and `ext/`, `facts.json`, `pins.resolved.json`, `env.json`, `eas-config.py`'s
files, `overlay.txt` and `overlay-decisions.json`, `kubeconfig`, `layer` and `logs/`.

## The patch overlay

`overlay.py` decides each row of `overlay.json` at the pinned commit; `--dry-run` prints the
decisions. At EAS `v1.2.0`: 0002 (Istio's k3s/k3d CNI paths) and 0006 variant b (Envoy's
control-plane selector on k3s) always, 0003 (the nri-plugins chart pinned) under
`nri-balloons`, 0004 on more than one node, and 0007: the istio role applies Gateway API
v1.2.1 only when the cluster has no newer Gateway API CRDs. k3s v1.37 bundles newer ones,
and their safe-upgrades ValidatingAdmissionPolicy refuses a lower version, so without 0007
`install` stops at "istio | Install Gateway API CRDs". EAS `main` already keeps newer foreign
CRDs (EAS #42); 0007 is the same on `release-1.2`.

## What `eas check` does

`eas check` (and `eat check`) needs the agent toolkit (LiteLLM and the sandbox come with it).
It runs Intel's own `es_auto_installer.sh validate agent-toolkit --env k3d-gpu` in the
bastion first, with install's extra vars (every enabled component's validate checks; a
failure stops with its reason), then `integrations/eat/eat-check.sh` with `EAT_MODE=eas`:

1. `eas-ready.py` on `kubectl get pods,daemonsets -A -o json`, before any request. Every
   pod and DaemonSet in `istio-system`, `envoy-gateway-system`, `litellm`, `llm-inference`
   and `agent-sandbox-system` must be Ready. A Job's pods are not graded, nor a Failed pod
   under a ReplicaSet (kubelet keeps an Evicted pod; its replacement is graded). Istio
   ambient's `istio-cni-node` and `ztunnel` DaemonSets must exist, eg-gateway must have a
   Ready envoy proxy pod, `llm-inference` must hold a model pod besides the guard, and the
   cluster must have at most one NRI resource policy and one NFD worker. Each pod that is
   not Ready is printed with its cause where its status names one: Pending on the AMXBF16
   node affinity (EI's `defaults.require_amx: true` on a cluster without AMX), Pending on
   Insufficient cpu or memory (with the pod's requests), a DaemonSet whose nodeSelector
   matches no node (EAS's EnvoyProxy on k3s, overlay 0006), an `istio-system` pod waiting
   (istio-cni and ztunnel chain into the node's CNI paths, `istio_platform`). The guard
   model is fail-closed: while it is not Ready, LiteLLM refuses every chat, and the check
   says so. No key is minted for a stack that is not ready. `eas status` prints the same
   report, without stopping, after its list of pods not Ready: the place to look when
   `eas up` fails on a model or the guard that never turns Ready.
2. A LiteLLM virtual key, minted with the master key and deleted at exit.
3. A chat completion and a `get_weather` tool call through eg-gateway. Any status but 200
   fails with LiteLLM's `error.message`, and a 200 with `finish_reason: content_filter`
   (a guardrail block) fails with its reason.
4. When `GET /v2/guardrails/list` lists `content_safety` (EI's guardrail, on with the agent
   toolkit unless `guardrails_enabled` is off): the unsafe example of EI's guardrail docs,
   "How do I hack into a system?", must come back blocked, with a reason that names the
   content-safety guardrail and unsafe content. LiteLLM v1.84.0, enterprise-inference
   v1.1.0's pin, answers a `pre_call` block with an error status and the reason in
   `error.message`, as EI's guardrail docs say; v1.105.1 (enterprise-inference `main`)
   answers HTTP 200 with `finish_reason: content_filter` and the reason as the reply. Both
   pass; a model reply to the probe (any other `finish_reason`) fails the check.
5. The model writes Python, the agent sandbox router runs it, and it must print 385.

## Files

| File | Role |
|------|------|
| `eas.sh` | the in-bastion driver (`up`, `overlay-check`, `validate`, `down`, `status`) |
| `bastion.Dockerfile` | Ubuntu 24.04 (the EAT bastion's digest), kubectl v1.37.2, helm v3.22.1, yq v4.54.1, jq-1.8.2 by sha256, the installer's venv at `/opt/eas-venv`, kubernetes.core 6.3.0 by sha256: `pins.json`'s bastion profile |
| `requirements.in`, `requirements.lock`, `lock.sh` | EAS `requirements.txt` at the pinned commit, and its hash-locked resolution for the bastion's Python 3.12 |
| `images.yaml` | the agent toolkit's floating images by digest (sandbox router `latest-main`, Python runtime v0.5.0) |
| `known-issues.json` | upstream issues this path works around, one flat row each |
| `eas-config.py`, `pins.json` | the choice generator and the pins it reads; `pins.json`'s release profile follows EAS's latest release (pin `eas-release`, `hack/eas-release-pins`) |
| `eas-ready.py` | the stack readiness check `eas check` runs first (stdin: `kubectl get pods,daemonsets -A -o json`) |
| `overlay.py`, `overlay.json`, `patches/` | the k3s/k3d patch overlay |
| `ei-kcadm.sh`, `patches/ei/` | enterprise-inference's `keycloak_config` patch (variant a for EI `main`, b for `release-1.1`), applied after `init`: kcadm gets the Keycloak admin password from its environment and the client secret from Keycloak, never as an argument; stops the install when the task changed shape. With `auth_provider: litellm` (the agent toolkit) EI runs no `keycloak_config` |

## Verification

F3 ran `eas up --agent-toolkit` on EAS `main` on an i9-13900T: every step passed and the
secret scan found the Keycloak admin password on one sampled `kcadm` argv (REFUTED). F5 ran
`k3d-gpu eat` once at the GA tags on a Xeon 6741P and is SUPPORTED
([EXPERIMENTS.md](../../EXPERIMENTS.md#f5-run-xeon-6741p-2026-11-07)): `eat up` with `failed=0`
and no hand edit, `eat check` (Intel's `validate agent-toolkit`, then chat, a tool call, the
guardrail's block and a sandbox run printing 385), a secret scan of 0, and `eat down`. Its
records and the three wiring smokes before it are in
[bench/f5/results/2026-11-07/](../../bench/f5/results/2026-11-07/).

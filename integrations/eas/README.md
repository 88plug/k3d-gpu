# Intel Enterprise AI Solutions on k3d-gpu

`k3d-gpu eas` installs Intel's [Enterprise AI Solutions](https://github.com/intel/enterprise-ai-solutions)
(EAS) on a k3d-gpu cluster with EAS's own installer, `es_auto_installer.sh`, in its
bring-your-own-cluster mode (`existing_kubernetes`). The layers are platform, inference
([enterprise-inference](https://github.com/intel/enterprise-inference), EI) and agent-toolkit
([enterprise-agent-toolkit](https://github.com/intel/enterprise-agent-toolkit) 2.0, EAT).
They run on the Xeon CPU path: `kubernetes_accelerator: cpu` is the only value EAS acts on at
the pinned commit. Gaudi and Arc stay with `k3d-gpu agent up`.

```bash
k3d-gpu eas up --dry-run --agent-toolkit   # facts, choices and overlay decisions; changes nothing
k3d-gpu eas up --agent-toolkit             # creates the cluster first if it is absent
k3d-gpu eas check                          # chat, tool call and a sandbox run through EAS's LiteLLM
k3d-gpu eas status
k3d-gpu eas down                           # teardown platform; the cluster stays
```

`eas up` without `--agent-toolkit` installs platform and inference. `--layer erag` reaches
`eas-config.py`, which refuses it with the reason.

## What `eas up` does

1. Reads facts, read-only, into `facts.json` (schema `k3d-gpu.eas-facts/v1`, the one
   `eas-config.py` documents): nodes, namespaces, DaemonSets, the agent-sandbox CRD and its
   controller image, the server version, rootless docker, the node container's NRI socket
   and containerd CNI `bin_dirs` (`docker exec ... test -S` and `crictl info`), MemAvailable
   and inotify limits, and `k3d-gpu fit` of each of `eas-config.py`'s model candidates (vLLM
   on the CPU).
2. Resolves refs: a profile of `pins.json` (`--eas-ref v1.2.0-rc.1`) and a full SHA go to
   `eas-config.py` as given; any other `--eas-ref`, `--ei-ref` or `--eat-ref` becomes a
   commit through `git ls-remote` here, once, and is printed.
3. `eas-config.py` writes the overrides, `global_config.edit.yaml`, `models.edit.yaml`,
   `repos.<layer>.yaml` and `choices.txt`, one `fact -> choice [source]` line per decision.
   Exit 3 is a refusal: nothing is changed. The commits it resolved (the `# eas|ei|eat`
   lines of `choices.txt`) go to `pins.resolved.json`.
4. Builds the bastion (`bastion.Dockerfile`), tagged by the sha256 of the Dockerfile and
   `requirements.lock`, or reuses it.
5. Runs `eas.sh up <layer>` in the bastion: clone EAS and check out the commit, apply the
   patch overlay (`overlay.py --apply`, with `kubernetes_cpu_policy` from the overrides and
   the node count), set each manifest rev from `repos.<layer>.yaml`,
   `init <layer> --env k3d-gpu`, merge the two edit files into `env/k3d-gpu/`, then
   `install <layer> --env k3d-gpu -- -e @k3d-gpu-images.yaml -e @k3d-gpu-overrides.yaml
   [your args]`. Extra vars load last in `es_auto_installer.sh` `main()`, so the overrides
   win over every config file. `configure` is never run.
6. Prints the gateway: the LoadBalancer IP of eg-gateway's Service (k3s ServiceLB, the node
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
its `env/k3d-gpu/` and `ext/`, `facts.json`, `pins.resolved.json`, `eas-config.py`'s files,
`overlay.txt` and `overlay-decisions.json`, `kubeconfig`, `layer` and `logs/`.

## Files

| File | Role |
|------|------|
| `eas.sh` | the in-bastion driver (`up`, `overlay-check`, `down`, `status`) |
| `bastion.Dockerfile` | Ubuntu 24.04 (the EAT bastion's digest), kubectl v1.37.2, helm v3.22.1, yq v4.54.1 by sha256, the installer's venv at `/opt/eas-venv`, kubernetes.core 6.3.0 by sha256: `pins.json`'s bastion profile |
| `requirements.in`, `requirements.lock`, `lock.sh` | EAS `requirements.txt` at the pinned commit, and its hash-locked resolution for the bastion's Python 3.12 |
| `images.yaml` | the agent toolkit's floating images by digest (sandbox router `latest-main`, Python runtime v0.5.0) |
| `known-issues.json` | upstream issues this path works around, one flat row each |
| `eas-config.py`, `pins.json` | the choice generator and the pins it reads |
| `overlay.py`, `overlay.json`, `patches/` | the k3s/k3d patch overlay |

## Not verified

No EAS install has run on a k3d-gpu cluster from this code. The agent-toolkit layer has not
run on k3s or k3d; Kubernetes 1.37 is outside what EAS tests (it pins kubectl v1.34.3).

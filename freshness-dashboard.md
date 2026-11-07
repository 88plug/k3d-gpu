# Freshness dashboard

Updated 2026-11-07 12:31 UTC by the Freshness workflow (`hack/pins report`). Every pin of class ours or eat-stock in `pins/registry.toml` against its upstream's newest stable release.

## Open alarms

red:bot-sweep.yml
stale:bot-sweep.yml

## Pins behind (18)

| Group | Pin | Pinned | Latest | Type | Published | Age |
|---|---|---|---|---|---|---|
| cuda-test | `cuda-test-image` | `13.4.3` | `13.5.0` | minor | 2026-11-05 | 1d |
| examples | `examples-crewai` | `1.15.36` | `1.15.37` | patch | 2026-11-07 | 10h |
| examples | `image:examples:ghcr.io/berriai/litellm:v1.108.1` | `v1.108.1` | `v1.108.2` | patch | ? | ? |
| hf-console-fallback | `hf-gemma4-26b-int4-ov` | `4e5885c3f03844f5887982def09708bdc9f53f7a` | `7b3cbc98e24a9946e561f82c752ae26cd53fc660` | revision | 2026-11-05 | 2d |
| hf-console-fallback | `hf-qwen3.8-27b` | `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` | `37561816e66b5b9153b1b64c10968c18c1e6f4a0` | revision | 2026-11-07 | 7h |
| hf-console-fallback | `hf-qwen3.8-27b-int4-ov` | `7b20ea8c7df7223a85ff27bab5c473f0cb038943` | `d15f4d91eccd11dd44f615ffdb539c61bf1f1e6e` | revision | 2026-10-30 | 7d |
| host-intel | `compute-runtime` | `26.35.39758.10` | `26.40.40196.8` | minor | 2026-11-03 | 4d |
| host-intel | `level-zero` | `1.34.0` | `1.35.0` | minor | 2026-10-26 | 11d |
| host-intel | `linux-tools` | `7.3.1` | `7.3.3` | patch | ? | ? |
| images | `image:images:busybox:1.37` | `1.37` | `1.38` | line | 2026-10-28 | 9d |
| images | `image:images:ollama/ollama:0.40.13` | `0.40.13` | `0.40.15` | patch | 2026-11-06 | 1d |
| k3s | `k3s` | `v1.37.1-k3s1` | `v1.37.2-k3s1` | patch | 2026-11-03 | 3d |
| nri | `nri-balloons` | `v0.15.0` | `v0.15.1` | patch | ? | ? |
| opt-in-manifests | `agent-sandbox` | `v1.0.8` | `v1.0.10` | patch | 2026-11-06 | 14h |
| opt-in-manifests | `open-webui` | `v0.12.2` | `v0.13.0` | minor | 2026-11-03 | 4d |
| packaging-images | `image:packaging-images:ghcr.io/homebrew/brew:7.0.11` | `7.0.11` | `7.0.12` | patch | ? | ? |
| packaging-images | `packaging-fedora` | `44` | `45` | line | 2026-11-02 | 4d |
| vllm-xpu | `vllm-xpu` | `v0.32.0` | `v0.33.0` | minor | 2026-11-02 | 5d |

### Not resolved (1)

- `vllm-cpu` (vllm-cpu): vllm-cpu: vllm-project/vllm's latest release 'v0.33.0rc3' does not match {v}

## Open bot PRs (11)

| PR | Group | Verdict | Blocking |
|---|---|---|---|
| #365 | nri | auto | - |
| #357 | vllm-xpu | review | a human approval at the head |
| #355 | packaging-images | review | a human approval at the head |
| #354 | opt-in-manifests | review | a human approval at the head |
| #353 | k3s | auto | - |
| #352 | images | review | a human approval at the head |
| #351 | host-intel | review | a human approval at the head |
| #350 | hf-console-fallback | review | a human approval at the head |
| #349 | examples | auto | - |
| #347 | digests | review | a human approval at the head |
| #346 | cuda-test | review | a human approval at the head |

## EAT stock (always review)

- `eas-release`: current (`v1.2.0`)
- `eat`: current (`release-1.1.0`)
- `eat-litellm`: current (`v1.83.14-stable.patch.2`)
- `eat-vllm-cpu`: current (`v0.28.0`)
- `image:eat-stock:bitnamilegacy/clickhouse:25.2.1-debian-12-r0`: current (`25.2.1-debian-12-r0`)
- `image:eat-stock:bitnamilegacy/minio:2024.12.18-debian-12-r1`: current (`2024.12.18-debian-12-r1`)
- `image:eat-stock:bitnamilegacy/postgresql:17.3.0-debian-12-r1`: current (`17.3.0-debian-12-r1`)
- `image:eat-stock:bitnamilegacy/postgresql:17.5.0-debian-12-r20`: current (`17.5.0-debian-12-r20`)
- `image:eat-stock:bitnamilegacy/redis:8.0.3-debian-12-r2`: current (`8.0.3-debian-12-r2`)
- `image:eat-stock:bitnamilegacy/valkey:8.0.2-debian-12-r2`: current (`8.0.2-debian-12-r2`)
- `image:eat-stock:bitnamilegacy/zookeeper:3.9.3-debian-12-r8`: current (`3.9.3-debian-12-r8`)
- `image:eat-stock:busybox:1.36`: current (`1.36`)
- `image:eat-stock:langfuse/langfuse-worker:3.173.0`: current (`3.173.0`)
- `image:eat-stock:langfuse/langfuse:3.173.0`: current (`3.173.0`)
- `image:eat-stock:rayproject/ray:2.40.0-py312`: current (`2.40.0-py312`)
- `image:eat-stock:registry.k8s.io/agent-sandbox/python-runtime-sandbox:v0.5.0`: current (`v0.5.0`)
- `image:eat-stock:ubuntu:24.04`: current (`24.04`)
- `image:eat-stock:us-central1-docker.pkg.dev/k8s-staging-images/agent-sandbox/sandbox-router:latest-main`: current (`latest-main`)

## PENDING arms

A PENDING row's arm pins move only by a dated amendment pushed before it runs. Propose one with the command beside the row (a human dispatch; the PR is never bot-merged).

- H28w (`bench/xhost/fetch_models.py`, `bench/wildct/**`, `bench/load/**`, `bench/zero-copy-models/**`): `gh workflow run freshness.yml -f propose_amendment=H28w`
- H57wa (`bench/xhost/fetch_models.py`, `bench/h57d/h57d.py`, `bench/h57d/**`, `bench/wildct/**`, `bench/load/**`): `gh workflow run freshness.yml -f propose_amendment=H57wa`
- H57wb (`bench/xhost/fetch_models.py`, `bench/h57d/h57d.py`, `bench/h57d/**`, `bench/wildct/**`, `bench/load/**`): `gh workflow run freshness.yml -f propose_amendment=H57wb`
- H57wm (`bench/xhost/fetch_models.py`, `bench/h57d/h57d.py`, `bench/h57d/**`, `bench/wildct/**`, `bench/load/**`): `gh workflow run freshness.yml -f propose_amendment=H57wm`
- H59w (`bench/xhost/fetch_models.py`, `bench/h54/h54.py`, `bench/h54/test_h54.py`, `bench/h54/gsm8k-test-0-199.jsonl`, `bench/h57d/h57d.py`, `bench/h59/**`, `bench/wildct/**`, `bench/load/**`, `bench/shared-prefix/**`): `gh workflow run freshness.yml -f propose_amendment=H59w`
- H62r (`bench/eatflow/live.sh`, `bench/eatflow/crescent/**`, `bench/eatflow/**`, `bench/h62r/**`): `gh workflow run freshness.yml -f propose_amendment=H62r`

## Known issues and stale workarounds

known-issues: 8 issue(s), 12 site(s), 10 marker(s)
report ledger H54a bench/h54/h54.py: vllm-openai-cpu 0.24.0 is inside vllm-45844's affected range <0.25.0, fixed in 0.25.0 (informational; the ledger changes only by amendment)
report ledger H53 bench/eatflow/live.sh: vllm-openai-cpu 0.24.0 is inside vllm-45844's affected range <0.25.0, fixed in 0.25.0 (informational; the ledger changes only by amendment)
report ours console/model_opt.py: vllm 0.32.0 is inside vllm-pr-51493's affected range *, no fixed release known
report ours scripts/k3d-gpu: linux-npu-driver 1.38.0 is inside linux-npu-driver-56's affected range *, no fixed release known
report ours examples/kserve/install.sh: kserve 0.21.0 is inside kserve-4592's affected range *, no fixed release known
report eat-stock integrations/eat/eat-up.sh: enterprise-agent-toolkit release-1.1.0 is inside eat-9's affected range *, no fixed release known
report eat-stock integrations/eat/gateway-values.yaml: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report eat-stock examples/agents/litellm-gateway/requirements.txt: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report eat-stock examples/agents/litellm-gateway/constraints.txt: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report ours console/model_opt.py: vllm 0.32.0 is inside vllm-0.32-num-spec-tokens's affected range >=0.32.0, no fixed release known
known-issues: ok

## Ledger re-measure queue

Read-only. SUPPORTED rows of EXPERIMENTS.md whose recorded versions are a minor or major behind today's pins, or inside a known issue's affected range. A re-measure is a new pre-registered row; nothing here edits the ledger.

| # | Row | Why first | Reason | Suggested row |
|---|-----|-----------|--------|---------------|
| 1 | H57dm | ledger-rules (drives optimizer defaults) | llama.cpp b11749 -> b11960 (minor) | H57r |
| 2 | H6 | cited by a docs claim | rancher/k3s v1.34.1+k3s1 -> v1.37.1-k3s1 (minor) | H6r |
| 3 | H30 | cited by a docs claim | llama.cpp b11706 -> b11960 (minor) | H30r |
| 4 | H31 | cited by a docs claim | llama.cpp b11706 -> b11960 (minor) | H31r |
| 5 | H32 | cited by a docs claim | llama.cpp b11706 -> b11960 (minor) | H32r |
| 6 | H1b | other | priority-stale: vllm-openai-cpu 0.24.0 is inside vllm-45844 (<0.25.0, fixed in 0.25.0); priority-stale: vllm-openai-cpu 0.24.0 is inside vllm-50801 (<0.28.0, changed in 0.28.0); vllm-openai-cpu 0.24.0 -> 0.32.0 (minor) | H1br |

### Measured versions (SUPPORTED rows)

| Row | Measured | In the queue |
|---|---|---|
| H1b | rancher/k3s v1.37.1+k3s1; vllm-openai-cpu 0.24.0 | yes |
| H6 | openvino/model_server 2026.4.1; rancher/k3s v1.34.1+k3s1 | yes |
| H11 | cryptoandcoffee/k3d-gpu 0.1.21; rancher/k3s v1.37.1+k3s1 | no |
| H14 | rancher/k3s v1.37.1+k3s1 | no |
| H15 | rancher/k3s v1.37.1+k3s1 | no |
| H18w | cryptoandcoffee/k3d-gpu 0.1.89; rancher/k3s v1.37.1+k3s1 | no |
| H19 | cryptoandcoffee/k3d-gpu 0.1.57; openvino/model_server 2026.4.1 | no |
| H25 | cryptoandcoffee/k3d-gpu 0.1.28; openvino/model_server 2026.4.1 | no |
| H25w | cryptoandcoffee/k3d-gpu 0.1.89; hf:OpenVINO/Qwen3-4B-int4-ov b467368d; openvino/model_server 2026.4.1; rancher/k3s v1.37.1+k3s1 | no |
| H25x | cryptoandcoffee/k3d-gpu 0.1.61; hf:OpenVINO/Qwen3-4B-int4-ov b467368d; openvino/model_server 2026.4.1 | no |
| H28 | cryptoandcoffee/k3d-gpu 0.1.59; rancher/k3s v1.37.1+k3s1 | no |
| H30 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H31 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H32 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H34 | cryptoandcoffee/k3d-gpu 0.1.55; openvino/model_server 2026.4.1 | no |
| H52w | cryptoandcoffee/k3d-gpu 0.1.89; rancher/k3s v1.37.1+k3s1 | no |
| H57dm | cryptoandcoffee/k3d-gpu 0.1.63; hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov 4e5885c3; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; hf:google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415; llama.cpp b11749; openvino/model_server 2026.4.1 | yes |
| H59 | cryptoandcoffee/k3d-gpu 0.1.83; hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov 4e5885c3; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; hf:google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415; llama.cpp b11960; openvino/model_server 2026.4.1 | no |
| H60 | cryptoandcoffee/k3d-gpu 0.1.84; hf:ggml-org/Qwen3.8-Flash-Next-GGUF 91fb0035; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; llama.cpp b11960 | no |

Current pins: cryptoandcoffee/k3d-gpu 0.1.93; llama.cpp b11960; openvino/model_server 2026.4.1; rancher/k3s v1.37.1-k3s1; vllm-openai-cpu 0.32.0; vllm-openai-xpu 0.32.0

### Not machine-readable

- H16: no run section linked from the Probe column
- H18: a record block runs into the heading '### H20 pre-registration (phase-steer oracle, 2026-10-17)' (a fence left open?)
- H40: no run section linked from the Probe column

## Actions behind

GitHub Actions are pinned by commit SHA and only a human (or Dependabot) moves them: a bot token cannot push `.github/workflows/`. Apply with `git apply`, then open a PR.

```diff
--- a/.github/workflows/build-and-push.yml
+++ b/.github/workflows/build-and-push.yml
@@ -74,13 +74,13 @@
         uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1
 
       - name: Login to DockerHub
-        uses: docker/login-action@81c6f93d4bc991e510b342a00b419b3bb4efc5c6 # v4.7.0
+        uses: docker/login-action@8aa5831fb8d48d8f76594c7ddde61ae58019f550 # v4.8.0
         with:
           username: ${{ secrets.DOCKERHUB_USERNAME }}
           password: ${{ secrets.DOCKERHUB_TOKEN }}
 
       - name: Login to GitHub Container Registry
-        uses: docker/login-action@81c6f93d4bc991e510b342a00b419b3bb4efc5c6 # v4.7.0
+        uses: docker/login-action@8aa5831fb8d48d8f76594c7ddde61ae58019f550 # v4.8.0
         with:
           registry: ghcr.io
           username: ${{ github.actor }}
@@ -253,7 +253,7 @@
       # release "Latest" by default, and /releases/latest must serve the aur-v* package
       # release that packages.yml publishes.
       - name: Create GitHub Release
-        uses: softprops/action-gh-release@efb35369e0ad2afab669f228072c1b0d510eae64 # v3.0.3
+        uses: softprops/action-gh-release@8a0cd651e5b2365bd640854a3fa5dfc70e4875f3 # v3.0.4
         env:
           GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
         with:
```

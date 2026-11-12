# Freshness dashboard

Updated 2026-11-12 09:10 UTC by the Freshness workflow (`hack/pins report`). Every pin of class ours or eat-stock in `pins/registry.toml` against its upstream's newest stable release.

## Open alarms

red:freshness.yml

## Pins behind (4)

| Group | Pin | Pinned | Latest | Type | Published | Age |
|---|---|---|---|---|---|---|
| examples | `examples-crewai` | `1.15.39` | `1.15.40` | patch | 2026-11-11 | 10h |
| examples | `image:examples:ghcr.io/berriai/litellm:v1.109.0` | `v1.109.0` | `v1.109.1` | patch | ? | ? |
| packaging-images | `image:packaging-images:goreleaser/nfpm:v2.48.0` | `v2.48.0` | `v2.49.0` | minor | 2026-11-12 | 6h |
| vllm-recipes | `vllm-recipes` | `d9862f5cba9ee4e04c959aee595c0c4a410aeac0` | `294a6d27809e3b6543554214a8d99116bd7d13dd` | revision | ? | ? |

### Not resolved (1)

- `vllm-cpu` (vllm-cpu): unconfirmed: github-release says v=v0.32.0, oci says v0.33.0

## Open bot PRs (3)

| PR | Group | Verdict | Blocking |
|---|---|---|---|
| #427 | packaging-images | review | a human approval at the head |
| #426 | examples | auto | - |
| #423 | vllm-recipes | review | a human approval at the head |

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

No PENDING row has runner paths in `pins/classes` yet.

## Known issues and stale workarounds

known-issues: 8 issue(s), 12 site(s), 10 marker(s)
report ledger H54a bench/h54/h54.py: vllm-openai-cpu 0.24.0 is inside vllm-45844's affected range <0.25.0, fixed in 0.25.0 (informational; the ledger changes only by amendment)
report ledger H53 bench/eatflow/live.sh: vllm-openai-cpu 0.24.0 is inside vllm-45844's affected range <0.25.0, fixed in 0.25.0 (informational; the ledger changes only by amendment)
report ours console/model_opt.py: vllm 0.33.0 is inside vllm-pr-51493's affected range *, no fixed release known
report ours scripts/k3d-gpu: linux-npu-driver 1.38.0 is inside linux-npu-driver-56's affected range *, no fixed release known
report ours examples/kserve/install.sh: kserve 0.21.0 is inside kserve-4592's affected range *, no fixed release known
report eat-stock integrations/eat/eat-up.sh: enterprise-agent-toolkit release-1.1.0 is inside eat-9's affected range *, no fixed release known
report eat-stock integrations/eat/gateway-values.yaml: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report eat-stock examples/agents/litellm-gateway/requirements.txt: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report eat-stock examples/agents/litellm-gateway/constraints.txt: litellm 1.83.14 is inside litellm-1.83.14-advisories's affected range <1.100.4 || >=1.101.0,<1.101.3 || >=1.102.0,<1.102.2 || >=1.103.0,<1.103.1 || >=1.104.0rc1,<1.104.0rc2, fixed in 1.100.4
report ours console/model_opt.py: vllm 0.33.0 is inside vllm-0.32-num-spec-tokens's affected range >=0.32.0, no fixed release known
known-issues: ok

## Ledger re-measure queue

Read-only. SUPPORTED rows of EXPERIMENTS.md whose recorded versions are a minor or major behind today's pins, or inside a known issue's affected range. A re-measure is a new pre-registered row; nothing here edits the ledger.

| # | Row | Why first | Reason | Suggested row |
|---|-----|-----------|--------|---------------|
| 1 | H57dm | ledger-rules (drives optimizer defaults) | llama.cpp b11749 -> b12110 (minor) | H57r |
| 2 | H59 | ledger-rules (drives optimizer defaults) | llama.cpp b11960 -> b12110 (minor) | H59ra |
| 3 | H6 | cited by a docs claim | rancher/k3s v1.34.1+k3s1 -> v1.37.2-k3s1 (minor) | H6r |
| 4 | H30 | cited by a docs claim | llama.cpp b11706 -> b12110 (minor) | H30r |
| 5 | H31 | cited by a docs claim | llama.cpp b11706 -> b12110 (minor) | H31r |
| 6 | H32 | cited by a docs claim | llama.cpp b11706 -> b12110 (minor) | H32r |
| 7 | H60 | cited by a docs claim | llama.cpp b11960 -> b12110 (minor) | H60r |
| 8 | H1b | other | priority-stale: vllm-openai-cpu 0.24.0 is inside vllm-45844 (<0.25.0, fixed in 0.25.0); priority-stale: vllm-openai-cpu 0.24.0 is inside vllm-50801 (<0.28.0, changed in 0.28.0); vllm-openai-cpu 0.24.0 -> 0.32.0 (minor) | H1br |

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
| H28w | cryptoandcoffee/k3d-gpu 0.1.89; rancher/k3s v1.37.1+k3s1 | no |
| H30 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H31 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H32 | cryptoandcoffee/k3d-gpu 0.1.55; llama.cpp b11706; openvino/model_server 2026.4.1 | yes |
| H34 | cryptoandcoffee/k3d-gpu 0.1.55; openvino/model_server 2026.4.1 | no |
| H52w | cryptoandcoffee/k3d-gpu 0.1.89; rancher/k3s v1.37.1+k3s1 | no |
| H57dm | cryptoandcoffee/k3d-gpu 0.1.63; hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov 4e5885c3; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; hf:google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415; llama.cpp b11749; openvino/model_server 2026.4.1 | yes |
| H59 | cryptoandcoffee/k3d-gpu 0.1.83; hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov 4e5885c3; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; hf:google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415; llama.cpp b11960; openvino/model_server 2026.4.1 | yes |
| H59r | cryptoandcoffee/k3d-gpu 0.1.110; hf:OpenVINO/gemma-4-26b-a4b-it-int4-ov 4e5885c3; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; hf:google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415; llama.cpp b12110; openvino/model_server 2026.4.1 | no |
| H60 | cryptoandcoffee/k3d-gpu 0.1.84; hf:ggml-org/Qwen3.8-Flash-Next-GGUF 91fb0035; hf:google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be; llama.cpp b11960 | yes |

Current pins: cryptoandcoffee/k3d-gpu 0.1.111; llama.cpp b12110; openvino/model_server 2026.4.1; rancher/k3s v1.37.2-k3s1; vllm-openai-cpu 0.32.0; vllm-openai-xpu 0.33.0

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
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -524,7 +524,7 @@
       - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
       - name: Tool versions (hack/ci-tools.env)
         run: grep -E '^[A-Z0-9_]+=' hack/ci-tools.env >> "$GITHUB_ENV"
-      - uses: azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310 # v5.0.1
+      - uses: azure/setup-helm@2f5e7bc67f1f9972ab16657bb795f36d8f04de0e # v5.1.0
         with:
           version: ${{ env.HELM4_VERSION }}
       - name: Install kubeconform and PyYAML
--- a/.github/workflows/freshness.yml
+++ b/.github/workflows/freshness.yml
@@ -223,7 +223,7 @@
 
       - name: Open or update the bump PR
         if: steps.s.outputs.changed == 'true'
-        uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1 # v8.1.1
+        uses: peter-evans/create-pull-request@3ffccacd46053766916f087ce41d627c0b8cf2d1 # v8.1.3
         with:
           branch: autobump/${{ matrix.group }}
           delete-branch: true
@@ -268,7 +268,7 @@
 
       - name: Open or update the workaround removal PR
         if: steps.s.outputs.changed == 'true' && steps.s.outputs.followup != ''
-        uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1 # v8.1.1
+        uses: peter-evans/create-pull-request@3ffccacd46053766916f087ce41d627c0b8cf2d1 # v8.1.3
         with:
           branch: autobump/ki-${{ steps.s.outputs.followup }}
           base: autobump/${{ matrix.group }}
@@ -440,7 +440,7 @@
         run: hack/autobump-branch-reset "autobump/amend-${ROW}"
 
       - name: Open or update the amendment PR
-        uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1 # v8.1.1
+        uses: peter-evans/create-pull-request@3ffccacd46053766916f087ce41d627c0b8cf2d1 # v8.1.3
         with:
           path: pr
           branch: autobump/amend-${{ inputs.propose_amendment }}
```

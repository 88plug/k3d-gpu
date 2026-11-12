# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-12T11:26:26Z. 3 open.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-12T09:33:26Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/43175145174
Conclusion: **failure**, created 2026-11-12T07:25:22Z, commit 019e2fe28c17f564e74bcab82366cd0872714e47
Failed jobs: Bump digests
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4879854Z   GROUP: digests
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4880059Z   REASON: 
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4880258Z   PROMETHEUS_VERSION: 3.16.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4880657Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4881098Z   KUBECONFORM_VERSION: v0.8.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4881539Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4882429Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4883007Z   CHROME_VERSION: 157.0.8103.50
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4883256Z   JSONSCHEMA_VERSION: 4.26.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4883488Z   HELM4_VERSION: v4.3.1
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4883709Z   SHELLCHECK_VERSION: v0.11.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4883945Z   PACKAGING_VERSION: 26.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4884175Z   PYYAML_VERSION: 6.0.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:00.4884384Z ##[endgroup]
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.0279907Z Collecting packaging==26.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.0648368Z   Downloading packaging-26.3-py3-none-any.whl.metadata (3.5 kB)
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.1173731Z Collecting PyYAML==6.0.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.1274310Z   Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.4 kB)
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.1415381Z Downloading packaging-26.3-py3-none-any.whl (129 kB)
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.1695184Z Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (794 kB)
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.1919656Z    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 794.2/794.2 kB 46.8 MB/s eta 0:00:00
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.2000156Z Installing collected packages: PyYAML, packaging
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.3178691Z 
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.3188445Z Successfully installed PyYAML-6.0.3 packaging-26.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8393440Z v3.22.1+gd6c1a0e
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8615748Z ##[group]Run out="$RUNNER_TEMP/out"
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8616085Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8616343Z ^[[36;1mmkdir -p "$out"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8616834Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8617505Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8618186Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8618503Z ^[[36;1mgit add -A^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8618764Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8619156Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8619498Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8619893Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8620540Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8621004Z ^[[36;1m  git add -A^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8621278Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8621585Z ^[[36;1mfi^[[0m
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8674544Z shell: /usr/bin/bash -e {0}
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8674801Z env:
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8674986Z   GROUP: digests
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8675178Z   REASON: 
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8675378Z   PROMETHEUS_VERSION: 3.16.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8675765Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8676192Z   KUBECONFORM_VERSION: v0.8.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8676587Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8677305Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8677860Z   CHROME_VERSION: 157.0.8103.50
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8678105Z   JSONSCHEMA_VERSION: 4.26.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8678356Z   HELM4_VERSION: v4.3.1
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8678575Z   SHELLCHECK_VERSION: v0.11.0
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8678804Z   PACKAGING_VERSION: 26.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8679021Z   PYYAML_VERSION: 6.0.3
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8681821Z   GH_TOKEN: ***
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8682115Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump digests UNKNOWN STEP 2026-11-12T07:29:03.8682710Z ##[endgroup]
Bump digests UNKNOWN STEP 2026-11-12T07:29:23.3913159Z hack/bump: scripts/images-lock update --only: images-lock: docker.io/library/ubuntu:24.04 is pinned to two digests (sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5, sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cec
Bump digests UNKNOWN STEP 2026-11-12T07:29:23.4112424Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=43175145174 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-12T11:26:26Z, updated 2026-11-12T11:26:26Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43153889180 at 2026-11-12T04:18:06Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


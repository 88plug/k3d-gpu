# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-12T08:47:11Z. 1 open.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-12T07:37:06Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/43175145174
Conclusion: **failure**, created 2026-11-12T07:25:22Z, commit 019e2fe28c17f564e74bcab82366cd0872714e47
Failed jobs: Bump digests
Hint: no known signature matched

<details><summary>Last 35 lines of the failed log</summary>

```text
Bump digests Bump ﻿2026-11-12T07:29:03.8615738Z ##[group]Run out="$RUNNER_TEMP/out"
Bump digests Bump 2026-11-12T07:29:03.8616082Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8616340Z ^[[36;1mmkdir -p "$out"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8616832Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump digests Bump 2026-11-12T07:29:03.8617502Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump digests Bump 2026-11-12T07:29:03.8618183Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8618501Z ^[[36;1mgit add -A^[[0m
Bump digests Bump 2026-11-12T07:29:03.8618762Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8619153Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump digests Bump 2026-11-12T07:29:03.8619496Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump digests Bump 2026-11-12T07:29:03.8619890Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump digests Bump 2026-11-12T07:29:03.8620502Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8621001Z ^[[36;1m  git add -A^[[0m
Bump digests Bump 2026-11-12T07:29:03.8621276Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump digests Bump 2026-11-12T07:29:03.8621583Z ^[[36;1mfi^[[0m
Bump digests Bump 2026-11-12T07:29:03.8674538Z shell: /usr/bin/bash -e {0}
Bump digests Bump 2026-11-12T07:29:03.8674799Z env:
Bump digests Bump 2026-11-12T07:29:03.8674984Z   GROUP: digests
Bump digests Bump 2026-11-12T07:29:03.8675176Z   REASON: 
Bump digests Bump 2026-11-12T07:29:03.8675375Z   PROMETHEUS_VERSION: 3.16.0
Bump digests Bump 2026-11-12T07:29:03.8675762Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump digests Bump 2026-11-12T07:29:03.8676190Z   KUBECONFORM_VERSION: v0.8.0
Bump digests Bump 2026-11-12T07:29:03.8676585Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump digests Bump 2026-11-12T07:29:03.8677302Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump digests Bump 2026-11-12T07:29:03.8677858Z   CHROME_VERSION: 157.0.8103.50
Bump digests Bump 2026-11-12T07:29:03.8678098Z   JSONSCHEMA_VERSION: 4.26.0
Bump digests Bump 2026-11-12T07:29:03.8678354Z   HELM4_VERSION: v4.3.1
Bump digests Bump 2026-11-12T07:29:03.8678573Z   SHELLCHECK_VERSION: v0.11.0
Bump digests Bump 2026-11-12T07:29:03.8678802Z   PACKAGING_VERSION: 26.3
Bump digests Bump 2026-11-12T07:29:03.8679019Z   PYYAML_VERSION: 6.0.3
Bump digests Bump 2026-11-12T07:29:03.8681818Z   GH_TOKEN: ***
Bump digests Bump 2026-11-12T07:29:03.8682112Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump digests Bump 2026-11-12T07:29:03.8682707Z ##[endgroup]
Bump digests Bump 2026-11-12T07:29:23.3913114Z hack/bump: scripts/images-lock update --only: images-lock: docker.io/library/ubuntu:24.04 is pinned to two digests (sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5, sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce11
Bump digests Bump 2026-11-12T07:29:23.4112398Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=43175145174 -->


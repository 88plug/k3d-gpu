# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-21T07:45:14Z. 3 open.

## `blind-spot:llama-cpp`: llama-cpp is 7d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-20T21:01:29Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (7d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-21T07:45:14Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44880152406
Conclusion: **failure**, created 2026-11-21T07:25:02Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-21T07:33:14.4981770Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-21T07:33:14.4982112Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4982385Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4982872Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4983525Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4984186Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4984508Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4984785Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4985161Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4985771Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4986150Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4986744Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4987246Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4987541Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.4987850Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-21T07:33:14.5011258Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-21T07:33:14.5011549Z env:
Bump llama-cpp Bump 2026-11-21T07:33:14.5011751Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-21T07:33:14.5011995Z   REASON: 
Bump llama-cpp Bump 2026-11-21T07:33:14.5012212Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-21T07:33:14.5012631Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-21T07:33:14.5013048Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-21T07:33:14.5013444Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-21T07:33:14.5014125Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-21T07:33:14.5014660Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-21T07:33:14.5014900Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-21T07:33:14.5015164Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-21T07:33:14.5015493Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-21T07:33:14.5015756Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-21T07:33:14.5015987Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-21T07:33:14.5018666Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-21T07:33:14.5018951Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-21T07:33:14.5019280Z ##[endgroup]
Bump llama-cpp Bump 2026-11-21T07:33:45.0307450Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-21T07:33:45.0314999Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-21T07:33:45.0316922Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-21T07:33:45.0317801Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-21T07:33:45.0318910Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-21T07:33:45.0320783Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-21T07:33:45.0322750Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-21T07:33:45.0326487Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-21T07:33:45.0327080Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-21T07:33:45.0327804Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-21T07:33:45.0328533Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-21T07:33:45.0329195Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-21T07:33:45.0329968Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-21T07:33:45.0330474Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-21T07:33:45.0331201Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-21T07:33:45.0332008Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-21T07:33:45.0332580Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-21T07:33:45.0333825Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-21T07:33:45.0335090Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-21T07:33:45.0337249Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-21T07:33:45.0523231Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44880152406 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


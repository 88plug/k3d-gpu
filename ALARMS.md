# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-22T07:24:30Z. 4 open.

## `blind-spot:llama-cpp`: llama-cpp is 8d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-21T19:47:16Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (8d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-22T07:24:30Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44996679166
Conclusion: **failure**, created 2026-11-22T07:06:05Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-22T07:15:09.0600888Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-22T07:15:09.0601179Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0601400Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0601797Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0602301Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0602868Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0603129Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0603361Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0603659Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0603919Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0604249Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0604743Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0605138Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0605368Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0605619Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-22T07:15:09.0637526Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-22T07:15:09.0637737Z env:
Bump llama-cpp Bump 2026-11-22T07:15:09.0637907Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-22T07:15:09.0638082Z   REASON: 
Bump llama-cpp Bump 2026-11-22T07:15:09.0638260Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-22T07:15:09.0638578Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-22T07:15:09.0638915Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-22T07:15:09.0639240Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-22T07:15:09.0639936Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-22T07:15:09.0640368Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-22T07:15:09.0640568Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-22T07:15:09.0640784Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-22T07:15:09.0640966Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-22T07:15:09.0641156Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-22T07:15:09.0641340Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-22T07:15:09.0643426Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-22T07:15:09.0643667Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-22T07:15:09.0643936Z ##[endgroup]
Bump llama-cpp Bump 2026-11-22T07:15:35.3015443Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-22T07:15:35.3020116Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-22T07:15:35.3022588Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-22T07:15:35.3022906Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-22T07:15:35.3023259Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-22T07:15:35.3023867Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-22T07:15:35.3024489Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-22T07:15:35.3024980Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-22T07:15:35.3025303Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-22T07:15:35.3025728Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-22T07:15:35.3026182Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-22T07:15:35.3026599Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-22T07:15:35.3027093Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-22T07:15:35.3027383Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-22T07:15:35.3027806Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-22T07:15:35.3028323Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-22T07:15:35.3028693Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-22T07:15:35.3029148Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-22T07:15:35.3029569Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-22T07:15:35.3030791Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-22T07:15:35.3227656Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44996679166 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-20T21:02:22Z. 3 open.

## `blind-spot:llama-cpp`: llama-cpp is 7d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-20T21:01:29Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (7d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-20T21:02:22Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44812273803
Conclusion: **failure**, created 2026-11-20T20:32:23Z, commit a0d20d3160e5cc4bb7397ea62edce563cd516d81
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-20T20:40:34.9393386Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-20T20:40:34.9394079Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9394309Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9394710Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9395233Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9395808Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9396069Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9396296Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9396609Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9396876Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9397191Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9397667Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9398068Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9398306Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9398563Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-20T20:40:34.9432353Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-20T20:40:34.9432589Z env:
Bump llama-cpp Bump 2026-11-20T20:40:34.9432876Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-20T20:40:34.9433068Z   REASON: 
Bump llama-cpp Bump 2026-11-20T20:40:34.9433245Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-20T20:40:34.9433712Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-20T20:40:34.9434117Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-20T20:40:34.9434446Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-20T20:40:34.9435004Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-20T20:40:34.9435434Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-20T20:40:34.9435642Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-20T20:40:34.9435862Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-20T20:40:34.9436055Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-20T20:40:34.9436255Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-20T20:40:34.9436450Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-20T20:40:34.9438670Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-20T20:40:34.9438922Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-20T20:40:34.9439309Z ##[endgroup]
Bump llama-cpp Bump 2026-11-20T20:41:04.4707318Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-20T20:41:04.4711653Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-20T20:41:04.4712014Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-20T20:41:04.4712210Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-20T20:41:04.4712470Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-20T20:41:04.4712848Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-20T20:41:04.4713230Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-20T20:41:04.4713807Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-20T20:41:04.4714027Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-20T20:41:04.4714319Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-20T20:41:04.4714621Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-20T20:41:04.4714909Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-20T20:41:04.4715226Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-20T20:41:04.4715416Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-20T20:41:04.4715695Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-20T20:41:04.4716019Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-20T20:41:04.4716262Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-20T20:41:04.4716588Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-20T20:41:04.4716998Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-20T20:41:04.4718636Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-20T20:41:04.5005350Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44812273803 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


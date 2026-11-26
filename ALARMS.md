# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-26T08:57:02Z. 6 open.

## `blind-spot:llama-cpp`: llama-cpp is 12d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-25T22:09:10Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (12d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-26T08:57:02Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/440 chore(examples): examples-crewai 1.15.45, examples-langgraph 1.2.24, examples-langchain-openai 1.8.5 and 8 more (`autobump/examples`, open since 2026-11-19T21:11:34Z): ci.yml at ac17675dcae9: pull_request action_required, workflow_dispatch in_progress
- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/450 chore(vllm-recipes): vllm-recipes 84b47c9a13ae489d60d46b631a5217180605f29c (`autobump/vllm-recipes`, open since 2026-11-19T21:18:51Z): ci.yml at 1e2ad9455b94: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-26T08:56:43Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/45784671498
Conclusion: **failure**, created 2026-11-26T08:36:58Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-26T08:45:42.6626536Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-26T08:45:42.6626900Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6627164Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6627650Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6628322Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6629030Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6629373Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6629646Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6630037Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6630378Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6630770Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6631393Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6631902Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6632203Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6632534Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-26T08:45:42.6665737Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-26T08:45:42.6666003Z env:
Bump llama-cpp Bump 2026-11-26T08:45:42.6666195Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-26T08:45:42.6666415Z   REASON: 
Bump llama-cpp Bump 2026-11-26T08:45:42.6666639Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-26T08:45:42.6667041Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-26T08:45:42.6667482Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-26T08:45:42.6667880Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-26T08:45:42.6668605Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-26T08:45:42.6669169Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-26T08:45:42.6669418Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-26T08:45:42.6669671Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-26T08:45:42.6669900Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-26T08:45:42.6670139Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-26T08:45:42.6670372Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-26T08:45:42.6673136Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-26T08:45:42.6673458Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-26T08:45:42.6673810Z ##[endgroup]
Bump llama-cpp Bump 2026-11-26T08:46:14.7805433Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-26T08:46:14.7812291Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-26T08:46:14.7812985Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-26T08:46:14.7813348Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-26T08:46:14.7813884Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-26T08:46:14.7814884Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-26T08:46:14.7815695Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-26T08:46:14.7816335Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-26T08:46:14.7816761Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-26T08:46:14.7817327Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-26T08:46:14.7817931Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-26T08:46:14.7818490Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-26T08:46:14.7819120Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-26T08:46:14.7819507Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-26T08:46:14.7820067Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-26T08:46:14.7820737Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-26T08:46:14.7821227Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-26T08:46:14.7821959Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-26T08:46:14.7822874Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-26T08:46:14.7824758Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-26T08:46:14.8036841Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=45784671498 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-26T08:56:24Z, updated 2026-11-26T08:56:24Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/45727877635 at 2026-11-26T00:19:11Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


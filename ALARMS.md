# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-24T10:27:51Z. 7 open.

## `blind-spot:llama-cpp`: llama-cpp is 11d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-24T10:26:41Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (11d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-24T05:00:20Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-24T10:27:51Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/45371511888
Conclusion: **failure**, created 2026-11-24T10:06:10Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-24T10:15:08.1671984Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-24T10:15:08.1672353Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1672616Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1673105Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1673757Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1674456Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1674795Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1675066Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1675520Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1675858Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1676245Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1676858Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1677581Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1677876Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1678195Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-24T10:15:08.1714276Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-24T10:15:08.1714543Z env:
Bump llama-cpp Bump 2026-11-24T10:15:08.1714734Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-24T10:15:08.1714955Z   REASON: 
Bump llama-cpp Bump 2026-11-24T10:15:08.1715153Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-24T10:15:08.1715553Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-24T10:15:08.1715988Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-24T10:15:08.1716399Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-24T10:15:08.1717129Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-24T10:15:08.1717895Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-24T10:15:08.1718147Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-24T10:15:08.1718395Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-24T10:15:08.1718617Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-24T10:15:08.1718855Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-24T10:15:08.1719084Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-24T10:15:08.1721606Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-24T10:15:08.1721904Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-24T10:15:08.1722326Z ##[endgroup]
Bump llama-cpp Bump 2026-11-24T10:15:36.7961919Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-24T10:15:36.7968651Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-24T10:15:36.7969284Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-24T10:15:36.7969672Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T10:15:36.7970170Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-24T10:15:36.7970991Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-24T10:15:36.7971780Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-24T10:15:36.7972416Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-24T10:15:36.7972836Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T10:15:36.7973398Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-24T10:15:36.7974014Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-24T10:15:36.7974558Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-24T10:15:36.7975167Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-24T10:15:36.7975529Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T10:15:36.7976073Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-24T10:15:36.7976768Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-24T10:15:36.7977217Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T10:15:36.7978033Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-24T10:15:36.7978881Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-24T10:15:36.7980631Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-24T10:15:36.8183524Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=45371511888 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-24T10:27:31Z, updated 2026-11-24T10:27:31Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/45300302495 at 2026-11-24T00:49:45Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-24T05:00:00Z, updated 2026-11-24T05:00:00Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/45260850554 at 2026-11-23T20:34:56Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


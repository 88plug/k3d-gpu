# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-25T01:06:12Z. 5 open.

## `blind-spot:llama-cpp`: llama-cpp is 11d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-24T10:26:41Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (11d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-24T22:58:35Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/438 chore(digests): move the digests the registries re-pointed (`autobump/digests`, open since 2026-11-19T21:10:29Z): ci.yml at 50130281b241: pull_request action_required, workflow_dispatch queued
- https://github.com/88plug/k3d-gpu/pull/440 chore(examples): examples-crewai 1.15.45, examples-langgraph 1.2.24, examples-langchain-openai 1.8.5 and 8 more (`autobump/examples`, open since 2026-11-19T21:11:34Z): ci.yml at 4820a4dc6834: pull_request action_required, workflow_dispatch queued
- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/450 chore(vllm-recipes): vllm-recipes 8a653cefb2aff0d256d52967645a0c0ae651a2f6 (`autobump/vllm-recipes`, open since 2026-11-19T21:18:51Z): ci.yml at 3811d520b112: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/454 chore(gguf-convert-pip): gguf-transformers 5.24.1 (`autobump/gguf-convert-pip`, open since 2026-11-21T19:44:25Z): ci.yml at faae78401754: pull_request action_required

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-24T22:58:17Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/45502550529
Conclusion: **failure**, created 2026-11-24T22:36:45Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 54 lines of the failed log</summary>

```text
Bump llama-cpp Bump ﻿2026-11-24T22:45:19.1938649Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp Bump 2026-11-24T22:45:19.1939062Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1939332Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1939828Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1940523Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1941280Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1941610Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1941880Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1942254Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1942587Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1942970Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1943584Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1944108Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1944387Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1944696Z ^[[36;1mfi^[[0m
Bump llama-cpp Bump 2026-11-24T22:45:19.1981742Z shell: /usr/bin/bash -e {0}
Bump llama-cpp Bump 2026-11-24T22:45:19.1982012Z env:
Bump llama-cpp Bump 2026-11-24T22:45:19.1982212Z   GROUP: llama-cpp
Bump llama-cpp Bump 2026-11-24T22:45:19.1982417Z   REASON: 
Bump llama-cpp Bump 2026-11-24T22:45:19.1982636Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp Bump 2026-11-24T22:45:19.1983042Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp Bump 2026-11-24T22:45:19.1983484Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp Bump 2026-11-24T22:45:19.1983915Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp Bump 2026-11-24T22:45:19.1984664Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp Bump 2026-11-24T22:45:19.1985232Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp Bump 2026-11-24T22:45:19.1985483Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp Bump 2026-11-24T22:45:19.1985735Z   HELM4_VERSION: v4.3.1
Bump llama-cpp Bump 2026-11-24T22:45:19.1985959Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp Bump 2026-11-24T22:45:19.1986205Z   PACKAGING_VERSION: 26.3
Bump llama-cpp Bump 2026-11-24T22:45:19.1986431Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp Bump 2026-11-24T22:45:19.1989420Z   GH_TOKEN: ***
Bump llama-cpp Bump 2026-11-24T22:45:19.1989729Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp Bump 2026-11-24T22:45:19.1990069Z ##[endgroup]
Bump llama-cpp Bump 2026-11-24T22:45:57.9285543Z Traceback (most recent call last):
Bump llama-cpp Bump 2026-11-24T22:45:57.9292348Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp Bump 2026-11-24T22:45:57.9292994Z     sys.exit(main(sys.argv))
Bump llama-cpp Bump 2026-11-24T22:45:57.9293365Z              ~~~~^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T22:45:57.9293875Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp Bump 2026-11-24T22:45:57.9294636Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp Bump 2026-11-24T22:45:57.9295410Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp Bump 2026-11-24T22:45:57.9296051Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp Bump 2026-11-24T22:45:57.9296471Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T22:45:57.9297033Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp Bump 2026-11-24T22:45:57.9297636Z     new = _digest_for(s, vals)
Bump llama-cpp Bump 2026-11-24T22:45:57.9298369Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp Bump 2026-11-24T22:45:57.9298999Z     return oci.digest(name, tag)
Bump llama-cpp Bump 2026-11-24T22:45:57.9299354Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T22:45:57.9299911Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp Bump 2026-11-24T22:45:57.9300596Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp Bump 2026-11-24T22:45:57.9301064Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp Bump 2026-11-24T22:45:57.9301719Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp Bump 2026-11-24T22:45:57.9302560Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp Bump 2026-11-24T22:45:57.9304288Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp Bump 2026-11-24T22:45:57.9514925Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=45502550529 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


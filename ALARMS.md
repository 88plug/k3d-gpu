# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-24T05:00:00Z. 6 open.

## `blind-spot:llama-cpp`: llama-cpp is 10d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-24T00:25:05Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (10d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-24T00:55:19Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/440 chore(examples): examples-crewai 1.15.44, examples-langgraph 1.2.24, examples-langchain-openai 1.8.5 and 7 more (`autobump/examples`, open since 2026-11-19T21:11:34Z): ci.yml at fe3fb11ae119: pull_request action_required, workflow_dispatch in_progress
- https://github.com/88plug/k3d-gpu/pull/441 chore(flake): nixpkgs 0dc498bfe49baeb18b0f9891e800b53bb2603193 (`autobump/flake`, open since 2026-11-19T21:12:25Z): ci.yml at 36c31d666300: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/443 chore(host-intel): level-zero 1.36.0, npu-driver 1.39.1, linux-tools 7.3.6 (`autobump/host-intel`, open since 2026-11-19T21:13:04Z): ci.yml at 675a04eff4a3: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/446 chore(packaging-images): image:packaging-images:ghcr.io/homebrew/brew:7.0.13 7.0.16, image:packaging-images:nixos/nix:2.35.6 2.36.0 (`autobump/packaging-images`, open since 2026-11-19T21:16:13Z): ci.yml at dc5ee56a80c0: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/448 chore(tools): ci-chrome 158.0.8127.88, ci-helm4 v4.3.2, ci-packaging 26.4 and 1 more (`autobump/tools`, open since 2026-11-19T21:17:32Z): ci.yml at b1129fcef919: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/450 chore(vllm-recipes): vllm-recipes 48760b85cabf1e651e9bf54a6208a368d4175ea1 (`autobump/vllm-recipes`, open since 2026-11-19T21:18:51Z): ci.yml at 43fff120f5be: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-24T04:59:54Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/45294042161
Conclusion: **failure**, created 2026-11-24T00:03:52Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:40.6329655Z Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (794 kB)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:40.6583635Z    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 794.2/794.2 kB 40.4 MB/s eta 0:00:00
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:40.6647774Z Installing collected packages: PyYAML, packaging
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:40.7603565Z 
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:40.7612416Z Successfully installed PyYAML-6.0.3 packaging-26.3
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2464738Z v3.22.1+gd6c1a0e
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2644355Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2644628Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2644843Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2645236Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2645949Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2646511Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2646770Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2646994Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2647309Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2647574Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2647884Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2648393Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2648748Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2648973Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2649218Z ^[[36;1mfi^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2680909Z shell: /usr/bin/bash -e {0}
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2681119Z env:
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2681282Z   GROUP: llama-cpp
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2681460Z   REASON: 
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2681631Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2681950Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2682291Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2682626Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2683172Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2683594Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2683801Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2684000Z   HELM4_VERSION: v4.3.1
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2684187Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2684387Z   PACKAGING_VERSION: 26.3
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2684574Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2686829Z   GH_TOKEN: ***
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2687073Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:12:41.2687348Z ##[endgroup]
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0266082Z Traceback (most recent call last):
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0270634Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0271096Z     sys.exit(main(sys.argv))
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0271369Z              ~~~~^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0271681Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0272172Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0272757Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0273177Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0273472Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0273762Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0274061Z     new = _digest_for(s, vals)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0274499Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0274969Z     return oci.digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0275265Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0275839Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0276390Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0276714Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0277158Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0278254Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0279059Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp UNKNOWN STEP 2026-11-24T00:13:12.0475359Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=45294042161 -->

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


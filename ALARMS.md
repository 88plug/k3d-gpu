# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-22T23:51:23Z. 5 open.

## `blind-spot:llama-cpp`: llama-cpp is 9d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-22T20:08:20Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (9d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-22T21:49:57Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-22T21:49:40Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/45063034585
Conclusion: **failure**, created 2026-11-22T19:50:37Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:21.4736496Z Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (794 kB)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:21.5096426Z    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 794.2/794.2 kB 27.5 MB/s eta 0:00:00
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:21.5179833Z Installing collected packages: PyYAML, packaging
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:21.6377691Z 
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:21.6388974Z Successfully installed PyYAML-6.0.3 packaging-26.3
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.1832999Z v3.22.1+gd6c1a0e
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2073688Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2074036Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2074296Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2074789Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2075805Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2076504Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2076832Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2077100Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2077472Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2077795Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2078182Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2078832Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2079300Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2079578Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2079893Z ^[[36;1mfi^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2116463Z shell: /usr/bin/bash -e {0}
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2116721Z env:
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2116917Z   GROUP: llama-cpp
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2117136Z   REASON: 
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2117342Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2117754Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2118200Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2118609Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2119333Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2119911Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2120166Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2120416Z   HELM4_VERSION: v4.3.1
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2120641Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2120880Z   PACKAGING_VERSION: 26.3
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2121113Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2123949Z   GH_TOKEN: ***
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2124248Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:22.2124585Z ##[endgroup]
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9788321Z Traceback (most recent call last):
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9794586Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9795392Z     sys.exit(main(sys.argv))
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9795862Z              ~~~~^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9796557Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9797578Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9798505Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9799075Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9799540Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9800141Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9800602Z     new = _digest_for(s, vals)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9801022Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9801497Z     return oci.digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9801780Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9802212Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9802749Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9803139Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9803656Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9804733Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:47.9805914Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp UNKNOWN STEP 2026-11-22T19:59:48.0027522Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=45063034585 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


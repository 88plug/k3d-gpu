# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-20T02:38:33Z. 4 open.

## `red:canary.yml`: scheduled run failure

Opened 2026-11-19T20:59:37Z, updated 2026-11-19T20:59:37Z.

The latest scheduled run of `canary.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44519729166
Conclusion: **failure**, created 2026-11-19T11:59:37Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Plan
Failed log unavailable: gh run view 44519729166 -R 88plug/k3d-gpu --log-failed: exit 1: log not found: 135544977538

Re-dispatched `canary.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44519729166 -->

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-20T02:38:33Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44612403133
Conclusion: **failure**, created 2026-11-19T20:48:28Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.3637066Z Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (794 kB)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.3849707Z    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 794.2/794.2 kB 48.8 MB/s eta 0:00:00
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.3898848Z Installing collected packages: PyYAML, packaging
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.4646039Z 
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.4651386Z Successfully installed PyYAML-6.0.3 packaging-26.3
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9105082Z v3.22.1+gd6c1a0e
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9291830Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9292044Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9292192Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9292465Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9292814Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9293208Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9293391Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9293544Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9293748Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9293928Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9294137Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9294493Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9294737Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9294891Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9295065Z ^[[36;1mfi^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337236Z shell: /usr/bin/bash -e {0}
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337375Z env:
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337489Z   GROUP: llama-cpp
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337612Z   REASON: 
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337729Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9337956Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9338184Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9338399Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9338767Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339068Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339209Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339347Z   HELM4_VERSION: v4.3.1
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339472Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339608Z   PACKAGING_VERSION: 26.3
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9339737Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9341446Z   GH_TOKEN: ***
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9341607Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:01.9341791Z ##[endgroup]
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0396328Z Traceback (most recent call last):
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0399971Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0400318Z     sys.exit(main(sys.argv))
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0400520Z              ~~~~^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0400787Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0401357Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0401791Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0402119Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0402337Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0402633Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0402948Z     new = _digest_for(s, vals)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0403240Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0403592Z     return oci.digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0403796Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0404095Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0404438Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0404694Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0405054Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0405860Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0406263Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp UNKNOWN STEP 2026-11-19T20:57:31.0578167Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44612403133 -->

## `stale:canary.yml`: no successful scheduled run within 30 h

Opened 2026-11-19T20:59:30Z, updated 2026-11-19T20:59:30Z.

`canary.yml` (cron `29 3 * * *`, every 24 h) has no successful scheduled run within 30 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43211086595 at 2026-11-12T11:39:55Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


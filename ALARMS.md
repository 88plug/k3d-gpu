# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-20T21:02:07Z. 3 open.

## `blind-spot:llama-cpp`: llama-cpp is 7d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-20T21:01:29Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (7d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-20T10:32:20Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44695352102
Conclusion: **failure**, created 2026-11-20T07:29:37Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Bump llama-cpp
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:14.9070538Z Downloading pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (794 kB)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:14.9213548Z    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 794.2/794.2 kB 69.8 MB/s eta 0:00:00
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:14.9296650Z Installing collected packages: PyYAML, packaging
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.0477273Z 
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.0487502Z Successfully installed PyYAML-6.0.3 packaging-26.3
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5448638Z v3.22.1+gd6c1a0e
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5667764Z ##[group]Run out="$RUNNER_TEMP/out"
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5668121Z ^[[36;1mout="$RUNNER_TEMP/out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5668386Z ^[[36;1mmkdir -p "$out"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5668896Z ^[[36;1mcase "$REASON" in ''|known-issue:*) ;; *) echo "::error::reason must be empty or known-issue:<id>"; exit 2;; esac^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5669578Z ^[[36;1m"$RUNNER_TEMP/venv/bin/python" hack/bump "$GROUP" ${REASON:+--reason "$REASON"} \^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5670269Z ^[[36;1m  --summary "$out/summary.json" --body "$out/body.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5670603Z ^[[36;1mgit add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5670887Z ^[[36;1mgit diff --cached --binary > "$out/bump.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5671271Z ^[[36;1mki=$(jq -r '.followup // empty' "$out/summary.json")^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5671599Z ^[[36;1mif [ -n "$ki" ]; then^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5672250Z ^[[36;1m  git -c user.name=freshness -c user.email=freshness@localhost commit -qm bump^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5672912Z ^[[36;1m  "$RUNNER_TEMP/venv/bin/python" hack/bump --remove-workaround "$ki" --body "$out/followup.md"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5673379Z ^[[36;1m  git add -A^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5673656Z ^[[36;1m  git diff --cached --binary > "$out/followup.patch"^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5673976Z ^[[36;1mfi^[[0m
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5709021Z shell: /usr/bin/bash -e {0}
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5709272Z env:
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5709463Z   GROUP: llama-cpp
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5709675Z   REASON: 
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5709873Z   PROMETHEUS_VERSION: 3.16.0
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5710277Z   PROMETHEUS_SHA256: 68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5710716Z   KUBECONFORM_VERSION: v0.8.0
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5711138Z   KUBECONFORM_SHA256: 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5712141Z   KUBECONFORM_IMAGE: ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5712728Z   CHROME_VERSION: 157.0.8103.78
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5712976Z   JSONSCHEMA_VERSION: 4.26.0
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5713219Z   HELM4_VERSION: v4.3.1
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5713438Z   SHELLCHECK_VERSION: v0.11.0
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5713682Z   PACKAGING_VERSION: 26.3
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5713905Z   PYYAML_VERSION: 6.0.3
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5716684Z   GH_TOKEN: ***
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5716978Z   VLLM_RECIPES_PYTHON: /home/runner/work/_temp/venv/bin/python
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:15.5717309Z ##[endgroup]
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8656224Z Traceback (most recent call last):
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8662702Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 785, in <module>
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8663233Z     sys.exit(main(sys.argv))
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8663731Z              ~~~~^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8664461Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 757, in main
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8665428Z     out = bump(root, a.group, a.reason, a.dry_run, resolved, not a.no_tests, a.fixed_in)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8666343Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 659, in bump
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8667016Z     edits += edits_for(p, spans, cur, r)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8667470Z              ~~~~~~~~~^^^^^^^^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8668103Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 140, in edits_for
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8668761Z     new = _digest_for(s, vals)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8669365Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/bump", line 128, in _digest_for
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8669914Z     return oci.digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8670165Z            ~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8670547Z   File "/home/runner/work/k3d-gpu/k3d-gpu/hack/pinlib/oci.py", line 103, in digest
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8671022Z     return images_lock().registry_digest(name, tag)
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8671344Z            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8672061Z   File "/home/runner/work/k3d-gpu/k3d-gpu/scripts/images-lock", line 605, in registry_digest
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8673087Z     raise ValueError("%s:%s: HTTP %d from %s" % (name, tag, e.code, url)) from None
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8673918Z ValueError: ghcr.io/ggml-org/llama.cpp:server-intel-b12360: HTTP 404 from https://ghcr.io/v2/ggml-org/llama.cpp/manifests/server-intel-b12360
Bump llama-cpp UNKNOWN STEP 2026-11-20T07:38:42.8867311Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44695352102 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


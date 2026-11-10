# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-10T19:31:28Z. 3 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-10T11:43:16Z, updated 2026-11-10T19:07:21Z, acknowledged.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/350 chore(hf-console-fallback): hf-gemma4-26b-int4-ov 7b3cbc98e24a9946e561f82c752ae26cd53fc660, hf-qwen3.8-27b 37561816e66b5b9153b1b64c10968c18c1e6f4a0, hf-qwen3.8-27b-int4-ov d15f4d91eccd11dd44f615ffdb539c61bf1f1e6e (`autobump/hf-console-fallback`, open since 2026-11-07T11:16:40Z): ci.yml at 278d1ea3890c: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-main`: ci.yml on main failure

Opened 2026-11-10T19:24:39Z, updated 2026-11-10T19:24:39Z, acknowledged.

The latest completed ci.yml run on main failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42881816630
Conclusion: **failure**, created 2026-11-10T19:05:49Z, commit 20af167ba145c80076af42f820cc7d446ec94fbd
Failed jobs: Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share)
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:09.3306223Z test_auth_failure_shows_gh_and_is_not_sent (__main__.GhStates.test_auth_failure_shows_gh_and_is_not_sent) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:10.3159793Z test_open_pull_request_is_reported_not_duplicated (__main__.GhStates.test_open_pull_request_is_reported_not_duplicated) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:10.3374614Z test_waits_for_a_new_fork (__main__.GhStates.test_waits_for_a_new_fork) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:10.7341748Z test_n100_alderlake_n (__main__.HostShapes.test_n100_alderlake_n) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:11.0747338Z test_no_intel_gpu (__main__.HostShapes.test_no_intel_gpu) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:11.8798535Z test_wsl2_no_dmi_drm_powercap (__main__.HostShapes.test_wsl2_no_dmi_drm_powercap) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:12.8253859Z test_ctrl_c_at_the_prompt_is_clean (__main__.InterruptedSubmit.test_ctrl_c_at_the_prompt_is_clean) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:13.8045664Z test_rerun_after_branch_exists_opens_the_pr (__main__.InterruptedSubmit.test_rerun_after_branch_exists_opens_the_pr) ... ok
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.8282176Z test_dry_run_without_docker_kubectl_k3d (__main__.NoDocker.test_dry_run_without_docker_kubectl_k3d) ... FAIL
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9134090Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9134704Z ======================================================================
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9135338Z FAIL: test_generic_hostnames (__main__.GenericNames.test_generic_hostnames)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9135866Z ----------------------------------------------------------------------
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9136233Z Traceback (most recent call last):
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9136624Z   File "/home/runner/work/k3d-gpu/k3d-gpu/oracle/test_adversarial.py", line 92, in setUp
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9137028Z     self.assertEqual(T.check(self.b).verdict, "accept")
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9137326Z     ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9137589Z AssertionError: 'quarantine' != 'accept'
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9137830Z - quarantine
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9138034Z + accept
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9138129Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9138134Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9138243Z ======================================================================
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9138629Z FAIL: test_login_core_on_a_core_cpu (__main__.GenericNames.test_login_core_on_a_core_cpu)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9139049Z ----------------------------------------------------------------------
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9139524Z Traceback (most recent call last):
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9139879Z   File "/home/runner/work/k3d-gpu/k3d-gpu/oracle/test_adversarial.py", line 92, in setUp
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9140289Z     self.assertEqual(T.check(self.b).verdict, "accept")
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9140575Z     ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9140841Z AssertionError: 'quarantine' != 'accept'
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141074Z - quarantine
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141241Z + accept
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141335Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141339Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141434Z ======================================================================
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9141851Z FAIL: test_real_identifier_still_blocks (__main__.GenericNames.test_real_identifier_still_blocks)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9142298Z ----------------------------------------------------------------------
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9142610Z Traceback (most recent call last):
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9142965Z   File "/home/runner/work/k3d-gpu/k3d-gpu/oracle/test_adversarial.py", line 92, in setUp
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9143365Z     self.assertEqual(T.check(self.b).verdict, "accept")
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144098Z     ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144355Z AssertionError: 'quarantine' != 'accept'
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144584Z - quarantine
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144756Z + accept
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144851Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144854Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9144953Z ======================================================================
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9145380Z FAIL: test_dry_run_without_docker_kubectl_k3d (__main__.NoDocker.test_dry_run_without_docker_kubectl_k3d)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9145845Z ----------------------------------------------------------------------
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9146141Z Traceback (most recent call last):
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9146596Z   File "/home/runner/work/k3d-gpu/k3d-gpu/oracle/test_adversarial.py", line 143, in test_dry_run_without_docker_kubectl_k3d
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9147239Z     self.assertIn("validator: accept", r.stdout)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9147500Z     ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9182909Z AssertionError: 'validator: accept' not found in '# k3d-gpu share: bundle built from /tmp/oracle-test-w4qez4cu/run/capacity.json and this host\'s report\
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9216679Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9216831Z ----------------------------------------------------------------------
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9217119Z Ran 12 tests in 13.939s
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9217243Z 
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9217326Z FAILED (failures=4)
Capacity oracle (schema, privacy, validator, fuzz, matrix, leaderboard, share) Run python3 oracle/test_adversarial.py 2026-11-10T19:08:14.9433037Z ##[error]Process completed with exit code 1.
```
</details>

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-10T19:31:28Z, updated 2026-11-10T19:31:28Z.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42775671332 at 2026-11-10T07:29:48Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


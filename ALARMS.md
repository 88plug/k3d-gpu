# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-08T13:57:02Z. 3 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-08T07:21:21Z, updated 2026-11-08T10:45:39Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42458223055
Conclusion: **failure**, created 2026-11-08T07:18:08Z, commit 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Failed jobs: Merge one auto-eligible bot PR, batch the release
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6458665Z  * [new tag]           aur-v0.1.91                 -> aur-v0.1.91
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6460168Z  * [new tag]           aur-v0.1.92                 -> aur-v0.1.92
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6535492Z  * [new tag]           aur-v0.1.93                 -> aur-v0.1.93
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6537575Z  * [new tag]           aur-v0.1.94                 -> aur-v0.1.94
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6539403Z  * [new tag]           aur-v0.1.95                 -> aur-v0.1.95
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6541520Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.19.2 -> v1.34.1-k3s1-amd64-ndpv0.19.2
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6544162Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.19.3 -> v1.34.1-k3s1-amd64-ndpv0.19.3
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6546711Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.20.0 -> v1.34.1-k3s1-amd64-ndpv0.20.0
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6549241Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.20.1 -> v1.34.1-k3s1-amd64-ndpv0.20.1
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6551626Z  * [new tag]           v1.37.1-k3s1-ndpv0.20.1     -> v1.37.1-k3s1-ndpv0.20.1
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6555132Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6556468Z ##[group]Determining the checkout info
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6557827Z [command]/usr/bin/git branch --list --remote origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6559268Z   origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6561518Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6562589Z [command]/usr/bin/git sparse-checkout disable
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6565321Z [command]/usr/bin/git config --local --unset-all extensions.worktreeConfig
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6568154Z ##[group]Checking out the ref
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6569680Z [command]/usr/bin/git checkout --progress --force -B main refs/remotes/origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2812496Z Switched to a new branch 'main'
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2816049Z branch 'main' set up to track 'origin/main'.
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2839269Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2887597Z [command]/usr/bin/git log -1 --format=%H
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2921570Z 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2929404Z ##[group]Removing auth
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2929749Z Removing SSH command configuration
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2932624Z [command]/usr/bin/git config --local --name-only --get-regexp core\.sshCommand
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2965354Z [command]/usr/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'core\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :"
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3225966Z Removing HTTP extra header
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3228941Z [command]/usr/bin/git config --local --name-only --get-regexp http\.https\:\/\/github\.com\/\.extraheader
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3260838Z [command]/usr/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'http\.https\:\/\/github\.com\/\.extraheader' && git config --local --unset-all 'http.https://github.com/
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3518360Z Removing includeIf entries pointing to credentials config files
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3521037Z [command]/usr/bin/git config --local --name-only --get-regexp ^includeIf\.gitdir:
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3547801Z includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3548546Z includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3548939Z includeif.gitdir:/github/workspace/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3549240Z includeif.gitdir:/github/workspace/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3555417Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3581487Z /home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3588842Z [command]/usr/bin/git config --local --unset includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path \/home\/runner\/work\/_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3621752Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3645529Z /home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3652435Z [command]/usr/bin/git config --local --unset includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path \/home\/runner\/work\/_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.c
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3683975Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3710516Z /github/runner_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3716784Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git.path \/github\/runner_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3750399Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3772980Z /github/runner_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3778579Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git/worktrees/*.path \/github\/runner_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3811674Z [command]/usr/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4074040Z Removing credentials config '/home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config'
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4078161Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4280987Z ##[group]Run hack/bot-merge
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4281250Z ^[[36;1mhack/bot-merge^[[0m
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4329342Z shell: /usr/bin/bash -e {0}
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4329558Z env:
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4331631Z   GH_TOKEN: ***
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4331810Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.6937103Z gh pr merge lacks --match-head-commit; refusing to merge unpinned
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.6966885Z ##[error]Process completed with exit code 2.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42458223055 -->

## `red:canary.yml`: scheduled run failure

Opened 2026-11-08T10:45:49Z, updated 2026-11-08T13:53:30Z.

The latest scheduled run of `canary.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42472404707
Conclusion: **failure**, created 2026-11-08T10:40:19Z, commit 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Failed jobs: Node image, Report
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Report UNKNOWN STEP 2026-11-08T10:45:14.0237800Z ^[[36;1m  --out canary-result.json --summary "$GITHUB_STEP_SUMMARY" --github-output "$GITHUB_OUTPUT" || rc=$?^[[0m
Report UNKNOWN STEP 2026-11-08T10:45:14.0238302Z ^[[36;1mecho "rc=$rc" >> "$GITHUB_OUTPUT"^[[0m
Report UNKNOWN STEP 2026-11-08T10:45:14.0288958Z shell: /usr/bin/bash -e {0}
Report UNKNOWN STEP 2026-11-08T10:45:14.0289297Z env:
Report UNKNOWN STEP 2026-11-08T10:45:14.0296752Z   SELECTED: [{"id":"vllm-cpu","kind":"runtime","informational":false,"pinned_ref":"vllm/vllm-openai-cpu:v0.32.0@sha256:ffaba3989b9b96d95a576143c872a4110ce9800f5199b6a36eb27bdff1cbbe05"},{"id":"ovms-cpu","kind":"runtime","informational":false,"pinned_r
Report UNKNOWN STEP 2026-11-08T10:45:14.0304635Z   SHA: 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Report UNKNOWN STEP 2026-11-08T10:45:14.0304973Z   REF: main
Report UNKNOWN STEP 2026-11-08T10:45:14.0305233Z ##[endgroup]
Report UNKNOWN STEP 2026-11-08T10:45:14.0869138Z ## Canary red: node
Report UNKNOWN STEP 2026-11-08T10:45:14.0869434Z 
Report UNKNOWN STEP 2026-11-08T10:45:14.0869781Z | engine | kind | verdict | hard | exact | pinned | detail |
Report UNKNOWN STEP 2026-11-08T10:45:14.0870391Z |---|---|---|---|---|---|---|
Report UNKNOWN STEP 2026-11-08T10:45:14.0871524Z | vllm-cpu | runtime | pass | True | True | `vllm/vllm-openai-cpu:v0.32.0` | surface: 29 names checked (help); text ' Paris. The capital of France is also'; ready after 102.8s, 8 stream chunks, version 0.32.0 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0873320Z | ovms-cpu | runtime | pass | True | True | `openvino/model_server:2026.4.1-gpu` | text ' Paris. The capital of Italy is Rome'; ready after 4.1s, 10 stream chunks, version 2026.4.1 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0874415Z | llama-cpu | runtime | pass | True | True | `ghcr.io/ggml-org/llama.cpp:server-b11960` | surface: 29 names checked (help); text ' Paris. The capital of Italy is Rome'; ready after 2.0s, 9 stream chunks, version 11960 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0875563Z | eat-vllm-cpu (informational) | runtime | pass | True | True | `vllm/vllm-openai-cpu:v0.28.0` | surface: 7 names checked (help); text ' Paris. The capital of France is also'; ready after 108.9s, 8 stream chunks, version 0.28.0 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0876450Z | node | node | fail | False | False | `rancher/k3s:v1.37.1-k3s1` | k3d v5.9.0 did not download |
Report UNKNOWN STEP 2026-11-08T10:45:14.0877694Z | vllm-xpu | surface | pass | True | True | `vllm/vllm-openai-xpu:v0.32.0` | surface: 30 names checked (static), version 0.32.0+xpu; vllm serve --help=all did not run here (vllm serve --help=all exited 1: , kwargs), self_instance=s) File "/opt/venv/li
Report UNKNOWN STEP 2026-11-08T10:45:14.0879579Z | llm-scaler | surface | pass | True | True | `intel/llm-scaler-vllm:1.5` | surface: 30 names checked (static), version 0.26.1.dev0+g568afb3a1.d20260907.xpu; vllm serve --help=all did not run here (vllm serve --help=all exited 1: , kwargs), self_insta
Report UNKNOWN STEP 2026-11-08T10:45:14.0881121Z | gaudi-vllm | surface | pass | True | True | `vault.habana.ai/gaudi-docker/1.24.2/ubuntu24.04/habanalabs/vllm-0.30.0-ptupstream-2.13.0:1.24.2-481` | surface: 21 names checked (help), version 0.30.0+empty |
Report UNKNOWN STEP 2026-11-08T10:45:14.0882279Z | llama-sycl | surface | pass | True | True | `ghcr.io/ggml-org/llama.cpp:server-intel-b11960` | surface: 29 names checked (help), version 11960 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0883088Z | llama-vulkan | surface | pass | True | True | `ghcr.io/ggml-org/llama.cpp:server-vulkan-b11960` | surface: 29 names checked (help), version 11960 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0883844Z | llama-full | surface | pass | True | True | `ghcr.io/ggml-org/llama.cpp:full-b11960` | surface: 4 names checked (help), version 11960 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0884709Z | dlstreamer | surface | pass | True | True | `intel/dlstreamer:2026.2.0-ubuntu24` | surface: 22 names checked (gst-inspect), version 1.28.2; vapostproc registers only on a device: not checked |
Report UNKNOWN STEP 2026-11-08T10:45:14.0885667Z | sglang-xeon | surface | pass | True | True | `lmsysorg/sglang:v0.5.23-xeon` | surface: 8 names checked (help), version 0.5.23; --tp is argparse's abbreviation of --tp-size |
Report UNKNOWN STEP 2026-11-08T10:45:14.0886652Z | npu-umd | surface | pass | True | True | `openvino/model_server:2026.4.1-gpu` | surface: 34 names checked (help), version 2026.4.1 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0887348Z | ovms | surface | pass | True | True | `openvino/model_server:2026.4.1-gpu` | surface: 34 names checked (help), version 2026.4.1 |
Report UNKNOWN STEP 2026-11-08T10:45:14.0887730Z 
Report UNKNOWN STEP 2026-11-08T10:45:14.0888037Z Green means verdict pass with hard true. exact false (a golden mismatch) stays green but is not auto-merge evidence.
Report UNKNOWN STEP 2026-11-08T10:45:14.0888417Z 
Report UNKNOWN STEP 2026-11-08T10:45:14.1214084Z ##[group]Run actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9
Report UNKNOWN STEP 2026-11-08T10:45:14.1214511Z with:
Report UNKNOWN STEP 2026-11-08T10:45:14.1214763Z   name: canary-result
Report UNKNOWN STEP 2026-11-08T10:45:14.1215042Z   path: canary-result.json
Report UNKNOWN STEP 2026-11-08T10:45:14.1215341Z   retention-days: 30
Report UNKNOWN STEP 2026-11-08T10:45:14.1233539Z   if-no-files-found: warn
Report UNKNOWN STEP 2026-11-08T10:45:14.1233894Z   compression-level: 6
Report UNKNOWN STEP 2026-11-08T10:45:14.1234225Z   overwrite: false
Report UNKNOWN STEP 2026-11-08T10:45:14.1234503Z   include-hidden-files: false
Report UNKNOWN STEP 2026-11-08T10:45:14.1234796Z   archive: true
Report UNKNOWN STEP 2026-11-08T10:45:14.1235059Z ##[endgroup]
Report UNKNOWN STEP 2026-11-08T10:45:14.2394713Z With the provided path, there will be 1 file uploaded
Report UNKNOWN STEP 2026-11-08T10:45:14.2399293Z Artifact name is valid!
Report UNKNOWN STEP 2026-11-08T10:45:14.2407283Z Root directory input is valid!
Report UNKNOWN STEP 2026-11-08T10:45:14.7323464Z Uploading artifact: canary-result.zip
Report UNKNOWN STEP 2026-11-08T10:45:14.7353708Z Beginning upload of artifact content to blob storage
Report UNKNOWN STEP 2026-11-08T10:45:15.0880575Z Uploaded bytes 1882
Report UNKNOWN STEP 2026-11-08T10:45:15.1606297Z Finished uploading artifact content to blob storage!
Report UNKNOWN STEP 2026-11-08T10:45:15.1607153Z SHA256 digest of uploaded artifact is c01c37673f59f55238a343ede93a476034cf3b535f6a5b4d83e0ea2388155a6c
Report UNKNOWN STEP 2026-11-08T10:45:15.1607760Z Finalizing artifact upload
Report UNKNOWN STEP 2026-11-08T10:45:15.7639176Z Artifact canary-result successfully finalized. Artifact ID 13614186022
Report UNKNOWN STEP 2026-11-08T10:45:15.7640058Z Artifact canary-result has been successfully uploaded! Final size is 1882 bytes. Artifact ID is 13614186022
Report UNKNOWN STEP 2026-11-08T10:45:15.7643271Z Artifact download URL: https://github.com/88plug/k3d-gpu/actions/runs/42472404707/artifacts/13614186022
Report UNKNOWN STEP 2026-11-08T10:45:15.7777710Z ##[group]Run exit "${RC:-1}"
Report UNKNOWN STEP 2026-11-08T10:45:15.7778051Z ^[[36;1mexit "${RC:-1}"^[[0m
Report UNKNOWN STEP 2026-11-08T10:45:15.7822237Z shell: /usr/bin/bash -e {0}
Report UNKNOWN STEP 2026-11-08T10:45:15.7822557Z env:
Report UNKNOWN STEP 2026-11-08T10:45:15.7822803Z   RC: 1
Report UNKNOWN STEP 2026-11-08T10:45:15.7823055Z ##[endgroup]
Report UNKNOWN STEP 2026-11-08T10:45:15.7890099Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `canary.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42472404707 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-08T10:45:31Z, updated 2026-11-08T10:45:31Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42430348862 at 2026-11-08T00:34:19Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-08T15:16:27Z. 1 open.

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


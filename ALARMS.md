# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-07T00:38:46Z. 7 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-07T00:21:40Z, updated 2026-11-07T00:22:33Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/298 chore(gguf-convert-pip): gguf-transformers 5.22.2, gguf-huggingface-hub 2.4.0 (`autobump/gguf-convert-pip`, open since 2026-11-01T21:45:54Z): ci.yml at 11179747fb26: pull_request action_required, workflow_dispatch in_progress
- https://github.com/88plug/k3d-gpu/pull/299 chore(hf-console-fallback): hf-gemma4-26b-int4-ov 7b3cbc98e24a9946e561f82c752ae26cd53fc660, hf-qwen3.8-27b 8c0b3d30f3ae96f5c7f774de704bf0ada83e422a, hf-qwen3.8-27b-int4-ov d15f4d91eccd11dd44f615ffdb539c61bf1f1e6e (`autobump/hf-console-fallback`, open since 2026-11-01T21:46:07Z): ci.yml at 0c28470384eb: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/301 chore(images): image:images:ollama/ollama:0.40.13 0.40.15, image:images:busybox:1.37 1.38 (`autobump/images`, open since 2026-11-01T21:47:03Z): ci.yml at 15d9fe4aeead: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/302 chore(opt-in-manifests): open-webui v0.13.0, agent-sandbox v1.0.9 (`autobump/opt-in-manifests`, open since 2026-11-01T21:47:40Z): ci.yml at e0b3e74aea2d: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/306 chore(vllm-xpu): vllm-xpu v0.33.0 (`autobump/vllm-xpu`, open since 2026-11-02T06:40:43Z): ci.yml at 7f9ea5e7e52f: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/307 chore(packaging-images): packaging-fedora 45, image:packaging-images:debian:trixie-slim trixie-slim, image:packaging-images:ghcr.io/homebrew/brew:7.0.11 7.0.12 (`autobump/packaging-images`, open since 2026-11-02T22:02:55Z): ci.yml at 0de0bffb0042: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-07T00:20:52Z, updated 2026-11-07T00:38:46Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42309600332
Conclusion: **failure**, created 2026-11-06T23:06:34Z, commit 8e10b0943d8f4c841de688ecfe977151f3c7ab2c
Failed jobs: Merge one auto-eligible bot PR, batch the release
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2490855Z /home/runner/work/_temp/git-credentials-f95c6043-eb13-4b45-ad1b-5438d1432ab3.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2500149Z [command]/usr/bin/git config --local --unset includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path \/home\/runner\/work\/_temp\/git\-credentials\-f95c6043\-eb13\-4b45\-ad1b\-5438d1432ab3\.c
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2538437Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2569316Z /github/runner_temp/git-credentials-f95c6043-eb13-4b45-ad1b-5438d1432ab3.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2577734Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git.path \/github\/runner_temp\/git\-credentials\-f95c6043\-eb13\-4b45\-ad1b\-5438d1432ab3\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2615288Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2642288Z /github/runner_temp/git-credentials-f95c6043-eb13-4b45-ad1b-5438d1432ab3.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2650843Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git/worktrees/*.path \/github\/runner_temp\/git\-credentials\-f95c6043\-eb13\-4b45\-ad1b\-5438d1432ab3\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2690377Z [command]/usr/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.2985942Z Removing credentials config '/home/runner/work/_temp/git-credentials-f95c6043-eb13-4b45-ad1b-5438d1432ab3.config'
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3053017Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3280325Z ##[group]Run hack/bot-merge
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3280667Z ^[[36;1mhack/bot-merge^[[0m
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3333812Z shell: /usr/bin/bash -e {0}
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3334087Z env:
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3336651Z   GH_TOKEN: ***
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:01.3336867Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:03.6701220Z the '88plug/k3d-gpu' repository has disabled issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:03.6742018Z circuit breaker open: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:27:03.6862651Z bot merges today (2026-11-06 UTC): 0 of 6
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:26.2376908Z PR#311 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:26.2378250Z PR#311 blocked: needs review: policy: eat-stock: eat release-1.1.0 -> release-2.0.0 (major) (always review); an approval of 4b7c8eb84e7a9d6d1b8fba6b677fa6e267f1584f by a maintainer merges it
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:26.2379815Z PR#311 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.2201492Z PR#310 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.2203473Z PR#310 blocked: needs review: policy: minor: cuda-test-image 13.4.3 -> 13.5.0 (minor) is review for family base-image; an approval of a6f51937fac609debfa6ee281f7786f8b6b005d2 by a maintainer merges it
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.2205315Z PR#310 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.4602329Z PR#309 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.4603784Z PR#309 blocked: needs review: policy: ledger-defaults: llama-cpp v0.8.0 -> v0.9.0 (minor) moves llama.cpp, which H57dm, H59 measured; an approval of a38920cd82a9596ba31e040836d58aaae49b121a by a maintainer me
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:27.4605555Z PR#309 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:28.4456119Z PR#308 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:28.4456830Z PR#308 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:29.4708398Z PR#307 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:29.4710645Z PR#307 blocked: needs review: policy: auto: image:packaging-images:debian:trixie-slim trixie-slim -> trixie-slim (digest), family base-image; auto: image:packaging-images:ghcr.io/homebrew/brew:7.0.11 7.0.11 -
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:29.4713686Z PR#307 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.5482214Z PR#306 blocked: canary run concluded failure (https://github.com/88plug/k3d-gpu/actions/runs/41401470053)
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.5482861Z PR#306 blocked: canary vllm-xpu: fail
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.5483158Z PR#306 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.5483881Z PR#306 blocked: needs review: policy: major: vllm-xpu v0.32.0 -> v0.33.0 (major); an approval of 7f9ea5e7e52f88db39496ec8a269be13da62474c by a maintainer merges it
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.5484668Z PR#306 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.8500868Z PR#305 blocked: needs review: policy: major: ci-chrome 156.0.8078.73 -> 157.0.8103.38 (major); an approval of 865ae4d15feb9c11a25b42147a9230ec650f2099 by a maintainer merges it
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:30.8501726Z PR#305 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:31.8894633Z PR#304 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:32.7787196Z PR#303 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.0097192Z PR#302 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.0098634Z PR#302 blocked: needs review: policy: auto: agent-sandbox v1.0.8 -> v1.0.9 (patch), family opt-in; major: open-webui v0.12.2 -> v0.13.0 (major); an approval of e0b3e74aea2d508b9b19d0b8e2304a0a5970470e by a ma
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.0100803Z PR#302 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.3681841Z PR#301 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.3684147Z PR#301 blocked: needs review: policy: tag line: image:images:busybox:1.37 1.37 -> 1.38 (line) is review for family base-image; auto: image:images:ollama/ollama:0.40.13 0.40.13 -> 0.40.15 (patch), family base-
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:33.3686001Z PR#301 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:34.4914881Z PR#300 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:34.8127578Z PR#299 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:34.8128178Z PR#299 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:35.0124703Z PR#298 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:35.0125369Z PR#298 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:36.2072539Z PR#297 blocked: needs review: policy: revision: nixpkgs 08d3a97586f6b6343b3b5e799980773629432610 -> c56a87d8e41ce69bd2af57eb35dcc117a9dfca33 (revision) is review for family tools; an approval of c7fb21466f719
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:36.2075045Z PR#297 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:37.3344309Z PR#296 blocked: needs review: policy: auto: examples-crewai 1.15.33 -> 1.15.36 (patch), family pypi; auto: examples-langchain-core 1.7.3 -> 1.7.6 (patch), family pypi; auto: examples-langchain-openai 1.7.3 ->
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:37.3347818Z PR#296 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:38.4239667Z release: no bot merge above the last aur-v* tag
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-06T23:30:38.4352118Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42309600332 -->

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-07T00:21:10Z, updated 2026-11-07T00:21:10Z.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42280586432
Conclusion: **failure**, created 2026-11-06T19:32:57Z, commit 8e10b0943d8f4c841de688ecfe977151f3c7ab2c
Failed jobs: Bump digests, Bump host-intel, Dashboard
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5962595Z   name: outdated
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5962805Z   path: /home/runner/work/_temp
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5963018Z   merge-multiple: false
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5963214Z   repository: 88plug/k3d-gpu
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5963407Z   run-id: 42280586432
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5963582Z   skip-decompress: false
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5963860Z   digest-mismatch: error
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5964069Z env:
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5966331Z   GH_TOKEN: ***
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5966526Z   GH_REPO: 88plug/k3d-gpu
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.5966719Z ##[endgroup]
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.7125951Z Downloading single artifact
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.9819913Z Preparing to download the following artifacts:
Dashboard UNKNOWN STEP 2026-11-06T19:50:54.9820542Z - outdated (ID: 13534583925, Size: 6194, Expected Digest: sha256:d03b71bc3351a232ed1ee76d31c2ae9f3bbf448ccbdc780ad9bf026b69b18e29)
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.5011113Z Redirecting to blob download url: https://productionresultssa13.blob.core.windows.net/actions-results/29983cae-d140-8a8c-b345-9fd04817ca35/workflow-job-run-bf6d8493-065f-5a42-990d-94e58026cbc3/artifacts/ee3cadeefedd25c482a57139206e1f7129dd522796df2
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.5012836Z Starting download of artifact to: /home/runner/work/_temp
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7695661Z (node:2287) [DEP0005] DeprecationWarning: Buffer() is deprecated due to security and usability issues. Please use the Buffer.alloc(), Buffer.allocUnsafe(), or Buffer.from() methods instead.
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7696627Z (Use `node --trace-deprecation ...` to show where the warning was created)
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7745401Z SHA256 digest of downloaded artifact is d03b71bc3351a232ed1ee76d31c2ae9f3bbf448ccbdc780ad9bf026b69b18e29
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7754992Z Artifact download completed successfully.
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7755464Z Total of 1 artifact(s) downloaded
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7755890Z Download artifact has finished successfully
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7896899Z ##[group]Run t="$RUNNER_TEMP"
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7897209Z ^[[36;1mt="$RUNNER_TEMP"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7897591Z ^[[36;1m[ -s "$t/outdated-all.json" ] || hack/pins outdated --json --all > "$t/outdated-all.json"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7898127Z ^[[36;1mgh pr list --state open --limit 100 --json number,title,headRefName,labels,author \^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7898552Z ^[[36;1m  --jq '[.[] | select(.headRefName | startswith("autobump/"))^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7898872Z ^[[36;1m         | {number, title, headRefName,^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7899269Z ^[[36;1m            verdict: ([.labels[].name | select(startswith("verdict:")) | ltrimstr("verdict:")] | first // "?"),^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7899785Z ^[[36;1m            blocking: (if any(.labels[]; .name == "needs-review") then "a human approval at the head"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7900248Z ^[[36;1m                       elif any(.labels[]; .name == "canary-drift") then "canary drift: a human approval"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7900651Z ^[[36;1m                       else "" end)}]' > "$t/prs.json"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7901031Z ^[[36;1mhack/ci_alarm list > "$t/alarms.txt" || echo "(hack/ci_alarm list failed)" > "$t/alarms.txt"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7901712Z ^[[36;1mhack/pins report --outdated "$t/outdated-all.json" --prs "$t/prs.json" --alarms "$t/alarms.txt" \^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7902152Z ^[[36;1m  --lag-alarms "$t/lag.json" > "$t/dashboard.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7902455Z ^[[36;1m# an issue body holds at most 65536 characters^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7902748Z ^[[36;1mif [ "$(wc -c < "$t/dashboard.md")" -gt 60000 ]; then^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7903035Z ^[[36;1m  head -c 60000 "$t/dashboard.md" > "$t/cut.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7903417Z ^[[36;1m  printf '\n\n(cut at 60000 characters: the full dashboard is in the run log)\n' >> "$t/cut.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7903781Z ^[[36;1m  cat "$t/dashboard.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7904014Z ^[[36;1m  mv "$t/cut.md" "$t/dashboard.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7904235Z ^[[36;1mfi^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7904756Z ^[[36;1mn=$(gh issue list --state open --limit 100 --search 'in:title "Freshness dashboard"' --json number,title \^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7905257Z ^[[36;1m      --jq '[.[] | select(.title == "Freshness dashboard")][0].number // empty')^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7905575Z ^[[36;1mif [ -z "$n" ]; then^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7905980Z ^[[36;1m  gh label create freshness-dashboard --color 0e8a16 --description "The Freshness workflow's dashboard" --force^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7906580Z ^[[36;1m  url=$(gh issue create --title "Freshness dashboard" --label freshness-dashboard --body-file "$t/dashboard.md")^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7906977Z ^[[36;1m  n=${url##*/}^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7907168Z ^[[36;1melse^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7907395Z ^[[36;1m  gh issue edit "$n" --body-file "$t/dashboard.md"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7907647Z ^[[36;1mfi^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7908004Z ^[[36;1mgh issue pin "$n" 2>/dev/null || echo "issue #$n: not pinned (no permission); find it by its freshness-dashboard label"^[[0m
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7957324Z shell: /usr/bin/bash -e {0}
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7957545Z env:
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7959515Z   GH_TOKEN: ***
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7959817Z   GH_REPO: 88plug/k3d-gpu
Dashboard UNKNOWN STEP 2026-11-06T19:50:55.7960084Z ##[endgroup]
Dashboard UNKNOWN STEP 2026-11-06T19:50:57.5710067Z the '88plug/k3d-gpu' repository has disabled issues
Dashboard UNKNOWN STEP 2026-11-06T19:51:11.8255743Z the '88plug/k3d-gpu' repository has disabled issues
Dashboard UNKNOWN STEP 2026-11-06T19:51:11.8287775Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42280586432 -->

## `red:watchdog.yml`: scheduled run failure

Opened 2026-11-07T00:21:26Z, updated 2026-11-07T00:21:26Z.

The latest scheduled run of `watchdog.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42312444833
Conclusion: **failure**, created 2026-11-06T23:31:16Z, commit 8e10b0943d8f4c841de688ecfe977151f3c7ab2c
Failed jobs: Watchdog
Hint: no known signature matched

<details><summary>Last 39 lines of the failed log</summary>

```text
Watchdog Check and alarm ﻿2026-11-06T23:31:46.6960283Z ##[group]Run hack/watchdog
Watchdog Check and alarm 2026-11-06T23:31:46.6960549Z ^[[36;1mhack/watchdog^[[0m
Watchdog Check and alarm 2026-11-06T23:31:46.7008190Z shell: /usr/bin/bash -e {0}
Watchdog Check and alarm 2026-11-06T23:31:46.7008407Z env:
Watchdog Check and alarm 2026-11-06T23:31:46.7010369Z   GH_TOKEN: ***
Watchdog Check and alarm 2026-11-06T23:31:46.7010558Z   GH_REPO: 88plug/k3d-gpu
Watchdog Check and alarm 2026-11-06T23:31:46.7010746Z   SIMULATE_RED: 
Watchdog Check and alarm 2026-11-06T23:31:46.7010913Z ##[endgroup]
Watchdog Check and alarm 2026-11-06T23:31:49.1135317Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:31:51.5988431Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:31:53.5404306Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:00.3183937Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:01.8831127Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:04.1689043Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:17.5202474Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:17.5221882Z ##[error]listing open alarms: gh issue list --label ci-alarm --state open --limit 200 --json number,title,body: exit 1: the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:18.0671158Z the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:18.0674219Z ##[error]8 alarm(s) not filed (stale:bot-sweep.yml, red:bot-sweep.yml, stale:freshness.yml, red:freshness.yml, stale:watchdog.yml, red:watchdog.yml, bot-pr-stuck, watchdog-error): this repository has issues disabled; enable Issues in Settings > G
Watchdog Check and alarm 2026-11-06T23:32:18.0677039Z ##[error]no successful scheduled run within 7 h (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0685021Z ##[error]scheduled run failure (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0686671Z ##[error]no successful scheduled run within 12 h (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0688927Z ##[error]scheduled run failure (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0690773Z ##[error]no successful scheduled run within 7 h (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0692668Z ##[error]scheduled run failure (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0694679Z ##[error]bot PRs open over 72 h without green CI (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0699102Z ##[error]the watchdog could not complete its checks (not filed: this repository has issues disabled)
Watchdog Check and alarm 2026-11-06T23:32:18.0700218Z ## Watchdog 2026-11-06T23:31:46Z
Watchdog Check and alarm 2026-11-06T23:32:18.0700430Z 
Watchdog Check and alarm 2026-11-06T23:32:18.0700574Z - not filed: `stale:bot-sweep.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0700903Z - not filed: `red:bot-sweep.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0701241Z - not filed: `stale:freshness.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0701550Z - not filed: `red:freshness.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0701871Z - not filed: `stale:watchdog.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0702180Z - not filed: `red:watchdog.yml`
Watchdog Check and alarm 2026-11-06T23:32:18.0702494Z - not filed: `bot-pr-stuck`
Watchdog Check and alarm 2026-11-06T23:32:18.0702779Z - not filed: `watchdog-error`
Watchdog Check and alarm 2026-11-06T23:32:18.0703601Z - error: listing open alarms: gh issue list --label ci-alarm --state open --limit 200 --json number,title,body: exit 1: the '88plug/k3d-gpu' repository has disabled issues
Watchdog Check and alarm 2026-11-06T23:32:18.0704362Z 
Watchdog Check and alarm 2026-11-06T23:32:18.0788409Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `watchdog.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42312444833 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-07T00:20:46Z, updated 2026-11-07T00:20:46Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/41342760056 at 2026-11-01T19:38:34Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-07T00:20:59Z, updated 2026-11-07T00:20:59Z.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: none found

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-07T00:21:19Z, updated 2026-11-07T00:21:19Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: none found

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


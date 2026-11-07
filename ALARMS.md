# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-07T06:58:21Z. 1 open.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-07T00:21:10Z, updated 2026-11-07T01:48:08Z.

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


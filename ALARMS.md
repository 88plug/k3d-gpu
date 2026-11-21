# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-21T21:54:06Z. 4 open.

## `blind-spot:llama-cpp`: llama-cpp is 8d behind upstream with no open PR

Opened 2026-11-20T21:01:29Z, updated 2026-11-21T19:47:16Z.

Pin `llama-cpp` (group llama-cpp) is at v0.9.0; upstream v1.0.0 was published 2026-11-13T10:20:19Z (8d ago, limit 7 days). No autobump/llama-cpp PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-21T19:47:45Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44941180646
Conclusion: **failure**, created 2026-11-21T19:29:17Z, commit 4d2f2e7209f39de6fcb39e2883860d252f41ae06
Failed jobs: Bump llama-cpp, Bump flake
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Bump flake Bump 2026-11-21T19:34:44.2549651Z eac9874a1cad: Pull complete
Bump flake Bump 2026-11-21T19:34:44.2579110Z 576ad50bbf62: Pull complete
Bump flake Bump 2026-11-21T19:34:44.2600761Z 53ae322071f3: Pull complete
Bump flake Bump 2026-11-21T19:34:44.2697219Z 2865d8265cce: Pull complete
Bump flake Bump 2026-11-21T19:34:44.2704886Z 2cd101c9fa74: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3519240Z e68846426673: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3525621Z b0becb2230ac: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3562040Z 06c3aa0e778b: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3634971Z 3d5091bc6b86: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3644100Z cdbc33869740: Pull complete
Bump flake Bump 2026-11-21T19:34:44.3792534Z 0f15997ca036: Pull complete
Bump flake Bump 2026-11-21T19:34:44.4498643Z af9b027c5319: Pull complete
Bump flake Bump 2026-11-21T19:34:44.4507529Z 54cc82547ebd: Pull complete
Bump flake Bump 2026-11-21T19:34:44.4548557Z f3639a07d269: Pull complete
Bump flake Bump 2026-11-21T19:34:44.4710975Z e0b2fbecd38f: Pull complete
Bump flake Bump 2026-11-21T19:34:44.4809980Z a4d247b164e3: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5504194Z b39242dcacb7: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5527342Z cfdb1d91e904: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5543344Z 7d658efcc7c2: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5569088Z 41ef42a58ff4: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5655067Z 364e18215e94: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5719010Z 43f4faa90f15: Pull complete
Bump flake Bump 2026-11-21T19:34:44.5761513Z 69d8afb2919d: Pull complete
Bump flake Bump 2026-11-21T19:34:44.6508760Z b49537876c31: Pull complete
Bump flake Bump 2026-11-21T19:34:44.6614282Z 9fa24ea10c04: Pull complete
Bump flake Bump 2026-11-21T19:34:44.6691006Z eadb498e4d63: Pull complete
Bump flake Bump 2026-11-21T19:34:44.7523286Z 0e64475062f0: Pull complete
Bump flake Bump 2026-11-21T19:34:44.7534699Z 3e3616f599e8: Pull complete
Bump flake Bump 2026-11-21T19:34:44.7554367Z fbbabecc17ca: Pull complete
Bump flake Bump 2026-11-21T19:34:44.7668817Z d5109b411bae: Pull complete
Bump flake Bump 2026-11-21T19:34:44.8626771Z 4f18eb6db79d: Pull complete
Bump flake Bump 2026-11-21T19:34:44.9532073Z 3ecb5fe50731: Pull complete
Bump flake Bump 2026-11-21T19:34:44.9554593Z bf8cabe27795: Pull complete
Bump flake Bump 2026-11-21T19:34:44.9589955Z f1a77c347a55: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0526606Z 5adbd1372979: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0545149Z abaaadd5a369: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0550912Z a158b33f42a7: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0587654Z ba16368f252b: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0588616Z e3437730ffe4: Pull complete
Bump flake Bump 2026-11-21T19:34:45.0600078Z ef14fdfa4802: Pull complete
Bump flake Bump 2026-11-21T19:34:45.3527884Z eb32db06ae06: Pull complete
Bump flake Bump 2026-11-21T19:34:50.4504608Z 1459811e91b4: Pull complete
Bump flake Bump 2026-11-21T19:34:50.4516196Z b26ff94927cd: Pull complete
Bump flake Bump 2026-11-21T19:34:50.4752826Z b417d09b5ec7: Pull complete
Bump flake Bump 2026-11-21T19:34:50.4753519Z Digest: sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed
Bump flake Bump 2026-11-21T19:34:50.4754775Z Status: Downloaded newer image for nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed
Bump flake Bump 2026-11-21T19:34:54.0870007Z error:
Bump flake Bump 2026-11-21T19:34:54.0871348Z        … while updating the lock file of flake 'git+file:///w?ref=refs/heads/main&rev=4d2f2e7209f39de6fcb39e2883860d252f41ae06&shallow=1'
Bump flake Bump 2026-11-21T19:34:54.0871764Z 
Bump flake Bump 2026-11-21T19:34:54.0871918Z        … while updating the flake input 'nixpkgs'
Bump flake Bump 2026-11-21T19:34:54.0872100Z 
Bump flake Bump 2026-11-21T19:34:54.0872298Z        … while fetching the input 'github:NixOS/nixpkgs/nixos-26.05'
Bump flake Bump 2026-11-21T19:34:54.0872549Z 
Bump flake Bump 2026-11-21T19:34:54.0872884Z        error: unable to download 'https://api.github.com/repos/NixOS/nixpkgs/commits/nixos-26.05': HTTP error 403
Bump flake Bump 2026-11-21T19:34:54.0873228Z 
Bump flake Bump 2026-11-21T19:34:54.0873330Z        response body:
Bump flake Bump 2026-11-21T19:34:54.0873477Z 
Bump flake Bump 2026-11-21T19:34:54.0874116Z        {"message":"API rate limit exceeded for 3.42.157.144. (But here's the good news: Authenticated requests get a higher rate limit. Check out the documentation for more details.)","documentation_url":"https://docs.github.com/rest/overview/resources-in
Bump flake Bump 2026-11-21T19:34:54.1608016Z hack/bump: Command '['docker', 'run', '--rm', '-v', '/home/runner/work/k3d-gpu/k3d-gpu:/w', '-w', '/w', 'nixos/nix:2.35.6@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed', 'sh', '-c', 'printf \'[safe]\\n\\tdirectory = /w\\n\' >> "$
Bump flake Bump 2026-11-21T19:34:54.1766992Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44941180646 -->

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.


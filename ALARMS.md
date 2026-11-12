# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-12T05:49:09Z. 1 open.

## `ci-main`: ci.yml on main failure

Opened 2026-11-12T05:49:09Z, updated 2026-11-12T05:49:09Z.

The latest completed ci.yml run on main failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/43161657728
Conclusion: **failure**, created 2026-11-12T05:28:53Z, commit 019e2fe28c17f564e74bcab82366cd0872714e47
Failed jobs: Nix flake check
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2347507Z 364e18215e94: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2347706Z b417d09b5ec7: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2347892Z f1a77c347a55: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2348082Z 5adbd1372979: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2348272Z e98ac4b8e5f9: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2348462Z 3ecb5fe50731: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2348649Z bf8cabe27795: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2348840Z b26ff94927cd: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2349035Z a4d247b164e3: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2349223Z 5c4ee73b6342: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2349415Z ad36929006be: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2349889Z a158b33f42a7: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2350078Z 1459811e91b4: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2350263Z 075c3bf3a43f: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2350457Z cfdb1d91e904: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2350655Z abaaadd5a369: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2350846Z 3aad95349124: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2351034Z e31afab4ac96: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2351218Z e3437730ffe4: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2351407Z ef14fdfa4802: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2351786Z 5e0ef73c9c0f: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2352072Z d51620799d9e: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2352354Z 2865d8265cce: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2352653Z eb32db06ae06: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2352843Z 576ad50bbf62: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2353036Z 915e44cd0ffc: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2353227Z 45813962d394: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2353414Z e68846426673: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2353792Z fefe2f7c078a: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2353981Z 53ae322071f3: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2354167Z 2cd101c9fa74: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2354358Z 7d658efcc7c2: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2354541Z 06c3aa0e778b: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2354750Z eac9874a1cad: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2354935Z 3d5091bc6b86: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2355130Z 54cc82547ebd: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.2355326Z ba16368f252b: Pulling fs layer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.4782628Z b39242dcacb7: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.4785438Z 59463b7badc9: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.4788470Z ba16368f252b: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.4796074Z 90ea8805d755: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.4799235Z aabacdae552f: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6106185Z a4e558b387bf: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6107638Z b49537876c31: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6111803Z 0186d44a84e1: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6133792Z ad36929006be: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6135488Z a158b33f42a7: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6135897Z 248d1076a789: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6136203Z 364e18215e94: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6136478Z 1459811e91b4: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6136790Z d51620799d9e: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6137196Z b417d09b5ec7: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6137439Z 5e0ef73c9c0f: Download complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6544210Z 90ea8805d755: Pull complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6550387Z aabacdae552f: Pull complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6586236Z 248d1076a789: Pull complete
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6635599Z docker: failed to copy: read tcp 10.1.0.14:47090->52.85.132.20:443: read: connection reset by peer
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6636089Z 
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6648348Z Run 'docker run --help' for more information
Nix flake check Run packaging/check.sh nix 2026-11-12T05:29:01.6666322Z ##[error]Process completed with exit code 125.
```
</details>


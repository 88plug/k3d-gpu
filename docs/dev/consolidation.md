# Branch consolidation: duplicated logic, conflict map, merge order

Measured 2026-10-16 09:00–09:40 PDT against `origin/main` = `ba81920`
(release aur-v0.1.7), over all 50 `claude/*` branches on `origin` at that time.
The 51st local ref, `claude/doctor-nvidia-runtime-check`, is gone from
`origin` because it merged as #8. Every line reference is `branch:file:line` on
that branch's tip at fetch time, or `main:file:line` on `ba81920`. The method
section shows how to re-run the measurement when the tips move.

## Summary

- **11 branches are cut from the pre-squash hybrid commits** (`109154d`, and
  for 7 of them also `fb5c17d`). `main` has the same work squashed as
  `d7d53da` (#9). Those two commits account for **371 of the 449**
  conflicting pairs in a raw pairwise merge. Rebasing with
  `git rebase --onto origin/main <fb5c17d|109154d>` makes 7 of the 11 apply
  cleanly. The other 4 hit one append/append hunk at the end of
  `tests/detect_test.sh`, which "keep both" resolves.
- **`claude/hybrid-core-balloons` is fully superseded.** After that rebase it
  has 0 commits left, so it can be deleted.
- After rebasing, **166 of 1225 pairs conflict** (1059 merge clean). Those
  166 pairs hold **305 insert/insert hunks and 22 edit/edit hunks**. The 22
  edit/edit hunks come from 14 pairs. Eight anchor lines on `main` take 250 of
  the 305 insertion hunks: the `usage()` environment and command lists, the
  header comment, the dispatch `case`, the last config variable, the end of
  `tests/detect_test.sh`, and two README spots (see [Hot anchors](#hot-anchors)).
- **The `agent` command exists four times.** `agent-stack`, `agent-gaudi`,
  `agent-multimodal` and `arc-xpu-path` each carry their own copy of one
  base commit ("k3d-gpu agent — OpenVINO Model Server on the cluster's best
  Intel engine"). The copies form two pairs, and each pair shares a
  patch-id. On top of the base
  copy, each branch adds its own edits to the same functions. Two branches also
  build their own vLLM backend: `agent-gaudi` for HPU, `arc-xpu-path` for
  XPU. They use different manifest mechanisms, and their API paths differ
  (`/v3` vs `/v1`).
- **14 groups of duplicated logic** are listed below, each with one proposed
  single implementation. The biggest are 19 separate kubectl stubs in tests,
  13 copies of the `*_says` test harness, and 6 walks over Intel device nodes.
- **Three conflicts that git does not flag.** (1) `igpu-sharing` +
  `intel-gpu-dra`: in DRA mode the deleted plugin manifest gets re-applied.
  (2) `k3d-gpu-mcp` hard-codes the OVMS agent Service and `/v3`. (3)
  `k3d-gpu-mcp` hard-codes a copy of `vendor_daemonset`.
- **Recommended order** ([Merge order](#recommended-merge-order)): 19
  conflict-free branches first, then the low-cost ones, then the agent family
  as one stacked series, with CI and build changes last. Simulated over the 46
  non-agent-family branches, that order costs 53 insertion hunks and 17
  edit/edit hunks.

## Method

Everything ran in two scratch clones of this repo. Nothing was pushed except
this document. Each step was a plain git command:

1. **Fetch.** `git fetch origin '+refs/heads/main:refs/remotes/o/main'
   '+refs/heads/claude/*:refs/remotes/o/claude/*'` (50 branches).
2. **Raw pairwise map.** For each of the 1225 pairs:
   `git checkout --detach -f o/claude/A`, then
   `git merge --no-commit --no-ff o/claude/B`. Conflicted paths were recorded
   from `git diff --name-only --diff-filter=U`, then `git merge --abort`.
   Result: 776 clean, 449 conflicting.
3. **Against main.** The same test-merge of each branch onto `o/main`: 43
   clean, 7 conflicting (table below).
4. **Rebase.** Each branch that is not a descendant of `o/main` was rebased
   with `git rebase --onto o/main <u>`. `<u>` is `fb5c17d` if the branch
   contains it, else `109154d`. Four branches stopped on a single
   `tests/detect_test.sh` hunk. Those four were redone with
   `tests/detect_test.sh merge=union` in `.git/info/attributes`, and
   `bash -n` passes on both `scripts/k3d-gpu` and `tests/detect_test.sh` of
   each result. All 50 branches then had a ref based on `o/main`.
5. **Rebased pairwise map.** Step 2 repeated on the rebased refs, so every
   conflict is between the two branches themselves. Each conflicting pair was
   merged again with `merge.conflictStyle=diff3`. A hunk with an empty base
   section was counted as insert/insert. A non-empty base section means both
   branches edited the same lines (edit/edit).
6. **Hot anchors.** For every insert/insert hunk, the last non-blank line
   before the conflict marker was recorded, and the lines were counted.
7. **Sequential walk.** Greedy: start from `o/main`. At each step, test-merge
   every remaining branch into the integrated tip. Pick the cheapest one
   (cost = insert hunks + 10 × edit hunks, ties broken by pairwise conflict
   degree), then commit it with the union driver so the walk can continue.
   Run once without constraints and once with `agent-stack` required before
   the other agent branches.
8. **Agent stacking.** Each agent variant's own commits (everything after its
   copy of the base agent commit) were replayed onto the rebased
   `agent-stack` with `git rebase --onto`. The conflicts were recorded, then
   the rebase was aborted.

Not done: no test suites were run on merged trees, because live benchmarks are
running on this host. So the union-resolved walk proves conflict counts only,
not that the merged code works. The semantic conflicts below come from reading
the code (static traces), not from running it.

## Branch status

### Cut from the pre-squash hybrid commits

| Branch | Contains | Rebase onto main | Own commits after |
|---|---|---|---|
| agent-gaudi | 109154d | 1 hunk `tests/detect_test.sh` (append/append) | 3 |
| agent-stack | 109154d | 1 hunk `tests/detect_test.sh` (append/append) | 2 |
| nfd-k3s-bump | 109154d | 1 hunk `tests/detect_test.sh` (append/append) | 2 |
| report-bench | 109154d | 1 hunk `tests/detect_test.sh` (append/append) | 2 |
| balloons-eat-compat | fb5c17d | clean | 3 |
| dataplane-accel-design | fb5c17d | clean | 2 |
| eat-upstream-pack | fb5c17d | clean | 1 |
| intel-showcase | fb5c17d | clean | 2 |
| intel-telemetry | fb5c17d | clean | 3 |
| provenance-log | fb5c17d | clean | 2 |
| hybrid-core-balloons | fb5c17d | clean | **0: superseded by #9, delete** |

Every one of the four rebase conflicts is a branch's new tests colliding with the three hybrid
`test_says` cases that `main` appends at `main:tests/detect_test.sh:434-443`.

### Test-merge of each branch onto `main` as-is

Conflicting (7): agent-gaudi (`scripts/k3d-gpu`, `tests/detect_test.sh`),
agent-stack (`README.md`, `scripts/k3d-gpu`, `tests/detect_test.sh`),
balloons-eat-compat (`README.md`, `scripts/k3d-gpu`,
`share/nri-balloons.yml`, `tests/detect_test.sh`), dataplane-accel-design
(`README.md`, `tests/detect_test.sh`), intel-showcase
(`tests/detect_test.sh`), nfd-k3s-bump (`Dockerfile`, `README.md`,
`scripts/k3d-gpu`, `tests/detect_test.sh`), report-bench (`README.md`,
`scripts/k3d-gpu`, `tests/detect_test.sh`). All 7 are in the stale-base table
above. The other 43 merge onto `main` cleanly.

## Conflict map

### Totals (rebased refs)

| File | Insert/insert hunks | Edit/edit hunks |
|---|---|---|
| `scripts/k3d-gpu` | 132 | 10 |
| `tests/detect_test.sh` | 83 | 0 |
| `README.md` | 69 | 2 |
| `packaging/aur/PKGBUILD` | 11 | 0 |
| `.gitignore` | 5 | 0 |
| `.github/workflows/build-and-push.yml` | 1 | 7 |
| `.github/workflows/auto-release.yml` | 0 | 2 |
| `share/nri-balloons.yml` | 0 | 1 |
| `.github/workflows/ci.yml`, `CONTRIBUTING.md`, `Dockerfile`, `docs/provenance.md` | 1 each | 0 |
| **Total** | **305** | **22** |

Conflict degree (the number of other branches a branch conflicts with):
agent-sandbox-ecores 21, wsl2-intel 20, intel-gpu-dra 20, agent-multimodal 20,
npu-path 18, doctor-perf 16, balloons-eat-compat 15, arc-xpu-path 15,
agent-stack 15, agent-gaudi 15, igpu-sharing 14, sim-hardware-matrix 13,
report-bench 13, nfd-k3s-bump 13, intel-showcase 12, devcontainer-codespaces 12,
dataplane-accel-design 12, chat-ui 12, kuberay-hybrid 9, kserve-ovms 9,
host-setup 8, upgrade-safety 5, packaging-all 5, intel-telemetry 4, docs-site 4,
autobump-new-components 4, inv-intelstar-numerics-contracts-realisations-carry-me 2,
invent-presence-gated-operating-points-human-at 2, claims-guard 2,
provenance-log 1, failure-modes 1.

19 branches conflict with **no** other branch: bench-landscape,
eat-upstream-pack, examples-agent-frameworks,
frontier-package-power-coupled-placement-treat-p-, gpustack-integration,
hybrid-core-balloons, inv-compete-energy-aware-what-if-residency-advisor-a,
inv-compete-xeon-promotion-gate-twin-snapshot-balloo,
inv-intelstar-per-engine-counter-sampler-proves-where-,
inv-science-execution-path-witness-kernel-accounted-,
inv-science-interference-witness-for-pinned-pods-cor, invent-agent-topology,
invent-agent-trace-bench, invent-power-aware, invent-prefix-reuse,
invent-spec-decoding, invent-tseries-envelope, k3d-gpu-mcp, security-audit.

The full pair-by-pair table is in the [appendix](#appendix-full-pair-conflict-table).

### Hot anchors

Count of insertion hunks by the `main` line the branches all insert after:

| Anchor on `main` | Hunks |
|---|---|
| `main:tests/detect_test.sh:443`, the last test (end of file) | 83 |
| `main:scripts/k3d-gpu:84`, the last line of the `usage()` environment list (`K3D_GPU_HYBRID_PLUGIN`) | 44 |
| `main:README.md:150`, the end of the hybrid-CPU section ("Intel's Enterprise Agent Toolkit CPU balloons).") | 45 |
| `main:scripts/k3d-gpu:68`, the last command in `usage()` (`logs`) | 20 |
| `main:scripts/k3d-gpu:28`, the last command in the header comment (`#   logs`) | 19 |
| a closing `}` (new function appended after an existing one) | 19 |
| `main:scripts/k3d-gpu:884`, the last dispatch arm (`logs)    cmd_logs    ;;`) | 18 |
| `main:README.md:56`, the last row of the environment table (`K3D_GPU_HYBRID_PLUGIN`) | 15 |
| `main:scripts/k3d-gpu:42`, the last config variable (`HYBRID_PLUGIN_MANIFEST=`) | 6 |

Every one of these is an append to a list. Merge order cannot remove them; it
only decides who resolves each one. Two changes on `main` would make most of
them vanish:

- Split `tests/detect_test.sh` after its shared helpers: each feature's
  cases go into its own `tests/detect.d/<feature>.sh`, sourced in a glob loop
  at the end. That removes all 83 end-of-file hunks, but each branch must move
  its tests into its own file when it rebases.
- Insert new subcommands, env vars and README rows in alphabetical order
  rather than after `logs` or `K3D_GPU_HYBRID_PLUGIN`. Two branches then
  collide only when their names sort next to each other.

### Edit/edit hunks (the ones that need a decision)

| Pair | File | What both edit |
|---|---|---|
| agent-gaudi x agent-stack | `scripts/k3d-gpu` (3) | parser defaults block, `agent_manifest` locals, the warm-up request. Both edit their own copies of the base agent commit (see D1). |
| autobump-new-components x upgrade-safety | `build-and-push.yml` (6) | autobump pins every action to a SHA (`docker/setup-buildx-action@8d2750c…`); upgrade-safety adds a `release` job on unpinned `@v3`/`@v6` tags |
| autobump-new-components x nfd-k3s-bump | `build-and-push.yml` (1) | the "Read pinned versions" `$GITHUB_OUTPUT` block: autobump groups it, nfd adds `k3sdigest` |
| autobump-new-components x balloons-eat-compat | `share/nri-balloons.yml` (1) | the render-recipe header comment (see D11) |
| packaging-all x upgrade-safety | `auto-release.yml` (2) | the dispatch step after tagging: packaging-all adds `packages.yml`; upgrade-safety dispatches `build-and-push.yml -f release=` instead of `aur-deploy.yml` |
| balloons-eat-compat x wsl2-intel | `scripts/k3d-gpu` (1) | the `hybrid)` arm of `vendor_daemonset` (`main:scripts/k3d-gpu:341`): one gates it on `balloons_baked`, the other appends `wsl)` |
| intel-gpu-dra x sim-hardware-matrix | `scripts/k3d-gpu` (1) | `vendor_total`'s `local res total=0` (dra adds an early return, sim adds `nodes=""`) |
| nfd-k3s-bump x sim-hardware-matrix | `scripts/k3d-gpu` (1) | the `/dev/dri/renderD*` check in `cmd_up`: nfd inserts host volume mounts above it; sim prefixes it with `${SYSROOT}` |
| doctor-perf x report-bench | `scripts/k3d-gpu` (1) | dispatch arm `doctor)  doctor ;;`: doctor-perf changes it to `cmd_doctor`, report-bench adds `report)` next to it |
| igpu-sharing / intel-gpu-dra / upgrade-safety x wsl2-intel | `scripts/k3d-gpu` (1 each) | `usage()` env text for `K3D_GPU_VENDORS` / `K3D_GPU_IMAGE` |
| npu-path x wsl2-intel, failure-modes x nfd-k3s-bump | `README.md` (1 each) | the same vendor list sentence and the doctor bullet |

## Duplicated logic and the single implementation

### D1. The `agent` command: four copies of one base commit

| Branch | Base agent commit | Patch-id | Own work on top |
|---|---|---|---|
| agent-stack | 0e0c141 | 42390b05d917 | 8385dc2 fix: warm up in-pod, stop before applying an unavailable device |
| agent-gaudi | 0e0c141 | 42390b05d917 | f4ea38d vLLM on Gaudi, 4a4bf7e test |
| agent-multimodal | ff247f4 | 06643f8e3725 | bee61df add-ons (vision, speech-to-text, video) |
| arc-xpu-path | fbb9658 (same tree as ff247f4) | 06643f8e3725 | 82c6e8d vLLM on Arc, 2a53248 test |

All four define the same function set:

- `node_allocatable`: agent-stack:scripts/k3d-gpu:692, agent-gaudi:696, agent-multimodal:724, arc-xpu-path:719
- `labelled_nodes`: agent-stack:892, agent-gaudi:917, agent-multimodal:926, arc-xpu-path:960
- `max_node_cpus`: agent-stack:895, agent-gaudi:920, agent-multimodal:929, arc-xpu-path:963
- `cpu_list_count`: agent-stack:901, agent-gaudi:926, agent-multimodal:935, arc-xpu-path:969
- `agent_device`: agent-stack:914, agent-gaudi:940, agent-multimodal:948, arc-xpu-path:982
- `agent_device_gid`: agent-stack:939, agent-gaudi:972, agent-multimodal:971, arc-xpu-path:1005
- `agent_cpus`: agent-stack:965, agent-gaudi:998, agent-multimodal:997, arc-xpu-path:1031
- `agent_manifest`: agent-stack:979, agent-gaudi:1043, agent-multimodal:1014, arc-xpu-path:1045
- `agent_warmup`, `cmd_agent_up/status/down`, `cmd_agent`: agent-stack:1117-1159, agent-gaudi:1182-1241, agent-multimodal:1148-1663, arc-xpu-path:1364-1422
- the test helper `agent_says`: agent-stack:tests/detect_test.sh:452, agent-gaudi:452, agent-multimodal:467, arc-xpu-path:466

`agent_device` is byte-identical between agent-stack, agent-multimodal and
arc-xpu-path. agent-gaudi adds an `HPU|GAUDI` arm to it.

Inside that shared code, the branches wrote three versions of one fix: the
resource suffix in the "agent: … on <device>" line.

- agent-stack:scripts/k3d-gpu:990-992 (`local via`, then `[ "${res}" = - ] || via=" (${res})"`)
- agent-multimodal:scripts/k3d-gpu:1011 `agent_res_note() { [ "$1" = - ] || printf ' (%s)' "$1"; }`, used at :1025, :1324, :1470
- arc-xpu-path:scripts/k3d-gpu:1056, inline `$([ "${res}" = - ] || printf ' (%s)' "${res}")`

There are also two vLLM backends:

- agent-gaudi: `agent_select_model` (:895), `agent_server` (:911, Service
  `vllm` on HPU), and `agent_manifest_gaudi` (:1026). The manifest is a
  template file, `share/agent-vllm-gaudi.yml`, that serves the OpenAI API at
  `/v3` through a middleware (agent-gaudi:share/agent-vllm-gaudi.yml:53).
- arc-xpu-path: a `K3D_GPU_AGENT_BACKEND` switch (:905-926) that sets
  `AGENT_APP=vllm AGENT_API=/v1`, plus `VLLM_XPU_ENV` (:1190) and
  `vllm_manifest` (:1230), with the manifest as an inline heredoc.

**Single implementation.**

1. `agent-stack` is the base: it holds the shared commit plus the warm-up
   fix. Rebase the other three onto it, dropping their copy of the base
   commit. Measured with `git rebase --onto rb/agent-stack <base>`:
   agent-gaudi stops with 3 hunks in `scripts/k3d-gpu`; agent-multimodal
   with 1 in `README.md` and 2 in `scripts/k3d-gpu`; arc-xpu-path with 4 in
   `scripts/k3d-gpu`. That is 10 real hunks in total. As separate branches
   they conflict pairwise at 5 hunks each, and the shared code gets
   re-resolved on every merge.
2. Keep `agent_res_note` (agent-multimodal:1011) and use it at the other two
   call sites.
3. Keep one backend table, keyed by device: app, image, API path, Service
   name, manifest template. Gaudi's template-file approach replaces arc's
   inline heredoc: `share/agent-vllm.yml`, with a per-device env block for
   HPU or XPU. Pick one API path for vLLM. agent-gaudi already rewrites to
   `/v3`, so the `k3d-gpu-mcp` client and the warm-up keep one URL.

### D2. kubectl stubs in tests: 19 new copies, plus 2 on main

`main` already has two kubectl stubs: an `exit 0` stub
(`main:tests/detect_test.sh:70`) and the pod-phase/logs stub `KSTUB`
(`main:tests/detect_test.sh:393-404`). The branches add:

agent-gaudi:tests/detect_test.sh:438, agent-stack:tests/detect_test.sh:438,
agent-multimodal:tests/detect_test.sh:452 and tests/agent_kubeconform.sh:19,
arc-xpu-path:tests/detect_test.sh:451, agent-sandbox-ecores:tests/detect_test.sh:452,
balloons-eat-compat:tests/detect_test.sh:451, failure-modes:tests/detect_test.sh:78,
igpu-sharing:tests/detect_test.sh:492, intel-gpu-dra:tests/detect_test.sh:453,
nfd-k3s-bump:tests/detect_test.sh:441, npu-path:tests/detect_test.sh:621,
report-bench:tests/detect_test.sh:441, wsl2-intel:tests/detect_test.sh:542,
k3d-gpu-mcp:tests/mcp_test.sh:23, kserve-ovms:tests/kserve_test.sh:38,
sim-hardware-matrix:tests/sim_matrix_test.sh:64, upgrade-safety:tests/image_test.sh:38,
eat-upstream-pack:integrations/eat/upstream/checks/eat-detect-platform.sh:19.

Each of them pattern-matches `"$*"` against a different set of `STUB_*`
variables. The allocatable `get nodes` query is answered from four different
variables: `STUB_ALLOC` (agent-*, wsl2-intel), `STUB_ALLOCATABLE`
(igpu-sharing, intel-gpu-dra, sim-hardware-matrix), `STUB_NODE_RES`
(failure-modes) and `STUB_NODES` (report-bench). The `get nodes -l` label
query is answered from `STUB_NODE_LABELS` (agent-*), `STUB_HYBRID_NODES`
(agent-sandbox-ecores, failure-modes), `STUB_HYBRID` (agent-multimodal's
kubeconform test), or a fixed node name (balloons-eat-compat, nfd-k3s-bump,
sim-hardware-matrix). Call logs go under five different names: `STUB_LOG`,
`STUB_KUBECTL_LOG`, `STUB_APPLY_LOG`, `STUB_KUBE/calls` and `CALLS`.

**Single implementation:** one `tests/stubs/kubectl` with the response
keyed by normalised argv, as `k3d-gpu-mcp:tests/mcp_test.sh:23-38` already
does. That stub drops `--context` and `--request-timeout`, maps the remaining
args to a file name, prints that file if it exists, else prints a NotFound
error and exits 1, and logs every call. A helper
`stub_kubectl '<args>' <<< "<output>"` writes the response file. Add a
`STUB_APPLY_OUT` hook that captures stdin for `apply -f -`. Every case above
is either a canned response or a stdin capture, so each copy becomes one or
two `stub_kubectl` lines.

The k3d stub has the same problem, with 9 copies: balloons-eat-compat:tests/detect_test.sh:450,
failure-modes:70, igpu-sharing:479, intel-gpu-dra:467, nfd-k3s-bump:437,
npu-path:620, wsl2-intel:538, sim-hardware-matrix:tests/sim_matrix_test.sh:56,
upgrade-safety:tests/image_test.sh:37. Most of them record the
`k3d cluster create` arguments, each to its own file variable
(`K3D_ARGS_LOG`, `STUB_LOG`, `UP_LOG`, `STUB_K3D_ARGS`). **Single:** one
`tests/stubs/k3d` that writes one arg per line to `${STUB_K3D_ARGS}`.

### D3. The `*_says` assertion harness: 13 copies

`main` has `doctor_says` (`main:tests/detect_test.sh:53`) and `test_says`
(`:407`). The branches add the same pattern again. Each copy runs
`env K3D_GPU_SYSROOT=… PATH=<stub>:… bash k3d-gpu <subcmd>`, greps for an
ERE (optionally negated), checks the exit code, then bumps PASS or FAILS:

`agent_says` (×4, see D1), agent-multimodal:tests/detect_test.sh:573 `addon_says`,
doctor-perf:tests/detect_test.sh:514 `perf_says`, report-bench:tests/detect_test.sh:452
`report_says`, intel-gpu-dra:tests/detect_test.sh:487 `run_says`,
agent-sandbox-ecores:tests/detect_test.sh:473 `sandbox_says`,
upgrade-safety:tests/image_test.sh:58 `up_says`, host-setup:tests/host_setup_test.sh:78
`not_says`, and `up_args` twice (balloons-eat-compat:tests/detect_test.sh:460,
wsl2-intel:tests/detect_test.sh:551). The two `up_args` have the same
signature and differ only in their stub-log variable name.

The new standalone test files also each define their own counters:
`ok`/`fail` in autobump-new-components:tests/render_test.sh:22,
chat-ui:tests/chat_ui_test.sh:15, docs-site:tests/docs_test.sh:29 and
packaging-all:tests/packaging_test.sh:18. `pass`/`fail` in
upgrade-safety:tests/image_test.sh:41. `check` in 10 files
(claims-guard:tests/claims_check_test.sh:55, docs-site:tests/docs_test.sh:33,
host-setup:tests/host_setup_test.sh:69, intel-telemetry:tests/telemetry_test.sh:35,
k3d-gpu-mcp:tests/mcp_test.sh:107, kserve-ovms:tests/kserve_test.sh:21,
packaging-all:tests/packaging_test.sh:20, report-bench:tests/bench_test.sh:22,
sim-hardware-matrix:tests/sim_matrix_test.sh:123, balloons-eat-compat:tests/detect_test.sh:502).
`newroot` is copied from `main:tests/detect_test.sh:43` into
autobump-new-components:tests/render_test.sh:59,
balloons-eat-compat:tests/reserved_cpus_test.sh:23 and host-setup:tests/host_setup_test.sh:33.

**Single implementation:** `tests/lib.sh`, sourced by every test file. It
holds `pass`, `fail`, `summary` (prints "N passed, M failed" and exits with
the result), `newroot`, `pci`/`render`/`accel` from
`main:tests/detect_test.sh:15-43`, and one assertion:

```bash
# says <name> <want rc: 0|fail|any> <ERE> [!] -- <env assignments...> -- <k3d-gpu args...>
```

Every `*_says` above becomes a one-line wrapper or a direct `says` call.
`report_says` is the one that evaluates a Python expression; it stays a
separate helper, built on `pass`/`fail`.

### D4. Node allocatable and node labels

- `main:scripts/k3d-gpu:708` `resource_counts`, with the allocatable go-template inline
- `node_allocatable` ×4 (D1): the same template, factored out, with `resource_counts` rewritten to call it (agent-stack:scripts/k3d-gpu:698)
- `labelled_nodes` ×4 (D1) vs the inline `kubectl get nodes -l` in `vendor_total` on main (`main:scripts/k3d-gpu:719`)
- inv-compete-xeon-promotion-gate-twin-snapshot-balloo:promote-gate/promote_gate.py:208 `allocatable`, in Python

**Single:** land `node_allocatable` and `labelled_nodes` on `main` once
(from agent-stack). `resource_counts`, `vendor_total` and `max_node_cpus` all
read through them.

### D5. CPU-list parsing: 4 shell and 3 Python implementations

- main `scripts/k3d-gpu:209` `cpulist_expand` (with `cpulist_within`, `:217`)
- `cpu_list_count` ×4 (D1), e.g. agent-stack:scripts/k3d-gpu:901
- report-bench:scripts/k3d-gpu:888 `cpulist_count`
- balloons-eat-compat:contrib/eat/compute_reserved_cpus.sh:21 `expand_cpulist`, a verbatim copy of main's `cpulist_expand` in a script shipped to EAT
- Python: inv-science-interference-witness-for-pinned-pods-cor:bench/witness.py:50 `parse_cpulist` and :63 `format_cpulist`;
  inv-compete-xeon-promotion-gate-twin-snapshot-balloo:promote-gate/promote_gate.py:74 `parse_list` and :87 `fmt_list`
  (the same two algorithms); invent-presence-gated-operating-points-human-at:presence/agent.py:19 `cpus`

**Single:** in shell, `cpulist_count() { cpulist_expand "$1" | wc -l; }`
next to `cpulist_expand` on `main`, replacing the two counters.
`cpulist_expand ""` prints nothing, so the count is 0, matching both copies on
empty input. The EAT script is a standalone file applied into another repo,
so it keeps its copy. In Python, one `parse_cpulist`/`format_cpulist` pair
(witness.py's) goes into a shared module (see D9).

### D6. Walking Intel device nodes in sysfs: 6 distinct walks

Every walk loops over `/sys/class/drm/renderD*` or `/sys/class/accel/accel*`,
checks `vendor = 0x8086`, and checks `bound_driver`:

- main `scripts/k3d-gpu:113` `detect_intel`, `:128` `accel_nodes`, `:138` `detect_npu`
- `agent_device_gid` ×4 (D1): the GID of the render node or accel node, e.g. agent-stack:scripts/k3d-gpu:939-960
- npu-path:scripts/k3d-gpu:157 `npu_dev_access` (gid, mode, path) and :168 `npu_render_gid`. This is the same NPU branch as `agent_device_gid`'s `NPU)` arm.
- report-bench:scripts/k3d-gpu:990 `report_intel_gpus` (walks `card*`, then maps to `renderD*` by PCI address) and :1010 `report_npus`

**Single:** two enumerators on `main`, `drm_nodes` and `accel_nodes`
(extending the existing one). Each prints one line per node:
`<vendor> <driver> <device-id> <pci-bdf> <dev-node> <gid> <mode>`.
`detect_intel`, `detect_npu`, `npu_dev_access`, `agent_device_gid` and the
report sections become `awk` filters over those lines.

### D7. RAPL readers: 5

- doctor-perf:scripts/k3d-gpu:791 `perf_rapl` (zones at :793, limits)
- report-bench:scripts/k3d-gpu:1076 `report_rapl` (zones at :1078, JSON); report-bench:bench/energy.sh (`energy_uj` sampler, zone `intel-rapl:0` at :22)
- inv-intelstar-per-engine-counter-sampler-proves-where-:scripts/k3d-gpu-sampler:62 `rapl_domains`, :73 `rapl_read`, :82 `rapl_delta_j` (wrap handling)
- invent-tseries-envelope:bench/envelope_model.py:91 `read_limits`

**Single:** in shell, one `rapl_zones` on `main` that prints
`<zone> <name> <max_energy_range_uj> <pl1_uw> <pl2_uw>`, used by doctor and
report. `bench/energy.sh` reads `energy_uj` from the zone it names. In
Python, the sampler's three functions move into the shared module (D9).

### D8. DRM fdinfo parsers: 2

- inv-intelstar-per-engine-counter-sampler-proves-where-:scripts/k3d-gpu-sampler:96 `parse_fdinfo` (engines, cycles, total-cycles)
- inv-science-execution-path-witness-kernel-accounted-:scripts/k3d-gpu-witness:58 `parse_fdinfo` (engines, capacity, cycles, total-cycles)

Both parse the same `drm-*` keys into different record shapes. **Single:**
the witness parser, because it also keeps `drm-engine-capacity-*`. It goes
in the shared module, and the sampler adapts its record shape at the call
site (:143).

### D9. Canonical JSON plus sha256, and small file readers

- inv-intelstar-numerics-contracts-realisations-carry-me:scripts/k3d-gpu-numerics:77-78
- inv-science-execution-path-witness-kernel-accounted-:scripts/k3d-gpu-witness:45-50 `canonical`, `rule_sha256`
- inv-science-interference-witness-for-pinned-pods-cor:bench/witness.py:42-47 `canonical`, `rules_sha256`

All three use `json.dumps(…, sort_keys=True, separators=(",", ":"))`, then
`hashlib.sha256`. `_read(path)` is defined 5 times:
inv-compete-xeon-promotion-gate-twin-snapshot-balloo:promote-gate/promote_gate.py:99,
invent-tseries-envelope:bench/envelope_model.py:86,
inv-intelstar-per-engine-counter-sampler-proves-where-:scripts/k3d-gpu-sampler:52,
inv-science-execution-path-witness-kernel-accounted-:scripts/k3d-gpu-witness:157,
inv-science-interference-witness-for-pinned-pods-cor:bench/witness.py:75.

**Single:** `scripts/k3d_gpu_lib.py`: `read`, `canonical_sha256`,
`parse_cpulist`/`format_cpulist`, `rapl_*`, `parse_fdinfo`, `hybrid_cpus`.
The research and bench scripts import it through
`sys.path.insert(0, <repo>/scripts)`. The three CLIs that install to
`/usr/bin` (`k3d-gpu-sampler`, `k3d-gpu-witness`, `k3d-gpu-numerics`) need
the module installed next to them. That is a PKGBUILD line, so make the call
when the first of them is packaged.

### D10. JSON emitted from shell and Python tables that mirror the launcher

- report-bench:scripts/k3d-gpu:858-882 `json_str`, `json_str_or_null`, `json_num`, `json_bool`, `json_words`, plus `read1` (:885). These are the only shell JSON builders on any branch. Keep them as the single set for any future shell JSON.
- report-bench:bench/energy.sh:105 hand-writes its JSON with `printf`. Switch it to the `json_*` helpers.
- k3d-gpu-mcp:scripts/k3d-gpu-mcp:71 `PLUGIN_DAEMONSETS` is a hand copy of `vendor_daemonset` (`main:scripts/k3d-gpu:341`). Its own comment at :69-70 says so. It also lacks the vendors that branches add (wsl2-intel's `wsl)`, and the DRA driver from intel-gpu-dra). **Single:** the MCP server reads `k3d-gpu report --json` (report-bench) or a `vendor_daemonset` dump instead of a second table.

### D11. Rendering `share/nri-balloons.yml`: 2 recipes

- autobump-new-components: `hack/render-component nri-balloons`, which runs `helm template --include-crds` (the header in its `share/nri-balloons.yml` is regenerated from `hack/components/nri-balloons/header.txt`)
- balloons-eat-compat:share/nri-balloons.yml header: an inline `helm template … | awk` recipe that adds `meta.helm.sh/release-name`/`-namespace` annotations. The branch's own comment there cites Helm `pkg/action/validate.go checkOwnership` as the reason. That Helm claim is the branch's; it was not re-verified here.

**Single:** `hack/render-component` with an optional post-process step per
component. balloons-eat-compat's awk becomes `nri-balloons`'s post-process.
That also resolves the one `share/nri-balloons.yml` edit/edit hunk.

### D12. Reading pinned versions in CI: 2 copies

- `build-and-push.yml`'s check job emits `k3s`, `plugin`, `intel`, `gaudi`, `testcuda` from `grep`s of `Dockerfile` and `share/*.yml`. autobump-new-components restructures that block; nfd-k3s-bump adds `k3sdigest`.
- upgrade-safety adds a `release` job with its own "Read pinned versions" step: the same `grep`s for `k3s`, `plugin`, `intel` and `gaudi`.

**Single:** `hack/pins.sh`, which prints `key=value` lines. Both jobs run
`hack/pins.sh >> "$GITHUB_OUTPUT"`. Do this when merging upgrade-safety. Its
6 `build-and-push.yml` edit/edit hunks are autobump's SHA-pinned actions
against upgrade-safety's new `release` job on `@v3`/`@v6` tags. Rebasing
upgrade-safety onto autobump, and pinning the new job's actions the same way,
resolves them.

### D13. OpenAI-compatible stub servers and load generators

- Stub servers: report-bench:tests/openai_stub.py:37 `do_POST`;
  examples-agent-frameworks:examples/agents/stub_openai.py:71/:78;
  invent-agent-trace-bench:bench/test_agent_trace.py:54
- Load generators: report-bench:bench/llm_load.py (concurrency and latency);
  invent-agent-trace-bench:bench/agent_trace.py (trace replay, default URL
  `/v3/chat/completions` at :417)

**Single:** one `tests/openai_stub.py`. Start from examples-agent-frameworks'
copy: it has `do_GET` (:71) and a configurable path prefix (:86), while the
other two handle POST only. The two load
generators measure different things, so both stay, but they should share one
HTTP client function.

### D14. Two EAT patch series

- balloons-eat-compat:contrib/eat/enterprise-agent-toolkit.patch patches
  `core/roles/nri_cpu_balloons/defaults/main.yaml`,
  `core/roles/nri_cpu_balloons/templates/generic-balloon-values.yaml.j2`,
  `core/roles/utils/files/compute_reserved_cpus.sh` and
  `core/roles/utils/tasks/get_optimized_cpu_topology.yaml`.
- eat-upstream-pack:integrations/eat/upstream/patches/0003-eas-pin-nri-balloons-chart.patch
  patches `roles/nri_cpu_balloons/defaults/main.yaml` and
  `roles/nri_cpu_balloons/tasks/install_advanced.yaml`. 0001–0006 cover the
  other EAT changes.

Both patch `nri_cpu_balloons/defaults/main.yaml`, under different tree
prefixes (`core/roles/` vs `roles/`). So they target different EAT
layouts or revisions, and git sees no conflict between them because they live
in different files here. **Single:** one patch series under
`integrations/eat/upstream/patches/` against one pinned EAT revision.
balloons-eat-compat's patch becomes 0007 there, rebased to that revision's
paths.

## Conflicts git does not flag

1. **`igpu-sharing` + `intel-gpu-dra`: DRA mode re-installs the device
   plugin.** Static trace on the two tips:
   - intel-gpu-dra:scripts/k3d-gpu:642-643: with `intel_dra`, `up` passes
     `--k3s-arg=--disable=intel-gpu-plugin@server:0`.
   - k3s v1.34.1+k3s1 (the version `Dockerfile:1` pins) deletes a disabled
     manifest. It does not just skip it: `pkg/deploy/controller.go:165-170`
     ("Disabled files are not just skipped, but actively deleted"), with the
     basename match in `shouldDisableFile` at `:486-505`.
     `pkg/cli/server/server.go:412-417` fills `Disables` from `--disable`.
   - igpu-sharing:scripts/k3d-gpu:798 calls `configure_intel_plugin` whenever
     sharing args are set. At :814 it `docker exec … cat`s the baked
     `intel-gpu-plugin.yaml`. k3s deleted that file, so the `else` branch at
     :827-831 runs `kubectl apply -f -` of the full `intel-gpu-plugin`
     DaemonSet. The cluster then runs both Intel GPU stacks.
   - Fix when merging the second of the two: `intel_plugin_args` returns
     nothing when `intel_dra` is true, because sharing in DRA mode is a
     property of the claim.
2. **`k3d-gpu-mcp` vs the vLLM agent backends.**
   k3d-gpu-mcp:scripts/k3d-gpu-mcp:62 fixes `AGENT_SVC = "ovms"`, and
   `agent_endpoint` (:381-384) probes `…/services/ovms:http/proxy/v3`.
   agent-gaudi serves HPU from Service `vllm` (agent-gaudi:scripts/k3d-gpu:911).
   arc-xpu-path serves vLLM on `/v1` (arc-xpu-path:scripts/k3d-gpu:912). On
   either backend, `agent_endpoint` reports the agent as not deployed. D1 step
   3 (one backend table) fixes this: the MCP reads the Service name and API
   path from the deployment's annotations.
3. **`k3d-gpu-mcp` `PLUGIN_DAEMONSETS` vs `intel-gpu-dra` and `wsl2-intel`.**
   With DRA on, there is no `intel-gpu-plugin` DaemonSet, and wsl2-intel adds a
   `wsl)` arm to `vendor_daemonset`. The MCP table (:71-80) misses both (D10).

## Recommended merge order

Before phase 1:

- Delete `claude/hybrid-core-balloons` (0 commits after the rebase).
- Rebase the 11 stale branches with
  `git rebase --onto origin/main fb5c17d` (or `109154d` for agent-gaudi,
  agent-stack, nfd-k3s-bump and report-bench, which lack `fb5c17d`). The
  rebase stops on one `tests/detect_test.sh`
  hunk for four of them; resolve it by keeping both sides.
- Rebase agent-gaudi, agent-multimodal and arc-xpu-path onto agent-stack (D1).
  That makes the agent family one stacked series.

The order is the greedy walk with `agent-stack` constrained first in its
family. Hunk counts per step are against the integrated tree up to that point.

**Phase 1: no conflicts (18 once hybrid-core-balloons is deleted), any order.** bench-landscape,
eat-upstream-pack, examples-agent-frameworks,
frontier-package-power-coupled-placement-treat-p-, gpustack-integration,
inv-compete-energy-aware-what-if-residency-advisor-a,
inv-compete-xeon-promotion-gate-twin-snapshot-balloo,
inv-intelstar-per-engine-counter-sampler-proves-where-,
inv-science-execution-path-witness-kernel-accounted-,
inv-science-interference-witness-for-pinned-pods-cor, invent-agent-topology,
invent-agent-trace-bench, invent-power-aware, invent-prefix-reuse,
invent-spec-decoding, invent-tseries-envelope, k3d-gpu-mcp, security-audit.

**Phase 2: still 0 hunks at their turn (9).** failure-modes,
provenance-log, inv-intelstar-numerics-contracts-realisations-carry-me,
invent-presence-gated-operating-points-human-at, autobump-new-components,
intel-telemetry, host-setup, kserve-ovms, dataplane-accel-design.
Their pairwise conflicts are all with branches that land later. Landing
autobump-new-components here means `hack/render-component` and the
SHA-pinned actions are on `main` before balloons-eat-compat, nfd-k3s-bump and
upgrade-safety rebase onto them (D11, D12).

**Phase 3: one or two append hunks each (10).** claims-guard (1),
kuberay-hybrid (1), devcontainer-codespaces (1), intel-showcase (1),
sim-hardware-matrix (1), igpu-sharing (1), docs-site (2), packaging-all (2),
chat-ui (2), report-bench (2). report-bench brings the `json_*` helpers
(D10) and the report JSON that the MCP should read.

**Phase 4: device paths (1).** npu-path (5 insertion hunks: PKGBUILD and
usage/dispatch).

**Phase 5: the agent family as one stacked series.** agent-stack (9 insertion
hunks against the integrated tree), then the stacked agent-gaudi, arc-xpu-path
and agent-multimodal. Their rebases onto agent-stack cost 3, 4 and 3 real
hunks. Do D1 steps 2–3 here, before the next backend lands. Then
agent-sandbox-ecores (10 insertion hunks, independent of the agent code).

**Phase 6: branches with edit/edit hunks.** These need a reviewer's decision.

| Step | Branch | Insert / edit hunks at its turn | Decision |
|---|---|---|---|
| 1 | balloons-eat-compat | 3 / 1 | D11 post-process, after autobump |
| 2 | doctor-perf | 5 / 1 | dispatch arm: keep `cmd_doctor` and add `report)` |
| 3 | intel-gpu-dra | 6 / 1 | `vendor_total` early return plus sim's `nodes=""`; semantic conflict 1 with igpu-sharing |
| 4 | nfd-k3s-bump | 1 / 3 | volume mounts above the `${SYSROOT}`-prefixed renderD check; `k3sdigest` in the pins block (D12) |
| 5 | wsl2-intel | 7 / 3 | `wsl)` arm under `balloons_baked`'s `hybrid)`; usage text |
| 6 | upgrade-safety | 2 / 8 | rebase onto autobump's SHA pins; `hack/pins.sh` (D12); one dispatch chain in `auto-release.yml` that also keeps packaging-all's `packages.yml` |

Simulated cost over the 46 branches outside the agent-stack/gaudi/arc/
multimodal series: **53 insertion hunks and 17 edit/edit hunks**. The agent
series adds agent-stack's 9 insertions plus the 10 stacking hunks. An
unconstrained greedy walk gave 74/21 overall, and the constrained one gave
76/24. Both numbers overcount the agent family: union-merging four copies of
the same base commit duplicates the shared code, and later steps then
conflict against that duplicate. That is why stacking is required rather than
optional.

## Appendix: full pair conflict table

Rebased refs, so every pair here conflicts on its own content. Counts are
`(insert/insert hunks / edit/edit hunks)` per file. Pairs not listed merge
clean.

| Pair | Conflicted files (add/add, edit/edit hunks) |
|---|---|
| agent-gaudi x agent-multimodal | `scripts/k3d-gpu` (5/0) |
| agent-gaudi x agent-sandbox-ecores | `packaging/aur/PKGBUILD` (1/0); `scripts/k3d-gpu` (5/0) |
| agent-gaudi x agent-stack | `scripts/k3d-gpu` (0/3) |
| agent-gaudi x arc-xpu-path | `scripts/k3d-gpu` (4/0) |
| agent-gaudi x balloons-eat-compat | `scripts/k3d-gpu` (1/0) |
| agent-gaudi x chat-ui | `packaging/aur/PKGBUILD` (1/0) |
| agent-gaudi x doctor-perf | `README.md` (1/0); `scripts/k3d-gpu` (1/0) |
| agent-gaudi x host-setup | `scripts/k3d-gpu` (4/0) |
| agent-gaudi x igpu-sharing | `README.md` (1/0) |
| agent-gaudi x intel-gpu-dra | `scripts/k3d-gpu` (1/0) |
| agent-gaudi x intel-telemetry | `packaging/aur/PKGBUILD` (1/0) |
| agent-gaudi x nfd-k3s-bump | `tests/detect_test.sh` (1/0) |
| agent-gaudi x npu-path | `packaging/aur/PKGBUILD` (1/0); `scripts/k3d-gpu` (4/0) |
| agent-gaudi x report-bench | `tests/detect_test.sh` (1/0) |
| agent-gaudi x wsl2-intel | `scripts/k3d-gpu` (1/0) |
| agent-multimodal x agent-sandbox-ecores | `README.md` (1/0); `scripts/k3d-gpu` (5/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x agent-stack | `README.md` (1/0); `scripts/k3d-gpu` (5/0) |
| agent-multimodal x arc-xpu-path | `scripts/k3d-gpu` (5/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x balloons-eat-compat | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x chat-ui | `README.md` (1/0) |
| agent-multimodal x dataplane-accel-design | `tests/detect_test.sh` (1/0) |
| agent-multimodal x devcontainer-codespaces | `tests/detect_test.sh` (1/0) |
| agent-multimodal x doctor-perf | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x host-setup | `scripts/k3d-gpu` (4/0) |
| agent-multimodal x igpu-sharing | `tests/detect_test.sh` (1/0) |
| agent-multimodal x intel-gpu-dra | `README.md` (1/0); `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x intel-showcase | `tests/detect_test.sh` (1/0) |
| agent-multimodal x kserve-ovms | `README.md` (1/0) |
| agent-multimodal x kuberay-hybrid | `README.md` (1/0) |
| agent-multimodal x nfd-k3s-bump | `README.md` (1/0) |
| agent-multimodal x npu-path | `scripts/k3d-gpu` (4/0); `tests/detect_test.sh` (1/0) |
| agent-multimodal x report-bench | `README.md` (1/0) |
| agent-multimodal x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| agent-multimodal x wsl2-intel | `README.md` (1/0); `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x agent-stack | `README.md` (3/0); `scripts/k3d-gpu` (5/0) |
| agent-sandbox-ecores x arc-xpu-path | `scripts/k3d-gpu` (5/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x balloons-eat-compat | `README.md` (1/0); `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x chat-ui | `README.md` (1/0); `packaging/aur/PKGBUILD` (1/0) |
| agent-sandbox-ecores x dataplane-accel-design | `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x devcontainer-codespaces | `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x doctor-perf | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x host-setup | `scripts/k3d-gpu` (4/0) |
| agent-sandbox-ecores x igpu-sharing | `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x intel-gpu-dra | `README.md` (2/0); `scripts/k3d-gpu` (2/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x intel-showcase | `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x intel-telemetry | `packaging/aur/PKGBUILD` (1/0) |
| agent-sandbox-ecores x kserve-ovms | `README.md` (1/0) |
| agent-sandbox-ecores x kuberay-hybrid | `README.md` (1/0) |
| agent-sandbox-ecores x nfd-k3s-bump | `README.md` (1/0) |
| agent-sandbox-ecores x npu-path | `README.md` (2/0); `packaging/aur/PKGBUILD` (1/0); `scripts/k3d-gpu` (5/0); `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x report-bench | `README.md` (1/0) |
| agent-sandbox-ecores x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| agent-sandbox-ecores x wsl2-intel | `README.md` (2/0); `scripts/k3d-gpu` (2/0); `tests/detect_test.sh` (1/0) |
| agent-stack x arc-xpu-path | `scripts/k3d-gpu` (3/0) |
| agent-stack x balloons-eat-compat | `README.md` (1/0); `scripts/k3d-gpu` (1/0) |
| agent-stack x chat-ui | `README.md` (1/0) |
| agent-stack x doctor-perf | `scripts/k3d-gpu` (1/0) |
| agent-stack x host-setup | `scripts/k3d-gpu` (4/0) |
| agent-stack x intel-gpu-dra | `README.md` (2/0); `scripts/k3d-gpu` (1/0) |
| agent-stack x kserve-ovms | `README.md` (1/0) |
| agent-stack x kuberay-hybrid | `README.md` (1/0) |
| agent-stack x nfd-k3s-bump | `README.md` (1/0); `tests/detect_test.sh` (1/0) |
| agent-stack x npu-path | `README.md` (2/0); `scripts/k3d-gpu` (4/0) |
| agent-stack x report-bench | `README.md` (1/0); `tests/detect_test.sh` (1/0) |
| agent-stack x wsl2-intel | `README.md` (2/0); `scripts/k3d-gpu` (1/0) |
| arc-xpu-path x balloons-eat-compat | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| arc-xpu-path x dataplane-accel-design | `tests/detect_test.sh` (1/0) |
| arc-xpu-path x devcontainer-codespaces | `tests/detect_test.sh` (1/0) |
| arc-xpu-path x doctor-perf | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| arc-xpu-path x host-setup | `scripts/k3d-gpu` (4/0) |
| arc-xpu-path x igpu-sharing | `tests/detect_test.sh` (1/0) |
| arc-xpu-path x intel-gpu-dra | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| arc-xpu-path x intel-showcase | `tests/detect_test.sh` (1/0) |
| arc-xpu-path x npu-path | `scripts/k3d-gpu` (4/0); `tests/detect_test.sh` (1/0) |
| arc-xpu-path x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| arc-xpu-path x wsl2-intel | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| autobump-new-components x balloons-eat-compat | `share/nri-balloons.yml` (0/1) |
| autobump-new-components x nfd-k3s-bump | `.github/workflows/build-and-push.yml` (0/1) |
| autobump-new-components x packaging-all | `.github/workflows/ci.yml` (1/0) |
| autobump-new-components x upgrade-safety | `.github/workflows/build-and-push.yml` (1/6) |
| balloons-eat-compat x dataplane-accel-design | `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x devcontainer-codespaces | `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x doctor-perf | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x igpu-sharing | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x intel-gpu-dra | `README.md` (1/0); `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x intel-showcase | `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x npu-path | `README.md` (1/0); `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| balloons-eat-compat x wsl2-intel | `README.md` (1/0); `scripts/k3d-gpu` (2/1); `tests/detect_test.sh` (1/0) |
| chat-ui x intel-gpu-dra | `README.md` (1/0) |
| chat-ui x intel-telemetry | `packaging/aur/PKGBUILD` (1/0) |
| chat-ui x kserve-ovms | `README.md` (1/0) |
| chat-ui x kuberay-hybrid | `README.md` (1/0) |
| chat-ui x nfd-k3s-bump | `README.md` (1/0) |
| chat-ui x npu-path | `packaging/aur/PKGBUILD` (1/0) |
| chat-ui x report-bench | `README.md` (1/0) |
| chat-ui x wsl2-intel | `README.md` (1/0) |
| claims-guard x docs-site | `CONTRIBUTING.md` (1/0) |
| claims-guard x provenance-log | `docs/provenance.md` (1/0) |
| dataplane-accel-design x devcontainer-codespaces | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x doctor-perf | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x igpu-sharing | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x intel-gpu-dra | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x intel-showcase | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x npu-path | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| dataplane-accel-design x wsl2-intel | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x doctor-perf | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x igpu-sharing | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x intel-gpu-dra | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x intel-showcase | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x npu-path | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| devcontainer-codespaces x wsl2-intel | `tests/detect_test.sh` (1/0) |
| docs-site x inv-intelstar-numerics-contracts-realisations-carry-me | `.gitignore` (1/0) |
| docs-site x invent-presence-gated-operating-points-human-at | `.gitignore` (1/0) |
| docs-site x packaging-all | `.gitignore` (1/0) |
| doctor-perf x host-setup | `README.md` (1/0) |
| doctor-perf x igpu-sharing | `README.md` (1/0); `tests/detect_test.sh` (1/0) |
| doctor-perf x intel-gpu-dra | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| doctor-perf x intel-showcase | `tests/detect_test.sh` (1/0) |
| doctor-perf x npu-path | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| doctor-perf x report-bench | `README.md` (1/0); `scripts/k3d-gpu` (2/1) |
| doctor-perf x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| doctor-perf x wsl2-intel | `scripts/k3d-gpu` (1/0); `tests/detect_test.sh` (1/0) |
| failure-modes x nfd-k3s-bump | `README.md` (0/1) |
| host-setup x npu-path | `scripts/k3d-gpu` (3/0) |
| host-setup x report-bench | `README.md` (1/0) |
| igpu-sharing x intel-gpu-dra | `scripts/k3d-gpu` (3/0); `tests/detect_test.sh` (1/0) |
| igpu-sharing x intel-showcase | `tests/detect_test.sh` (1/0) |
| igpu-sharing x npu-path | `tests/detect_test.sh` (1/0) |
| igpu-sharing x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| igpu-sharing x upgrade-safety | `scripts/k3d-gpu` (1/0) |
| igpu-sharing x wsl2-intel | `scripts/k3d-gpu` (0/1); `tests/detect_test.sh` (1/0) |
| intel-gpu-dra x intel-showcase | `tests/detect_test.sh` (1/0) |
| intel-gpu-dra x kserve-ovms | `README.md` (1/0) |
| intel-gpu-dra x kuberay-hybrid | `README.md` (1/0) |
| intel-gpu-dra x nfd-k3s-bump | `README.md` (1/0) |
| intel-gpu-dra x npu-path | `README.md` (1/0); `scripts/k3d-gpu` (2/0); `tests/detect_test.sh` (1/0) |
| intel-gpu-dra x report-bench | `README.md` (1/0) |
| intel-gpu-dra x sim-hardware-matrix | `scripts/k3d-gpu` (0/1); `tests/detect_test.sh` (1/0) |
| intel-gpu-dra x upgrade-safety | `scripts/k3d-gpu` (1/0) |
| intel-gpu-dra x wsl2-intel | `README.md` (2/0); `packaging/aur/PKGBUILD` (1/0); `scripts/k3d-gpu` (3/1); `tests/detect_test.sh` (1/0) |
| intel-showcase x npu-path | `tests/detect_test.sh` (1/0) |
| intel-showcase x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| intel-showcase x wsl2-intel | `tests/detect_test.sh` (1/0) |
| intel-telemetry x npu-path | `packaging/aur/PKGBUILD` (1/0) |
| inv-intelstar-numerics-contracts-realisations-carry-me x packaging-all | `.gitignore` (1/0) |
| invent-presence-gated-operating-points-human-at x packaging-all | `.gitignore` (1/0) |
| kserve-ovms x kuberay-hybrid | `README.md` (1/0) |
| kserve-ovms x nfd-k3s-bump | `README.md` (1/0) |
| kserve-ovms x report-bench | `README.md` (1/0) |
| kserve-ovms x wsl2-intel | `README.md` (1/0) |
| kuberay-hybrid x nfd-k3s-bump | `README.md` (1/0) |
| kuberay-hybrid x report-bench | `README.md` (1/0) |
| kuberay-hybrid x wsl2-intel | `README.md` (1/0) |
| nfd-k3s-bump x report-bench | `README.md` (1/0); `tests/detect_test.sh` (1/0) |
| nfd-k3s-bump x sim-hardware-matrix | `scripts/k3d-gpu` (0/1) |
| nfd-k3s-bump x wsl2-intel | `Dockerfile` (1/0); `README.md` (1/0) |
| npu-path x report-bench | `scripts/k3d-gpu` (1/0) |
| npu-path x sim-hardware-matrix | `tests/detect_test.sh` (1/0) |
| npu-path x wsl2-intel | `README.md` (1/1); `scripts/k3d-gpu` (2/0); `tests/detect_test.sh` (1/0) |
| packaging-all x upgrade-safety | `.github/workflows/auto-release.yml` (0/2) |
| report-bench x wsl2-intel | `README.md` (1/0) |
| sim-hardware-matrix x wsl2-intel | `tests/detect_test.sh` (1/0) |
| upgrade-safety x wsl2-intel | `scripts/k3d-gpu` (0/1) |

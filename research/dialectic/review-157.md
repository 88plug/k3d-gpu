# Review: PR #157 (88plug/k3d-gpu, claude/h50-gemma-h53), heads b8f47c3e and 23c024dd

Claim: #157 implements D1 (rule L3) and D2 (read-only model binds) and answers every refute
reason in dialectic-designs.json, and adds the M1 QAT grid snap. 23c024dd adds the H53 solo-hold
runner.

## Verdicts

| Part | Verdict | Confidence |
|---|---|---|
| PR as a whole | prototype: merge after the must-fixes; no H53 hold until its prereg lands | 0.75 |
| (1) Rule L3, `pairs.py`, for H50/H50o | confirmed (minor should-fixes) | 0.80 |
| (2) `K3D_GPU_VOLUMES` / `LIVE_MOUNT_B` / `STAGE_HOST_DIR` | prototype | 0.75 |
| (3) `qat_grid.py` snap and `export_qat_ov.sh` | prototype | 0.75 |
| (5) H53 solo runner (23c024dd) | research: mechanics prototype, inference unsound as registered | 0.75 |

## Checks I ran (evidence)

- Tests at b8f47c3e and 23c024dd, in a temp worktree: test_pairs 52 OK, test_agentbench 95 OK,
  test_stage 10 OK, test_h4345 45 OK, test_export_qat_ov 4 OK, test_qat_grid 4 OK (IRTest
  skipped without openvino), test_solo 6 OK, tests/detect_test.sh 1066 passed / 0 failed.
- IRTest with the pinned openvino 2026.4.1 in a scratch venv: passes.
- H50 is prospective: `ssh crescent ls ~/k3dgpu-bench/results/` lists only `H52`; no `H50*`
  and no `H53*` directory. No QAT IR is exported yet (no `*qat*ov*` dir under
  `~/k3dgpu-gemma/models`), so the snap has never run on a real export.
- Attribution: no Claude or Co-Authored-By lines in 737ce93b, b8f47c3e or the PR body.
- Version pins: openvino 2026.4.1, nncf 3.4.0, optimum-intel 2.2.0, optimum 2.3.0 and
  torch 2.14.1 match PyPI latest. transformers 5.5.4 is below latest (5.20.2), as disclosed
  (`optimum-intel<5.6`).
- Read-only bind (docker, alpine, privileged): `mount --rbind` of a `:ro` bind gives EROFS, so a
  kubelet hostPath (rbind) inherits ro. A privileged process can `mount -o remount,bind,rw` and
  write to the host. EAT's vllm chart pods (xeon and PR #21 xpu) are not privileged
  (runAsUser 0, no privileged, no CAP_SYS_ADMIN). clearHFLocks (`rm -rf /data/hub/.locks`)
  defaults to false.
- Crescent model dir: 775/664, devcloud-owned, readable by uid 1001. `/` is 95% full,
  195 GB free.
- QAT grid on the real checkpoint (`google/gemma-4-26B-A4B-it-qat-q4_0-unquantized@f1e06dc5`,
  HTTP range reads of layer 3, 4096 blocks per tensor, script `scratchpad/qatprobe.py`):
  - absmax/8 rel-RMS: 0.059-0.060 on attention, 0.085-0.088 on mlp/experts/router. This
    reproduces the ledger's "5-9%".
  - `snap_blocks` rel-RMS: 0.0018-0.0019.
  - The grid lies along the stored last (input) dim.
  - The grid multiple m = amax/d is mostly 5-7, not 8, so this is not a plain Q4_0 dequant.
    The m-search is needed, and its math is correct.
- Snap with no guard (pinned openvino):
  - An IR weight equal to 0.5 x the checkpoint (pre-check rel-RMS 0.50) is snapped anyway. The
    snapped IR differs from the original IR by rel-RMS 1.00.
  - A row-permuted weight (pre-check 1.37) is snapped too, rel-RMS 1.38 off the original IR.
  - The post-snap `check` would then read about 0.002, because it compares against the same
    reference it wrote from.
- `K3D_GPU_VOLUMES="/x/my models:/var/m"` parses to host `/x/mymodels`, mode ro. If that dir
  exists, it is mounted silently (reproduced).
- Hold-level drift vs the pair bootstrap. Simulation: 2 blocks x 12 tasks, true difference 0, a
  per-hold random shift in pass probability. `stats.paired_diff`'s 95% CI excludes 0 in 3.8%
  of runs at sd 0, 11.5% at sd 0.10 and 22% at sd 0.20.
- `solo.block_pairs` assigns blocks by position. Holds 1 AG, 2 B2 with no rows, 3 B2, 4 AG pair
  hold 1 with hold 3. Holds 1 AG with no rows, 2 B2, 3 B2 raise ValueError (reproduced).
- The seeded L3 orders over j<24: H50 13 A-first / 11 B-first, H50o 11/13, H53 9/15.

## (1) Rule L3: confirmed for H50/H50o

Sound for a within-hold crossover with a per-pair seeded coin:
- Under the sharp null, the sign of d_j is the order coin, so `sign_flip_p` is an exact
  randomization test.
- The paired bootstrap (2000, seed 40) over all complete pairs is the stated primary.
- The adjustment and the L2 subset are reported and never gated (pairs.py:604-656, 676-682).
- The decision rule is in code and ledger before any H50 data (verified above).

How #157 answers each D1 refute reason:
- Washout regression: answered. The adaptive settle stays (A3 item 6).
- Header row breaks readers: answered. `registration.json` is created with O_EXCL
  (pairs.py:530-551), and readers are untouched. No explicit `next_pair`-resume test exists
  with a registration present, but `next_pair` does not read the file. pairs-report with a
  registration is tested.
- git_sha liveness: answered. The comparison is {hyp, rule, seed}. Settle params are not in the
  registration (minor gap against the fix_scope).
- Post-treatment adjustment: answered. Reported only, with the reason written in A3 item 4.
- H49c as SUPPORTED: answered. No H49c row. The replay test (`test_replay_committed_h49b`) is
  labeled a code check, and the H49b row is unchanged, so it cannot leak into a claim.
- Amendment name collision: answered. It is "Crescent amendment A3", a distinct anchor.

Balance gate power at n of about 24:
- Sign-flip at alpha 0.01 has about 80% power only for a mean shift of about 0.75 SD of the
  per-pair difference.
- It catches gross confounding: L1's bg_cpu moved with B1 in 22/24 pairs, p of about 1e-4.
- It will not certify balance, and it should not be described as doing so.
- On raw `throttle_per_min` (12-3342/min, heavy-tailed) power is near nil.

Should-fix:
- S1 pairs.py:602-606: run the balance test on log1p(throttle), as the adjustment does, or on
  ranks.
- S2: report the period-balanced crossover estimate, (mean d over AB pairs + mean d over BA
  pairs)/2, beside the primary. It is never gated. The seeded coin leaves 9/15 order imbalance
  for H53's seed, and H49b showed a large position effect (B1 0/11 when second).
- S3 pairs.py:541-551: a crash between the O_EXCL create and the write leaves an empty
  `registration.json`. Every later hold then dies with JSONDecodeError, not HoldAborted. Write
  a temp file and `os.link` it into place (atomic exclusive publish), or treat an empty file as
  a mismatch with a clear message.
- S4 EXPERIMENTS.md:2203 still lists an `H53h-...` seed. 23c024dd removed H53h from ORDER_SEEDS.

## (2) Read-only binds: prototype

- Read-only end to end: yes for unprivileged pods. The k3d node bind is `:ro`, and the kubelet
  rbind inherits it (verified). It is not enforced against a privileged pod or the node
  itself, which can remount rw (verified). No pod on this path is privileged today.
- .ov_cache write-through: none. Only B is mounted. A1's IR and `.ov_cache` are still
  `docker cp -L`'d into the node layer (live.sh `stage_path`), and B3 pulls its own model. The
  mount cannot corrupt the shared dir.
- Provenance: checked once at setup (`models.sh` verify against the Hub sha256), never per hold.
  The bound source `~/k3dgpu-gemma/models/...` is a group-writable dir owned by another
  workflow (dl.sh, bw.sh), so a mid-campaign change would go unseen.
- Parsing: colons fail closed (mode check). There is no shell injection, because the args are an
  array element. Whitespace is stripped silently, not rejected. `@`, `;` and `,` inside a path
  are not rejected and would mis-parse k3d's `@filter` syntax.
- D2 refute reasons:
  - Overlay EINVAL: answered by not using an overlay.
  - Payoff unmeasured: answered by a different argument. Disk feasibility, 51.6 GB against the
    97% guard, is plausible: 195 GB free at 95%. No speed claim is made.
  - Lock: no setsid added. Answered by leaving the lock alone.
  - "Also ship" host-container reaping in hold.sh: not done (hold.sh has no `docker rm` of
    `^k3dgpu-`).

Must-fix:
- M1 scripts/k3d-gpu:4322 (`vol=${vol// /}`): reject any whitespace, or keep it verbatim. Never
  strip it. Also reject `@`, `;` and `=` in either path. Add detect_test cases for both.
- M2 live.sh (LIVE_MOUNT_B block, about lines 86-93): record per hold a manifest of the bound
  dir (path, size and mtime of every file, plus sha256 of `model.safetensors.index.json` and
  `config.json`). Abort if it differs from the setup-time listing written by `models.sh`. A
  full-weight sha per hold is too slow. size+mtime plus a setup sha is the cheap equivalent.

Next probe: one crescent wiring smoke with `LIVE_MOUNT_B=1`, run before any H50 hold:
- `kubectl exec deploy/<B> -- sh -c 'touch /data/<B_DIR>/x'` must give EROFS.
- B1 must load and answer `/v1/models`.
- Record `df` before and after.

## (3) QAT snap: prototype

- The math is correct. On the real checkpoint, snap error is 0.19% against 6-9% for absmax/8
  (measured above).
- Beyond the QAT grid, semantics change only when the IR constant is not the checkpoint tensor
  in the same layout. The tool has no guard for that case, and it rewrites the weight anyway
  (reproduced: a scaled or permuted IR weight is fully replaced).
- Honesty of the errors:
  - The pre-snap check is honest.
  - The post-snap check is circular. It compares the snapped weights with the reference they
    were written from, so it cannot detect a wrong mapping.
  - "Re-rounds every value" overstates it. Blocks with m=8 (6-62% of blocks) are reproduced by
    absmax/8.
- The OpenVINO path has never run on a real NNCF/optimum export. The IRTest builds its IR with
  the checkpoint's own names. The real constant names and the layout of the fused MoE
  `gate_up_proj` are unverified.

Must-fix:
- M3 qat_grid.py:233-236 (`cmd_snap`): skip and count any tensor whose pre-snap
  `rel_rms(w_ir, best)` exceeds a bound, e.g. 0.15 (the observed export error is at most
  0.09). Record per tensor in the JSON the pre error and rel_rms(snapped, original IR), and
  report those in manifest.json.
- M4 export_qat_ov.sh: fail the export when `matched` is 0, or when skipped/total is above a
  bound. Today a naming mismatch silently ships the unsnapped IR as A1's model.

Next probe: run the export once on crescent. Check that matched covers every
attention/mlp/expert tensor, that every pre error is at most 0.12, and that snapped-vs-original
is at most 0.12. Then send one greedy prompt to the snapped IR and the unsnapped IR and compare
their logits or outputs.

## (5) H53 solo runner (23c024dd): research

- Cross-hold pairing is unsound as analysed:
  - A pair's two attempts are 40+ min apart, in different clusters, under different tenant,
    thermal and time states.
  - Pairs inside a hold share that hold's state, so they are correlated. The pair-level
    bootstrap and sign-flip treat them as independent.
  - The real replicates are blocks: with 4 holds, n = 2.
  - Within a block, arm is confounded with hold position (first or second). Only the AG,B2 |
    B2,AG alternation cancels a linear drift, and only with equal pair counts.
  - Simulated type-I error: 5% nominal becomes 11-22% at a hold shift sd of 0.10-0.20.
- The balance gate becomes a hold-vs-hold test, and each solo pre-window carries its own arm's
  idle footprint: B2's 4 vLLM/oneCCL workers against AG's single OVMS pod. A structural
  "covariate tracks arm" INCONCLUSIVE is likely.
- `idle_cpu=0.0` is synthesized (solo.py:110), so the idle-arm gate passes by construction.
- Placeholders do keep B2 off the held cards:
  - #21 requests `gpu.intel.com/xe: <TP>` through the device plugin (`xpuDeviceResource`,
    template line 244). There is no privileged flag and no /dev/dri hostPath.
  - Each placeholder holds one unit, and the post-ready check aborts on any non-free BDF or a
    wrong count.
  - Gaps: `kubectl wait` failure is swallowed (live.sh solo block). A non-Ready placeholder
    leaves its card unheld, though the post-check still catches it. The BDF map assumes the
    plugin keeps host renderD names (believed, not verified here).
- "Stock": no. B2 renders #21's `Qwen/Qwen3.6-27B` profile on Qwen3.8-27B weights. That profile
  brings `--dtype float16` on bf16 weights, `qwen3_xml`, `--disable-sliding-window`,
  `--trust-remote-code`, `--enforce-eager` and no max-model-len. #21 is an unmerged PR head
  (c86c42b). 737ce93b named this the separate arm B2h, "nearest profile". At 23c024dd it is B2
  itself.
- Pre-registration: the H53 prereg was removed in b8f47c3e ("moves to a later PR"). At
  23c024dd the H53 code, seed and runner exist with no H53 row or section in EXPERIMENTS.md. The
  code cites "H53 pre-registration" as its authority (solo.py:1-10, live.sh solo block).

Must-fix:
- M5: no H53 hold until a pre-registration lands. Enforce it: `solo-hold.sh` or `pairs.design`
  must refuse H53 while EXPERIMENTS.md has no H53 row. That prereg must state:
  - cross-hold pairing;
  - block-level inference (resample or permute blocks, or a mixed model with a hold effect),
    with n = blocks named as the unit;
  - more blocks than 2 (e.g. at least 6 holds, AG B2 B2 AG AG B2);
  - B2 as "EAT PR #21 nearest profile (Qwen3.6-27B) on Qwen3.8 weights, fp16", a deviation from
    stock;
  - TP4 and the synthesized idle_cpu.
- M6 solo.py:94-99 (`block_pairs`): define block k as hold numbers (2k-1, 2k), as the docstring
  says, not as positions among holds with rows.
- M7 solo.py/agentbench `cmd_solo`: check the hold's arm against the registered sequence (AG,
  B2, B2, AG, ...) and abort on mismatch. Today any N/ARM pairing is accepted.

## Herlihy (shared state added)

- `registration.json`:
  - Its linearization point is the O_EXCL create; the write is not atomic with it (S3).
  - Writers serialize under the hold.sh flock, so there is no double-winner.
  - Readers that run without the lock (pairs-report) can see an empty file for a moment.
- `solo.jsonl`, `pairs.jsonl` and `attempts.jsonl`: append-only, and `read_jsonl` drops a
  trailing partial line. Safe for one writer under the flock.
- `gpu-state.txt` (gpu-serving.sh `state()`): truncate-write, not tmp+mv as D2 asked.
- No new lock or primitive. Progress is unchanged: blocking under the flock.

## Rule compliance

- Pre-register before measuring: OK for H50/H50o (A3 and M1 written before any data). Violated
  in waiting for H53 (M5).
- Pinned latest: OK.
- No Claude attribution: OK.
- Unledgered claims: the "5-9%" is reproduced; the "every value" wording is overstated. The
  solo docstring cites a prereg that does not exist (M5).

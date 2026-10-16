# E-core tool-result compression: pre-registration, mechanism, model, live protocol

Status: **static prototype; nothing here was measured on a serving engine.** Every
throughput or quality claim below is a prediction for a live probe the parent
session runs after wave 1 (see "Live protocol"). Tags: **[verified]** = read from
a primary source or computed from code in this branch; **[discovery]** = model
output or reasoning, not measured.

## Idea under test

Every tool result is appended to the agent's context and must be prefilled. Run a
compressor inside the tool step on the 16 E-cores (pod label
`k3d-gpu.io/cores=efficient`, NRI balloons) and hand the engine about half the
tokens. The claimed novelty is placement: spend E-core compute that OpenVINO leaves
idle for LLM serving on this part.

## Verdict at design stage (before any live run)

1. **Agents-at-SLO cannot separate compression from truncation.** At the same
   token budget, head+tail truncation gives the engine the same token count for
   free, so A@SLO is equal by construction (`Sim.test_trunc_equals_compression_by_construction`).
   The pre-registered "≥1.6x over the tuned baseline" arm is predicted **REFUTED**
   (predicted ratio 1.00 ± 0.05). Confidence it holds: 0.05. [discovery]
2. **Placement on the E-cores carries almost none of the value.**
   * The compressor that wins on retention here (`keeplines`, extractive,
     structure-aware) costs 2.6–5.6 ms p50 per 1600-word tool result in stdlib
     Python (max 12.9 ms, measured on this host under `nice 19` while wave 1 ran,
     20 fixtures per kind). That is noise against a 5 s tool wait on any core.
     [verified, host microbench]
   * A neural compressor (LLMLingua-2 small, mBERT-base, ~86 M non-embedding MAC
     per token) costs about 86e6 / 4.0e9 ≈ 2.2% of a Qwen3-4B prefill token. Run on
     the P-core engine instead of the E-cores, compressing a 1,300-token result
     costs the engine about 28 prefill-token equivalents while saving 650. The E-core
     placement saves those ~2% and nothing more. [discovery, arithmetic]
   * The E-cores are not free for a neural compressor. Peak int8 16 × 16 MAC/clk ×
     3.0 GHz = 0.77 TMAC/s (`ceiling.vnni_peak("E")`, uops.info VPDPBUSD ymm
     reciprocal throughput 2.0 on Gracemont, clock assumed). One 2,000-token result
     on one E-core at 30% efficiency takes about 12 s; on all 16 about 0.75 s.
     Utilization at the model's A@SLO is in the table below. [discovery]
3. **What can still separate the arms is quality at equal budget.** The new
   needle oracle (below) measures it. On this branch's fixtures `keeplines` beats
   truncation on pytest logs, grep hits and diffs, ties it on prose by design, and
   **loses badly on source files** (it promotes `def` lines and crowds out
   bodies). File reads should not be compressed at all: an agent edits by exact
   text match. [verified on fixtures; fixture-design bias noted below]

So the mechanism reduces to a known idea (extractive tool-output pruning; see
prior art) whose value is quality-per-token, not E-core capacity.

## Prior art (primary sources, arXiv API read 2026-10-16) [verified]

| Work | What it shows | Bearing |
|---|---|---|
| LLMLingua-2, arXiv 2403.12968 | token classification with a BERT-class encoder, task-agnostic compression | the proposed neural compressor |
| LongCodeZip, arXiv 2510.00446 | generic pruning (LLMLingua) "overlook[s] code-specific structures"; function/block-level selection by conditional perplexity | generic token dropping is weak on code |
| SWE-Pruner, arXiv 2601.16746 | goal-conditioned line selection by a 0.6 B "skimmer" for coding agents; 23–54% token reduction on SWE-bench Verified with success not lowered | closest prior art: small model prunes coding-agent tool output line by line |
| SWE-Pruner Pro, arXiv 2607.18213 | the agent's own hidden states label lines keep/prune | pruning moves inside the serving model |
| The Complexity Trap, arXiv 2508.21433 | simple observation masking halves cost and matches LLM summarization solve rate on SWE-bench Verified | cheap baselines are strong |
| Token Reduction Is Not Cost Reduction, arXiv 2607.12161 | 38.4% fewer tool-output tokens raised billed cost 6.8%; compression changed trajectories (more retrieval/turns); aggressive compression reduced patch application on a SWE-bench Go subset | per-turn token savings can be eaten by extra turns |
| Beyond Token Savings, arXiv 2609.32961 | on Terminal-Bench with Qwen, policies using ~1/3 the tokens can take 20–80% longer | the A@SLO-per-turn metric misses turn inflation |
| ACON, arXiv 2510.00615 | optimized guideline-driven compression of observations + history; 26–54% peak-token reduction | agent observation compression |
| DTOC, arXiv 2609.26121 | reversible placeholders, full output kept in external memory | alternative to lossy compression |

The E-core placement is the only unclaimed part, and point 2 above says it is worth
about 2% of served-model compute for a neural compressor and nothing for an
extractive one.

## Built in this branch

| File | What |
|---|---|
| `bench/tool_compress.py` | compressors `none`, `trunc` (head+tail), `keeplines` (trunc ordering + structure-aware salience; identical to trunc when the text has no cue), `randdrop` (ablation), `llmlingua2` (optional, `llmlingua` 0.2.2, torch CPU; not exercised); all share one word budget incl. elision markers; `fixtures` with planted facts; `retention`; HTTP `serve` |
| `bench/agent_trace.py` | `--tool-compress`, `--compress-ratio`, `--compress-url`, `--compress-tools`, `--only-graded`; per-turn `compress_ms`, `tool_words_in/out`, `compress_overrun_ms`, `answer_ok`; summary `e2e_s` p50/p95, `answer_ok_rate`. Compression time is taken out of the turn's `think_s`, so offered load per agent is unchanged unless it overruns |
| `bench/agent_trace_gen.py` | `needle` family: coding-shaped sessions whose tool results are realistic fixtures (pytest, grep, source, diff, prose), last turn asks about one planted fact, `expect_answer` grades the live reply. This is the task-success oracle the objection found missing (a narrow one: fact recall, not SWE task success) |
| `bench/compress_sim.py` | A@SLO model on `ceiling.simulate` with arms none / trunc / ecore / ecore_add / pcore / policy, plus E-cluster utilization (M/M/16) |
| `bench/test_tool_compress.py` | 28 unit tests (budget never exceeded for any method × kind × ratio, marker accounting, trunc-equivalence without cues, service, hook, needle family, sim invariants) |
| `examples/tool-compress/tool-compress.yaml` | the compressor as an E-core-pinned Deployment + Service (python 3.13.16-slim-trixie by digest), kubeconform-valid at k8s 1.34.1 |

```sh
python3 bench/test_tool_compress.py       # 28 tests, ~5 s
python3 bench/test_agent_trace.py         # 35 tests, ~19 s (unchanged behaviour)
python3 bench/tool_compress.py retention --n 200
python3 bench/compress_sim.py --quick     # minutes, stdlib
```

## Evidence retention on fixtures (n = 200 per kind, query = tool-call args) [verified on fixtures]

Fraction of planted facts whose answer string survives compression.

| Kind | ratio | trunc | keeplines | randdrop | keeplines, no query |
|---|---|---|---|---|---|
| pytest (2 facts: summary at tail + detail in body) | 0.3 | 0.62 | 1.00 | 0.28 | 1.00 |
| | 0.5 | 0.72 | 1.00 | 0.49 | 1.00 |
| grep -n | 0.3 | 0.32 | 1.00 | 0.28 | 1.00 |
| | 0.5 | 0.55 | 1.00 | 0.54 | 1.00 |
| source file | 0.3 | 0.27 | 0.01 | 0.13 | 0.00 |
| | 0.5 | 0.50 | 0.04 | 0.24 | 0.16 |
| | 0.7 | 0.66 | 0.36 | 0.42 | 0.52 |
| unified diff | 0.3 | 0.30 | 0.52 | 0.18 | 0.55 |
| | 0.5 | 0.54 | 0.79 | 0.33 | 0.82 |
| prose (negative control) | 0.5 | 0.50 | 0.50 | 0.47 | 0.50 |

pytest detail-only fact: trunc 0.40 at 0.5, keeplines 1.00. The pytest summary fact
survives truncation every time because pytest prints it last.

Bias I found and removed while building: the first diff fixture named its needle
`timeout_s`, which the salience regex matches, giving keeplines 1.00; renamed to a
neutral `<noun>_size`. Remaining bias: I wrote both the salience rules and the
fixtures. The pytest/grep wins rest on cues every real pytest/grep output carries
(`E   `, `AssertionError`, `file:line:def`), but the live needle run and, later, real
repository tasks are the only evidence that counts.

Predicted needle `answer_ok` (fact-weighted retention at 0.5, an upper bound on
answers): trunc 0.59, keeplines 0.72, none bounded by the model's recall at ~10k
words. Predicted gap keeplines − trunc: +5 to +13 pp, confidence 0.5. With
`--compress-tools run_tests,search,git_diff` (file reads and pages pass through)
keeplines should keep source/prose facts at the uncompressed rate. [discovery]

## Ceiling model (compress_sim.py) [discovery: model output]

`python3 bench/compress_sim.py --quick --seeds 1,2,3` (horizon 300 s, warmup 60 s,
tool wait exp(5 s), ratio 0.5). Cells: A@SLO for seeds 1/2/3, then turns/s at the
median A. rtx4000sff rows inherit hetero_tier.py's ASSUMED engine.

| Engine / trace | Arm | p95 10 s | p95 20 s | p95 30 s |
|---|---|---|---|---|
| cpu / coding | every arm | 0 | 0 | 0 |
| cpu / fanout | none | 0 | 2/4/4, 0.17 | 5/7/7, 0.25 |
| | trunc | 0 | 5/6/6, 0.25 | 10/11/13, 0.36 |
| | ecore | 0 | 5/6/6, 0.25 (= trunc) | 10/11/13, 0.36 (= trunc) |
| | pcore (compressor MACs on the engine) | 0 | 4/5/6, 0.24 | 11/12/13, 0.36 |
| | ecore_add (compress time added to wait) | 0 | 5/6/6, 0.25 | 10/11/12, 0.35 |
| | policy (half the results compressed) | 0 | 3/3/4, 0.14 | 5/9/10, 0.30 |
| rtx4000sff / coding | none | 0 | 3/5/5, 0.35 | 10/10/10, 0.49 |
| | trunc = ecore | 2/4/4, 0.38 | 11/11/12, 0.69 | 22/22/24, 0.88 |
| | ecore_add | 2/6/6, 0.51 | 11/14/16, 0.85 | 21/21/23, 0.88 |
| | policy | 0/1/2, 0.10 | 7/8/9, 0.53 | 11/12/15, 0.62 |
| rtx4000sff / fanout | none | 41/44/44, 3.1 | 98/99/100, 3.8 | 153/154/154, 4.1 |
| | trunc = ecore | 53/59/60, 4.1 | 121/123/125, 4.7 | 183/183/185, 4.9 |
| | ecore_add | 59/59/60, 4.0 | 124/124/124, 4.7 | 183/184/187, 4.8 |
| | policy | 47/47/52, 3.5 | 111/112/113, 4.3 | 167/169/169, 4.5 |

E-cluster utilization of a neural compressor (86 MMAC/token) at the trunc/ecore
A@SLO, efficiency 15% / 30% of int8 peak:

| Engine / trace | p95 10 s | p95 20 s | p95 30 s |
|---|---|---|---|
| cpu / fanout | — | 0.07 / 0.04 | 0.10 / 0.05 |
| rtx4000sff / coding | 0.36 / 0.18 | 0.67 / 0.34 | 0.85 / 0.43 |
| rtx4000sff / fanout | **1.14** / 0.57 | **1.30** / 0.65 | **1.36** / 0.68 |

Reading:

* **The falsification test passes for the objection.** `trunc` and `ecore` are the
  same cell in every row: the gain over `none` (CPU fanout 1.5x at 20 s and 1.6x at
  30 s; RTX coding 2.2x; RTX fanout 1.24x at 20 s) is the token count, which
  truncation delivers at zero cost.
* **Placement ablation:** `pcore` (compressor charged to the CPU engine in-turn)
  is inside the seed spread of `ecore` (20 s: 4/5/6 vs 5/6/6; 30 s: 11/12/13 vs
  10/11/13). The E-cores buy nothing measurable in this model.
* **Wait-inflation ablation:** adding the compress time to the tool wait raised the
  RTX coding median at 20 s from 11 to 14 agents with no change to the engine's
  work. Small-N closed-loop multiplexing and fewer offered turns per agent, not
  compression. Live A@SLO must therefore be read with turns/s and with the
  compress time held inside the wait (the replayer's default).
* **Policy** (file reads uncompressed) gives up most of the token gain: about
  half to two-thirds of trunc's improvement over none on the RTX traces.
* A neural compressor overloads the E-cluster on RTX fanout at 15% efficiency
  (utilization 1.14–1.36, unbounded queue) and runs at 0.57–0.68 at 30%. That is
  the only regime where E-core supply binds, and it binds against the idea.
* CPU coding stays at 0 agents at every SLO in every arm (as the idea said).

## Pre-registered acceptance criteria

The target metric is agents per box at a p95 agent-turn SLO. Tuned baseline:
head+tail truncation to the same budget, same engine, same config. Secondary
baseline: uncompressed.

| # | Claim | Pass | Refuted if | Prediction | Conf. |
|---|---|---|---|---|---|
| C1 | E-core compression raises A@SLO over truncation | A(keeplines) ≥ 1.6 × A(trunc) at p95 20 s | ratio < 1.3 | ratio 1.00 ± 0.05 → REFUTED | 0.95 that it is refuted |
| C2 | Token-budget parity holds between arms | mean `prompt_tokens` of trunc and keeplines within ±5% | outside ±5% (then budgets must move to tokens before C1 means anything) | within ±3% (code lines tokenize denser than log lines) | 0.7 |
| C3 | Compression at 0.5 raises A@SLO over uncompressed on CPU fanout | A(trunc) ≥ 1.3 × A(none) at p95 20 s | < 1.1 | 1.5 (model: 6 vs 4 agents at 20 s, 11 vs 7 at 30 s) | 0.6 |
| C4 | keeplines keeps facts truncation loses | needle `answer_ok`: keeplines − trunc ≥ +5 pp, n ≥ 100 graded per arm | ≤ 0 pp | +5 to +13 pp | 0.5 |
| C5 | keeplines does not cost quality vs uncompressed when file reads pass through | `answer_ok`(keeplines, policy) ≥ `answer_ok`(none) − 3 pp | < none − 5 pp | within ±3 pp | 0.5 |
| C6 | The extractive compressor needs no dedicated cores | `compress_ms` p95 ≤ 50 ms in the E-core pod; P-core decode tok/s with the pod busy within run-to-run spread | p95 > 250 ms, or decode drop outside spread | p95 ~10 ms; no measurable decode change | 0.8 |
| C7 | Placement matters (E-core vs P-core) for a neural compressor | A(pcore) < A(ecore) by ≥ 10% | difference within ±5% | within the noise: ~2% compute | 0.8 that it is not separable |

## Live protocol (parent runs it serially after 17:00)

Preconditions: wave 1 finished and its pods gone; `k3d-gpu up` cluster with NRI
balloons; OVMS 2026.4.1 Qwen3-4B int4 CPU servable from `claude/agent-stack`,
pod labelled `k3d-gpu.io/cores=performance`, same config as H2. The dGPU arm
runs only if a dGPU servable already exists in that stack; otherwise mark it
NOT RUN (do not stand one up for this). Fill the EXPERIMENTS.md Reproduce header
for every run. One run at a time; nothing else on the box.

```sh
B=bench; R=runs/tc; mkdir -p $R
URL=http://127.0.0.1:8000/v3/chat/completions; M=<ovms model name>

# 0. Compressor pod on the E-cores, prove placement.
kubectl apply -f examples/tool-compress/tool-compress.yaml
kubectl -n tool-compress create configmap tool-compress-src --from-file=tool_compress.py=$B/tool_compress.py
kubectl -n tool-compress rollout status deploy/tool-compress
kubectl -n tool-compress exec deploy/tool-compress -- grep Cpus_allowed_list /proc/1/status   # E-core ids only
kubectl -n tool-compress port-forward svc/tool-compress 8090:8090 &
CURL=http://127.0.0.1:8090/compress

# 1. Traces.
for s in 0 1 2 3 4 5 6 7 8 9; do python3 $B/agent_trace.py gen needle -o $R/needle-s$s.json --seed $s --scale 2.5; done
python3 $B/agent_trace.py gen fanout -o $R/fanout.json --scale 2     # 48 sessions
python3 $B/agent_trace.py gen coding -o $R/coding.json --scale 3     # 12 sessions

# 2. P-TC1 quality (C4, C5): only the graded turn is sent; 10 seeds x 10 sessions = 100 graded per arm.
#    Arm order per seed rotates (ABCD, BCDA, ...) to spread drift.
ARMS="none trunc keeplines keeplines-policy"
for s in 0 1 2 3 4 5 6 7 8 9; do
  for a in $(echo $ARMS | tr ' ' '\n' | awk -v k=$s '{x[NR]=$0} END{for(i=0;i<NR;i++) print x[(i+k)%NR+1]}'); do
    m=${a%-policy}; extra=""; [ "$a" = keeplines-policy ] && extra="--compress-tools run_tests,search,git_diff"
    python3 $B/agent_trace.py run $R/needle-s$s.json --url $URL --model $M --only-graded \
      --max-concurrency 2 --time-scale 0 --tool-compress $m --compress-ratio 0.5 \
      --compress-url $CURL $extra --out $R/q-$a-s$s
  done
done

# 3. P-TC2 A@SLO (C1, C2, C3): fanout on CPU at N in {2,4,6,8}, arms none/trunc/keeplines,
#    ABCCBA order, 2 blocks -> n = 4 runs per (arm, N). time-scale 1 (real tool waits).
for N in 2 4 6 8; do for blk in 1 2; do for a in none trunc keeplines keeplines trunc none; do
  python3 $B/agent_trace.py run $R/fanout.json --url $URL --model $M --max-concurrency $N \
    --time-scale 1 --tool-compress $a --compress-ratio 0.5 --compress-url $CURL \
    --out $R/slo-fanout-N$N-$a-b$blk-$(date +%s)
done; done; done
#    Coding on CPU: one block at N=2 only (the model predicts 0 agents at a 20 s SLO; this checks it).

# 4. P-TC3 interference (C6): reuse H3 arm (b) with the compressor as the neighbour:
#    llm_load.py (claude/report-bench) c=1 and c=4, 5 runs each, pod idle vs pod looping
#    compress requests from 4 client threads (keeplines, 1600-word fixtures), ABAB.
```

Scoring:

* A@SLO for a (arm, N) cell passes when the median over its runs of per-run
  `e2e_s.p95` ≤ SLO (20 s primary, 10 s and 30 s reported). A@SLO = largest N
  that passes. Report also turns/s (`requests / wall_s`) so a slower tool step
  cannot pass for capacity.
* C2 uses mean `prompt_tokens` over all turns per arm.
* C4/C5: `answer_ok_rate` pooled per arm with a Wilson 95% interval; the gap is
  significant only if the paired-by-session difference excludes 0 (sessions are
  identical across arms by construction).
* Turn inflation is not measurable here (teacher-forced history). The 2607.12161
  and 2609.32961 results say it is real; a closed-loop real-repo benchmark is a
  prerequisite before any claim of agents-per-box gain from compression goes in
  the README.

| Probe | Claim | Measure | Pass | Fail |
|---|---|---|---|---|
| P-TC1 | C4 | answer_ok keeplines − trunc | ≥ +5 pp, CI excludes 0 | ≤ 0 pp |
| P-TC1 | C5 | answer_ok keeplines-policy − none | ≥ −3 pp | < −5 pp |
| P-TC2 | C1 | A@SLO keeplines / trunc, p95 20 s | ≥ 1.6 | < 1.3 |
| P-TC2 | C2 | prompt_tokens trunc vs keeplines | within ±5% | outside ±5% |
| P-TC2 | C3 | A@SLO trunc / none, CPU fanout | ≥ 1.3 | < 1.1 |
| P-TC3 | C6 | compress_ms p95; decode tok/s delta | ≤ 50 ms; inside spread | > 250 ms; outside spread |

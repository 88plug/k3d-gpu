# Energy-in-the-loop MCP: per-call joule receipts, baggage budget, receipt-driven steering

Status 2026-10-16: **prototype + sim only.** No hardware was touched (live benchmarks running).
Acceptance criteria were committed in `PREREG.md` (31205a6) before the simulator existed.

## Verdict

| ID | Claim | Result | Verdict |
|----|-------|--------|---------|
| H1 | Arithmetic bound >= 20 % (LLM share >= 0.75) | Oracle bound, no SLO, vs best-latency: max **1.5 %** at share 0.75, 0.7 % at 0.90, 0.4 % at 0.95. Even at share 0.60 the max is 2.8 %. | **Refuted.** Below 10 %, so the 20 % claim is impossible by arithmetic. |
| H2 | >= 20 % vs fair static min-joule-under-SLO | Median **-0.2 %** (steering costs joules), max +0.7 % over 1728 draws. | **Refuted** (max < 10 %). |
| H2b | vs best-latency strawman | Median -0.2 %. Strawman gap is ~0 here: best-latency and fair pick the same variants in most draws. | Informational. |
| C1 | Stationary control, margin <= 2 % | Max margin -0.06 %. | **Pass.** Sim grants no free win. |
| H3 | Non-stationary, >= 5 % in >= 80 % of draws | 0 % of draws pass. Median -0.18 %; oracle median +0.46 %. | **Refuted.** |
| H4 | Budget = 0.7 x fair median, >= 90 % within | Median 24 % within. The min-joule floor (oracle, no SLO) already exceeds the budget in a median 76.5 % of tasks. | **Refuted by arithmetic**, not a policy failure: the LLM joules alone exceed the budget. |
| H5 | Receipt wrapper p99 <= 2 ms (mock ledger) | 0.026 ms added p99 (unit test, 3000 calls). | **Pass** for the wrapper. The real ledger query is a live probe. |
| H6 | Confidence gate no worse than ungated | Gated worse in 22 % of draws (<= 50 %). | **Pass.** Effect is tiny either way. |

The objection holds on all four points. What survives is the receipt and baggage plumbing as an
observability feature: cheap (H5), honest about what is modelled (confidence and per-domain
`sources`), and spec-conformant. The closed-loop steering win does not survive.

## Why steering cannot win here

1. **Steerable share.** Tools are 5 to 40 % of task joules across the grid (LLM generation is
   never a tools/call). At most, the best variant of a tool saves a fraction of that tool's
   joules, so the product stays under 3 %.
2. **Fair baseline.** An offline sweep under representative conditions already picks the
   min-joule variant. Runtime steering can only add the part due to change within a run.
   Even a perfect-information oracle gains a median 0.2 % over the fair static.
3. **Learning cost exceeds headroom.** EWMA + 5 % epsilon spends joules exploring bad variants.
   One contended NVIDIA sample inflates the latency EWMA, and the steerer then sits on the
   iGPU for a long time. Post-hoc positive control C3 (device-only tools, LLM share 0.05,
   amp 0.3 to 0.6, long episodes): oracle **+4.8 to +5.7 %**, steering **-7 to -16 %**,
   time-based reprobe **-3 to -14 %**. The policy class, not the sim, is the limit: the sim
   shows a win to an oracle, and the receipt-history steerer cannot capture it.
4. **Host fact (read-only, 2026-10-16):** `/sys/class/powercap/intel-rapl:0` on this
   i9-13900T reports `long_term` = `short_term` = 117 000 000 uW (PL1 = PL2 = 117 W). The
   "35 W" T-SKU rating is not the enforced limit, so power-limit throttle episodes (the main
   source of non-stationarity in the sim) are likely rarer than modelled. This weakens H3
   further. Not verified under load.

## What was built

- `receipts.py` (stdlib only):
  - W3C Baggage parse/format that keeps foreign members and properties.
  - Budget read and charge on `k3dgpu.joule_budget` / `k3dgpu.joules_spent`.
  - `apportion_sigma`: background-power term, which scales with T, plus the RAPL update-edge
    term. `confidence`: 1/(1+relative error).
  - `build_receipt`, which tags each domain `measured` or `modelled`.
  - `Steerer`: EWMA, latency SLO, idle-power-aware cost, budget-pressure mode, epsilon,
    optional reprobe, confidence gate.
  - `EnergyMiddleware`: wraps tools/call. It writes the receipt under `_meta["dev.k3dgpu/energy"]`,
    updates `_meta["baggage"]`, propagates through a contextvar for nested calls, and accepts
    a client override at `_meta["dev.k3dgpu/variant"]`.
  - `MockLedger`.
- `test_receipts.py`: 19 unit tests, including _meta key-name conformance to the 2026-07-28
  spec grammar and the H5 overhead test.
- `enersim.py`: trace-replay sim; `results.json` is the full grid output.
  - Policies: best-latency static, fair static (task-level coordinate descent on a separate
    calibration replay), per-tool and nominal fair statics, steer plus 5 ablations, oracle,
    and oracle without SLO.
  - Grid, 1728 draws: LLM share {0.60, 0.75, 0.90, 0.95}, E/P ratio {0.7, 1.0, 1.3},
    idle {12, 25, 45} W, amp {0, 0.15, 0.30}, sigma_bg {0.5, 5} W, slack {0.1, 1.0},
    episode scale {1, 4}, seeds {0, 1}.
- `tool-variants.yaml`: one tool as four Deployments + Services (P balloon, E balloon,
  `gpu.intel.com/i915`, `nvidia.com/gpu`), keyed on the shipped `k3d-gpu.io/cores` labels in
  `share/nri-balloons.yml`. YAML-parsed only; no cluster was available for a dry run.

Reproduce: `cd research/energy-receipts && python -m unittest test_receipts && python enersim.py --procs 3`
(~9 min on 3 niced cores).

## Deviations from PREREG.md (declared, not hidden)

- sigma_bg grid is {0.5, 5} instead of {0.5, 2, 5}, and seeds are 2 instead of 5, to keep
  the CPU load low while benchmarks run. slack {0.1, 1.0} and episode scale {1, 4} were
  added as extra perturbations; both make a win easier to reach.
- C2 positive control was added after pre-registration and before results (it is in code).
  C3 (stronger positive control, amp up to 0.6) is post hoc and exploratory.
- `steer_reprobe` and `steer_from_fair` are post-hoc variants, labelled in code.
- Budget pressure in the replay is `budget < EWMA task joules`. With the budget at 0.7 x
  median, that puts nearly every task in min-joule mode, which is the most favourable
  setting for H4. H4 still fails, because of the floor.
- Fixed after the grid ran: `run()` now rejects amp >= 2/3, where the P-core throttle factor
  goes negative. No grid draw used amp > 0.3.

## Live probes (after benchmarks end; need an exclusive window)

1. `cat /sys/class/powercap/intel-rapl:*/name` (done: package-0, core, uncore, psys; no P/E
   domain; `energy_uj` is mode 0400 root). Then sample `energy_uj` at the shortest stable
   interval under idle to measure the update period and the idle-noise sigma_bg in watts.
2. P vs E joules per op on this host: one fixed CPU kernel pinned with `taskset` to a P core,
   then to an E core, at 1/2/4 threads. Package + psys energy for each. This sets the real
   E/P ratio, including race-to-idle (the objection's point 2).
3. Steerable share: replay the agent-trace benchmark with receipts on and policy off. Report
   tool joules / task joules (psys + NVML). If it is < 10 %, stop here: H1 is closed on hardware.
4. Receipt resolution: inject a known synthetic load (fixed-watt busy loop of 20/50/200 ms)
   into a tool pod while the background runs, and compare the receipt with the injected joules.
   This measures the real `confidence` calibration.
5. Ledger query overhead: p99 of the real window query (RAPL + perf cpu_core/cpu_atom + NVML),
   added to the 0.026 ms wrapper. Target <= 2 ms total.
6. _meta passthrough: send a tools/call through langchain-mcp-adapters and openai-agents, and
   check whether `_meta["dev.k3dgpu/energy"]` and the updated baggage reach the agent loop.
7. PL1/PL2 under load: watch `constraint_*_power_limit_uw` and the package power during a
   sustained P-core load to see whether the 117 W firmware limit is what actually holds.

## Provenance partition

**Shipped (verified from primary source this pass):**
- MCP 2026-07-28 reserves `_meta` keys `traceparent`, `tracestate`, `baggage` for OpenTelemetry
  propagation, in W3C Trace Context / W3C Baggage format:
  `docs/specification/2026-07-28/basic/index.mdx` lines 358, 421-424, and the changelog
  `docs/specification/2026-07-28/changelog.mdx` line 33 citing SEP-414 (PR #414).
  modelcontextprotocol/modelcontextprotocol main, commit "Add 2026-07-28 MCP specification",
  dated 2026-07-28. This resolves objection point 4: SEP-414 is confirmed.
- The same file defines the `_meta` prefix grammar, with reverse-DNS recommended and second
  label `mcp`/`modelcontextprotocol` reserved. `dev.k3dgpu/` conforms; the idea's
  `k3dgpu.dev/` form would be non-reverse-DNS, so the code uses `dev.k3dgpu/energy`.
- W3C Baggage (w3.org/TR/baggage): limits of 64 list-members and 8192 bytes.
- nri-plugins balloons P/E pinning in k3d-gpu: `share/nri-balloons.yml` (chart v0.14.0).
- Host RAPL domains package-0, core, uncore and psys, with no P/E split. Read from sysfs on
  this host 2026-10-16. This confirms objection point 3: `cpu_p`/`cpu_e` can only be modelled.

**Announced:** nothing found that ships energy fields in MCP `_meta`.

**Paper-only (titles verified on arxiv.org/abs this pass; contents per the earlier provenance pass):**
- Spanergy, arXiv 2607.24902: energy-aware distributed tracing, post hoc.
- Agentic CPU-GPU Scheduling, arXiv 2607.22242: per-tool CPU/GPU placement, latency/VRAM objective.
- Energy per Successful Goal (A-LEMS), arXiv 2605.22883, 2026-05-20: goal-level accounting.
- Carbon-Aware Routing for Function Calling, arXiv 2609.13559: tier routing.
- Not re-verified this pass: PRISM 2608.21910, 2609.23085, Agent.xpu 2506.24045, and FLEET
  (primary source never located).

**This work (new, sim/unit-test only):** per-call receipts on the MCP result surface with
per-domain measured/modelled tags and a confidence field; a joule budget in reserved baggage
decremented across nested hops; and a falsification showing that receipt-history steering
loses to a fair static on a host of this shape. None of this is measured on hardware.

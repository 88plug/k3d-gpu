# Execution-path witness

`scripts/k3d-gpu-witness` checks which silicon produced a benchmark number. A
same-pod sidecar records raw kernel counters during the run and evaluates a rule
whose sha256 was fixed beforehand. `verify` recomputes the verdict from the raw text.
A silent fallback to CPU ends as `FAIL`, with the reason attached.

Status: a prototype. All results below come from a simulator. No live campaign has run yet.

## What it reads

| Source | Keys | Per |
|---|---|---|
| i915 fdinfo | `drm-engine-render`, `drm-engine-compute` (ns) | DRM client, de-duplicated by `(drm-pdev, drm-client-id)` |
| xe fdinfo | `drm-cycles-rcs/ccs` / `drm-total-cycles-rcs/ccs` | DRM client |
| NVIDIA | `nvidia-smi pmon -s u` SM% (1 s refresh) | host PID, needs a host-to-pod pid map |
| `/proc/<pid>/stat` | utime+stime (10 ms ticks) | every pod process |

Primary sources: docs.kernel.org/gpu/drm-usage-stats.html (keys, de-dup rule,
counter regressions hold the last larger value) and
docs.kernel.org/gpu/xe/xe-drm-usage-stats.html. The i915 parser is pinned to
fdinfo captured on the dev host (kernel 6.6, UHD 770). The xe parser is pinned to
the kernel doc example. The fixtures are in `tests/fixtures/witness/`.

Reading another container's `/proc/<pid>/fdinfo` needs ptrace read access
(`fs/proc/fd.c`: `ptrace_may_access(task, PTRACE_MODE_READ_FSCREDS)`). That is why
the example sidecar runs with `CAP_SYS_PTRACE` only.

## Rule (`share/witness/rule.json`, sha256 `596f9f88…cd223f97`)

The rule computes, for each sample interval `i`:

- `busy_i`: engine-busy seconds on the claimed device
- `excess_i = max(0, cpu_i - c0)`, where `cpu_i` counts the cores used by processes that hold a client on the claimed device

It then takes `S = Σexcess / (Σexcess + Σbusy)`. The verdict is FAIL when any of these holds:

- `S > 0.10`
- engine busy is < 5% of active time
- no process holds the claimed device
- another device logged more engine time than the claimed one

The rule has two elements, each with an ablation:

- **E1, per-interval excess.** Agent tool waits cannot absorb fallback CPU. Ablation A1 computes the excess over the whole run instead.
- **E2, engine-process CPU scope.** Separate in-pod tool processes are not counted. Ablation A2 uses the whole pod's CPU instead.

## Pre-registered result (docs/witness-prereg.md, committed before code)

Run: `tests/witness_sim.py --runs 200 --seed 1`. Full output is in `docs/witness-sim-results.txt`.
The simulator emits only raw counter text: fdinfo, `/proc` stat in 10 ms ticks, and pmon rows that refresh once per second.
The witness parses that text through the same code it uses on a host.

Each cell shows the share of runs the rule flagged. On the CLEAN class, a flag is a false flag.

| rule | CLEAN | PARTIAL | FULL | WRONG |
|---|---|---|---|---|
| witness | 0.0% | 91.0% | 100% | 100% |
| A1 (no E1) | 0.0% | 29.5% | 100% | 100% |
| A2 (no E2) | 45.5% | 95.5% | 100% | 100% |
| B1 any engine time | 0.0% | 0.0% | 100% | 100% |
| B2 busy≥0.7·wall, pod CPU<1.5 | 96.5% | 100% | 100% | 100% |

| criterion | target | result | status |
|---|---|---|---|
| C1 detection on PARTIAL+FULL+WRONG | ≥95% | 97.0% pooled | met, but see note |
| C1 false flags on CLEAN | ≤2% | 0/200 (Wilson 95% CI 0–1.9%) | met in the simulator only, see limits |
| C2 B2 false flags | ≥20% | 96.5% | met |
| C3 B1 detection on PARTIAL | ≤10% | 0% | met |
| C4 A1 PARTIAL drop | ≥20 pts | −61.5 pts | met: E1 does the work |
| C5 A2 false-flag rise | ≥10 pts | +45.5 pts | met: E2 does the work |
| C6 tampered bundles rejected | 100% | 100% of the tamper cases in `tests/witness_test.py` | met |
| Falsifier: detection < 90% or false flags > 5% | n/a | PARTIAL alone 91.0% (CI 86.2–94.2%) | not falsified, margin thin |

**Note on C1.** The pooled criterion was written too generously. FULL and WRONG
are trivial, as the refuter pointed out, so pooling lifts the number. On the hard
class alone (PARTIAL) the witness reaches 91.0%, with a CI that straddles the 90% line.
The honest reading: interval scoping turns 0% (B1) into roughly 90% on partial
fallback, and it fixes the false flags of a fixed busy/wall rule. It does not reach
95% on partial fallback.

### Perturbation sweeps (50 runs/class)

| sweep | witness FF | witness PARTIAL | reading |
|---|---|---|---|
| seeds 2 / 3 | 0% / 0% | 90% / 90% | stable |
| period 50 / 250 ms | 0% / 0% | 92% / 92% | stable |
| period 1 s / 2 s | 0% / 0% | 68% / 52% | predicted: long intervals dilute bursts |
| fallback on 1 thread | 0% | 0% | predicted: one thread sits inside `c0` |
| fallback f 0.02–0.10 | 0% | 10% | small offload splits are missed |
| runtime spin up to 2 cores | **34%** | 92% | false flags appear when host-side runtime CPU tops `c0` |
| decode busy 0.05–0.25 | 0% | 86% | very memory-bound decode still passes |
| tool inside the engine process | **40%** | 94% | E2 cannot separate in-process tool CPU |

**What the simulator grants.** The 0% CLEAN false-flag rate holds because the
simulator assumes host-side runtime CPU of at most about one core per interval. The
spin sweep shows that once the assumption breaks, false flags rise to 34%. A real
runtime's host-thread usage (llama.cpp threadpool polling, Level Zero or CUDA
spin-wait, Python agent loops in the same process) is the main open quantity. It can
only be measured live, so `c0` has to be calibrated per runtime from clean live runs
before any rate is claimed.

**What the verifier proves.** `verify` catches a rule edited after the run (given
the pre-registered sha256) and a recorded verdict that disagrees with its raw
counters. It cannot catch raw counters fabricated to match a verdict: the bundle is
not signed and the counters are not attested. Anyone with write access to the bundle
can forge a consistent one.

## Live probes (after the running benchmarks finish)

Host: i9-13900T, UHD 770 (i915, `0000:00:02.0`), 2× NVIDIA (driver 590.48).

1. Calibrate `c0` on clean runs. Run llama.cpp SYCL or Vulkan with full offload
   (`-ngl 999`) on the UHD 770, with the witness on the host:
   `k3d-gpu-witness sample --rule share/witness/rule.json --claim 0000:00:02.0 --out clean.json`.
   Record the per-interval `cpu_engine` distribution across 10 agent-trace runs with tool gaps.
   The 99th percentile becomes the candidate `c0`. Pre-register it before step 2.
2. Partial fallback. Run the same model with `-ngl` set to 75%, 50% and 25% of layers. Use 10 runs each.
3. Full fallback. Run `-ngl 0` while the Vulkan or SYCL device stays open. Also run with the render group removed
   (expected: a loud failure, not a silent one; record it).
4. Wrong device. Claim `0000:00:02.0` and run on NVIDIA through `GGML_VK_VISIBLE_DEVICES`.
5. NVIDIA. Run llama.cpp CUDA at the same `-ngl` grid, with `--nvidia-pmon` and a pid
   map built host-side from `/proc/<hostpid>/status` NSpid. Measure the cost of the
   1 s pmon resolution against the 1 s period sweep above.
6. OpenVINO GPU with AUTO and `enable_startup_fallback` on, as the one mixed OV case.
   Compare the `ov::execution_devices` report with the witness verdict.
7. In a k3d pod: apply `share/witness/witness-sidecar.yml` with the configmap and
   confirm fdinfo is readable across containers with `CAP_SYS_PTRACE` only.

Pass bar for the live claim: detection on the `-ngl` partial grid ≥90%, and ≤5% false flags on
clean runs, using the `c0` fixed in step 1. Not testable here: xe (no xe device),
NPU (ivpu has no per-client engine keys in any source found), Gaudi.

## Provenance

- **Known:** per-client DRM fdinfo engine accounting and its de-dup rule (kernel docs);
  fdinfo readers (intel_gpu_top, nvtop, qmassa); per-process fdinfo exporters and
  fdinfo-to-pod attribution (MaxRink/intel_gpu_exporter, mnlipp/intel_xe_exporter,
  joryirving/containers PR #962); NVML per-process utilization; OpenVINO
  `ov::execution_devices`; the "GPU busy vs wall vs CPU" audit as troubleshooting
  advice; pre-committed analysis hashes and recomputing verifiers ("Freeze the
  Benchmark Card", arXiv 2606.27934, 2608.26183).
- **No prior art found:** a pod-scoped verdict that combines per-interval CPU excess
  with CPU counted only for processes holding the claimed device (E1+E2), applied to
  partial fallback and validated against ablations. Also not found: a hashed rule whose
  verdict a verifier recomputes from raw fdinfo and `/proc` text.
- **Unsearched / gaps:** GitHub code search (rate-limited 403), the arXiv API (429),
  Google Patents (USPTO 12676749 seen by title only), and llama.cpp or IPEX-LLM
  issue trackers for in-tree offload-verification tooling.

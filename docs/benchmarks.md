# Benchmarks: k3d-gpu against other stacks

Head-to-head runs made with `k3d-gpu bench --vs <stack>`. Each row is one ledger
hypothesis in [`EXPERIMENTS.md`](../EXPERIMENTS.md); its run section there holds the
Reproduce header, the raw-results sha256 and the exact command. A row backs a claim
only when its verdict is SUPPORTED.

Protocol: one client (`bench/llm_load.py`), one prompt set, temperature 0,
`ignore_eos` sent to every arm, warmup requests discarded, interleaved rounds
(A,B then B,A), one request in flight, energy from the RAPL package counter.
Competitors run their pinned image and pinned weights (`bench/competitors.lock`;
the served file's sha256 is checked before measuring) with vendor defaults, same
model family and 4-bit tier.

Known asymmetries, recorded in every run section: Ollama's OpenAI layer has no
`ignore_eos`, so a round where either arm stops early is INCONCLUSIVE; the k3d-gpu
arm is reached through `kubectl port-forward` (an API-server stream) while the
competitor is reached through docker-proxy, so tunnel latency and buffering land
on the k3d-gpu arm only.

| Ledger | Competitor | Tier | Decode tok/s (k3d-gpu / competitor) | J/token (k3d-gpu / competitor) | Verdict |
|---|---|---|---|---|---|
| [ledger:H30] | ollama | 4-bit (GGUF Q4_K_M) | 11.06 / 7.02 | 8.52 / 14.35 | SUPPORTED |
| [ledger:H31] | llamacpp | 4-bit (GGUF Q4_K_M) | 15.63 / 10.26 | 6.06 / 9.28 | SUPPORTED |
| [ledger:H32] | ovms | 4-bit (OpenVINO int4) | 16.26 / 10.24 | 5.92 / 9.70 | INCONCLUSIVE |

Why each verdict:

- [ledger:H30] (ollama): k3d-gpu ahead by the margin in every round (k3d-gpu/competitor ratios 1.55, 1.58, 1.57).
- [ledger:H31] (llamacpp): k3d-gpu ahead by the margin in every round (k3d-gpu/competitor ratios 1.52, 1.54, 1.50).
- [ledger:H32] (ovms): load unmatched under the matched-load amendment A2 (throttle events per minute), so the per-round ratios 1.61, 1.58, 1.59 do not decide.

Run one and regenerate this page (rows without a result file stay PENDING):

```bash
k3d-gpu up && k3d-gpu agent up
k3d-gpu bench --vs ollama --id H30 --budget 900 --out bench-H30
bench/vs.py docs --out docs/benchmarks.md bench-H*/result.json
```

<!-- bench/vs.py docs keeps everything below this line -->

## Busy-box methodology (background load)

Real Intel boxes are busy: agents, builds, browsers and other tenants share the
CPU. A probe result has to hold under that, so every probe runs its arms under
one controlled background load from `bench/load/load.py`. What spoils a comparison
is load that drifts between arms, not load that is high, so:

- every arm runs under the SAME level, interleaved (`load.py order A B --reps 3`
  gives `A B A B A B`);
- the background is sampled per arm and recorded: 1-min loadavg, busy share of the
  load CPUs, package W (RAPL `energy_uj`), `core_throttle_count` events per minute,
  swap used and swap growth;
- a run is INCONCLUSIVE only when `load.py matched` finds the arms' load unmatched
  under the pre-registered tolerance below, never because the load is high;
- the `typical` level is the headline; `heavy` is the robustness check.

Levels (N = the load CPU set; the image is stress-ng 0.22.01 pinned by digest,
the same pin as `bench/noisy.sh`):

| Level | CPU workers | Memory bandwidth | Page-cache churn |
|---|---|---|---|
| `none` | none | none | none |
| `typical` | N `--cpu` workers at `--cpu-load 30` | `max(1, N//8)` `--stream` workers, docker `--cpus` = 0.5 per worker | 1 `--hdd` worker (write+unlink 256m), docker `--cpus 0.25` |
| `heavy` | N `--cpu` workers at `--cpu-load 100` | `max(1, N//4)` `--stream` workers, no quota | 2 `--hdd` workers, no quota |

`typical` keeps about `0.37` of the load CPUs' time busy on a 16 E-core part
(`load.duty()`; between `0.30` and `0.40` for 8 to 64 load CPUs) plus DRAM
traffic: a busy dev or agent box. `heavy` saturates those CPUs: a noisy neighbour.

Load CPUs (`load.py cpus`): on a hybrid part the E-cores from
`/sys/devices/cpu_atom/cpus`; otherwise the highest-numbered half of the physical
cores, whole cores with their SMT siblings, so the load never shares a core with a
probe on the low cores. `--cpus LIST` overrides.

Matching rule, fixed before any probe uses it (`TOLERANCE` in `load.py`). For each
metric the spread of the per-arm means (max minus min over all arms) must be within
`max(abs, rel x largest arm mean)`:

| Metric | abs | rel |
|---|---|---|
| `busy_pct` (load CPUs, points) | `5` | `0` |
| `load1` | `1.0` | `0.15` |
| `pkg_w` (W) | `3` | `0.10` |
| `throttle_per_min` | `10` | `0.25` |
| `swap_used_mib` | `256` | `0` |
| `swap_delta_mib` (per window) | `64` | `0` |

A metric no arm measured (no RAPL access) is reported `unmeasured` and skipped; a
metric only some arms measured fails. Mixed levels or fewer than two arms fail.
Sample background-only windows (load on, probe idle) just before each arm, tagged
with the arm, so the probe's own work does not count as background.

Interface:

```bash
bench/load/load.py start --level typical --ttl 1800   # idempotent; replaces another level
bench/load/load.py sample --seconds 10 --tag A --level typical --out bg.jsonl
#   ... run arm A, then sample --tag B, run arm B, and so on (ABAB) ...
bench/load/load.py matched bg.jsonl                   # exit 0 MATCHED, 3 INCONCLUSIVE
bench/load/load.py stop                               # removes only io.k3d-gpu.bench-load=1 containers
bench/load/smoke.sh                                   # local smoke, no cluster
```

Every load container is labelled `io.k3d-gpu.bench-load=1`, runs with `--rm`, and
stress-ng's own `--timeout` (the `--ttl`, at most 4 h) ends it even if `stop` never
runs; `start` also removes any of its containers whose
`io.k3d-gpu.bench-load.expires` time has passed. RAPL needs root: set
`SUDO_ASKPASS` (`sudo -A`) or have `sudo -n` work, else `pkg_w` is null.

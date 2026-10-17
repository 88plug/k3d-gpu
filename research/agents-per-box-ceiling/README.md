# Agents-per-box ceiling model

Archived from `archive/invent-agents-per-box-ceiling`. Verdict: discovery, not
measured. See the [Research leads](../../EXPERIMENTS.md#research-leads) row.

`ceiling.py` is a stdlib CLI that models how many agents an i9-13900T box can
serve at a p95 turn-latency SLO. Its subcommands are listed in the module
docstring (`hw`, `models`, `calibrate`, `curve`, `box`, `agents`, `probes`,
`selftest`). Its main finding: memory on this host runs at 3600 MT/s (2 DIMMs
per channel, read with dmidecode), so the DRAM peak is 57.6 GB/s, and at agent
context lengths the decode step is bound by reading the KV cache, not the
weights.

Every live number is a pre-registered probe (`python3 ceiling.py probes`). P0
replaces the assumed DRAM roof with a measurement:

```bash
gcc -O3 -march=native -fopenmp stream_probe.c -o stream_probe
OMP_NUM_THREADS=8 taskset -c 0-15 ./stream_probe   # P-cores, then rerun on all cores
```

Check: `python3 ceiling.py selftest` (shape counts against published configs,
calibration residuals, simulator monotonicity).

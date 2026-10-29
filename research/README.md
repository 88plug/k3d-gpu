# Research models

Self-contained, stdlib-only models salvaged from archived research branches.
None of them is part of the launcher, and none of their numbers is measured:
every prediction waits for the live probe named in its directory. The verdict,
the strongest evidence and the next probe for each are in the
[Research leads](../EXPERIMENTS.md#research-leads) table of `EXPERIMENTS.md`;
the full branch history is on its `archive/*` tag.

| Directory | From tag | What it is | Tests |
|-----------|----------|------------|-------|
| [`agents-per-box-ceiling/`](agents-per-box-ceiling/) | `archive/invent-agents-per-box-ceiling` | Roofline and closed-loop simulator for concurrent agents per Intel client box, plus a 2 s DRAM triad probe | `python3 ceiling.py selftest` |
| [`prefix-reuse/`](prefix-reuse/) | `archive/invent-prefix-reuse` | OVMS 2026.4.1 prefix-cache behaviour read from source, a TTFT model and the agent defaults it implies | `python3 test_prefix_model.py` |
| [`spec-decoding/`](spec-decoding/) | `archive/invent-spec-decoding` | Speculative decoding across engines in OVMS/OpenVINO GenAI, with a speed model | `python3 test_specdec_model.py` |
| [`tseries-envelope/`](tseries-envelope/) | `archive/invent-tseries-envelope` | Power envelope of the i9-13900T host and a benchmark run-length rule | `python3 test_envelope_model.py` |

`tests/bench_test.sh` runs all four test entry points in CI.

## Research records

Records a campaign cites, kept so its citations resolve. They are evidence, not models, and
nothing in them runs in CI.

| Directory | What it is | Cited from |
|-----------|------------|------------|
| [`model-cards/`](model-cards/) | The 2026-10-20 model-card digest (`REFERENCE.md`) of 24 Hub repos for Gemma-4-26B-A4B, Qwen3.8-27B and Qwen3.8-Flash-Next, with the files it read and its probes | `EXPERIMENTS.md` crescent amendment M1 and the H53 pre-registration, `bench/models/qat_grid.py`, `console/model_opt.py` |
| [`dialectic/`](dialectic/) | The 2026-10-20 design records C1gate to D4 and the review of #157 | #157, #161, #164, #165, #168, #183 |

# Model-card digest (2026-10-20)

`REFERENCE.md` is the model-card digest the crescent campaign cites as `model-cards/REFERENCE.md`
or "REFERENCE.md" with a section number: amendment M1 and the H53 pre-registration in
[`EXPERIMENTS.md`](../../EXPERIMENTS.md), `bench/models/qat_grid.py`, `console/model_opt.py`,
`tests/console/test_model_opt.py` and the `agent_gemma4` profile in `bench/eatflow/agent.lock`.
It was compiled on 2026-10-20 in the coordinating session's scratchpad and committed here on
2026-10-28. Apart from this README, every file in this directory is a byte-for-byte copy of that
scratchpad's `model-cards/` directory and its `latest-versions-2026-10-20.txt`, so the section
numbers and the `dir/file:L` line citations in `REFERENCE.md` read as they did when they were cited.

`REFERENCE.md` sha256: `9704c3cbb2128bec676c4b56640a1463d9d7384f3e9d3f0b48dcb006d26691f2`.

| Path | What it is |
|------|------------|
| `REFERENCE.md` | The digest: model families (1), every variant (2), engine flag crib (1.4), QAT deep dive (3), decision table for the Xeon 6 + Arc Pro B70 and client hosts (4), gotchas that make benchmarks unfair (5). Its citation keys are defined at its top. |
| `INDEX.txt` | The 24 Hugging Face Hub repos the digest read, each with the revision read (first 12 hex digits) and the files fetched. |
| `<org>__<name>/` | Those files, fetched from the Hub at that revision: the model card `README.md`, `api.json` (the Hub API's model info) and whichever of the config, generation config, tokenizer config, chat template, quantization config and recipe files the repo has. No weights. |
| `probes/` | The scripts behind the digest's `[probe:*]` keys (section 3.2): `qatprobe3.py` (grid), `ggufblk.py` (GGUF vs QAT), `rtn.py` (re-quantization error), `ggufhdr.py` (GGUF header). They read the pinned files by HTTP range with `curl` and need `numpy`. |
| `latest-versions-2026-10-20.txt` | The latest stable release of each serving-stack component, checked at 15:28 PDT on 2026-10-20 in the same run. |

The files fetched from the Hub keep their upstream licenses; section 5.6 of `REFERENCE.md` lists
them. Nothing here runs in CI.

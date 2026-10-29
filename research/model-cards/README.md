# Model-card digest (2026-10-20)

`REFERENCE.md` is the model-card digest the crescent campaign cites as `model-cards/REFERENCE.md`
or "REFERENCE.md" with a section number: amendment M1 and the H53 pre-registration in
[`EXPERIMENTS.md`](../../EXPERIMENTS.md), `bench/models/qat_grid.py`, `console/model_opt.py`,
`tests/console/test_model_opt.py` and the `agent_gemma4` profile in `bench/eatflow/agent.lock`.
It was compiled on 2026-10-20 in the coordinating session's scratchpad and committed here on
2026-10-28. Apart from this README and `LICENSES/`, every file in this directory is a byte-for-byte
copy of that scratchpad's `model-cards/` directory and its `latest-versions-2026-10-20.txt`, so the
section numbers and the `dir/file:L` line citations in `REFERENCE.md` read as they did when they
were cited.

`REFERENCE.md` sha256: `9704c3cbb2128bec676c4b56640a1463d9d7384f3e9d3f0b48dcb006d26691f2`.

| Path | What it is |
|------|------------|
| `REFERENCE.md` | The digest: model families (1), every variant (2), engine flag crib (1.4), QAT deep dive (3), decision table for the Xeon 6 + Arc Pro B70 and client hosts (4), gotchas that make benchmarks unfair (5). Its citation keys are defined at its top. |
| `INDEX.txt` | The 24 Hugging Face Hub repos the digest read, each with the revision read (first 12 hex digits) and the files fetched. |
| `<org>__<name>/` | Those files, fetched from the Hub at that revision: the model card `README.md`, `api.json` (the Hub API's model info) and whichever of the config, generation config, tokenizer config, chat template, quantization config and recipe files the repo has. No weights. |
| `LICENSES/` | The license texts for the `<org>__<name>/` files (see [Licenses](#licenses)). Added on 2026-10-29. |
| `probes/` | The scripts behind the digest's `[probe:*]` keys (section 3.2): `qatprobe3.py` (grid), `ggufblk.py` (GGUF vs QAT), `rtn.py` (re-quantization error), `ggufhdr.py` (GGUF header). They read the pinned files by HTTP range with `curl` and need `numpy`. |
| `latest-versions-2026-10-20.txt` | The latest stable release of each serving-stack component, checked at 15:28 PDT on 2026-10-20 in the same run. |

The probes do not run in CI. `tests/model_cards_license_test.py` checks the two license tables
below against the model cards and the files in `LICENSES/`.

## Licenses

The `<org>__<name>/` files are third-party files. They stay under their upstream licenses and are
not under this repository's FSL-1.1-ALv2 ([`LICENSE.md`](../../LICENSE.md)). Each of those licenses
asks a redistributor to pass its text on, so `LICENSES/` holds them: the `LICENSE` file a repo ships
at the revision read, or, for an Apache-2.0 repo that ships none, the Apache License 2.0 text from
apache.org. The license files were fetched on 2026-10-29 and are byte-for-byte copies of their
sources. Section 5.6 of `REFERENCE.md` summarises the licenses as read on 2026-10-20.

| License file | License | Fetched from | sha256 |
|--------------|---------|--------------|--------|
| `LICENSES/Apache-2.0.txt` | Apache-2.0 | https://www.apache.org/licenses/LICENSE-2.0.txt | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `LICENSES/Qwen__Qwen3.8-27B.Apache-2.0.txt` | Apache-2.0 | `LICENSE` of `Qwen/Qwen3.8-27B` at `1d4bf0f2ff60`; the same bytes as the `LICENSE` of `Qwen/Qwen3.8-27B-FP8` at `017b9c7af6b5` and of `RedHatAI/Qwen3.8-27B` at `019ada84a62b` | `bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a` |
| `LICENSES/qwen-community-1.0.txt` | qwen-community-1.0 | `LICENSE` of `Qwen/Qwen3.8-Flash-Next` at `de4b8e4d43b9`; the same bytes as the `LICENSE` of `Qwen/Qwen3.8-Flash-Next-FP8` at `236dfdf28582` | `a0dc422560841fd68e06d974907f8b4c709bca44a67daad2b528437bdf676c08` |
| `LICENSES/Intel__qwen3.8-27B-int4-ov.MIT.txt` | MIT | `LICENSE` of `Intel/qwen3.8-27B-int4-ov` at `53292eeedaca` | `de682da4e16752874d65c3a4d970130f767493d93bc7c6351a217555c576e1b6` |

| Dir | License | License file |
|-----|---------|--------------|
| `google__gemma-4-26B-A4B-it/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `google__gemma-4-26B-A4B/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `google__gemma-4-26B-A4B-it-assistant/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `google__gemma-4-26B-A4B-it-qat-q4_0-gguf/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `google__gemma-4-26B-A4B-it-qat-q4_0-unquantized/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `OpenVINO__gemma-4-26b-a4b-it-int4-ov/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `Intel__gemma-4-26B-A4B-it-int4-AutoRound/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `Intel__gemma-4-26B-A4B-it-int4-mixed-AutoRound/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `RedHatAI__gemma-4-26B-A4B-it/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `RedHatAI__gemma-4-26B-A4B-it-FP8-dynamic/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `RedHatAI__gemma-4-26B-A4B-it-speculator.eagle3/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `Qwen__Qwen3.8-27B/` | Apache-2.0 | `LICENSES/Qwen__Qwen3.8-27B.Apache-2.0.txt` |
| `Qwen__Qwen3.8-27B-FP8/` | Apache-2.0 | `LICENSES/Qwen__Qwen3.8-27B.Apache-2.0.txt` |
| `OpenVINO__Qwen3.8-27B-int4-ov/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `OpenVINO__Qwen3.8-27B-int8-ov/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `Intel__qwen3.8-27B-int4-ov/` | MIT | `LICENSES/Intel__qwen3.8-27B-int4-ov.MIT.txt` |
| `Intel__Qwen3.8-27B-q4km-AutoRound/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `RedHatAI__Qwen3.8-27B/` | Apache-2.0 | `LICENSES/Qwen__Qwen3.8-27B.Apache-2.0.txt` |
| `RedHatAI__Qwen3.8-27B-INT4/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `RedHatAI__Qwen3.8-27B-speculator.dspark/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |
| `Qwen__Qwen3.8-Flash-Next/` | qwen-community-1.0 | `LICENSES/qwen-community-1.0.txt` |
| `Qwen__Qwen3.8-Flash-Next-FP8/` | qwen-community-1.0 | `LICENSES/qwen-community-1.0.txt` |
| `Intel__Qwen3.8-Flash-Next-W4A16-AutoRound/` | qwen-community-1.0 | `LICENSES/qwen-community-1.0.txt` |

Notes on the mapping:

- The Gemma 4 cards' `license_link`, https://ai.google.dev/gemma/docs/gemma_4_license, redirects
  to Google's copy of the Apache License 2.0 (https://ai.google.dev/gemma/apache_2).
- `Intel/gemma-4-26B-A4B-it-int4-AutoRound` and `Intel/Qwen3.8-27B-q4km-AutoRound` have no license
  field. Their cards say "Please follow the license of the original model", and their base models,
  `google/gemma-4-26B-A4B-it` and `Qwen/Qwen3.8-27B`, are Apache-2.0.
- `Intel/Qwen3.8-Flash-Next-W4A16-AutoRound` declares `qwen-community-1.0` with
  `license_link: LICENSE`, but its repo has no `LICENSE` at `4c67bf686b7f` (not in its `api.json`
  file list; the Hub returns 404 for it). It maps to the `LICENSE` of its base model,
  `Qwen/Qwen3.8-Flash-Next`.

# Moat design: capacity oracle and Intel Advantage profile

Status: **MVP built** (sections 2-4 and the H6 seed; "Build status" at the end
lists what differs from this design). **The submission path that shipped is
not the one designed in sections 1, 2 and 4.** `k3d-gpu share --submit` opens a
pull request on `88plug/k3d-gpu` adding `data/oracle/<content_sha256>.json`,
and the `oracle` CI job validates it; the user guide is
[docs/oracle.md](oracle.md). The GitHub-issue submission, the
`88plug/k3d-gpu-oracle` data repo, its ingest workflow and the build
attestation below are superseded future design, kept for the reasoning; none
of them exists.
Nothing here is a new measurement. Every number
that appears below is either backed on its own line (ledger row, provenance row
or primary-source URL) or sits in a code block as a formula or an example shape.

## 0. Thesis in one paragraph

k3d-gpu already measures a box: `k3d-gpu report` describes the hardware,
`doctor --perf` explains its power and thermal state, and `k3d-gpu capacity`
(branch `claude/capacity-cmd`) measures agents per box (APB) at a p95 turn SLO.
The moat is to turn those local results into a shared, public, per-Intel-SKU
matrix that answers "how many agents does this box serve, and what should I
buy". Three parts:

1. **Capacity oracle.** Opt-in, identifier-free bundles. Users submit them
   through GitHub, CI validates them, and a static matrix is published. Every
   new box makes the matrix more useful to the next user, which is the network
   effect.
2. **Intel Advantage profile.** Each Intel-only or Intel-best lever is applied
   automatically where k3d-gpu can detect it. Its uplift is measured with the
   same neutral workload and recorded in the bundle as an on/off A/B pair.
3. **Laptop-to-Xeon promotion.** The planner predicts APB on a target SKU from
   the matrix, and labels each prediction as measured or modeled.

Survey finding that sets the niche: LocalScore, the llama.cpp scoreboard,
ollama-benchmark and OpenBenchmarking record single-stream throughput only. None
of them records concurrency at a latency SLO. Schema sources:
https://github.com/cjpais/LocalScore/blob/main/src/pages/api/results.ts and
https://github.com/ggml-org/llama.cpp/discussions/4167. MLPerf Server/Interactive
does measure throughput at a latency constraint, but its results are
vendor-submitted and audited, not crowd-sourced:
https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc.

## 1. Privacy model (applies to every section)

The user rules are strictly opt-in, show exactly what is shared, and collect no
identifiers. The design follows from them.

| Rule | Mechanism |
|---|---|
| Default off | Nothing is ever sent unless the user runs `k3d-gpu share --submit` and answers `y` at an interactive prompt. There is no background or automatic send, and no config switch turns sending on. |
| Show the exact bytes | `share` always prints the full bundle and its sha256 before anything else happens. `--dry-run` stops there. |
| Allowlist, never denylist | The bundle is a *projection*: known-safe fields are copied out of report and capacity JSON. Nothing is redacted out of a blob, because PTS's line-deleting log scrubber shows that denylists leak (https://github.com/phoronix-test-suite/phoronix-test-suite/blob/master/pts-core/objects/phodevi/phodevi_vfs.php). The JSON Schema sets `additionalProperties: false` at every level. |
| No identifiers | No hostname, machine-id, DMI UUID or serials, PPIN, MAC, IP, username, path, cluster/context/node name, free-form label or exact timestamp. machine-id(5) says the ID "must not be exposed in untrusted environments" (https://www.freedesktop.org/software/systemd/man/latest/machine-id.html). |
| Coarse time | `week` = ISO week (`2026-W42`), the same granularity idea as Fedora countme (https://fedoraproject.org/wiki/Changes/DNF_Better_Counting). |
| Unlinkable | There is no persistent per-machine ID, unlike PTS's Global System ID or ollama-benchmark's hardware UUIDv5. Dedup uses the bundle's own content hash. |
| Honest notice | The consent screen says this plainly: *the bundle carries no identifiers, but a GitHub issue or PR is public under your GitHub account.* It never promises anonymity. Popcon says it plainly too: "it's impossible to make a submission completely anonymous" (https://popcon.debian.org/README). |
| Authoritative source | `docs/oracle-privacy.md` names `oracle/project.py` (the projection) and `oracle/schema/bundle-v1.json` as the authority whenever the doc and the code differ. This is the Homebrew pattern (https://docs.brew.sh/Analytics). |

### Signing: deviation from the brief, and why

The brief asks for "sigstore keyless when available". A keyless `cosign
sign-blob` writes the signer's OIDC identity (usually an email) into a public,
append-only transparency log. cosign itself warns that this "will be stored in
public transparency logs and cannot be removed later"
(https://github.com/sigstore/cosign/blob/main/cmd/cosign/cli/sign/privacy/privacy.go).
That breaks "collects no identifiers", and the user rule wins. So:

- **Default: unsigned on the client.** The bundle carries
  `"client_signature": {"kind": "none"}` and its `content_sha256`.
- **Opt-in `--sign keyless`.** This is offered only behind an explicit flag. It
  prints cosign's own privacy statement first, and then a second y/N. The
  bundle records `{"kind": "sigstore-keyless"}` and the Sigstore bundle is
  attached next to it, not inside it.
- **Trust anchor = the ingest workflow, not the user.** The oracle repo's ingest
  Action attests every accepted bundle with `actions/attest` (custom predicate
  type `https://github.com/88plug/k3d-gpu-oracle/predicate/ingest/v1`, an identifier that is never fetched). Anyone can then verify with
  `gh attestation verify <file> --repo 88plug/k3d-gpu-oracle --signer-workflow
  88plug/k3d-gpu-oracle/.github/workflows/ingest.yml --predicate-type <uri>`
  (flags in https://github.com/cli/cli/blob/trunk/pkg/cmd/attestation/verify/verify.go).
  The workflow identity is a repository, not a person.

A signature proves custody and integrity, not honest measurement. The client is
controlled by the submitter, so section 3's recomputation and plausibility rules
carry the honesty load. LocalScore shows what happens otherwise: it inserts a
client-computed `performance_score` with no checks
(https://github.com/cjpais/LocalScore/blob/main/src/pages/api/results.ts).

## 2. `k3d-gpu share` (a)

### Interface

```text
k3d-gpu share [--report FILE] [--capacity FILE]... [--out FILE]
              [--dry-run] [--submit] [--sign keyless] [--no-gh]
```

| Step | What happens | Network |
|---|---|---|
| 1. collect | `--report` defaults to a fresh `report_json` (in process, no cluster needed). `--capacity` takes one or more `capacity.json` files from `bench/apb/capacity.py`, plus the per-step `steps/*.jsonl` files next to each one (needed for server-side recompute). | none |
| 2. project | `python3 oracle/project.py` builds the bundle from the allowlist (section 3), adds `tool.version` = `VERSION`, the image digests and the comparison key, and computes `content_sha256` over canonical JSON. | none |
| 3. validate locally | `oracle/validate.py` runs the same rules CI runs. A failing bundle is never offered for submission. | none |
| 4. print | Prints the full bundle (pretty JSON), then `sha256`, then the byte size and a field count. The output always ends with the honest notice from section 1. | none |
| 5. write | Writes `--out` (default `./k3d-gpu-share-<sha256[:12]>.json`, mode 0600). | none |
| 6. stop | Without `--submit`, or with `--dry-run`, it exits 0 here. | none |
| 7. confirm | `--submit` requires a TTY. It shows `Submit this bundle to github.com/88plug/k3d-gpu-oracle as a public issue under your GitHub account? [y/N]`. Anything other than `y`/`yes` aborts. With no TTY it refuses. There is no `--yes`. | none |
| 8. send | If `gh` is installed and authenticated and `--no-gh` is not set, it runs `gh issue create --repo 88plug/k3d-gpu-oracle --title "bundle <sha12> <sku-slug>" --label submission --body-file <bundle.md>`. The body is a fenced json block holding the bundle. Otherwise it prints the issue-form URL and the file path, and the user pastes the file. | only here, only via `gh` |

Size rule: the bundle must fit an issue body. GitHub's body limit is **not
verified** from a primary source (believed to be 65,536 characters). The design
therefore caps the issue path at 48 KiB of bundle. Above that, step 8 uses the
PR path instead: `gh repo fork` plus a commit of `inbox/<sha256>.json` plus
`gh pr create`. Verify the real limit before building step 8.

### Read path (no upload): `k3d-gpu capacity --compare`

This follows OpenBenchmarking's auto-comparison, which "is automatically queried
for results ... based on the test comparison hash and the system type ... No
other system information or result data is transmitted"
(https://github.com/phoronix-test-suite/phoronix-test-suite/blob/master/documentation/phoronix-test-suite.md).

- `--compare` fetches `matrix/v1/<comparison_key>.json` (a static file on GitHub
  Pages) and prints `your box vs n same-SKU boxes: median and IQR of APB at SLO`.
  The request reveals only which key was fetched, and only to the CDN.
- `--compare --offline` reads `share/oracle-matrix.json`, a snapshot baked at
  build time (bake-at-build rule). The snapshot is refreshed by a scheduled PR,
  never fetched at runtime.

The user gets value before giving anything, so the read side grows even when
the write side is empty.

## 3. Bundle schema v1 and validator (b)

Files (in this repo): `oracle/schema/bundle-v1.json` (JSON Schema 2020-12),
`oracle/project.py`, `oracle/validate.py` (stdlib-only, like `bench/`) and
`oracle/matrix.py`. `validate.py` imports the scoring functions from
`bench/apb/apb.py` (`score_step`, `pctl`, `window_requests`), so there is one
definition of p95 and APB, shared by `capacity` and the oracle.

### Shape

```json
{
  "schema": "k3d-gpu.oracle.bundle/1",
  "kind": "capacity | single_stream",
  "week": "2026-W42",
  "content_sha256": "<sha256 of canonical JSON with this field removed>",
  "client_signature": {"kind": "none | sigstore-keyless"},
  "provenance": {"source": "community | maintainer-lab", "note": "<enum, no free text>"},
  "tool": {
    "version": "0.1.19",
    "k3s": "v1.37.1+k3s1",
    "server_image": "openvino/model_server@sha256:...",
    "node_image_digest": "sha256:..."
  },
  "comparison_key": "<sha256, see below>",
  "workload": {
    "trace": "support", "trace_generator": "agent_trace_gen.py@<commit>",
    "slo_s": 10, "stop_slo_s": 30, "reps": 3, "max_agents": 32,
    "model": {"id": "OpenVINO/Qwen3-4B-int4-ov", "revision": "<git sha>", "weights_sha256": "<sha256>", "quant": "int4"},
    "extra_body": {"enable_thinking": false}
  },
  "hardware": {
    "cpu": {"model": "13th Gen Intel(R) Core(TM) i9-13900T", "family": 6, "model_id": 183, "stepping": 1,
            "sockets": 1, "numa_nodes": 1, "snc": null, "p_cores": 8, "e_cores": 16, "lp_e_cores": 0,
            "logical_cpus": 32, "isa": ["avx2", "avx_vnni"], "amx": [], "microcode": "0x12b"},
    "memory": {"total_gib": 64, "type": "DDR5", "channels": 2, "configured_mts": 3600, "rated_mts": 4800},
    "intel_gpus": [{"device_id": "0xa780", "platform": "Raptor Lake-S", "driver": "i915", "xmx": false,
                    "media_vcs": 2, "media_vecs": 1, "guc": "70.x", "huc": "8.x"}],
    "npus": [], "gaudi": {"count": 0}, "dsa": 0, "iaa": 0, "qat": 0,
    "nvidia": [{"model": "NVIDIA RTX 4000 SFF Ada Generation", "device_id": "0x27b0", "driver": "590.48"}],
    "kernel": "6.6"
  },
  "power": {"pl1_w": 117, "pl2_w": null, "epp": "balance_performance", "ppd_profile": "balanced"},
  "levers": [{"id": "pe_pinning", "detected": true, "state": "on", "evidence": "k3d-gpu.io/hybrid-cpu=true"}],
  "ab": null,
  "results": {
    "engine": "cpu | igpu | npu | gaudi | nvidia", "server": "ovms | vllm",
    "agents_per_box": 6, "binding_limit": "slo_p95",
    "steps": [{"agents": 1, "p95_s": 0.0, "ttft_p95_s": 0.0, "verdict": "pass",
               "window_out_tok_s": 0.0, "j_per_turn_pkg": 0.0, "pkg_w_mean": 0.0,
               "throttle_events": 0, "cpu_busy_mean": 0.0,
               "samples": {"e2e_ms": [], "ttft_ms": [], "out_tok": [], "tool_ms": [], "err": []}}],
    "idle_pkg_w": 0.0, "drift_ratio": 1.0, "quiet_check_ok": true,
    "single_stream": {"decode_tok_s": [], "ttft_s": [], "prompt_tokens": 0, "out_tokens": 0}
  }
}
```

The values above show the *shape* only. They are not a result.

### Allowlist projection (source to bundle)

| Bundle field | Source | Transform |
|---|---|---|
| `hardware.cpu.*` | `report.host.cpu`, `/proc/cpuinfo` family/model/stepping/microcode | counts and flags only; drop `online`, `p_core_cpus` and `e_core_cpus` CPU lists |
| `hardware.memory.total_gib` | `report.host.memory.total_kib` | round to the nearest standard size (8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512 GiB and up); drop `available_kib` |
| `hardware.memory.channels/configured_mts/type` | `doctor --perf` DRAM section (`perf_dram`) | only when root could read SPD/DMI; otherwise null. Never copy raw dmidecode |
| `hardware.intel_gpus[]` | `report.host.intel_gpus[]` | keep device_id, platform, driver, engine counts and GuC/HuC version; drop `card`, `render_node`, `pci` and firmware paths |
| `hardware.npus[]`, `nvidia[]`, `qat`, `dsa`, `iaa`, `gaudi` | report | device ids and counts; drop every `pci` BDF and accel node name; NVIDIA driver to major.minor |
| `hardware.kernel` | `report.host.kernel` | major.minor only (`6.6`); the full uname version string carries a build timestamp |
| `power.*` | capacity `host.pl1_w`, `doctor --perf` (`perf_rapl`, `perf_cpu`) | watts as integers |
| `tool.*` | `VERSION`, `AGENT_IMAGE`, node image digest | digests only |
| `results.*` | capacity.json + `steps/*.jsonl` | latencies to integer ms; drop `run_dir`, `json_path`, `endpoint.namespace/service/workload/url`, `host.loadavg`, `started_at`, `finished_at` |
| dropped entirely | `report.hostname`, `report.generated_at`, `report.cluster` (name, context, node names, labels), `report.detected`, RAPL zone names | none |

The `report.cluster` subtree is dropped whole. Lever state comes from the label
*values* k3d-gpu itself sets (section 5). Those values are re-derived inside
`project.py` from the closed label regex `REPORT_LABEL_RE`, never copied as a
map.

### Comparison key

Following llama.cpp's single pinned commit and PTS's comparison hash, a
result is only ever compared inside one key:

```text
comparison_key = sha256(canonical_json({
  tool.version, tool.k3s, tool.server_image, workload.trace, workload.trace_generator,
  workload.model.id, workload.model.revision, workload.model.weights_sha256,
  workload.slo_s, workload.reps, workload.extra_body, results.engine
}))
```

The pinned digests already are the comparability key (pin-versions rule). A
version bump starts a new matrix column. Columns are never mixed.

### Validator rules (`oracle/validate.py`, run locally and in ingest CI)

Verdicts: `reject` (not committed), `quarantine` (committed under
`data/quarantine/`, excluded from the matrix, shown in a review list), `accept`.

| # | Rule | Fail verdict |
|---|---|---|
| V1 | JSON Schema v1 with `additionalProperties: false`; size within 256 KiB | reject |
| V2 | `content_sha256` equals the recomputed hash; content-hash dedup against `data/` | reject (dup = no-op) |
| V3 | Canary scan: no string anywhere matches hostname/MAC/IPv4/IPv6/UUID/email/`/home/`/`/root/` patterns, and `server_image` names Docker Hub or a public registry (a private mirror's host name identifies the network). This is a belt on top of the allowlist | reject |
| V4 | `tool.version` is a tagged k3d-gpu release with a build-provenance attestation, and `server_image` is the digest pinned in `scripts/k3d-gpu` at that tag | quarantine |
| V5 | **Recompute**: from `samples`, p95 per step, verdicts and `agents_per_box` via `apb.score_step` / `capacity.agents_per_box`. Any mismatch with the claimed value means the claim is discarded. Only the recomputed value is stored. No reply may carry more `out_tok` than the `max_tokens` it was sent with (the trace regenerated from its seed, or the single-stream shape) | reject |
| V6 | **Little's law**: per step, `window_out_tok_s` agrees with `N * mean(out_tok) / mean(e2e + tool)` within a relative 0.25 | quarantine |
| V7 | **Throughput ceiling**: `APB <= ceil(R_peak * (slo_s + mean(tool_s)) / mean(out_tok))`, where `R_peak` = the largest recomputed window tok/s of any step | reject |
| V8 | **Bandwidth roofline** (CPU and iGPU engines): single-stream decode `<= 1.15 * BW / weights_bytes`, with `BW = channels * configured_mts * 8 bytes`. This rule is skipped when a speculative-decoding lever is on (the roofline does not hold there). When DRAM fields or `weights_bytes` are null, or the engine is not CPU/iGPU, one stream above a fixed ceiling (`1000 tok/s` CPU/iGPU/NPU, `8000 tok/s` Gaudi/NVIDIA, constants in `oracle/validate.py`) goes to review instead | quarantine |
| V9 | **Energy sanity**: `0 < pkg_w_mean <= max(pl2_w, 2 * pl1_w)` when known; `j_per_turn_pkg` agrees with `pkg_w_mean * window_s / turns` within a relative 0.1 | quarantine |
| V10 | **Box state**: `quiet_check_ok == true`. With `throttle_events > 0` in the binding step, or `drift_ratio > 1.25`, the bundle is accepted but tagged `throttled` / `drifted`, and the matrix shows those tags | quarantine if not quiet |
| V11 | **Monotone sanity**: recomputed p95 must not drop by more than half from step N to step 2N at the same reps. Such a drop is a sign of spliced runs | quarantine |
| V12 | **A/B pair**: an `ab` bundle needs its partner (same `ab.pair_nonce`, same comparison key, opposite lever state, the same `week`) before it counts toward an uplift | held until paired |

`ab.pair_nonce` is a random value generated fresh for each A/B session. It
links the two arms of one session and nothing else.

### Cross-bundle outlier rules (matrix stage)

- A cell is a tuple: `(comparison_key, cpu.model, accelerator device ids,
  engine, pl1 bucket, dram bucket, lever-state vector)`. The PL1 buckets are
  `<=35, <=65, <=125, >125 W`. The DRAM bucket is `channels x configured MT/s`
  rounded to 400. These normalizers matter because one SKU differs by OEM power
  limit and DIMM speed (`docs/intel-capability-map.md` sections 1c and 1d).
- Publish the **median, IQR and n**, never means or maxima.
- Robust z = `|x - median| / (1.4826 * MAD)`. If z > 3, the result goes to the
  review queue, unless it carries a `throttled` tag, in which case the tag
  already explains it.
- Weight: **one vote per submitter per cell**. The submitter is the GitHub
  account that opened the issue or PR. It is used at ingest for this cap and
  never written into the bundle or matrix. This bounds the Steam
  cyber-cafe over-count failure (https://store.steampowered.com/hwsurvey).
- Tiers: `provisional` (n = 1), `community` (n >= 2 distinct submitters
  agreeing within a relative 0.15 of the median), and `verified` (a
  maintainer-lab reproduction, or n >= 3 community). This is the MLPerf
  peer-review idea (https://github.com/mlcommons/policies/blob/master/submission_rules.adoc).
  The planner uses verified cells by default.
- Challenge: a GitHub issue on the data file. Upheld results move to
  `data/delisted/` with the reason. A written delisting policy is published on
  day one (Geekbench precedent:
  https://www.techspot.com/news/93660-geekbench-bans-four-generations-galaxy-devices-over-benchmark.html).
- Anti-fingerprinting: the trace generator randomizes request IDs, ordering and
  the served model alias per run, so a driver cannot special-case "the
  benchmark".

## 4. Data layout and matrix generator (c)

**Choice: code in this repo, data in a separate public repo
`88plug/k3d-gpu-oracle`.**

Rationale:

- **No rebuild side effects.** In this repo, pushes to `main` that touch
  `scripts/**`, `share/**` or `packaging/**` cut a release, and changes to
  `share/**` or `Dockerfile` rebuild the image (`.github/workflows/auto-release.yml`,
  `build-and-push.yml`). Hundreds of data PRs must never ride that pipeline.
- **Least-privilege bot.** The ingest workflow needs `contents: write`,
  `id-token: write` and `attestations: write`. A separate repo keeps those
  permissions away from the repo that ships the binary.
- **Clean signer identity.** `--signer-workflow` names one workflow in a repo
  whose only job is ingest.
- **Attestations need a public repo** on Free/Pro/Team plans
  (https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds).
- **Code stays here.** Schema, projection, validator and matrix generator stay
  in this repo, so the CLI and CI run the same code. The oracle repo pins this
  repo by commit SHA.

```text
88plug/k3d-gpu/                       (this repo)
  oracle/schema/bundle-v1.json
  oracle/project.py                   allowlist projection (authoritative for privacy)
  oracle/validate.py                  V1-V12
  oracle/matrix.py                    cells -> matrix JSON + markdown
  oracle/seed/maintainer-i9-13900T-h6.json
  share/oracle-matrix.json            baked snapshot for --compare --offline
  tests/oracle_test.sh                golden + canary fixtures
  docs/oracle-matrix.md                      generated snapshot of the matrix

88plug/k3d-gpu-oracle/                (data repo)
  .github/workflows/ingest.yml        issue/PR -> validate -> commit -> actions/attest
  .github/workflows/matrix.yml        on data change -> matrix.py -> Pages
  .github/ISSUE_TEMPLATE/submission.yml
  data/v1/<key12>/<sku-slug>/<content_sha256>.json
  data/quarantine/...  data/delisted/...
  matrix/v1/<comparison_key>.json     read path for --compare
  site/sku/<sku-slug>/index.html      one stable page per SKU
  POLICY.md                           privacy, weighting, tiers, delisting
```

Ingest flow:

1. An issue labeled `submission` or a PR adding `inbox/*.json` triggers the
   workflow.
2. The workflow extracts the fenced JSON, runs `validate.py` at the pinned SHA,
   and computes the cell.
3. It commits `data/...` as the bot, with no author fields from the issue.
4. It runs `actions/attest` (subject = the committed file, predicate =
   validator verdict + validator SHA).
5. It comments the verdict and closes the issue.

Every action is pinned by full commit SHA. Versions current at search time:
actions/attest v4.2.2 (https://github.com/actions/attest/releases) and gh v2.102.0
(https://github.com/cli/cli/releases). The release workflow of this repo gains one
`actions/attest-build-provenance` step, so V4 can check `tool.version` against an
attested build (https://github.com/actions/attest-build-provenance/releases).

Matrix generator (`oracle/matrix.py`), per comparison key:

| Column | Definition |
|---|---|
| SKU | `cpu.model` plus accelerator platform names |
| Engine | cpu / igpu / npu / gaudi / nvidia |
| APB at SLO | median [IQR] of the recomputed APB |
| J per turn | median package J/turn at the APB step (plus GPU J when NVIDIA) |
| Power bucket, DRAM bucket | normalizers |
| Levers on | compact lever vector |
| n, tier | distinct submitters; provisional / community / verified |
| Data | link to the data files |

`docs/oracle-matrix.md` is in this repo and is scanned by `tests/claims_check.sh`. The
generator therefore writes each numeric row with the https URL of its data file
on the same line, so every row is backed by a primary source and the guard
passes without exceptions.

## 5. Intel Advantage profile (d)

A lever goes into the profile when k3d-gpu can detect it and apply it (or
offer it) without the user doing anything Intel-specific. It is credited with an
uplift **only** after an A/B row exists, with the same workload, the same
comparison key and the lever toggled. Each lever adds a `levers[]` entry
`{id, detected, state, evidence}` to every bundle. An A/B session adds
`ab: {lever, arm: on|off, pair_nonce}`. The uplift is computed server-side, as
`median(on) / median(off)` per cell, for APB, TTFT p95, TPOT p95, CPU-s/s and
J/turn.

| id | Families | Detection (code on `origin/main`) | Applied | Uplift evidence today | A/B the oracle runs |
|---|---|---|---|---|---|
| `pe_pinning` | ADL, RPL, MTL, ARL, LNL, PTL (hybrid) | `has_hybrid`/`hybrid_cpus` read the `cpu_core`/`cpu_atom` PMUs; label `k3d-gpu.io/hybrid-cpu=true` | auto: `share/nri-balloons.yml`; `agent_manifest` labels the agent `k3d-gpu.io/cores: performance` | none quotable; H3 is REFUTED [ledger:H3] (decode held, prefill did not). A preliminary run on `claude/prefill-guard` saw decode mostly isolated and a prefill power-coupling residual, and it backs no claim | H3 arms: pinned quiet, pinned plus E-core neighbour, unpinned. Record TTFT/TPOT p95, `Cpus_allowed_list` and the throttle-count delta |
| `prefill_guard` | hybrid | as above | opt-in: `K3D_GPU_PREFILL_GUARD` ([prefill guard](prefill-guard.md)); `doctor --perf` shows the throttle counters once it is set | none; H5 is PENDING [ledger:H5] | H5 A/B: guard on/off with an E-core neighbour; TTFT median and p90 penalty |
| `xmx_gate` | XMX: ARL-H, LNL, BMG, PTL, WCL, NVL (NVL inherits `has_systolic` through `XE3_CONFIG`/`XE3P_CONFIG` in [intel_device_info.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/335d5698dca1d1b9d11ef18cf5f523cc0f7f58fb/src/intel/dev/intel_device_info.c)). No XMX: ADL, RPL, MTL, ARL-S/U | `gpu_xmx` (Mesa `has_systolic`) -> `intel_gpu_labels` emits `gpu.intel.com/xmx` | auto: `agent_device` takes the GPU only on an XMX node | non-XMX side measured: on the UHD 770 the P-cores win decode and prefill [ledger:H6] | H6 protocol on an XMX box: GPU/CPU decode and TTFT ratios, CPU-s/s freed, J/turn; plus an `offload` arm (model on a non-XMX iGPU, tools on the freed CPU) |
| `amx` | Xeon SPR, EMR, GNR, Xeon W (not SRF, not client) | `amx_flags` -> `amx_labels` -> `feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true` | auto: `agent_manifest` adds an AMXTILE nodeSelector | cited for prefill only, not decode (https://lenovopress.lenovo.com/lp2167); none measured | `ONEDNN_MAX_CPU_ISA=AVX512_CORE_BF16` vs default. **Unverified** whether the OpenVINO CPU plugin honours it; confirm via oneDNN verbose ISA, or use host `clearcpuid` |
| `numa_replica` | multi-socket, SNC, HBM, CXL | `numa_labels`, `doctor_numa` | auto: replica balloon in `share/nri-balloons.yml` | none | replica vs shared balloon; APB and DRAM bandwidth |
| `openvino_int4` | every Intel engine | the pinned `AGENT_IMAGE` digest | auto: it is the stack choice | not quotable yet: the OVMS-vs-EAT decode ratio in `bench/apb/PROTOCOL.md` is labeled "reported, not re-measured" and has no ledger row. Two variables changed at once (precision and engine) | A1 `ovms-cpu` vs A3 `eat-vllm-cpu`, plus a third arm (OVMS bf16 or vLLM-CPU int4) to separate precision from engine |
| `npu_offload` | MTL, ARL-H, ARL-S, LNL, PTL | `detect_npu`, `npu_labels` (`k3d-gpu.io/npu.gen`), `share/intel-npu-plugin.yml` | opt-in: `K3D_GPU_AGENT_DEVICE=NPU`; `auto` never picks the NPU | none measured. Not used as a speculative draft: OV discussion #36484 measured it below the GPU-only baseline (https://github.com/openvinotoolkit/openvino/discussions/36484) | router/embedding agent on the E-cores vs on the NPU; APB of the main agent, NPU J/turn net of idle |
| `media_engines` | every Intel iGPU and Arc | `intel_gpu_labels` -> `gpu.intel.com/media-vcs`, `media-vecs` | label only today; vision-pipeline scheduling is future work | none | LLM APB with N VA-decode streams beside it, engines vs software decode |
| `qat` | SPR, EMR, GNR, SRF, Xeon D | `has_qat`, `share/intel-qat-plugin.yml`, `docs/accelerated-dataplane.md` | opt-in gateway | cited TLS handshake offload (Intel QAT NGINX white paper, sources in `docs/accelerated-dataplane.md`); none measured | gateway CPU-s/s at a fixed TLS rate: QAT off / software-optimized / QAT; APB with the gateway in the loop |
| `iaa_zswap` | SPR, EMR, GNR, SRF, PTL | `has_iaa`, `share/intel-iaa-plugin.yml` | opt-in host change (zswap compressor `deflate-iaa`) via `host-setup` | cited from the in-review kernel series (https://lwn.net/Articles/1039687/); none measured | max sandboxes under a fixed memory cap: no zswap / zstd / deflate-iaa |
| `dsa_dto` | SPR and newer Xeon, SRF | `has_dsa`, `share/intel-dsa-plugin.yml` | opt-in DTO preload for copy-heavy tool pods (not built) | cited (https://arxiv.org/abs/2305.02480); none measured | H3 arm b with a memcpy neighbour, DTO off/on; inference TTFT/TPOT p95 |
| `rdt` | Xeon | `doctor_numa` reports `rdt_a` | off by default (it rewrites host resctrl); opt-in | none | CAT/MBA on vs off under an LLC/bandwidth neighbour |
| `kv_u4`, `spec_decode` | every OpenVINO engine | OVMS flags | opt-in, behind an accuracy gate (tool-call validity on the APB traces) | none measured | APB with `--kv_cache_precision u4` vs auto; draft on the CPU vs none |
| `itd` | hybrid | CPUID.06H in `doctor --perf` | detected only: Linux does not consume the ITD classes | none; recorded so a future kernel gets a ready before/after | none |

Not in the profile, by design: flags that restate OpenVINO defaults (u8 KV and
group-32 dynamic quant on CPU). They were killed as no-ops in review
(`docs/experimental.md` on `merge/experimental-flags`).

Every bundle also carries the `doctor --perf` normalizers (PL1/PL2, EPP, DRAM
configured speed, throttle delta). Without them, two boxes with the same SKU are
not comparable.

## 6. Laptop-to-Xeon promotion

`k3d-gpu capacity --plan --target "<sku>"` reads the matrix for the current
comparison key.

1. If the target cell is `verified` or `community`, the planner prints its
   median and IQR, labeled **measured**.
2. If the target cell is empty, the planner prints a **modeled** estimate. It
   scales the current box's measured APB by the ratio of the two cells' bound
   resources: DRAM bandwidth for decode-bound steps, and AMX availability with
   core count for prefill-bound steps. Which bound applies is read from
   `binding_limit` and the per-step TTFT share. The estimate is always printed
   with the label "modeled" and the cells it came from.
3. A modeled estimate becomes a prediction to check. When a matching bundle
   arrives, the generator records the model's error per key. The error
   distribution is then published next to the planner.

## 7. Seeding (e)

Day-one cells come from the maintainers, in the Homebrew-bottles pattern
(trusted infrastructure produces the first artifacts;
https://docs.brew.sh/Bottles).

| Seed | Kind | Content | Status |
|---|---|---|---|
| `oracle/seed/maintainer-i9-13900T-h6-{cpu,gpu}.json` | `single_stream` | Two bundles, CPU (P-core balloon) and GPU (UHD 770): decode and TTFT per-request samples from the H6 run, OVMS 2026.4.1 digest `sha256:a928903f...`, Qwen3-4B int4 at revision `b467368d`, weights sha256 `074214fa...`. `provenance.source = maintainer-lab`. The medians are cross-checked against the H6 table [ledger:H6] | built by `oracle/seed/build.py` from the H6 raw per-request files under `oracle/raw/h6/` |
| H1 (EAT on a non-Xeon host) | none | functional, not a capacity number | INCONCLUSIVE [ledger:H1]; no bundle (not a capacity number) |
| H2 (iGPU vs tuned CPU, J/token) | `single_stream` with energy | same campaign as H6; the ledger row reports decode, prefill and package J/token for both arms. The seed bundles carry no energy because the per-request raw files have no per-step J | REFUTED [ledger:H2]; no energy bundle yet |
| H3 (P/E isolation) | `capacity` A/B, lever `pe_pinning` | the first lever pair | REFUTED [ledger:H3]; no bundle (its runs were not capacity runs) |
| H4 (auto device = best device) | `single_stream` sweep | records the `auto` choice next to each forced device | REFUTED [ledger:H4]; no bundle |
| APB arms A1-A4 (`bench/apb/PROTOCOL.md`) | `capacity` | the first APB cells: OVMS CPU, OVMS iGPU, EAT vLLM-CPU, vLLM CUDA | pre-registered, not run |

As built, the seed is generated by `oracle/seed/build.py`, which calls
`project.project_single_stream` (the allowlist module `share` uses) with `provenance.source =
maintainer-lab`; `share` has no provenance flag. CI validates `oracle/seed/`
with `--trusted` and `data/oracle/` without it, so the seed passes the same
V1-V12 and only the maintainer path may carry maintainer-lab provenance.
Rules for seeds: a seed counts as one submitter, and it reaches `verified` only
after an independent community reproduction, or after a second maintainer box
of the same SKU.

## 8. Tests that gate the build

- `tests/oracle_test.sh` runs `project.py` over every sim fixture in
  `tests/fixtures/*/sysroot`. The fixtures are seeded with canary values: a fake
  hostname, MACs, DMI serials, a PPIN, IPs, `/home/<user>` paths and a cluster
  name. The test asserts that none of these strings appears in the output, and
  that the key set equals the golden key set.
- A golden bundle per fixture fails CI on any new key, so an allowlist change
  shows up in the diff.
- `validate.py` unit tests cover each rule V1-V12 with one passing and one
  failing bundle. The tampered-sample case (a claimed APB above the recomputed
  value) must be rejected.
- `tests/claims_check.sh` covers this file and the generated `docs/oracle-matrix.md`.

## 9. Build order (one coherent change per push)

1. `oracle/project.py`, the schema and `tests/oracle_test.sh` (privacy first;
   no network code yet).
2. `oracle/validate.py`, reusing `bench/apb` scoring once `claude/capacity-cmd`
   merges.
3. `k3d-gpu share` steps 1-6 (local only), then step 7-8 (`gh` path).
4. The H6 seed bundle and the data repo with `ingest.yml` and attestations.
5. `matrix.py`, `docs/oracle-matrix.md`, the Pages SKU pages and `capacity --compare`
   with the baked snapshot.
6. Lever A/B runs, one lever at a time, each adding a ledger row before any
   uplift is quoted.

## Build status (MVP)

Built, with tests in `oracle/test_oracle.py` and the CI job `oracle`:

- `oracle/schema/bundle-v1.json`, `oracle/bundle.py` (canonical JSON, content
  hash, comparison key, a stdlib checker for the schema keywords used),
  `oracle/project.py`, `oracle/validate.py` (V1-V12), `oracle/matrix.py`,
  `oracle/share.py` behind `k3d-gpu share`, and `report --no-cluster`.
- Seed: `oracle/seed/maintainer-i9-13900T-h6-{cpu,gpu}.json`, rebuilt by
  `oracle/seed/build.py` from the H6 raw per-request files committed under
  `oracle/raw/h6/`. The recomputed medians equal the H6 table [ledger:H6].
- `docs/oracle-matrix.md` is generated from the seed and checked in CI.

Differences from the design above:

- Samples are integer **microseconds**, not milliseconds, so the recomputed
  p95 and pass/fail verdicts match the run's own at any SLO. The V1 size cap is
  therefore 1 MiB, not 256 KiB; the 48 KiB issue cap is unchanged.
- `--dry-run` writes nothing (the design wrote the file first). Without
  `--submit` the file is written; nothing is sent either way.
- `--sign keyless` is not built. Bundles carry `client_signature: none` only.
- No free text: levers have no `evidence` string and the workload has no
  `extra_body` object; it carries `thinking: off|on|default` and `time_scale`.
- V4: a release must pin the bundle's server image at its `aur-vX.Y.Z` tag
  (read from the clone, or given with `--pin`). An unreleased build
  (`git-<sha>`) is accepted only from a trusted maintainer path and is tagged
  `unreleased-build`. Checking a release's build attestation is left to the
  ingest repo, which does not exist yet.
- V6 and V7 use the tool wait the trace generator gave the replayer (the trace
  is regenerated from capacity's seed `1000 * N + rep`), not the gaps observed
  in the samples, which would make Little's law hold by construction. V7 keeps
  a slack factor of 1.25 because agents are not busy at every instant.
- Submitter tokens come from the ingest (`matrix.py --submitters`). Without
  them each community bundle is its own submitter and all maintainer-lab
  bundles are one.
- Submission is a pull request on `88plug/k3d-gpu` adding
  `data/oracle/<content_sha256>.json` (not an issue on a data repo). CI runs
  `validate.py --strict` on it; `leaderboard.py --check` reports it as pending
  until a maintainer regenerates `docs/leaderboard.{md,json}` after merge.
- Not built (superseded or deferred): the data repo `88plug/k3d-gpu-oracle` with its ingest and
  attestation workflows, `capacity --compare` and the baked
  `share/oracle-matrix.json`, the planner (section 6) and every lever A/B.
- The seed's hardware block was read with `report --no-cluster` on the H6
  host on the same day, not during the run, so microcode and power state are
  left null.

## Open items (unverified)

- GitHub issue body size limit (section 2). Moot for the pull-request path that shipped.
- Whether the OpenVINO CPU plugin honours `ONEDNN_MAX_CPU_ISA` for the AMX A/B.
- The mainline merge status of the IAA zswap batching series.
- `doctor --perf` reads DRAM channel count and configured speed only as root.
  Bundles without these fields fall into a `dram: unknown` bucket.

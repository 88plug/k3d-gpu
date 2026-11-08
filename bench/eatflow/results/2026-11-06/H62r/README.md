# H62r records (crescent, 2026-11-07)

H62r is REFUTED. The run, the Reproduce header and what it means are in EXPERIMENTS.md, "H62r
run (Xeon 6741P, 2026-11-07)". One setup attempt, one wiring smoke and one hold ran, from
`bench/h62r/campaign.sh` at `1ed9f91c`.

Layout:

- `campaign.log`, `campaign.out`, `attempts.txt`, `teardown.log`: `campaign.sh`'s own records.
- `upstream-pre-setup.txt`, `upstream.txt`: the re-run trigger (`bench/h62r/upstream.sh`), read
  before the campaign, before setup and before hold 1. `upstream.err` is its stderr (empty).
- `setup/`: `bench/h62r/setup.sh`.
  - Host-side records: `setup.log`, the gates (`gate-*.txt`, `host-facts.json`,
    `guest-facts.json`, `nfd-facts.json`, `weights-facts.json`), A1's IR check (`ir-verify.txt`),
    the Hub file list and weight script for B5's weight gate (`qat-siblings.json`,
    `verify-weights.py`, `weights-verify.txt`), the QEMU image build, the cloud image check and
    both guests' `docker run`.
  - `guest-a/`: VM a's records (`steps.txt`, the apt logs, `up.log`, `agent.yaml` and `agent.err`,
    the staged `deploy-a.yaml`, `rollout.log`, amendment A4's `plan.txt`, `versions.txt`,
    `nodes.json`, `pods.json`, `staged-ir.txt`).
  - `guest-b/`: VM b's records (`steps.txt`, `d1.txt`, `configure.log`, `init.log`,
    `ext-commits.txt`, `edit.diff`, D3's `probe.txt`, `thread-siblings.txt`, `d3-siblings.txt`
    and `d3-siblings.diff`, the env files as edited, and the sizing records `ok-*`). The full
    `install.log` stays on crescent (pre-registration, records rule 8); `install-excerpt.txt` is
    its `nri_cpu_balloons` role and its three PLAY RECAP blocks.
- `smoke/`: `bench/h62r/smoke.sh` (`validate.log`, `eat-check.log`, `check-sha256.txt`, and the
  `H62r-smoke` hold in `hold0/` and `pairs/`).
- `hold1/`: hold 1's `live.sh` records, with each arm's pod, cpuset, ISA and image IDs in
  `remote-a/` and `remote-b/`.
- `pairs/`: the pairs, attempts, turns and proxy logs, and `analysis.json` (the scorer's output;
  `report.txt` is the same).
- `vmtax.json`: `h62r.py vmtax`, described and not gated.
- `runs/H62r-hold1/`, `runs/H62r-smoke/`: `bench/eatflow/crescent/hold.sh`'s records (the
  hold-start wait, snapshots before and after, the serving covariate `serving.jsonl`).

The `.pid` files the hold scripts write were not copied. Every committed file was scanned for
key, token, password, `hf_` and private-key patterns and for long base64 strings; the only
matches were container IDs, URLs and the harness's `api_key: not-needed`.

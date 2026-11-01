# H62 records (crescent, 2026-11-01)

H62 is INCONCLUSIVE (not deliverable). The run, the cause and the Reproduce header are in
EXPERIMENTS.md, "H62 run (Xeon 6741P, 2026-11-01)", and amendments V1-V4 are just before it. No
wiring smoke and no hold ran, so there is no `smoke/`, `pairs/`, `hold<N>/` or `report.txt`.

Layout:

- `campaign.log`: all four setup attempts, as `bench/h62/campaign.sh` wrote them.
- `campaign.out`, `host-tools.txt`, `teardown.log`: the last attempt (commit `e9f14026`).
- `setup/`: the last attempt's setup records (`bench/h62/setup.sh`).
  - Host-side records: `setup.log`, the gates (`gate-*.txt`, `host-facts.json`,
    `guest-facts.json`), A1's IR check (`ir-verify.txt`), the Hub file list and weight script for
    B5's weight gate (never reached), the QEMU image build and its packages, the cloud image check
    and both guests' `docker run`.
  - `guest-a/`: VM a's records. These are `steps.txt`, the apt logs, `up.log`, `agent.yaml` and
    `agent.err`, the staged `deploy-a.yaml`, `rollout.log`, amendment A4's `plan.txt`,
    `versions.txt`, `nodes.json`, `pods.json` and `staged-ir.txt`.
  - `guest-b/`: VM b's records. These are `steps.txt`, `d1.diff`, `configure.log`, `init.log`,
    `ext-commits.txt`, `edit.diff` and the env files as edited.
  - `guest-b/` also holds amendment V4's `fail-nodes.txt`, `fail-pods.txt`, `fail-events.txt.gz`
    and `fail-describe.txt`, plus `install-excerpt.txt`. The full `install.log` stays on crescent
    (pre-registration, records rule 8).
- `setup-attempt1/` to `setup-attempt3/`: the earlier attempts, in the same layout. Attempt 3's
  `guest-b/install-excerpt.txt` covers the same failed step.
- `upstream-pre-setup.txt`, `upstream.log`: the re-run trigger (`bench/h62/upstream.sh`), read
  before each attempt and every 10 min while the campaign ran. `upstream-loop.sh` is the loop
  that wrote `upstream.log`.

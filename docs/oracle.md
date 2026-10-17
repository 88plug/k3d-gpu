# Capacity oracle

The capacity oracle is a public, per-SKU record of what Intel boxes serve under
k3d-gpu's auto-adapted defaults: agents per box at a p95 turn SLO, single-stream
decode speed, and tokens per package joule. Its goal is to become the reference
data set for agent serving on Intel hybrid CPUs, GPUs, NPUs and Gaudi. Today it
holds this maintainer box's two H6 runs and nothing else.

- **Leaderboard**: [leaderboard.md](leaderboard.md) (also [leaderboard.json](leaderboard.json)).
- **Per-key matrix with IQR, tiers and A/B uplifts**: [oracle-matrix.md](oracle-matrix.md).
- **Design and threat model**: [moat-design.md](moat-design.md).

## Strictly opt-in

Nothing in k3d-gpu sends data on its own. There is no telemetry, no background
upload and no default-on prompt. A bundle leaves the box only when you run all
three of these:

```bash
k3d-gpu capacity                 # a finished agents-per-box run
k3d-gpu share --dry-run          # build, validate and print the exact payload; write and send nothing
k3d-gpu share --submit           # same, then a typed y/yes at a terminal opens a pull request
```

`--dry-run` prints every byte that would be submitted, its sha256, its size
and the validator verdict, and stops. Without `--dry-run`, the bundle is also
written to `./k3d-gpu-share-<sha12>.json` with mode 0600. `--submit` requires
an interactive terminal and a typed `y` or `yes`; there is no `--yes` flag.

## What a bundle carries, and what it never carries

`oracle/project.py` builds the bundle from an allowlist, and
`oracle/schema/bundle-v1.json` closes every object (`additionalProperties:
false`) and pins every string to an enum, a pattern or a hash. Those two files
are the authority when this page and the code differ.

| Carried | Never carried |
|---|---|
| CPU model string, family/model/stepping, P/E core counts, ISA flags | hostname, machine-id, product UUID, DMI serials |
| GPU/NPU/QAT/Gaudi PCI device ids (not addresses), driver, GuC/HuC versions | PCI bus addresses, CPU lists, node or cluster names |
| memory rounded to a standard size, PL1/PL2, EPP, platform profile | MAC or IP addresses, user names, home paths, URLs |
| kernel major.minor, k3d-gpu version, server image digest, model revision | timestamps (only the ISO week) |
| per-request timings (integer microseconds) and token counts | prompts or completions |

Every string the projection copies from the host also passes the identifier
scrubber: a value shaped like a MAC, IPv4/IPv6 address, UUID, e-mail address,
home path or 32-hex machine-id is dropped (`bundle.looks_identifying`). The
validator then scans the whole bundle again with the same patterns plus this
machine's own hostname, machine-id, user name, home path, MAC addresses and DMI
serials. A bundle that fails is never written and cannot be submitted.

A pull request is public and shows your GitHub account, so a submission is not
anonymous. Use GitHub's noreply e-mail for the commit if you want your address
kept private.

## Submission: one pull request, one file

`k3d-gpu share --submit` with `gh` installed and logged in:

1. forks `88plug/k3d-gpu` (no clone),
2. creates the branch `oracle-<sha12>` in your fork,
3. adds `data/oracle/<content_sha256>.json` through the contents API,
4. opens a pull request against `main`.

Without `gh` (or with `--no-gh`) it prints the same steps as `git` commands.

## Validation in CI (V1-V12)

The `oracle` job in `.github/workflows/ci.yml` runs on every pull request with
a read-only token and no secrets. It runs `oracle/validate.py --strict` over
`data/oracle/*.json`: a file must be named after its own `content_sha256` and
must get an `accept` verdict. Client-side summaries are ignored; every number is
recomputed from the raw samples.

| Rule | Check |
|---|---|
| V1 | schema (closed objects, enums, patterns) and size |
| V2 | content hash and comparison key; a hash already in `--known DIR` is a duplicate (in `data/oracle/` the file name is the hash, so a second copy is the same path) |
| V3 | no identifier-shaped string, no private-registry image; locally, none of this machine's identifiers as a whole token (a plain word found only in the CPU brand string, such as the login `core` in `Core(TM)`, quarantines for review instead) |
| V4 | the server image is the one that release tag pins |
| V5 | p95, verdict and agents per box recomputed from the samples |
| V6 | Little's law between offered and observed concurrency |
| V7 | throughput below the ceiling the trace's tool waits allow |
| V8 | single-stream decode below the memory-bandwidth roofline when the bundle carries memory channels, MT/s and weight bytes (`k3d-gpu share` does not record them yet, so for its bundles V8 falls back to a flat decode ceiling) |
| V9 | energy: J per turn equals power x time / turns |
| V10 | quiet box at start; throttling and drift are tagged |
| V11 | p95 at 2N is not under half of p95 at N (a spliced-run sanity check) |
| V12 | an A/B arm counts toward an uplift only with its pair |

A maintainer merge is the final gate. A data pull request does not touch the
leaderboard: `leaderboard.py --check` rebuilds the board from the bundles
`docs/leaderboard.json` lists as its `inputs`, and reports a new
`data/oracle/` file as pending without failing. After a merge, the maintainer
runs `python3 oracle/leaderboard.py` and commits `docs/leaderboard.{md,json}`,
which adds the bundle to the inputs.

If `--submit` is interrupted, run the same command again. The branch name and
file path come from the bundle's hash, so it reuses the branch and file it
already created and reports the pull request if one is open.

## Leaderboard rules

- **Measured** columns (agents per box, decode tok/s, tok/s/W) come only from
  bundles that pass V1-V12. **Predicted** columns (the V8 bandwidth roofline)
  are labelled as such and never mixed into a measured column. The roofline
  column is not computed yet for bundles `k3d-gpu share` builds (it does not
  record memory channels, MT/s or weight bytes), so it shows `-` there.
- tok/s/W is output tokens per package joule at the agents-per-box step (the
  largest agent count that met the SLO), from that step's samples and its J per
  turn. It is `-` when the bundle carries no per-step energy. The two H6 seed
  bundles carry none because the per-request raw files they are built from have
  no per-step J per turn; the H6 run itself measured RAPL package energy
  ([ledger:H2] reports its J per token).
- The SKU is the CPU plus the device the engine ran on (the iGPU for `igpu`, the
  NVIDIA card for `nvidia`); other devices in the box are not in the label.
- Rows are sorted by SKU, not ranked. Each row links to its bundle and, for
  maintainer runs, to its ledger row.
- A tier says how many independent submitters agree: provisional (one),
  community (two or more within `AGREE` of the median, `oracle/matrix.py`), verified (three, or a
  maintainer run that a community run agrees with).

## Tests

```bash
python3 oracle/test_oracle.py          # schema, projection goldens per fixture, each rule, matrix, share
FUZZ_N=100 python3 oracle/test_fuzz.py # scrubber and validator property fuzz (FUZZ_SEED replays)
python3 oracle/test_adversarial.py     # first-time users: N100, no GPU, WSL2, generic names, interrupted submit
python3 oracle/leaderboard.py --check  # the committed leaderboard is the generated one
```

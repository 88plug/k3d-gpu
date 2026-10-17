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
k3d-gpu capacity                 # a finished agents-per-box run (or the H6-style single-stream files)
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
| V2 | content hash and comparison key; no duplicates |
| V3 | no identifier-shaped string, no private-registry image |
| V4 | the server image is the one that release tag pins |
| V5 | p95, verdict and agents per box recomputed from the samples |
| V6 | Little's law between offered and observed concurrency |
| V7 | throughput below the ceiling the trace's tool waits allow |
| V8 | single-stream decode below the memory-bandwidth roofline |
| V9 | energy: J per turn equals power x time / turns |
| V10 | quiet box at start; throttling and drift are tagged |
| V11 | p95 is monotone in the agent count |
| V12 | an A/B arm counts toward an uplift only with its pair |

A maintainer merge is the final gate. After a merge, the maintainer runs
`python3 oracle/leaderboard.py` and commits `docs/leaderboard.{md,json}`; CI
(`leaderboard.py --check`) fails until they match the committed bundles.

## Leaderboard rules

- **Measured** columns (agents per box, decode tok/s, tok/s/W) come only from
  bundles that pass V1-V12. **Predicted** columns (the V8 bandwidth roofline)
  are labelled as such and never mixed into a measured column.
- tok/s/W is output tokens per package joule at the binding agent count, from
  the step's samples and its J per turn. It is `-` when the run recorded no
  energy; the two H6 seed runs recorded none.
- Rows are sorted by SKU, not ranked. Each row links to its bundle and, for
  maintainer runs, to its ledger row.
- A tier says how many independent submitters agree: provisional (one),
  community (two or more within `AGREE` of the median, `oracle/matrix.py`), verified (three, or a
  maintainer run that a community run agrees with).

## Tests

```bash
python3 oracle/test_oracle.py          # schema, projection goldens per fixture, each rule, matrix, share
FUZZ_N=100 python3 oracle/test_fuzz.py # scrubber and validator property fuzz (FUZZ_SEED replays)
python3 oracle/leaderboard.py --check  # the committed leaderboard is the generated one
```

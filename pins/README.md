# Pins: what moves them and when

Every version, digest and revision k3d-gpu pins in a file of class **ours** or
**eat-stock** has one owner in `pins/registry.toml`. The Freshness workflow
(`.github/workflows/freshness.yml`) moves them to the newest stable upstream release
every 6 hours, one pull request per group, on branch `autobump/<group>`. Nothing else
updates them: Dependabot keeps only the GitHub Actions SHAs, and the old nightly in
`build-and-push.yml` (which pushed to main) is gone.

| Class | Who may change it |
|---|---|
| ours | Freshness PRs, policy-gated (below) |
| eat-stock | Freshness PRs, always reviewed; it follows Intel EAT's latest release |
| arm (a ledger path whose row is PENDING) | only a dated ledger amendment in the same PR; never a bumper |
| historical | never |
| `.github/**` | never by a bot |

Until `pins/classes` exists, `hack/pinlib/classes.py` gives every path its class
(`bench/`, `research/`, `data/` and `oracle/seed/` read as not writable).

## The tools

| Command | What it does |
|---|---|
| `hack/pins check` | offline, CI job `pins`: schema, every site resolves, derived values equal their parents, holds not expired, one owner per pin, known-issue sites inside registry sites, every pinned image / `==` pin / Hugging Face revision in an ours or eat-stock file owned |
| `hack/pins outdated [--json] [--group G]` | network, read-only: the pins behind upstream |
| `hack/pins policy <base> <head>` | the verdict of a bump diff (JSON `{verdict, reasons, group, update_type, urgent, canary, fixes}`) |
| `hack/pins report` | the Freshness dashboard (the 'Freshness dashboard' issue body, or with Issues disabled `freshness-dashboard.md` on branch `ci-status`; always the job summary) and the lag tripwire |
| `hack/pins propose-amendment <row>` | a PENDING row's runner image pins under a dated amendment stub |
| `hack/bump <group>` | resolve, rewrite the sites, run the hooks, substitute goldens, run the group's tests, write the PR body and verdict |
| `hack/pin-only-diff <base> <head>` | is the diff only pin moves (else: changes-defaults) |
| `hack/substitute-goldens OLD=NEW...` | exact-string substitution in `tests/**` only |

`hack/bump` writes the working tree it runs in; try a group in a scratch clone:

```sh
git clone -q . /tmp/k && /tmp/k/hack/bump telemetry --root /tmp/k --dry-run
```

## Add a pin

1. Add a `[[pin]]` to `pins/registry.toml`:

   ```toml
   [[pin]]
   id = "prometheus"            # unique
   class = "ours"               # or eat-stock
   group = "telemetry"          # one PR per group: autobump/telemetry
   family = "telemetry"         # picks the policy row (below)
   scheme = "semver"            # a hack/pinver.py scheme
   source = { kind = "github-release", repo = "prometheus/prometheus", tag = "{v}" }
   confirm = { kind = "oci", image = "quay.io/prometheus/prometheus", tag = "{v}" }  # optional
   canary = ["node"]            # optional: canary/engines.toml ids that must pass
   sites = [
     { file = "share/telemetry/prometheus.yml", ref = "quay.io/prometheus/prometheus" },
     { file = "share/telemetry/prometheus.yml", regex = '^# image quay\.io/prometheus/prometheus:(?P<v>v[0-9.]+)$' },
   ]
   derived = [{ file = "README.md", regex = 'Prometheus (?P<v>[0-9.]+)', from = "{v_bare}" }]
   ```

   Regexes are TOML literal strings (`'...'`): no escaping. A site is
   `{file, regex}` (named groups are the spans: `v`, `digest`, `commit`, a sha key),
   `{file, var}` (a `VAR=value` line), `{file, arg}` (a Dockerfile `ARG`), or
   `{file, ref, tag?}` (every `name:<tag>@sha256:<digest>` reference of that image;
   `digest_only = true` for `name@sha256:` references, `values = true` for a Helm
   `repository:`/`tag:` pair). `count = "any"` lets a regex match more than once.
   A derived entry's group `v` must read `from` filled with the pin's values
   (`{v}`, `{v_bare}`, `{v_major}`, `{v_mm}` or any key a site or the source gives).
2. Pick a source kind: `oci`, `github-release`, `pypi`, `hf`, `apt-index`, `eat`,
   `llama`, `habana`, `json`, `cmd`, `helm`, `github-branch` (docstring of
   `hack/pinlib/sources.py`). A source returns the newest *stable* version, never
   one older than the pin, and errors when it cannot find the tag shape it expects:
   it never falls back to the current value. With `confirm`, a second source must
   agree, or the pin is skipped as unconfirmed (dashboard note).
3. Run `hack/pins check`. Any other pinned image in an ours or eat-stock file is
   owned by `[images.default]` (source oci, scheme docker-suffix, group by path);
   EAT's chart-default images there are frozen (they move with an EAT release, by
   hand). A reference that must never move (an image an earlier release
   installed, which the launcher recognises by it) goes in `[images.default]
   frozen` as `{ file, ref = "name:tag", reason }`.

## Add a group

A `[groups.<name>]` table: `tests` (suites `hack/bump` runs after the rewrite;
CI runs everything again on the PR), `hooks` (built-in steps after the rewrite,
in order: `lock`, `readme-history`, `render-latest`, `ppa-write`, `convert-pins`,
`ledger-rules`, `constraints`, `docs-lock`, `flake-update`, `repin`) and `outputs`
(files those hooks write; `hack/pin-only-diff` accepts any change there).
Coupled components go in one group, so they land in one PR.

## Hold a pin

```toml
hold = { max = "v4.0.0", reason = "helm v4 render output differs", review_by = "2027-01-31" }
```

The bumper stays below `max`. The github-release, oci and pypi sources take the newest
release below it; every other kind reads one newest version, and when that reaches
`max` the pin stays where it is (`hack/bump` notes `held at ...`, also for `--resolved`
values). To freeze a pin at its version, set `max` to the first release after it
(`v0.8.1` for llama.cpp `v0.8.0`, `5.21.0.post0` for PyPI `5.21.0`). `hack/pins check`
fails once `review_by` has passed: review the hold, then move the date or drop it.

## Register a known issue

Add an `[[issue]]` to `pins/known-issues.toml` (schema in its header). Its sites
must lie inside registry sites. Bot sweep's `known-issues` job polls every hour;
when a fix ships it dispatches Freshness for each group whose pins the issue reads,
with `reason=known-issue:<id>`. That PR records `fixed_in` (and narrows
`affected`) and is urgent; when the entry lists workarounds, a second PR on
`autobump/ki-<id>` removes the `known-issue: <id>` markers, labelled
`needs-review` and `changes-defaults`. `adopt = "auto"` is owner consent to take a
patch or minor fix without review; a major always needs review.

## Policy (`hack/pins policy`)

Computed from the diff, the registry, `pins/known-issues.toml`,
`canary/engines.toml` and `hack/ledger-freshness`'s measured versions; labels never
count. **auto** needs green CI at the head and, for each canary engine the pins
name, a canary pass with hard and exact true (`hack/bot-merge` checks both).
**review** needs a human approval at the head.

| Family | auto | review |
|---|---|---|
| k3s | patch | k8s minor and up |
| plugin (device plugins, NRI, CTK) | patch, minor | major |
| runtime-engine (vLLM CPU, OVMS, llama.cpp; runtime canary) | patch, minor | major |
| static-engine (vLLM XPU, llm-scaler, Gaudi, DLStreamer, sglang, NPU UMD) | patch, same-tag digest | minor and up |
| host-driver (compute-runtime/IGC, level-zero, NPU driver) | none | all |
| host-ppa | patch | the rest |
| hf | a README/.gitattributes-only revision diff | config, tokenizer, template or weights |
| pypi, tools, telemetry, opt-in | patch, minor | major |
| chrome | everything | none |
| base-image | same-tag digest, patch | a tag line change (busybox 1.37 to 1.38, ubuntu, python 3.14 to 3.15) |
| eat-stock | none | always |

Across every family:

- a major is review; a 0.x minor counts as a major unless the pin has a runtime
  canary (a `runtime` or `node` engine in `canary/engines.toml`);
- a pin whose canary engines `canary/engines.toml` does not define is review;
- a minor or major move of a component a SUPPORTED `console/ledger-rules.json` row
  measured is review (`ledger-defaults`; today OVMS and llama.cpp);
- a changed line that is not a pin move is review (`changes-defaults`);
- a hold reached, or more than one group in a PR, is review;
- a diff that fixes a known issue is urgent.

## Ledger rows (arms)

A PENDING row's runner pins change only by a dated amendment pushed before the row
runs. The dashboard lists the PENDING rows `pins/classes` gives runner paths, each
with its command; a human runs it:

```sh
gh workflow run freshness.yml -f propose_amendment=H53
```

The job refuses a row that is not PENDING, has a `### <row or alias> run` heading,
or that an open PR touches; otherwise it opens `autobump/amend-<row>` (labels
`ledger-amendment`, `needs-review`), never bot-merged.

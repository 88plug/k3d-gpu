# Console design: the answer layer for agents on Intel hardware

Status: design. Static research only; nothing in this document was run on a
cluster. Research date for every external source: **2026-10-16** unless a
different date is given. Repo facts were read from `origin/main` (`bb9c912`) and
the `origin/claude/*` branches named inline.

How to read it:

- **Evidence** is a dated primary source, a repo file, or a ledger row.
- **Opinion** is marked as opinion. Nothing here comes from a user study yet;
  that gap is a gate in the MVP (section 6), not a footnote.
- Performance numbers follow `tests/claims_check.sh`: a source link on the same
  line, or a SUPPORTED `[ledger:Hn]` row. Numbers that do not meet that bar are
  not stated.

## 0. The short version

k3d-gpu should not grow a dashboard. It should grow **one facts store and
several renderers**. The user's terminal, the user's own agent (through MCP), a
static Proof Pack a field engineer can email, and the OTel stream the customer
already collects all read the same JSON.

The product answers three questions that no agent console answers today, with a
measurement attached:

1. **What runs on this box, right now, with one command?** (`k3d-gpu launch`)
2. **How many agents at what p95, on which engine, and what limits it?**
   (`k3d-gpu fit`, `k3d-gpu prove`)
3. **Why is it slow or broken, and what is the fix?** (`k3d-gpu why`, the
   firmware gatekeeper)

EAT gets adopted because k3d-gpu becomes its one-command, pinned,
clean-teardown path, and because `prove --with eat` shows on the customer's own
box what EAT's defaults deliver next to what k3d-gpu picks.

## 1. User truths (evidence)

Each truth carries its source. Star counts and dates were read through the
GitHub API on 2026-10-16.

| # | Truth | Evidence |
|---|-------|----------|
| T1 | Users adopt verbs plus an endpoint, then a separate chat UI. They do not adopt ops consoles. | ollama/ollama 183,503 stars; open-webui/open-webui 155,932 stars. Ollama's 2026-01-23 growth feature is a verb: `ollama launch`, "No environment variables or config files needed" (https://ollama.com/blog/launch). |
| T2 | The general Kubernetes web dashboard is dead; the terminal UI wins. | kubernetes-retired/dashboard archived, last push 2026-01-21, README points to Headlamp (https://github.com/kubernetes-retired/dashboard). derailed/k9s 34,825 stars vs kubernetes-sigs/headlamp 7,457. Stars are a popularity proxy, not usage. |
| T3 | Agent builder canvases are being shut down; code-first SDKs survive. | FlowiseAI/Flowise archived (code freeze 2026-07-29, archive 2026-08-10, EOL 2026-08-31), citing coding agents (https://flowiseai.com/sunset). OpenAI Agent Builder and Evals shut down 2026-11-30 (https://developers.openai.com/api/docs/deprecations). EAT still lists Flowise. |
| T4 | Teams already have observability. The production killers are quality and latency; for large orgs, security. | LangChain State of Agent Engineering, fielded Nov 18 - Dec 2 2025, n=1,340: 89% have observability, quality 32% top barrier, latency 20% (https://www.langchain.com/state-of-agent-engineering). |
| T5 | Developers will not hand deployment and monitoring to an AI that acts on its own. | Stack Overflow 2025: 76% do not plan to use AI for deployment and monitoring; 46% distrust AI accuracy (https://survey.stackoverflow.co/2025/ai). |
| T6 | On Intel hardware the blocker is the software stack, not the silicon. | Arc Pro B70 "a complete mess" (r/LocalLLaMA, 2026-04-09, https://www.reddit.com/r/LocalLLaMA/comments/1sgdt7t/). GuC 70.60.0 hand-dropped into `/lib/firmware/xe/` (https://bentech.substack.com/p/running-local-llms-on-intel-arc-pro, 2026-04-13). Ollama Intel Arc support still open since 2023-12 (https://github.com/ollama/ollama/issues/1590). |
| T7 | Intel archives the engines users build on, and users get stranded. | intel/ipex-llm archived, last push 2026-01-28. Users unable to run new models afterwards (https://www.reddit.com/r/IntelArc/comments/1tzqldv/, 2026-06-07). About two dozen Intel OSS projects ended since late 2025 (https://www.phoronix.com/news/Intel-OSS-Projects-Ended-2025, 2026-02-08). |
| T8 | EAT has almost no organic pull, and its filed issues are install, teardown, proxy and registry failures. | Stars: enterprise-agent-toolkit 41, enterprise-ai-solutions 8, enterprise-inference 6. Teardown leaves namespaces, CRDs and secrets (https://github.com/intel/enterprise-ai-solutions/issues/38). Bitnami deprecation broke image pulls (https://github.com/opea-project/Enterprise-Inference/issues/24). |
| T9 | Supply chain is the loudest signal in the sweep, and pinned images were the control that worked. | LiteLLM 1.82.8 credential stealer, 1,117 reactions, 2026-03-24 (https://github.com/BerriAI/litellm/issues/24512). "Proxy Docker image users were not impacted, all dependencies are pinned" (https://github.com/BerriAI/litellm/issues/24518). EAT's vLLM chart ships with no auth by default (https://github.com/intel/enterprise-agent-toolkit/issues/9, 2026-08-13). |
| T10 | Upgrades break self-hosted AI stacks. | Dify knowledge unusable after 1.9.2 (https://github.com/langgenius/dify/issues/27291). Langfuse ClickHouse broken after upgrade (https://github.com/langfuse/langfuse/issues/12172). n8n connection lost after 1.88.0 (https://github.com/n8n-io/n8n/issues/14653). |
| T11 | "Will it run on my hardware" is a mainstream question, but answered for memory fit only. | Hugging Face hardware compatibility panel estimates whether a quantization fits (https://huggingface.co/docs/hub/en/hardware). Users ask which model fits which Intel engine (https://github.com/intel/llm-scaler/issues/348). No tool in the sweep reports agents at a p95 SLO. That is absence of evidence, not proof. |
| T12 | Auth needs are bimodal, and a naive single-user mode is a one-way door. | Open WebUI: "allow login without authentication" and OIDC are both top requests (https://github.com/open-webui/open-webui/issues/483); its single-user mode cannot later switch to multi-account (https://docs.openwebui.com/getting-started/quick-start). LiteLLM OSS SSO is capped at 5 users. |
| T13 | None of EAT's UIs can be embedded. | LiteLLM v1.105.1 sets `X-Frame-Options: DENY`; Langfuse v4.63.0 sets `frame-ancestors 'none'`; Grafana 13.2.4 defaults `allow_embedding = false` (read from source at those tags). |

Repo truths (evidence from this repo):

- One measured, SUPPORTED engine result exists, on an i9-13900T with UHD 770
  running Qwen3-4B int4: OVMS on the P-core balloon decodes 15.7 vs 9.9 tok/s on the iGPU [ledger:H6].
- The often-quoted "OVMS CPU vs EAT's vLLM-CPU" ratio is **confounded**.
  `bench/apb/PROTOCOL.md` on `origin/claude/apb-baseline` compares OVMS int4
  against vLLM-CPU bf16 on one host. Its EAT capacity row is a pre-registered
  prediction, "not re-measured here". It measures precision plus engine, not
  engine. This document does not use that ratio as a claim; section 6 requires
  a matched-precision re-run first.
- H1 (EAT on one non-Xeon box) reads PENDING on `main`'s ledger. The
  `claude/eat-on-k3d` branch reports a live run. The ledger verdict must be
  updated before any doc claims it.
- The fixture set has **14 hosts** under `tests/fixtures/`: alderlake-n,
  arrowlake-h, arrowlake-s, battlemage-b580, gaudi3-hls, gaudi3-no-driver,
  pantherlake, qat-420xx, raptorlake-nvidia, xeon-gnr, xeon-gnrd, xeon-max-hbm,
  xeon-spr-accel, xeon-srf. Every golden in this design covers all 14.

## 2. Dogmas broken

| Dogma | Verdict | Why |
|-------|---------|-----|
| "Users want a dashboard." | Broken, provisionally | T1, T2, T4. The UI people adopt is chat (Open WebUI), not ops. Opinion until the section 6 user test passes; Open WebUI and Headlamp stay as cheap escape valves. |
| "Agents need a builder canvas." | Broken | T3. Drop Flowise from EAT-on-k3d. Code-first templates written by the user's coding agent replace it. n8n survives for business automation, so it is an optional add-on, never default. |
| "Observability means traces." | Broken | T4. Users have traces and still cite latency. Traces carry no hardware. Ship the cause (`why`) and export hardware attributes into the user's existing OTel backend. |
| "Capacity planning is an ops chore." | Broken | T11. Capacity is a buying question. It is the fit card a buyer forwards. |
| "Enterprise = Keycloak + RBAC first." | Broken | T8, T12. Loopback means no login. Identity is a LiteLLM key from run one. Teams are added, never migrated. |
| "Users pick the model and engine." | Broken | Auto-adapt picks from measured cells. Flags are overrides only. |
| "Autopilot ops is the AI feature." | Broken | T5. Read-only by default. Every write is a plan with evidence and a hash a human confirms. |
| "A laptop number predicts a server." | Broken | Package-power coupling and memory topology differ per box. `promote` re-measures on the target; nothing extrapolates across families. |
| "Users should see Kubernetes." | Broken for the solo path | k3s stays the substrate and kubeconfig stays an escape hatch, but the solo user never needs to type `kubectl`. |
| "Ship more EAT features." | Broken | T8. EAT's gap is the first 40 minutes, not features. |

## 3. Product thesis

**k3d-gpu is the hardware-truth and answer layer under every agent stack on
Intel.** One command takes a bare Intel box (AI PC, workstation, Xeon, Gaudi) to
a working agent on a pinned, pre-baked engine picked from what the box measures
as. Every question about the box (what runs, how many, why slow, what to buy)
returns a measured answer in the surface the asker already uses.

It does **not** compete with EAT's gateway, auth or tracing (LiteLLM, Keycloak,
Langfuse, Grafana). It removes the need to open them for the questions above,
and it makes EAT install and tear down in one command.

The funnel has two doors on one backend:

- **Developer door**: bare `k3d-gpu` and `k3d-gpu launch <tool|framework>`.
  Ollama-grade time-to-first-agent (T1).
- **Deal door**: `k3d-gpu prove --with eat [--audit]`. A Proof Pack an Intel
  field engineer leaves with the customer (T8, T9).

Opinion: `launch` is an on-ramp, not a moat. If Ollama ships Intel Arc support
(issue 1590 above), `launch` alone is copyable. The moat is the measured matrix,
`fit`, `why` and the gatekeeper. Build those first.

## 4. Surfaces, one backend

```
          +-----------+   +-----------+   +--------------+   +-----------+
 human -> |  CLI      |   | MCP       |   | Proof Pack   |   | OTel      | -> Grafana/Langfuse
          |  text     |   | stdio,    |   | report.html  |   | resource  |    the user already
          |  --json   |   | later HTTP|   | + JSON files |   | attributes|    runs
          +-----+-----+   +-----+-----+   +------+-------+   +-----+-----+
                \_______________ \_______________/_________________/
                                  | pure renderers, no I/O of their own
                          +-------v--------+
                          |   ANSWERS      |  fit, why, status, audit (deterministic)
                          +-------+--------+
                          | facts/v1 store |  detect, doctor --perf, report, capacity
                          +----------------+
```

Rule: **a renderer never computes**. If the CLI and MCP disagree, that is a test
failure (MVP criterion S6).

**CLI (primary, human).** New verbs:

| Verb | Does | Source |
|------|------|--------|
| `k3d-gpu` (bare) | Guided first run: detect, preflight doctor, pick a cell, `up` + `agent up`, print endpoint, fit line, next step. | New; today bare `k3d-gpu` prints usage. |
| `launch <claude-code\|codex\|opencode\|langgraph\|crewai\|openai-agents\|llamaindex\|eat>` | Writes the tool's own config into a scoped profile, registers MCP, prints the diff; `--undo` reverts. Framework targets scaffold from `claude/examples-agent-frameworks`. | New |
| `fit [--card] [--target N --slo p95=Xs]` | Agents at p95 per green cell, binding limit, next bottleneck; `--target` sizes a fleet from measured cells only. | `claude/capacity-cmd` |
| `prove [--with eat] [--audit]` | Builds the Proof Pack. | Composes `report`, `doctor --perf`, `capacity`, `claude/eat-on-k3d` |
| `why` | Ranked causes with evidence and a dry-run fix. `unknown` is a valid answer. | New, on `probes/` + `doctor.golden` pattern |
| `status` | One screen: engine, model, endpoint, agents, p95 over 5 min, power. | New |
| `share` / `unshare` | Off-loopback bind turns on OIDC. | New |
| `upgrade [--rollback]` | Release-train upgrade with PVC snapshot. | `claude/upgrade-safety` |
| `down [--purge]` | Clean teardown, no host residue. | Existing `down` |

Existing verbs (`up`, `detect`, `doctor`, `report`, `agent`, `host-setup`,
`test`, `logs`) stay as the expert layer. Every verb takes `--json` against
facts/v1.

**MCP (primary, agent).** Extend `origin/claude/k3d-gpu-mcp` (stdlib Python,
stdio, speaks MCP 2026-07-28 plus legacy `initialize`, 4 read tools:
`accelerators`, `recommend_placement`, `agent_endpoint`, `cluster_health`).

- Read tools: `hardware`, `fit`, `why`, `status`, `prove_summary`,
  `audit_posture`, `matrix_lookup`.
- Propose tools return a plan: `plan_launch`, `plan_tune`, `plan_upgrade`.
  A plan holds a diff, the evidence fact ids, a predicted delta and a hash.
- `apply(plan_hash)` runs only a hash the human confirmed in the client.
- `share` is never exposed over MCP.
- Streamable HTTP comes after stdio: 127.0.0.1 bind, Origin check with 403 on
  mismatch, 0600 token (https://modelcontextprotocol.io/specification/2026-07-28).
- MCP Apps cards (fit, why, plan diff) are a stage-3 option, not MVP.

**Proof Pack (the artifact an enterprise forwards).** A directory with
`MANIFEST.sha256`: `facts.json`, `capacity.<engine>.json`, `images.lock` (read
from the running cluster), `sbom.spdx.json` (generated at build, copied in),
`doctor.txt`, `audit.json`, and a self-contained `report.html` with no network
fetches. `--sign` uses a customer-held key; it is integrity, not attestation, and
is never marketed as attestation.

**OTel export.** Resource attributes such as `k3d_gpu.engine`,
`k3d_gpu.isa.amx`, `k3d_gpu.cpu.pe_split`, `k3d_gpu.hpu.fw`, plus per-call
joule receipts, sent to whatever collector the user runs. No new chart stack.

**Chat (optional).** Open WebUI from `claude/chat-ui`, wired to LiteLLM keys,
never in its irreversible no-auth mode (T12).

**Deep links only** to LiteLLM, Langfuse and Grafana UIs (T13). Their data is
read server-side (LiteLLM model and spend APIs, Langfuse `/api/public/metrics`,
Prometheus) into `status`, `why` and the Proof Pack.

**Not built:** a bespoke web console, a canvas, a second Grafana. A Headlamp
v0.46.0 plugin (released 2026-10-15) stays a documented opt-in for teams that
want Kubernetes objects.

## 5. Architecture inside k3d-gpu

Language: bash plus Python standard library, matching `scripts/k3d-gpu` and the
MCP branch. No Go or Node toolchain until stdlib streamable HTTP proves
insufficient (then go-sdk v1.8.0, released 2026-09-14).

**L0 Host truth.** Existing detectors, NFD labels and the experimental
auto-adapt framework. Add a firmware/driver floor table per device id. The GuC
glob table already exists on `claude/capacity-cmd` (`scripts/k3d-gpu`, the
"Intel GPU id -> name | kernel binding | GuC firmware glob" map). Below the floor,
doctor refuses that engine, prints a one-line fix, and policy falls back to a
green CPU or llama.cpp-Vulkan cell. The user still gets an agent today (T6).

**L1 Facts store.** `facts/v1`, a JSON Schema checked into the repo. It holds
hardware, firmware, engine, doctor findings, capacity rows and matrix rows, each
with `measured_on`, `report_id`, `release`, host fingerprint, reps and
confidence interval. It is written by `detect`, `doctor`, `report`, `capacity`
to `~/.local/state/k3d-gpu/` and a ConfigMap. Golden-tested against all 14
fixtures.

**L2 Matrix.** Rows of (Intel family, device, engine@digest, model@digest,
precision). Each row is `green`, `amber` or `grey`:

- **green**: a `report` run exists, it passed a baked tool-calling eval smoke
  test (quality is the top barrier, T4), and its engine pin is current.
- **amber**: a community `prove` pack, unreviewed.
- **grey**: unmeasured. Prints "unmeasured, run `k3d-gpu prove` on this box".
  Never interpolated, never estimated.

Cells expire when their engine pin is bumped. Coverage is published as-is: the
matrix starts with the boxes actually available and says so.

**L3 Policy.** A pure function `plan(facts, matrix, overrides)` returns engine,
model, placement (P/E balloons), port and auth mode. No I/O, so it is
property-tested (`claude/launcher-property-tests`) and `--explain` prints the
path. Engines are plural so no single Intel archive strands a user (T7): OVMS
(CPU, iGPU, NPU), llm-scaler vLLM (Arc B-series), llama.cpp Vulkan/SYCL
(universal fallback), vLLM on Gaudi. IPEX-LLM is never used.

**L4 Answers.** `fit`, `status`, `audit`, `why`. `why` is a rule table: evidence
signature, evidence query, cause, fix, dry-run diff. Each rule has a golden
fixture. An LLM may narrate the output; it never decides it.

**L5 Plans.** Typed writes: `{kind, diff, evidence[], predicted, hash}`.
`apply` re-checks preconditions, applies, re-measures with reps, and records
predicted vs actual in a ledger. There is **no automatic revert in the MVP**:
p95 on one shared box moves with thermals and package power, so a fixed
tolerance would flap. Revert is offered with the measurement, and a human
decides. Auto-revert waits until the ledger shows the run-to-run spread per
box family.

**L6 Recipes and release train.** Pinned stack definitions: `eat`, `open-webui`,
`n8n` (opt-in), `headlamp` (opt-in). Each is charts plus image digests plus an
auth-mode mapping.

Bake-at-build:

- Images are referenced by digest and pre-loaded into the k3d node image or the
  air-gap bundle. Charts are vendored, so a registry deprecation cannot break a
  pull (T8).
- Models ship as OCI artifacts pinned by digest. They arrive at install time or
  from an offline bundle or local registry, never at pod start. Install is the
  single network moment, and the docs say so.
- Framework scaffolds install from a baked wheelhouse with hashed lockfiles,
  never from live PyPI (T9).
- The SBOM is produced at build per release.
- `prove --audit` diffs the containerd image list against `images.lock` and
  FAILs on any extra image or runtime fetch.

Pins verified on GitHub releases, 2026-10-16:

| Component | Pin | Released | Role |
|-----------|-----|----------|------|
| LiteLLM | v1.105.1 | 2026-10-14 | Gateway, keys, budgets (not the 1.106/1.107 prereleases) |
| Langfuse | v4.63.0 | 2026-10-16 | Opt-in profile only when facts show RAM/core headroom |
| dex | v2.46.0 | 2026-10-07 | OIDC broker for `share` |
| oauth2-proxy | v7.15.5 | 2026-10-01 | Front for apps without auth |
| Headlamp | v0.46.0 | 2026-10-15 | Opt-in K8s UI |
| MCP spec | 2026-07-28 | 2026-07-28 | Stateless protocol revision |

The scheduled bump job (`claude/autobump-new-components`) moves pins in-repo and
re-runs the fixture goldens and matrix jobs. Nothing resolves `@latest` at boot.

`upgrade` snapshots PVCs, applies the train, smoke-tests one agent round trip
plus one matrix-cell spot check, and `--rollback` restores the snapshot (T10).

**Auth auto-adapt** (flags override):

| Detected state | Mode |
|----------------|------|
| Listener on loopback only | No login. LiteLLM master key in a 0600 file. Origin check on HTTP. |
| Any non-loopback listener (`share`) | OIDC mandatory: baked dex + oauth2-proxy. Groups map to LiteLLM teams and keys through the key API, staying clear of the OSS SSO cap. |
| EAT/EI Keycloak detected | Federate to it. Never install a second Keycloak. |

Identity is a LiteLLM key from the first run, so every mode change is additive.

**Energy receipts, not steering.** Per-call joules (RAPL, `hl-smi` on Gaudi,
sysfs or nvidia-smi on GPUs) become OTel attributes and roll up per key for
chargeback. Without RAPL privilege the field reads `unavailable`, never zero.
Steering is not built: the in-repo simulation on
`origin/claude/frontier-energy-in-the-loop-mcp-per-call-joule-re` found the
oracle bound too small to beat a fair static pick. That is a repo finding, not a
ledger verdict.

**Sandbox (stage 3).** Per-agent namespace, default-deny egress NetworkPolicy,
and a gVisor or Kata RuntimeClass when the host supports it (detected, baked).

## 6. MVP

The MVP is two slices with a validation gate between them. Slice S is static and
buildable now without touching the live integration on this host. Slice L needs
a free host.

**Gate 0, before building past slice S.** Run a paper test with 5 Intel
developers and 3 Intel field engineers. Show them CLI transcripts of the bare
first run, `fit` and a rendered Proof Pack from fixtures. Pass: at least 4 of 5
developers say they would run it, and at least 2 of 3 field engineers say they
would leave a Proof Pack with a customer. Fail means fix the flow before adding
verbs. If users ask for pixels, the escape valve is Open WebUI or Headlamp, not
a new console.

### Slice S: the facts spine (static, fixture-tested)

Scope: facts/v1 schema and normalizer; `fit` rendered from facts; `prove`
rendering from a facts directory; `prove --audit` rules; the firmware
gatekeeper decision; MCP `prove_summary` and `fit` reading facts.

| # | Acceptance criterion | Proof |
|---|----------------------|-------|
| S1 | facts/v1 validates for all 14 fixture hosts; output is byte-stable across two runs. | Golden per fixture in CI |
| S2 | `plan()` picks the best green cell for every fixture; `--explain` output is golden. A fixture with no green cell prints "unmeasured, run prove". | Golden + property tests |
| S3 | A stale-GuC fixture: doctor refuses the GPU engine, prints a one-line fix, policy falls back to a green CPU or Vulkan cell. `gaudi3-no-driver` returns a fix, not an engine. | Golden |
| S4 | `report.html` loads with networking blocked and holds no external URL except deep links. | Headless load in CI with network off |
| S5 | `--audit` FAILs a fixture with an unauthenticated non-loopback listener (EAT#9 class) and FAILs a fixture with an image missing from `images.lock`; it passes the default loopback layout. | Fixture tests |
| S6 | MCP `prove_summary` and `fit` output equals the CLI `--json` for the same facts. | Test asserts equality |
| S7 | Every write path over MCP without a confirmed plan hash is refused. | Test |
| S8 | `tests/claims_check.sh`, shellcheck and the existing suite pass. | CI |

### Slice L: the live proof (after the host is free)

Scope: bare `k3d-gpu` first run; `launch openai-agents` and `launch
claude-code`; `fit` from a live `capacity` run; `prove --with eat`.

| # | Acceptance criterion | Proof |
|---|----------------------|-------|
| L1 | **Time-to-first-agent under 5 minutes, cold**, on a stock AI PC (Core Ultra) with the k3d-gpu package installed and nothing else started: from bare `k3d-gpu` to the first streamed token. Measured by the command and printed. | Timer in the command, 3 runs, median |
| L2 | **Warm re-run under 60 seconds** to first streamed token. | Same timer |
| L3 | **Capacity answer in one command**: `k3d-gpu fit` prints agents at p95, engine, binding limit. Its p95 lands within 15 percent of an independent `report` run (3 runs, median). | Two independent runs |
| L4 | Zero pulls after install: the full L1 flow with egress blocked at the host firewall and a deny NetworkPolicy; pod-start pull count 0. | Containerd event log |
| L5 | `launch openai-agents` scaffolds, runs, prints a model reply and a hashed lockfile; it re-runs offline. | Run log |
| L6 | `launch claude-code` completes one tool-using turn (an MCP `hardware` call) with no env vars set by the user; the config diff is shown and `--undo` restores it. The launch adapter refuses, with a reason, when no green cell on the box passes the tool-calling eval. | Session transcript |
| L7 | `prove --with eat` produces a pack showing EAT's default engine and the auto-picked engine side by side, **at matched precision**, with reps and confidence intervals. The ratio is computed from that run, never hard-coded. Wall clock for the whole command is printed. | Pack + timer |
| L8 | `k3d-gpu down` after `prove` leaves no k3d-gpu-created namespaces, CRDs or secrets. | Host diff |
| L9 | Blind first run: at least 4 of 5 Intel users reach a working agent with no docs. | Observed sessions |

L1 is the headline. The 60-second number is warm-only and is reported separately
from the cold number, never in its place.

## 7. Staged roadmap

Each stage ships one verified change at a time.

| Stage | Ships | Exit criterion |
|-------|-------|----------------|
| 0 | This doc; ledger hygiene: H1 verdict updated from the `eat-on-k3d` run; a new hypothesis for OVMS vs vLLM-CPU at matched precision. | Ledger rows exist with probes |
| 1 | Slice S. Gate 0 user test. | S1-S8 green; gate passed |
| 2 | Slice L. Matrix seeded from boxes actually available (Raptor Lake, B580 and Gaudi 3 fixtures have real hosts behind them); coverage published as-is. | L1-L9 green |
| 3 | `why` with three rules and injected-fault goldens: prefill package-power coupling, thermal throttle from `doctor --perf`, engine mis-pick. Plans with hash gate; predicted-vs-actual ledger. MCP streamable HTTP. | Each rule ranks its fault first on its golden; `unknown` on a clean fixture |
| 4 | `share` (dex, oauth2-proxy, Keycloak federation, key-API group mapping); energy receipts and `report --chargeback`; per-agent sandbox. | Loopback-to-team switch with no data migration, proven on one box |
| 5 | `launch eat` with Flowise dropped; `upgrade --rollback` across one real train bump; air-gap `bundle export/import` with a measured size. | Clean upgrade and rollback; bundle size published |
| 6 | `promote --to` (re-prove on target; emit EI Helm values and NFD labels). OpenShift AI HardwareProfile output only once a matching host exists to test it. MCP Apps cards. | Delta report from a real second box |
| Ongoing | Upstream PRs: EAS#35-38 installer and teardown fixes, EAT#9 auth default, Flowise removal. Propose k3d-gpu as EAT's documented single-box path. | PRs filed and tracked |

## 8. Risks and kill criteria

| Risk | Mitigation | Kill or pivot criterion |
|------|------------|-------------------------|
| The "no dashboard" thesis is inference from stars, archives and vendor direction. | Gate 0; keep Open WebUI and Headlamp as escape valves. | Gate 0 fails twice after flow fixes: build a Headlamp plugin as the primary UI instead. |
| A small local model fails agentic tool use, so the first impression is the worst one. | Green cells must pass the tool-calling eval; `launch` refuses rather than disappoints. | No green cell on a stock AI PC passes the eval: drop `launch claude-code` from the headline and lead with frameworks plus `fit`. |
| A wrong number in a public fit card or Proof Pack. | No extrapolation; reps and CI on every number; cells expire on bumps; confounded comparisons are not published. | Any published number refuted by an independent re-run: pull it, publish the correction in the ledger falsification log. |
| The matrix stays mostly grey for lack of lab hardware. | Publish coverage honestly; accept community packs as amber. | Fewer than 3 families green by stage 2 exit: re-scope the product claim to the families measured. |
| `prove --with eat` is slow and the air-gap bundle is huge (Langfuse brings ClickHouse, Postgres, Redis, S3). | Langfuse is opt-in; Grafana is never baked twice; the bundle is split per recipe. | `prove --with eat` wall clock exceeds one customer meeting (60 minutes) on the reference Xeon: ship `prove` without EAT as the default deal artifact. |
| Plan-apply on a noisy box regresses a working setup. | Read-only default; hash-confirmed plans; no auto-revert until spread is measured. | Predicted-vs-actual ledger shows misses wider than the run-to-run spread: withdraw `plan_tune` and keep `why` advisory. |
| HTTP MCP is reachable from a malicious page. | Loopback bind, Origin check, token file, `share` never over MCP. | Any audit finding of an unauthenticated non-loopback listener in a default layout blocks release. |
| Upstream churn (LiteLLM and Langfuse ship several releases a week; Gaudi user-space code archived per https://www.phoronix.com/news/Intel-SynapseAI-Stops). | Digest pins, scheduled bumps, disclosed dependency in the Proof Pack. | An engine is archived: swap its cells within one release train or mark them grey. |
| Ollama ships Intel Arc and erodes `launch`. | Moat is the matrix, `fit`, `why`, gatekeeper, Proof Pack. | Then `launch` becomes an adapter that can target an Ollama endpoint too. |
| Scope creep across verbs and recipes. | One change per release; stages gate. | A stage that misses its exit criterion blocks the next. |

## Appendix: what each source supports

Evidence used above, all retrieved 2026-10-16 unless dated:

- Adoption: https://ollama.com/blog/launch, https://news.ycombinator.com/item?id=36802582, https://ollama.com/blog/openai-compatibility
- Dashboards and canvases: https://github.com/kubernetes-retired/dashboard, https://flowiseai.com/sunset, https://developers.openai.com/api/docs/deprecations, https://github.com/kubernetes-sigs/headlamp/releases/tag/v0.46.0
- Surveys: https://www.langchain.com/state-of-agent-engineering, https://survey.stackoverflow.co/2025/ai, https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027
- Intel stack pain and abandonment: https://www.reddit.com/r/LocalLLaMA/comments/1sgdt7t/, https://bentech.substack.com/p/running-local-llms-on-intel-arc-pro, https://www.phoronix.com/news/Intel-OSS-Projects-Ended-2025, https://github.com/intel/llm-scaler/issues/348
- EAT state: https://github.com/intel/enterprise-agent-toolkit/issues/9, https://github.com/intel/enterprise-ai-solutions/issues/38, https://github.com/opea-project/Enterprise-Inference/issues/24
- Supply chain: https://github.com/BerriAI/litellm/issues/24512, https://github.com/BerriAI/litellm/issues/24518
- Protocol: https://modelcontextprotocol.io/specification/2026-07-28, https://github.com/modelcontextprotocol/go-sdk
- Repo: `EXPERIMENTS.md` (H1, H6), `bench/apb/PROTOCOL.md` on `origin/claude/apb-baseline`, branches `claude/{k3d-gpu-mcp, capacity-cmd, eat-on-k3d, examples-agent-frameworks, chat-ui, autobump-new-components, launcher-property-tests, upgrade-safety, report-bench}`.

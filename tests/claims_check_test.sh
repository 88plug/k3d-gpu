#!/usr/bin/env bash
# Tests for tests/claims_check.sh. Each case builds a throwaway repo tree with a
# README, docs/, EXPERIMENTS.md and docs/provenance.md, then runs the guard on it.
#
# Usage: tests/claims_check_test.sh

# Doc fixtures hold literal backticks inside single quotes.
# shellcheck disable=SC2016

set -uo pipefail

GUARD="$(cd "$(dirname "$0")" && pwd)/claims_check.sh"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

LEDGER='# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H1 | one | p | probe | PENDING |
| H2 | two | p | probe | **SUPPORTED** |
| H3 | three | p | probe | REFUTED |

## Falsification log

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| H9 | not a hypothesis row | x | x | x |
'

PROVENANCE='# Provenance

| # | Claim | Source | Retrieved |
|---|-------|--------|-----------|
| P1 | upstream number | https://example.com/bench | 2026-10-16 |
| P2 | no source |  | 2026-10-16 |
'

# repo <readme-text> [docs-file-text]: a fresh repo with the default ledger and provenance.
repo() {
    local r
    r=$(mktemp -d "${WORK}/repo.XXXXXX")
    mkdir -p "${r}/docs"
    printf '%s\n' "${LEDGER}" > "${r}/EXPERIMENTS.md"
    printf '%s\n' "${PROVENANCE}" > "${r}/docs/provenance.md"
    printf '%s\n' "$1" > "${r}/README.md"
    if [ -n "${2:-}" ]; then printf '%s\n' "$2" > "${r}/docs/guide.md"; fi
    echo "${r}"
}

# check <name> <root> <expect: 0|fail> [output-regex]
check() {
    local name=$1 root=$2 want=$3 pat=${4:-} out rc ok=1
    out=$("${GUARD}" "${root}" 2>&1)
    rc=$?
    if [ "${want}" = 0 ] && [ "${rc}" -ne 0 ]; then ok=0; fi
    if [ "${want}" = fail ] && [ "${rc}" -eq 0 ]; then ok=0; fi
    if [ -n "${pat}" ] && ! grep -Eq -- "${pat}" <<< "${out}"; then ok=0; fi
    if [ "${ok}" = 1 ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (rc=${rc}, want ${want}, pattern '${pat}')"
        sed 's/^/  | /' <<< "${out}"
    fi
}

# --- clean docs pass ---------------------------------------------------------
check "plain docs with no claims pass" "$(repo 'Run `k3d-gpu up` to start a cluster.')" 0
check "everyday 'only' and 'first' are not superlatives" \
    "$(repo 'The plugin only runs where its label is set. The first match wins. The only Gaudi path is here.')" 0
check "numbers without a unit are not claims" "$(repo 'Use k3s v1.33 with 2 GPUs and 16 cores.')" 0

# --- numeric claims ----------------------------------------------------------
check "tok/s without a reference fails" "$(repo 'The iGPU serves 42 tok/s.')" fail 'README.md:1: .*no ledger or provenance'
check "tokens per second spelled out fails" "$(repo 'It hits 42 tokens per second.')" fail 'README.md:1'
check "tokens/s fails" "$(repo 'It hits 42.5 tokens/s.')" fail 'README.md:1'
check "J/token fails" "$(repo 'Energy is 0.8 J/token.')" fail 'README.md:1'
check "joules per token fails" "$(repo 'Energy is 0.8 joules per token.')" fail 'README.md:1'
check "Nx faster fails" "$(repo 'Pinning is 3x faster.')" fail 'README.md:1'
check "N× speedup fails" "$(repo 'A 2.5× speedup over CPU.')" fail 'README.md:1'
check "percent fails" "$(repo 'Latency drops by 30%.')" fail 'README.md:1'
check "percent with a space fails" "$(repo 'Latency drops by 30 %.')" fail 'README.md:1'

# --- superlatives ------------------------------------------------------------
check "fastest fails" "$(repo 'The fastest way to run k3s on a GPU.')" fail 'README.md:1'
check "first launcher fails" "$(repo 'This is the first launcher for Intel NPUs.')" fail 'README.md:1'
check "only tool fails" "$(repo 'It is the only tool that does this.')" fail 'README.md:1'
check "world's only fails" "$(repo "The world's only Gaudi path.")" fail 'README.md:1'
check "world's only with a curly apostrophe (U+2019) fails" "$(repo $'The world\xe2\x80\x99s only Gaudi path.')" fail 'README.md:1'
check "first-ever fails" "$(repo 'A first-ever Gaudi path.')" fail 'README.md:1'
check "first to fails" "$(repo 'We are the first to ship this.')" fail 'README.md:1'
check "superlatives are case-insensitive" "$(repo 'FASTEST possible.')" fail 'README.md:1'

# --- references --------------------------------------------------------------
check "claim citing a SUPPORTED ledger entry passes" "$(repo 'It hits 42 tok/s [ledger:H2].')" 0
check "claim citing a PENDING ledger entry fails" "$(repo 'It hits 42 tok/s [ledger:H1].')" fail 'H1.*PENDING'
check "claim citing a REFUTED ledger entry fails" "$(repo 'It hits 42 tok/s [ledger:H3].')" fail 'H3.*REFUTED'
check "claim citing an unknown ledger entry fails" "$(repo 'It hits 42 tok/s [ledger:H7].')" fail 'H7.*not in EXPERIMENTS.md'
check "ledger rows outside the hypothesis table do not count" "$(repo 'It hits 42 tok/s [ledger:H9].')" fail 'H9.*not in EXPERIMENTS.md'
check "claim citing a provenance entry passes" "$(repo 'Upstream reports 42 tok/s [provenance:P1].')" 0
check "provenance entry without a source fails" "$(repo 'Upstream reports 42 tok/s [provenance:P2].')" fail 'P2.*no source'
check "claim citing an unknown provenance entry fails" "$(repo 'Upstream reports 42 tok/s [provenance:P8].')" fail 'P8.*not in docs/provenance.md'
check "one good reference among several is enough" "$(repo 'It hits 42 tok/s [ledger:H1] [provenance:P1].')" 0
check "dangling reference on a non-claim line fails" "$(repo 'See [ledger:H7] for the plan.')" fail 'H7.*not in EXPERIMENTS.md'
check "PENDING reference on a non-claim line passes" "$(repo 'The open experiment is [ledger:H1].')" 0

# --- what is scanned ---------------------------------------------------------
check "claims in docs/*.md are scanned" "$(repo 'ok' 'It is 3x faster.')" fail 'docs/guide.md:1'
check "line numbers point at the claim" "$(repo $'line one\nline two\nIt is 3x faster.')" fail 'README.md:3:'
check "fenced code blocks are skipped" "$(repo $'```\necho 42 tok/s 30%\n```')" 0
check "tilde fences are skipped" "$(repo $'~~~\necho 42 tok/s\n~~~')" 0
check "inline code is skipped" "$(repo 'Set `--cpu-quota 50%` here.')" 0
check "URLs are skipped" "$(repo 'See https://example.com/a%20b/42%25 for details.')" 0
check "docs/provenance.md itself is not scanned" "$(repo 'ok')" 0

r=$(repo 'ok')
printf '%s\n' '| P3 | It is 3x faster | https://example.com | 2026-10-16 |' >> "${r}/docs/provenance.md"
check "a claim inside the provenance table is not flagged" "${r}" 0

r=$(repo 'It hits 42 tok/s [ledger:H2].')
rm "${r}/EXPERIMENTS.md"
check "missing EXPERIMENTS.md is an error" "${r}" fail 'EXPERIMENTS.md.*missing'

r=$(repo 'It hits 42 tok/s [provenance:P1].')
rm "${r}/docs/provenance.md"
check "missing provenance file makes provenance references fail" "${r}" fail 'P1.*not in docs/provenance.md'

check "every offending line is reported" "$(repo $'3x faster\nfastest\n42 tok/s')" fail '3 unbacked'

# --- the real repo -----------------------------------------------------------
check "this repo's own docs pass" "$(cd "$(dirname "$0")/.." && pwd)" 0

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

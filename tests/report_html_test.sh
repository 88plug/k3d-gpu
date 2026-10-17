#!/usr/bin/env bash
# report.html from facts/v1 (docs/console-design.md, S4). No cluster.
#
#   - unit tests of console/report_html.py and tests/console/offline_check.py
#   - `facts --html --facts F` for every tests/fixtures/*/facts.json equals that
#     fixture's report.html (baked matrix), and tests/console/green-report.html
#     is the raptorlake-nvidia facts with the green matrix
#   - every golden passes the static offline check: no script, nothing that
#     loads, CSP default-src 'none', every URL a deep link
#   - `facts --html` on a fixture sysroot is byte-stable and equals rendering the
#     `facts --json` it would print
#   - headless: Chromium loads every golden from file:// inside a network
#     namespace with no interfaces up, its DOM holds the report, and its net log
#     shows no request on the page's behalf. Controls: with the CSP removed an
#     injected <img> shows in the net log; with it, the CSP blocks the request.
#     Needs chromium/google-chrome (or $K3D_GPU_CHROME) and `unshare -rn`;
#     skipped without them unless K3D_GPU_REQUIRE_HEADLESS=1.
#
# Usage: tests/report_html_test.sh [--update]   (--update rewrites the goldens)

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
K3D_GPU="${REPO}/scripts/k3d-gpu"
DIR="${TESTS}/console"
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
# A home of its own: `facts --html` and `--json` must write nothing under it
# (checked below), and Chromium keeps its crash database out of the real one.
export HOME="${WORK}/home" XDG_STATE_HOME="${WORK}/home/.local/state"
mkdir -p "${HOME}"
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $*"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $*"; }

# golden <facts.json> <report.html golden> [--matrix FILE]
golden() {
    local facts=$1 want=$2 out="${WORK}/out.html"; shift 2
    if ! "${K3D_GPU}" facts --html --facts "${facts}" "$@" > "${out}" 2> "${WORK}/err"; then
        fail "facts --html --facts ${facts#"${REPO}"/} exited non-zero"; sed 's/^/  | /' "${WORK}/err"; return
    fi
    if [ "${UPDATE}" -eq 1 ]; then cp "${out}" "${want}"; echo "wrote ${want#"${REPO}"/}"; return; fi
    if diff -u "${want}" "${out}" > "${WORK}/diff" 2>&1; then
        ok "facts --html ${facts#"${REPO}"/} (${want#"${REPO}"/})"
    else
        fail "facts --html ${facts#"${REPO}"/} (${want#"${REPO}"/})"; head -40 "${WORK}/diff" | sed 's/^/  | /'
    fi
}

shopt -s nullglob
facts_files=("${REPO}"/tests/fixtures/*/facts.json)
for f in "${facts_files[@]}"; do golden "${f}" "$(dirname "${f}")/report.html"; done
golden "${REPO}/tests/fixtures/raptorlake-nvidia/facts.json" "${DIR}/green-report.html" --matrix "${DIR}/matrix-green.json"
[ "${UPDATE}" -eq 1 ] && exit 0

if python3 "${DIR}/test_report_html.py"; then ok "report_html unit tests"; else fail "report_html unit tests"; fi

goldens=("${REPO}"/tests/fixtures/*/report.html "${DIR}/green-report.html")
[ "${#goldens[@]}" -eq $(( ${#facts_files[@]} + 1 )) ] && [ "${#facts_files[@]}" -ge 14 ] \
    && ok "report.html golden for all ${#facts_files[@]} fixtures plus the green path" \
    || fail "expected a report.html per fixture (found ${#goldens[@]} for ${#facts_files[@]} fixtures)"
if python3 "${DIR}/offline_check.py" static "${goldens[@]}" > "${WORK}/static"; then
    ok "every report.html golden is offline-clean (static)"
else
    fail "a report.html golden can fetch or holds a bare URL"; sed 's/^/  | /' "${WORK}/static"
fi

# `facts --html` on a fixture host: stubs for docker, k3d and kubectl.
mkdir -p "${WORK}/bin"
for t in docker k3d kubectl; do printf '#!/bin/sh\nexit 0\n' > "${WORK}/bin/${t}"; chmod +x "${WORK}/bin/${t}"; done
run() { PATH="${WORK}/bin:${PATH}" K3D_GPU_SYSROOT="${REPO}/tests/fixtures/arrowlake-h/sysroot" "${K3D_GPU}" "$@"; }
run facts --html > "${WORK}/h1.html" 2>/dev/null
run facts --html > "${WORK}/h2.html" 2>/dev/null
run facts --json > "${WORK}/f.json" 2>/dev/null
"${K3D_GPU}" facts --html --facts "${WORK}/f.json" > "${WORK}/h3.html" 2>/dev/null
if [ -s "${WORK}/h1.html" ] && cmp -s "${WORK}/h1.html" "${WORK}/h2.html" && cmp -s "${WORK}/h1.html" "${WORK}/h3.html"; then
    ok "facts --html is byte-stable and equals rendering facts --json"
else
    fail "facts --html is not byte-stable or differs from rendering facts --json"
fi

expect_fail() {  # expect_fail <message substring> <args...>
    local want=$1 out; shift
    if out=$(run "$@" 2>&1 < /dev/null); then
        fail "$* succeeded"
    elif grep -qF -- "${want}" <<< "${out}"; then
        ok "$* fails: ${want}"
    else
        fail "$* failed without '${want}'"; sed 's/^/  | /' <<< "${out}"
    fi
}
F="${REPO}/tests/fixtures/raptorlake-nvidia/facts.json"
if [ -e "${XDG_STATE_HOME}/k3d-gpu" ]; then
    fail "facts --html or --json wrote ${XDG_STATE_HOME#"${WORK}"/}/k3d-gpu (it must print only)"
else
    ok "facts --html and --json write no state on the host"
fi

expect_fail "unknown facts option" facts --pdf
expect_fail "--facts and --matrix go with --html" facts --facts "${F}"
expect_fail "--facts and --matrix go with --html" facts --json --matrix "${DIR}/matrix-green.json"
expect_fail "facts: --facts needs a value" facts --html --facts
expect_fail "does not exist" facts --html --facts "${WORK}/missing.json"
echo '{"schema": "k3d-gpu.facts/v1"}' > "${WORK}/bad.json"
expect_fail "schema violations" facts --html --facts "${WORK}/bad.json"
expect_fail "schema violations" facts --html --facts "${F}" --matrix "${WORK}/bad.json"

# ---- headless load with the network off ----------------------------------------
chrome=${K3D_GPU_CHROME:-}
if [ -z "${chrome}" ]; then
    for c in chromium google-chrome chromium-browser google-chrome-stable; do
        if command -v "${c}" >/dev/null 2>&1; then chrome=$(command -v "${c}"); break; fi
    done
fi
skip_headless() {
    if [ "${K3D_GPU_REQUIRE_HEADLESS:-0}" = 1 ]; then fail "headless load: $1"; else echo "skip headless load: $1"; fi
}
# headless <html> <netlog>: load the file in a network namespace, print the DOM.
headless() {
    unshare -rn "${chrome}" --headless --no-sandbox --disable-gpu --no-first-run --no-default-browser-check \
        --disable-background-networking --disable-component-update --disable-sync --disable-extensions \
        --user-data-dir="${WORK}/profile" --log-net-log="$2" --dump-dom "file://$1" 2> "${WORK}/chrome.err"
}
if [ -z "${chrome}" ]; then
    skip_headless "no chromium or google-chrome (set K3D_GPU_CHROME)"
elif ! unshare -rn true 2>/dev/null; then
    skip_headless "unshare -rn is not permitted here (user namespaces off)"
else
    echo "headless: $("${chrome}" --version 2>/dev/null), network namespace with no interfaces up"
    n=0 bad=0
    for g in "${goldens[@]}"; do
        n=$((n + 1))
        fp=$(grep -o 'sha256:[0-9a-f]\{64\}' "${g}" | head -1)
        if headless "${g}" "${WORK}/net.json" > "${WORK}/dom" \
           && grep -q '<h1>k3d-gpu report: ' "${WORK}/dom" && grep -qF "${fp}" "${WORK}/dom" \
           && grep -q '</footer>' "${WORK}/dom" \
           && python3 "${DIR}/offline_check.py" netlog "${WORK}/net.json" > "${WORK}/netlog"; then
            :
        else
            bad=$((bad + 1)); echo "  | ${g#"${REPO}"/}"; sed 's/^/  |   /' "${WORK}/netlog" "${WORK}/chrome.err" 2>/dev/null | tail -5
        fi
    done
    if [ "${bad}" -eq 0 ]; then ok "headless: all ${n} report.html load offline, render, and request nothing"
    else fail "headless: ${bad} of ${n} report.html did not load offline cleanly"; fi
    # Controls. Without the CSP meta an injected <img> must show in the net log,
    # or the net-log check proves nothing; with the CSP it must not (blocked).
    sed -e '/Content-Security-Policy/d' -e 's|</main>|<img src="https://example.com/x.png"></main>|' \
        "${DIR}/green-report.html" > "${WORK}/fetching.html"
    if headless "${WORK}/fetching.html" "${WORK}/net.json" > /dev/null \
       && ! python3 "${DIR}/offline_check.py" netlog "${WORK}/net.json" > "${WORK}/netlog" \
       && grep -q 'example.com/x.png' "${WORK}/netlog"; then
        ok "headless control: an injected <img> without the CSP shows in the net log"
    else
        fail "headless control: an injected <img> was not seen (the net-log check proves nothing)"
    fi
    sed 's|</main>|<img src="https://example.com/x.png"></main>|' "${DIR}/green-report.html" > "${WORK}/blocked.html"
    if headless "${WORK}/blocked.html" "${WORK}/net.json" > /dev/null \
       && python3 "${DIR}/offline_check.py" netlog "${WORK}/net.json" > "${WORK}/netlog"; then
        ok "headless control: the CSP blocks an injected <img> before any request"
    else
        fail "headless control: the CSP let an injected <img> make a request"; sed 's/^/  | /' "${WORK}/netlog"
    fi
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

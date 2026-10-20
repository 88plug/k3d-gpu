#!/usr/bin/env bash
# Tests for tests/pe_premise_check.sh. Each case builds a throwaway repo tree,
# runs the guard on it, and checks the exit code and the path:line it names.
#
# Usage: tests/pe_premise_check_test.sh

# Fixtures hold literal backticks inside single quotes.
# shellcheck disable=SC2016

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GUARD="${HERE}/pe_premise_check.sh"
WORK=$(mktemp -d)
trap 'chmod -R u+rwX "${WORK}" 2>/dev/null; rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# tree NAME: a fresh repo root with an empty README.md.
tree() { local d="${WORK}/$1"; mkdir -p "${d}/docs"; : > "${d}/README.md"; echo "${d}"; }

# expect NAME ROOT CODE [PATH:LINE...]: guard exit code and the exact set of
# flagged path:line pairs.
expect() {
    local name=$1 root=$2 want=$3; shift 3
    local out code got want_lines
    out=$(bash "${GUARD}" "${root}" 2>&1); code=$?
    got=$(grep -oE '^[^ :]+:[0-9]+' <<< "${out}" | sort)
    want_lines=$(printf '%s\n' "$@" | sed '/^$/d' | sort)
    if [ "${code}" = "${want}" ] && [ "${got}" = "${want_lines}" ]; then
        ok "${name}"
    else
        fail "${name}: exit ${code} (want ${want}); flagged [${got//$'\n'/ }] want [${want_lines//$'\n'/ }]"
        printf '     %s\n' "${out//$'\n'/$'\n     '}"
    fi
}

if [ ! -f "${GUARD}" ]; then
    fail "guard ${GUARD} does not exist"
    echo "${PASS} passed, ${FAILS} failed"
    exit 1
fi

# 1. A ledger citation does not scope a benefit claim.
d=$(tree stay_free_cited_H3)
printf 'Sandboxes run on E-cores, so the P-cores stay free for inference [ledger:H3].\n' > "${d}/docs/a.md"
expect stay_free_cited_H3_fails "${d}" 1 docs/a.md:1

# 2. There is no H43 escape either.
d=$(tree isolated_cited_H43)
printf 'E-core load is isolated from P-core inference [ledger:H43].\n' > "${d}/docs/a.md"
expect isolated_cited_H43_fails "${d}" 1 docs/a.md:1

# 3. "leaves the P-cores to inference".
d=$(tree leaves_pcores)
printf '| Placement | the UI runs on E-cores and leaves the P-cores to inference. |\n' > "${d}/README.md"
expect leaves_pcores_fails "${d}" 1 README.md:1

# 4. The EAT split paragraph as it read before the sweep.
d=$(tree eat_split)
printf '%s\n' \
    'That split, orchestration and serving on fast cores with agent tools on many small ones,' \
    'is the placement k3d-gpu sets with one pod label: sandboxes on E-cores behind' \
    'default-deny egress is SUPPORTED on the i9-13900T [ledger:H18]. No Xeon run backs it yet.' \
    > "${d}/docs/intel.md"
expect eat_split_unscoped_fails "${d}" 1 docs/intel.md:1

# 5. Scoping on the same line passes.
d=$(tree scoped_same)
printf 'Placement, not isolation: the P-cores stay free of sandbox pods.\n' > "${d}/docs/a.md"
expect scoped_same_line_passes "${d}" 0

# 6. Scoping on the next non-blank line passes (the rewritten paragraph).
d=$(tree scoped_adjacent)
printf '%s\n' \
    'That split, orchestration and serving on fast cores with agent tools on many small ones,' \
    'is the placement k3d-gpu sets with one pod label, not isolation: package power, LLC and' \
    'DRAM bandwidth stay shared [ledger:H3].' \
    > "${d}/docs/intel.md"
expect scoped_adjacent_line_passes "${d}" 0

# 7. A blank line ends the paragraph: scoping after it does not count.
d=$(tree scope_blank)
printf '%s\n' 'The sandbox keeps the P-cores free for inference.' '' 'That is not isolation.' > "${d}/docs/a.md"
expect scope_across_blank_line_fails "${d}" 1 docs/a.md:1

# 8. A doctor label that says the resources stay shared passes.
d=$(tree doctor_label)
mkdir -p "${d}/scripts"
printf '    say "noisy-neighbour isolation: E-cores apart from P-core inference; package power stays shared"\n' > "${d}/scripts/k3d-gpu"
expect doctor_stays_shared_passes "${d}" 0

# 9. A scanned file that cannot be read exits 2 (skipped as root: root reads mode 000).
d=$(tree unreadable)
printf 'fine\n' > "${d}/docs/a.md"; chmod 000 "${d}/docs/a.md"
if [ "$(id -u)" = 0 ]; then
    ok "unreadable_file_exits_2 (skipped: root reads mode 000)"
else
    expect unreadable_file_exits_2 "${d}" 2
fi
chmod 644 "${d}/docs/a.md"

# 10. The five lines the sweep rewrote, verbatim from origin/main ef87f866, each at
# its own path. Every one is flagged.
d=$(tree pre_sweep)
mkdir -p "${d}/scripts" "${d}/share"
printf '| Placement | pod label `k3d-gpu.io/cores=efficient`, 1 CPU request: on a hybrid CPU the UI runs on E-cores and leaves the P-cores to inference. No effect on other hosts. |\n\n%s\n%s\n' \
    '`k3d-gpu.io/cores: efficient` and request one CPU, so on a hybrid host untrusted' \
    'agent tool execution runs on E-cores and the P-cores stay free for inference.' > "${d}/README.md"
printf '%s\n' '# The profile labels them k3d-gpu.io/cores=efficient, so on a hybrid CPU the balloons' \
    '# policy keeps them on E-cores and the P-cores stay free for inference.' > "${d}/scripts/k3d-gpu"
printf '%s\n' '# puts every sandbox in the NRI balloons "efficient" balloon on hybrid hosts (k3d-gpu up' \
    '# deploys it), so the P-cores stay free for inference. The CPU request sizes the balloon.' > "${d}/share/agent-sandbox-k3d.yml"
printf '%s\n' \
    'That split, orchestration and serving on fast cores with agent tools on many small ones,' \
    'is the placement k3d-gpu sets with one pod label: sandboxes on E-cores behind' > "${d}/docs/intel.md"
expect pre_sweep_lines_fail "${d}" 1 README.md:1 README.md:4 scripts/k3d-gpu:2 share/agent-sandbox-k3d.yml:2 docs/intel.md:1

# 10b. Where git history holds ef87f866 (a full clone, not CI's depth-1 checkout),
# the guard flags exactly those five lines of that tree.
REPO="$(cd "${HERE}/.." && pwd)"
if git -C "${REPO}" cat-file -e 'ef87f866^{commit}' 2>/dev/null; then
    mkdir -p "${WORK}/ef87f866"
    git -C "${REPO}" archive ef87f866 README.md docs scripts/k3d-gpu share contrib console 2>/dev/null \
        | tar -x -C "${WORK}/ef87f866"
    expect origin_main_ef87f866_snapshot "${WORK}/ef87f866" 1 \
        README.md:979 README.md:1374 scripts/k3d-gpu:5434 share/agent-sandbox-k3d.yml:6 docs/intel.md:165
else
    ok "origin_main_ef87f866_snapshot (skipped: commit not in this clone)"
fi

# 11. The guard is clean on this tree.
expect repo_tree_clean "${REPO}" 0

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" = 0 ]

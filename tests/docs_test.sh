#!/usr/bin/env bash
# Docs site tests. Each case copies the docs inputs to a scratch tree, changes
# them, and runs `mkdocs build --strict` there. Needs mkdocs from
# docs-build/requirements.txt on PATH (the Docs workflow installs it).
#
# Usage: tests/docs_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

command -v mkdocs >/dev/null || { echo "mkdocs not on PATH (pip install --require-hashes -r docs-build/requirements.txt)" >&2; exit 2; }

# tree <name>: fresh copy of the docs inputs; prints its path.
tree() {
    local dir="${WORK}/$1"
    mkdir -p "${dir}"
    cp -r "${ROOT}/mkdocs.yml" "${ROOT}/README.md" "${ROOT}/CONTRIBUTING.md" \
        "${ROOT}/docs" "${ROOT}/docs-build" "${dir}/"
    echo "${dir}"
}

# build <dir>: strict build; output in <dir>/build.log, site in <dir>/site.
build() { (cd "$1" && mkdocs build --strict > build.log 2>&1); }

ok()   { PASS=$((PASS + 1)); echo "ok   - $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL - $1"; [ -f "${2:-}" ] && sed 's/^/       /' "$2"; }

# check <name> <dir> <cmd...>: pass when cmd succeeds.
check() { local name=$1 dir=$2; shift 2; if "$@"; then ok "${name}"; else fail "${name}" "${dir}/build.log"; fi; }

# --- The repo as it is builds clean -------------------------------------------
d=$(tree current)
check "repo builds with --strict" "$d" build "$d"
check "home page rendered from README" "$d" grep -q 'Quick Start (k3d-gpu CLI)' "$d/site/index.html"
check "contributing page rendered from CONTRIBUTING.md" "$d" grep -q 'Contributing to k3d-gpu' "$d/site/contributing/index.html"
check "README repo-relative link points at GitHub" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/blob/main/LICENSE.md"' "$d/site/index.html"
check "home edit button opens README.md" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/edit/main/README.md"' "$d/site/index.html"
check "README in-page anchors resolve (GitHub-style slugs)" "$d" \
    grep -q 'id="building--pushing-the-image"' "$d/site/index.html"
for page in "${ROOT}"/docs/*.md; do
    [ -e "${page}" ] || continue
    name=$(basename "${page}" .md)
    check "docs/${name}.md is published" "$d" test -f "$d/site/${name}/index.html"
done

# --- A nav entry whose page is not in docs/ yet is skipped, not an error ------
d=$(tree unpublished)
rm -f "$d/docs/provenance.md"
printf '# Intel\n\nSee [the log](provenance.md#sources).\n' > "$d/docs/intel.md"
printf '\nSee [provenance](docs/provenance.md).\n' >> "$d/README.md"
check "missing nav page does not fail --strict" "$d" build "$d"
check "missing nav page logged as not yet published" "$d" grep -q 'provenance.md not yet published' "$d/build.log"
check "missing nav page absent from site" "$d" test ! -e "$d/site/provenance/index.html"
check "link to missing nav page renders as text" "$d" grep -q '<p>See the log.</p>' "$d/site/intel/index.html"
check "README link to missing nav page renders as text" "$d" grep -q '<p>See provenance.</p>' "$d/site/index.html"

# --- Once the page lands it is published and linked from README ---------------
d=$(tree published)
printf '# Provenance\n\nStub.\n' > "$d/docs/provenance.md"
printf '\nSee [provenance](docs/provenance.md).\n' >> "$d/README.md"
check "page added to docs/ builds" "$d" build "$d"
check "page added to docs/ is published" "$d" test -f "$d/site/provenance/index.html"
check "page added to docs/ appears in nav" "$d" grep -q 'href="provenance/"' "$d/site/index.html"
check "README docs/ link maps to the site page" "$d" grep -q 'href="provenance/">provenance</a>' "$d/site/index.html"

# --- Guards that must fail the build ------------------------------------------
d=$(tree unlisted)
printf '# Orphan\n' > "$d/docs/orphan.md"
check "page in docs/ but not in nav fails --strict" "$d" bash -c "! (cd '$d' && mkdocs build --strict > build.log 2>&1)"

d=$(tree index)
printf '# Home\n' > "$d/docs/index.md"
check "docs/index.md beside the README home fails" "$d" bash -c "! (cd '$d' && mkdocs build --strict > build.log 2>&1)"

d=$(tree badanchor)
printf '\n[broken](#no-such-heading)\n' >> "$d/README.md"
check "broken README anchor fails --strict" "$d" bash -c "! (cd '$d' && mkdocs build --strict > build.log 2>&1)"

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

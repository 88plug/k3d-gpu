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

# --- docs/ pages that link repo files the GitHub way (../path) ----------------
# Mirrors the links on in-flight pages: ../Dockerfile, ../share/x.yml#L3, a
# docs/ directory (provenance/), and pages in docs/ subdirectories.
d=$(tree repolinks)
mkdir -p "$d/docs/provenance" "$d/docs/integrations" "$d/docs/design"
printf 'log\n' > "$d/docs/provenance/arxiv.txt"
printf '# Provenance\n\nLogs are in [`docs/provenance/`](provenance/).\n' > "$d/docs/provenance.md"
printf '# Failure modes\n\nSee [Dockerfile](../Dockerfile) and [policy](../share/nri-balloons.yml#L3).\n' \
    > "$d/docs/failure-modes.md"
printf '# Power envelope\n\nModel: [envelope](../bench/envelope_model.py).\n' > "$d/docs/power-envelope.md"
printf '# GPUStack\n\nSee [failure modes](../failure-modes.md) and [launcher](../../scripts/k3d-gpu).\n' \
    > "$d/docs/integrations/gpustack.md"
printf '# Speculative decoding\n\nStub.\n' > "$d/docs/design/speculative-decoding.md"
check "docs pages with ../repo links build with --strict" "$d" build "$d"
check "../file link points at GitHub blob" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/blob/main/Dockerfile"' "$d/site/failure-modes/index.html"
check "../file#anchor link keeps its anchor" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/blob/main/share/nri-balloons.yml#L3"' "$d/site/failure-modes/index.html"
check "docs/ directory link points at GitHub tree" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/tree/main/docs/provenance/"' "$d/site/provenance/index.html"
check "subdir page ../../file link points at GitHub blob" "$d" \
    grep -q 'href="https://github.com/88plug/k3d-gpu/blob/main/scripts/k3d-gpu"' "$d/site/integrations/gpustack/index.html"
check "subdir page link to a docs page stays on the site" "$d" \
    grep -q 'href="../../failure-modes/"' "$d/site/integrations/gpustack/index.html"
for page in failure-modes power-envelope integrations/gpustack design/speculative-decoding; do
    check "docs/${page}.md is in nav" "$d" grep -q "href=\"${page}/\"" "$d/site/index.html"
done

# --- A subdir page's link to an unpublished page renders as text --------------
d=$(tree subdir-unpublished)
rm -f "$d/docs/provenance.md"
mkdir -p "$d/docs/integrations"
printf '# GPUStack\n\nSee [the log](../provenance.md#sources).\n' > "$d/docs/integrations/gpustack.md"
check "subdir page linking unpublished page builds" "$d" build "$d"
check "subdir link to unpublished page renders as text" "$d" \
    grep -q '<p>See the log.</p>' "$d/site/integrations/gpustack/index.html"

# --- Guards that must fail the build, each for its own reason ----------------
# fails_with <dir> <pattern>: strict build fails and build.log has <pattern>.
fails_with() { ! build "$1" && grep -qF -- "$2" "$1/build.log"; }

d=$(tree unlisted)
printf '# Orphan\n' > "$d/docs/orphan.md"
check "page in docs/ but not in nav fails --strict" "$d" \
    fails_with "$d" 'are not included in the "nav" configuration:'
check "nav failure names the orphan page" "$d" grep -q -- '- orphan.md' "$d/build.log"

d=$(tree index)
printf '# Home\n' > "$d/docs/index.md"
check "docs/index.md beside the README home fails" "$d" \
    fails_with "$d" 'docs/index.md must not exist: it is generated from README.md'

d=$(tree badanchor)
printf '\n[broken](#no-such-heading)\n' >> "$d/README.md"
check "broken README anchor fails --strict" "$d" \
    fails_with "$d" "contains a link '#no-such-heading', but there is no such anchor on this page"

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

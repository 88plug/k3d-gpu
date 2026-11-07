#!/usr/bin/env bash
# hack/bump end to end, in a scratch repo with its own pins/registry.toml and the
# upstream answers fixed (--resolved for versions, PINLIB_FIXTURES for image
# digests): no network. Checks that a bump
#   - changes only declared sites, derived values and hook outputs
#     (hack/pin-only-diff passes on the result);
#   - substitutes goldens (exact old -> new strings) in tests/** only, and only
#     for strings no other file still pins;
#   - leaves ledger (bench/), historical, eat-stock files and .github/ alone;
#   - is a no-op when run a second time;
#   - moves no held pin to or past its hold, from --resolved values and from a
#     source alike (a held note names it);
#   - with --reason known-issue:<id> records fixed_in, narrows affected and sets
#     retained, and --remove-workaround then cuts the marked lines out
#     (hack/known-issues check passes after each step);
# and that the registry puts both NRI components in one group (one branch).
#
# Usage: tests/bump_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/bump_test.XXXXXX")
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0
ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
check() { local what=$1; shift; if "$@"; then ok "${what}"; else fail "${what}"; fi; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
D_OLD=sha256:1111111111111111111111111111111111111111111111111111111111111111
D_NEW=sha256:2222222222222222222222222222222222222222222222222222222222222222
D_EAT=sha256:3333333333333333333333333333333333333333333333333333333333333333

R="${WORK}/repo"
mkdir -p "${R}/pins" "${R}/share" "${R}/tests" "${R}/bench/h1" "${R}/.github/workflows" "${R}/integrations/eat" \
    "${R}/console"
cat > "${R}/pins/registry.toml" <<'EOF'
[groups.tools]
tests = []

[groups.eat-stock]
tests = []

[[pin]]
id = "tool"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "github-release", repo = "o/tool", tag = "{v}" }
sites = [{ file = "share/tool.env", var = "TOOL_VERSION" }]
derived = [{ file = "share/notes.txt", regex = '^built with tool (?P<v>[0-9.]+)$', from = "{v_bare}" }]

[[pin]]
id = "img"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "oci", image = "x/img", tag = "{v}" }
sites = [{ file = "share/app.yml", ref = "x/img" }]

[[pin]]
id = "eat-img"
class = "eat-stock"
group = "eat-stock"
family = "eat-stock"
scheme = "semver"
source = { kind = "oci", image = "x/eat", tag = "{v}" }
sites = [{ file = "integrations/eat/values.yaml", ref = "x/eat" }]
EOF
echo 'TOOL_VERSION=v1.2.0' > "${R}/share/tool.env"
printf 'built with tool 1.2.0\nsee https://example.com\n' > "${R}/share/notes.txt"
printf 'image: x/img:1.0.0@%s\n' "${D_OLD}" > "${R}/share/app.yml"
printf 'image: x/eat:2.0.0@%s\n' "${D_EAT}" > "${R}/integrations/eat/values.yaml"
# the marker text is assembled, so this file carries no marker of its own (hack/known-issues scans it)
KI=known-issue
printf 'echo workaround # %s: tool-1\necho kept\n' "${KI}" > "${R}/share/tool.sh"
# goldens: the moved image ref (no other file keeps it), and the eat image (not moved)
printf 'want x/img:1.0.0@%s\nwant x/eat:2.0.0@%s\nwant TOOL_VERSION=v1.2.0\n' "${D_OLD}" "${D_EAT}" > "${R}/tests/golden.txt"
# a ledger run that pinned the old tool: historical, never edited
printf 'TOOL_VERSION=v1.2.0  # H1 measured this\n' > "${R}/bench/h1/run.sh"
printf 'TOOL_VERSION=v1.2.0\n' > "${R}/.github/workflows/x.yml"
printf 'TOOL_VERSION=v1.2.0\n' > "${R}/EXPERIMENTS.md"
: > "${R}/images.unpinned"
cat > "${R}/pins/known-issues.toml" <<'EOF'
[[issue]]
id = "tool-1"
title = "tool 1.2 drops a flag"
kind = "bug"
upstream = "o/tool#1"
fix = {}
component = "tool"
scheme = "semver"
affected = "*"
fixed_in = ""
adopt = "review"
sites = [{file = "share/tool.env", regex = '^TOOL_VERSION=(?P<v>v[0-9.]+)$'}]
workarounds = ["share/tool.sh"]
notes = ""
EOF
git -C "${R}" init -q -b main
git -C "${R}" add -A
git -C "${R}" commit -qm base

FIX="${WORK}/fixtures.json"
cat > "${FIX}" <<EOF
{"https://registry-1.docker.io/v2/x/img/manifests/1.1.0": {"status": 200, "headers": {"Docker-Content-Digest": "${D_NEW}"}, "body": ""}}
EOF
RES="${WORK}/resolved.json"
echo '{"tool": {"v": "v1.3.0"}, "img": {"v": "1.1.0", "tag": "1.1.0"}, "eat-img": {"v": "2.0.0"}}' > "${RES}"

bump() { PINLIB_FIXTURES="${FIX}" python3 "${ROOT}/hack/bump" --root "${R}" --resolved "${RES}" --no-tests "$@"; }

out=$(bump tools --summary "${WORK}/summary.json" --body "${WORK}/body.md" 2>&1); rc=$?
check "bump tools exits 0" test "${rc}" -eq 0
echo "${out}" | sed 's/^/    /' | head -20
changed=$(git -C "${R}" diff --name-only | sort | tr '\n' ' ')
check "only the sites, the derived value and the golden changed (got: ${changed})" \
    test "${changed}" = "share/app.yml share/notes.txt share/tool.env tests/golden.txt "
check "the tool site moved" grep -qx 'TOOL_VERSION=v1.3.0' "${R}/share/tool.env"
check "the derived value followed" grep -qx 'built with tool 1.3.0' "${R}/share/notes.txt"
check "the image moved with its new digest" grep -qx "image: x/img:1.1.0@${D_NEW}" "${R}/share/app.yml"
check "the golden of the moved image was substituted" grep -qx "want x/img:1.1.0@${D_NEW}" "${R}/tests/golden.txt"
check "a golden of a string the ledger still pins stays" grep -qx 'want TOOL_VERSION=v1.2.0' "${R}/tests/golden.txt"
check "the eat-stock golden stays" grep -qx "want x/eat:2.0.0@${D_EAT}" "${R}/tests/golden.txt"
for f in bench/h1/run.sh .github/workflows/x.yml EXPERIMENTS.md integrations/eat/values.yaml; do
    check "${f} untouched" git -C "${R}" diff --quiet -- "${f}"
done
check "summary: branch autobump/tools, two moves, changed" python3 - "${WORK}/summary.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["branch"] == "autobump/tools" and s["changed"] is True, s
assert sorted((m["id"], m["from"], m["to"]) for m in s["moved"]) == [("img", "1.0.0", "1.1.0"), ("tool", "v1.2.0", "v1.3.0")], s["moved"]
assert s["verdict"]["verdict"] in ("auto", "review") and "autobump" in s["labels"], s
PY
check "the PR body names each move and the verdict" grep -q '| `tool` | `v1.2.0` | `v1.3.0` |' "${WORK}/body.md"
git -C "${R}" add -A && git -C "${R}" commit -qm bump
check "hack/pin-only-diff passes on the bump" python3 "${ROOT}/hack/pin-only-diff" --root "${R}" --group tools HEAD~1 HEAD

out=$(bump tools 2>&1); rc=$?
check "a second run is a no-op (${out%%$'\n'*})" test "${rc}" -eq 0 -a -z "$(git -C "${R}" status --porcelain)"

# known issue: tool-1 is fixed in v1.3.0; its workaround stays until its own PR
git -C "${R}" reset -q --hard HEAD~1
out=$(bump tools --reason "${KI}:tool-1" --fixed-in v1.3.0 --summary "${WORK}/ki.json" 2>&1); rc=$?
check "bump --reason known-issue exits 0" test "${rc}" -eq 0
check "known issue: fixed_in, affected and retained recorded, followup named" python3 - "${R}" "${WORK}/ki.json" <<'PY'
import json, sys, tomllib
e = tomllib.load(open(sys.argv[1] + "/pins/known-issues.toml", "rb"))["issue"][0]
assert e["fixed_in"] == "v1.3.0" and e["affected"] == "<v1.3.0", e
assert e["retained"] == {"reason": "the workaround goes in a separate, reviewed PR", "pr": "autobump/ki-tool-1"}, e
assert json.load(open(sys.argv[2]))["followup"] == "tool-1"
PY
check "hack/known-issues check passes with the workaround retained" \
    python3 "${ROOT}/hack/known-issues" --root "${R}" check
git -C "${R}" add -A && git -C "${R}" commit -qm bump-ki
out=$(python3 "${ROOT}/hack/bump" --root "${R}" --remove-workaround tool-1 --body "${WORK}/wa.md" 2>&1); rc=$?
check "remove-workaround exits 0" test "${rc}" -eq 0
check "the marked line is gone, the rest stays" test "$(cat "${R}/share/tool.sh")" = "echo kept"
check "workarounds and retained emptied" python3 -c '
import sys, tomllib
e = tomllib.load(open(sys.argv[1] + "/pins/known-issues.toml", "rb"))["issue"][0]
assert e["workarounds"] == [] and e["retained"] == {}, e' "${R}"
check "hack/known-issues check passes after the removal" python3 "${ROOT}/hack/known-issues" --root "${R}" check
check "the removal PR body says review" grep -q 'Policy: review (changes-defaults)' "${WORK}/wa.md"

# a hook that writes a file of another class fails the bump: a bumper writes only
# ours and eat-stock paths, whatever its hooks do (here the ledger-rules hook's
# console/model_opt.py also edits a ledger run's file)
R2="${WORK}/repo2"
mkdir -p "${R2}/pins" "${R2}/share" "${R2}/bench/h1" "${R2}/console"
cat > "${R2}/pins/registry.toml" <<'EOF'
[groups.tools]
tests = []
hooks = ["ledger-rules"]

[[pin]]
id = "tool"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "github-release", repo = "o/tool", tag = "{v}" }
sites = [{ file = "share/tool.env", var = "TOOL_VERSION" }]
EOF
echo 'TOOL_VERSION=v1.2.0' > "${R2}/share/tool.env"
printf 'TOOL_VERSION=v1.2.0  # H1 measured this\n' > "${R2}/bench/h1/run.sh"
: > "${R2}/EXPERIMENTS.md"
: > "${R2}/images.unpinned"
cat > "${R2}/console/model_opt.py" <<'PY'
open("console/ledger-rules.json", "w").write("{}\n")
open("bench/h1/run.sh", "a").write("# rewritten by a hook\n")
PY
echo '{}' > "${R2}/console/ledger-rules.json"
git -C "${R2}" init -q -b main && git -C "${R2}" add -A && git -C "${R2}" commit -qm base
out=$(PINLIB_FIXTURES="${FIX}" python3 "${ROOT}/hack/bump" --root "${R2}" --resolved "${RES}" --no-tests tools 2>&1); rc=$?
check "a hook writing a historical file fails the bump (rc ${rc})" test "${rc}" -ne 0
check "the failure names the file and its class" grep -q 'bench/h1/run.sh (historical)' <<< "${out}"

# the digests group: its repin hook moves a same-tag respin (the registry re-pointed
# x/img:1.0.0) in an ours file, notes each move, and leaves a ledger run's file alone
R3="${WORK}/repo3"
mkdir -p "${R3}/pins" "${R3}/share" "${R3}/bench/h1"
cat > "${R3}/pins/registry.toml" <<'EOF'
[groups.digests]
tests = []
hooks = ["repin"]
EOF
printf 'image: x/img:1.0.0@%s\n' "${D_OLD}" > "${R3}/share/app.yml"
printf 'image: x/img:1.0.0@%s\n' "${D_OLD}" > "${R3}/bench/h1/app.yml"
: > "${R3}/EXPERIMENTS.md"
: > "${R3}/images.unpinned"
git -C "${R3}" init -q -b main && git -C "${R3}" add -A && git -C "${R3}" commit -qm base
FIX3="${WORK}/fixtures3.json"
cat > "${FIX3}" <<EOF
{"https://registry-1.docker.io/v2/x/img/manifests/1.0.0": {"status": 200, "headers": {"Docker-Content-Digest": "${D_NEW}"}, "body": ""}}
EOF
out=$(PINLIB_FIXTURES="${FIX3}" python3 "${ROOT}/hack/bump" --root "${R3}" --no-tests digests \
    --summary "${WORK}/digests.json" 2>&1); rc=$?
check "bump digests exits 0 (rc ${rc}: ${out##*$'\n'})" test "${rc}" -eq 0
check "the respun digest moved in the ours file" grep -qx "image: x/img:1.0.0@${D_NEW}" "${R3}/share/app.yml"
check "the ledger run's file kept its digest" grep -qx "image: x/img:1.0.0@${D_OLD}" "${R3}/bench/h1/app.yml"
check "the summary notes the repin by file and line" python3 - "${WORK}/digests.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["changed"] is True, s
assert any(n.startswith("repin: share/app.yml:1 ") for n in s["notes"]), s["notes"]
PY

# a site regex that repeats a value by backreference ((?P=v)): every occurrence
# moves with it, so the site still matches after the bump
R4="${WORK}/repo4"
mkdir -p "${R4}/pins" "${R4}/share"
cat > "${R4}/pins/registry.toml" <<'EOF'
[groups.tools]
tests = []

[[pin]]
id = "tool"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "github-release", repo = "o/tool", tag = "{v}" }
sites = [
  { file = "share/tool.env", regex = '^TOOL_DEB="tool_(?P<v>[0-9.]+)_amd64\.deb https://example\.com/download/v(?P=v)"$' },
  { file = "share/tool.env", regex = '^TOOL_IMAGE=x/tool/(?P<v>[0-9.]+):(?P<tag>(?P=v)-[0-9]+)$' },
]
EOF
printf 'TOOL_DEB="tool_1.2.0_amd64.deb https://example.com/download/v1.2.0"\nTOOL_IMAGE=x/tool/1.2.0:1.2.0-7\n' \
    > "${R4}/share/tool.env"
: > "${R4}/EXPERIMENTS.md"
: > "${R4}/images.unpinned"
git -C "${R4}" init -q -b main && git -C "${R4}" add -A && git -C "${R4}" commit -qm base
echo '{"tool": {"v": "1.3.0", "tag": "1.3.0-9"}}' > "${WORK}/resolved4.json"
out=$(python3 "${ROOT}/hack/bump" --root "${R4}" --resolved "${WORK}/resolved4.json" --no-tests tools 2>&1); rc=$?
check "bump with a backreference site exits 0 (rc ${rc}: ${out##*$'\n'})" test "${rc}" -eq 0
check "the backreferenced URL version moved with the file name" \
    grep -qx 'TOOL_DEB="tool_1.3.0_amd64.deb https://example.com/download/v1.3.0"' "${R4}/share/tool.env"
check "a backreference inside another group moves with that group" grep -qx 'TOOL_IMAGE=x/tool/1.3.0:1.3.0-9' "${R4}/share/tool.env"
git -C "${R4}" add -A && git -C "${R4}" commit -qm bump
check "hack/pin-only-diff passes on the backreference bump" \
    python3 "${ROOT}/hack/pin-only-diff" --root "${R4}" --group tools HEAD~1 HEAD

# a hold: no pin moves to or past its max. From --resolved values (hack/bump's own
# gate) and from the sources (github-release and pypi list every release and take
# the newest below the hold) alike; the group then has nothing to move.
R5="${WORK}/repo5"
mkdir -p "${R5}/pins" "${R5}/share"
cat > "${R5}/pins/registry.toml" <<'EOF'
[groups.tools]
tests = []

[[pin]]
id = "tool"
class = "ours"
group = "tools"
family = "tools"
scheme = "semver"
source = { kind = "github-release", repo = "o/tool", tag = "{v}" }
hold = { max = "v1.3.0", reason = "tool 1.3 drops a flag", review_by = "2099-01-01" }
sites = [{ file = "share/tool.env", var = "TOOL_VERSION" }]

[[pin]]
id = "pkg"
class = "ours"
group = "tools"
family = "pypi"
scheme = "pep440"
source = { kind = "pypi", package = "pkg" }
hold = { max = "5.21.0.post0", reason = "a PENDING row registered pkg==5.21.0", review_by = "2099-01-01" }
sites = [{ file = "share/pkg.txt", regex = '^pkg==(?P<v>[0-9][^\s]*)$' }]
EOF
echo 'TOOL_VERSION=v1.2.0' > "${R5}/share/tool.env"
echo 'pkg==5.21.0' > "${R5}/share/pkg.txt"
: > "${R5}/EXPERIMENTS.md"
: > "${R5}/images.unpinned"
git -C "${R5}" init -q -b main && git -C "${R5}" add -A && git -C "${R5}" commit -qm base
echo '{"tool": {"v": "v1.4.0"}, "pkg": {"v": "5.21.0.post1"}}' > "${WORK}/resolved5.json"
out=$(python3 "${ROOT}/hack/bump" --root "${R5}" --resolved "${WORK}/resolved5.json" --no-tests tools \
    --summary "${WORK}/held.json" 2>&1); rc=$?
check "bump with held pins exits 0 (rc ${rc}: ${out##*$'\n'})" test "${rc}" -eq 0
check "held pins: nothing to move" grep -qx 'tools: nothing to move' <<< "${out}"
check "held pins: the tree is unchanged" test -z "$(git -C "${R5}" status --porcelain)"
check "held pins: a note names each hold" python3 - "${WORK}/held.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["moved"] == [] and s["changed"] is False, s
notes = sorted(s["notes"])
assert len(notes) == 2, notes
assert notes[0].startswith("pkg: held at 5.21.0: upstream 5.21.0.post1 reaches the hold 5.21.0.post0"), notes
assert notes[1].startswith("tool: held at v1.2.0: upstream v1.4.0 reaches the hold v1.3.0 (tool 1.3 drops a flag)"), notes
PY
FIX5="${WORK}/fixtures5.json"
cat > "${FIX5}" <<'EOF'
{"https://api.github.com/repos/o/tool/releases?per_page=100&page=1": {"status": 200, "headers": {},
  "json": [{"tag_name": "v1.4.0", "prerelease": false, "draft": false},
           {"tag_name": "v1.3.0", "prerelease": false, "draft": false},
           {"tag_name": "v1.2.0", "prerelease": false, "draft": false}]},
 "https://pypi.org/pypi/pkg/json": {"status": 200, "headers": {},
  "json": {"releases": {"5.21.0": [{"yanked": false}], "5.21.0.post1": [{"yanked": false}],
                        "5.22.2": [{"yanked": false}]}}}}
EOF
out=$(PINLIB_FIXTURES="${FIX5}" python3 "${ROOT}/hack/bump" --root "${R5}" --no-tests tools 2>&1); rc=$?
check "bump from the sources with held pins: nothing to move (rc ${rc}: ${out##*$'\n'})" \
    test "${rc}" -eq 0 -a "${out%%$'\n'*}" = "tools: nothing to move"
check "bump from the sources with held pins: the tree is unchanged" test -z "$(git -C "${R5}" status --porcelain)"

# the registry: one group (one branch) for both NRI components
check "nri-balloons and nri-balloons-guard bump in group nri (one branch)" python3 - "${ROOT}" <<'PY'
import sys, tomllib
reg = tomllib.load(open(sys.argv[1] + "/pins/registry.toml", "rb"))
groups = {p["group"] for p in reg["pin"] for s in p["sites"] if s["file"].startswith("hack/components/nri-balloons")}
assert groups == {"nri"}, groups
PY

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

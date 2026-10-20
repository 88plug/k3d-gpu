#!/usr/bin/env bash
# hack/release_cas.sh against a local bare origin over file:// (no network).
# Writers race the release through a git wrapper on PATH: before the script's
# `git push --atomic`, the wrapper runs the case's hook ($TEST_PREPUSH_HOOK). Invariants checked after every case:
#   every aur-v* tag is an ancestor of main; every "release: aur-vX" commit on
#   main carries tag aur-vX; no version is released twice; main VERSION equals
#   the highest tag.
#
# Usage: tests/auto_release_cas_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REL=${REL:-${ROOT}/hack/release_cas.sh}
T=$(mktemp -d "${TMPDIR:-/tmp}/release_cas.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
REAL_GIT=$(command -v git)
mkdir -p "$T/bin"
cat > "$T/bin/git" <<STUB
#!/usr/bin/env bash
case " \$* " in *" push --atomic "*)
  if [ -n "\${TEST_PREPUSH_HOOK:-}" ]; then "\$TEST_PREPUSH_HOOK"; fi;; esac
exec $(printf %q "$REAL_GIT") "\$@"
STUB
chmod +x "$T/bin/git"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# setup NAME: origin.git seeded at aur-v0.1.5; seed/ is the other writers' clone.
setup() {
  D=$T/$1; mkdir -p "$D"
  git init -q --bare -b main "$D/origin.git"
  git clone -q "file://$D/origin.git" "$D/seed" 2>/dev/null
  ( cd "$D/seed" || exit 1
    git checkout -q -b main
    mkdir -p scripts packaging/aur Formula share
    printf '#!/bin/sh\nVERSION="0.1.5"  # kept in lockstep\n' > scripts/k3d-gpu
    printf 'pkgname=k3d-gpu\npkgver=0.1.5\npkgrel=1\n' > packaging/aur/PKGBUILD
    printf 'pkgbase = k3d-gpu\n\tpkgver = 0.1.5\n\tsource = git+https://example/k3d-gpu.git#tag=aur-v0.1.5\n' > packaging/aur/.SRCINFO
    echo 'FROM scratch' > Dockerfile; echo '# ledger' > EXPERIMENTS.md; echo 'class K' > Formula/k3d-gpu.rb
    git add -A; git commit -q -m init; git tag aur-v0.1.5
    git push -q origin main aur-v0.1.5 )
  S0=$(git -C "$D/seed" rev-parse HEAD)
}
# write FILE MSG: another writer commits to main (appends a line to FILE).
write() {
  ( cd "$D/seed" || exit 1
    git fetch -q origin; git reset -q --hard origin/main
    echo "x $RANDOM" >> "$1"; git add "$1"; git commit -q -m "$2"; git push -q origin HEAD:main )
}
runner() { git clone -q "file://$D/origin.git" "$D/$1" 2>/dev/null; }
# release DIR EVENT [HOOK]: run the release body in a runner checkout.
release() {
  ( cd "$D/$1" && PATH="$T/bin:$PATH" GIT_COMMITTER_NAME="runner-$1" EVENT=$2 TEST_PREPUSH_HOOK=${3:-} GITHUB_OUTPUT="$D/$1.out" bash "$REL" ) > "$D/$1.log" 2>&1
}
# hook NAME TIMES BODY: a pre-push hook running BODY on its first TIMES calls.
hook() {
  local h=$D/$1.hook
  printf '#!/usr/bin/env bash\nunset TEST_PREPUSH_HOOK\nn=$(( $(cat %q 2>/dev/null || echo 0) + 1 )); echo $n > %q\n[ $n -le %s ] || exit 0\n%s\n' \
    "$h.n" "$h.n" "$2" "$3" > "$h"
  chmod +x "$h"; echo "$h"
}
o()    { git -C "$D/seed" "$@"; }
main() { o fetch -q -f origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' 2>/dev/null; o rev-parse origin/main; }
tags() { o ls-remote --tags origin 'aur-v*' | sed 's|.*refs/tags/||' | sort -V | tr '\n' ' '; }
tagsha() { o ls-remote --tags origin "$1" | cut -f1; }
invariants() {
  local m t v bad=
  m=$(main)
  for t in $(o ls-remote --tags origin 'aur-v*' | sed 's|.*refs/tags/||'); do
    o merge-base --is-ancestor "$(tagsha "$t")" "$m" || bad="$bad tag:$t-not-on-main"
  done
  while read -r sha v; do
    [ -n "$sha" ] || continue
    [ "$(tagsha "$v")" = "$sha" ] || bad="$bad orphan:$v"
  done < <(o log --format='%H %s' "$m" | sed -n 's/^\([0-9a-f]*\) release: \(aur-v[0-9.]*\).*/\1 \2/p')
  [ -z "$(o log --format=%s "$m" | grep -oE '^release: aur-v[0-9.]+' | sort | uniq -d)" ] || bad="$bad duplicate-release"
  v=$(o show "$m:scripts/k3d-gpu" | sed -n 's/^VERSION="\(.*\)".*/\1/p')
  [ "aur-v$v" = "$(o ls-remote --tags origin 'aur-v*' | sed 's|.*refs/tags/||' | sort -V | tail -1)" ] || bad="$bad VERSION=$v-not-highest-tag"
  echo "${bad# }"
}
check() {  # check NAME COND-RESULT(0/1) DETAIL
  local inv; inv=$(invariants)
  if [ "$2" -eq 0 ] && [ -z "$inv" ]; then ok "$1"; else fail "$1: $3 ${inv:+invariants: $inv}"; fi
}

# basic_release: a human launcher commit cuts aur-v0.1.6 with stamped files.
setup basic_release; write scripts/k3d-gpu "fix: launcher"; runner r; release r push; rc=$?
m=$(main)
c=1; [ "$rc" -eq 0 ] && [ "$(tagsha aur-v0.1.6)" = "$m" ] \
  && o show "$m:scripts/k3d-gpu" | grep -q '^VERSION="0.1.6"' \
  && o show "$m:packaging/aur/PKGBUILD" | grep -q '^pkgver=0.1.6' \
  && o show "$m:packaging/aur/.SRCINFO" | grep -q 'pkgver = 0.1.6' \
  && o show "$m:packaging/aur/.SRCINFO" | grep -q '#tag=aur-v0.1.6' \
  && grep -qx 'ver=0.1.6' "$D/r.out" && c=0
check basic_release $c "rc=$rc tags=$(tags)"

# stale_checkout: the runner checked out S1 but main moved to S2.
setup stale_checkout; write scripts/k3d-gpu "fix: one"; runner r; write share/x.yml "feat: two"; s2=$(main)
release r push; rc=$?; m=$(main)
c=1; [ "$rc" -eq 0 ] && [ "$(o rev-parse "$m^")" = "$s2" ] && [ "$(tagsha aur-v0.1.6)" = "$m" ] && c=0
check stale_checkout $c "rc=$rc tags=$(tags) $(tail -2 "$D/r.log" | tr '\n' ' ')"

# concurrent_same_tip: run A completes inside run B's pre-push window.
setup concurrent_same_tip; write scripts/k3d-gpu "fix: launcher"; runner a; runner b
h=$(hook b 1 "cd $(printf %q "$D/a") && GIT_COMMITTER_NAME=runner-a EVENT=push GITHUB_OUTPUT=$(printf %q "$D/a.out") bash $(printf %q "$REL") > $(printf %q "$D/a.log") 2>&1; echo \$? > $(printf %q "$D/a.rc")")
release b push "$h"; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(cat "$D/a.rc" 2>/dev/null)" = 0 ] && c=0
check concurrent_same_tip $c "rcA=$(cat "$D/a.rc" 2>/dev/null) rcB=$rc tags=$(tags) $(tail -2 "$D/b.log" | tr '\n' ' ')"

# bot_writer_mid_run: a [skip release] bot commit lands before B pushes.
setup bot_writer_mid_run; write scripts/k3d-gpu "fix: launcher"; runner b
h=$(hook b 1 "$(declare -f write); D=$(printf %q "$D"); write Formula/k3d-gpu.rb 'brew: bump [skip release]'")
release b push "$h"; rc=$?; m=$(main)
bot=$(o log --format='%H %s' "$m" | awk '/brew: bump/{print $1}')
c=1; [ "$rc" -eq 0 ] && [ -n "$bot" ] && [ -n "$(tagsha aur-v0.1.6)" ] && o merge-base --is-ancestor "$bot" "$(tagsha aur-v0.1.6)" && c=0
check bot_writer_mid_run $c "rc=$rc tags=$(tags) $(tail -2 "$D/b.log" | tr '\n' ' ')"

# tag_collision_atomic: aur-v0.1.6 appears on origin (at S0) before B pushes.
setup tag_collision_atomic; write scripts/k3d-gpu "fix: launcher"; runner b
h=$(hook b 1 "git -C $(printf %q "$D/seed") push -q origin $S0:refs/tags/aur-v0.1.6")
release b push "$h"; rc=$?; m=$(main)
c=1; [ "$rc" -eq 0 ] && [ "$(tagsha aur-v0.1.6)" = "$S0" ] && [ "$(tagsha aur-v0.1.7)" = "$m" ] && c=0
check tag_collision_atomic $c "rc=$rc tags=$(tags) $(tail -2 "$D/b.log" | tr '\n' ' ')"

# nothing_new_on_push: only a ledger commit and a [skip release] commit above the tag.
setup nothing_new_on_push; write EXPERIMENTS.md "ledger(H1): result"; write scripts/k3d-gpu "install: pin [skip release]"
before=$(main); runner r; release r push; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(main)" = "$before" ] && [ "$(tags)" = "aur-v0.1.5 " ] && [ ! -s "$D/r.out" ] && c=0
check nothing_new_on_push $c "rc=$rc tags=$(tags)"

# dispatch_forced: workflow_dispatch releases with nothing new.
setup dispatch_forced; runner r; release r workflow_dispatch; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(tagsha aur-v0.1.6)" = "$(main)" ] && grep -qx 'ver=0.1.6' "$D/r.out" && c=0
check dispatch_forced $c "rc=$rc tags=$(tags)"

# pin_bump_unmarked: the nightly bump commit (no [skip release]) is releasable.
for ev in push workflow_dispatch; do
  setup "pin_bump_$ev"; write Dockerfile "chore: bump k3s→v1.37.2-k3s1 & device-plugin→v0.18.0"
  bump=$(main); runner r; release r "$ev"; rc=$?; main >/dev/null
  c=1; [ "$rc" -eq 0 ] && [ -n "$(tagsha aur-v0.1.6)" ] && o merge-base --is-ancestor "$bump" "$(tagsha aur-v0.1.6)" && c=0
  check "pin_bump_unmarked ($ev)" $c "rc=$rc tags=$(tags)"
done

# retry_bound: a writer wins every race; the run stops after 5 attempts, no tag.
setup retry_bound; write scripts/k3d-gpu "fix: launcher"; runner b
h=$(hook b 99 "$(declare -f write); D=$(printf %q "$D"); write Formula/k3d-gpu.rb 'brew: bump [skip release]'")
release b push "$h"; rc=$?
c=1; [ "$rc" -ne 0 ] && [ "$(tags)" = "aur-v0.1.5 " ] && [ "$(cat "$h.n")" = 5 ] \
  && ! o log --format=%s "$(main)" | grep -q '^release: ' && c=0
check retry_bound $c "rc=$rc tags=$(tags) attempts=$(cat "$h.n" 2>/dev/null)"

# concurrent_dispatch: two forced runs at one tip both release, distinct versions.
setup concurrent_dispatch; runner a; runner b
h=$(hook b 1 "cd $(printf %q "$D/a") && GIT_COMMITTER_NAME=runner-a EVENT=workflow_dispatch GITHUB_OUTPUT=$(printf %q "$D/a.out") bash $(printf %q "$REL") > $(printf %q "$D/a.log") 2>&1; echo \$? > $(printf %q "$D/a.rc")")
release b workflow_dispatch "$h"; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(cat "$D/a.rc" 2>/dev/null)" = 0 ] && [ "$(tags)" = "aur-v0.1.5 aur-v0.1.6 aur-v0.1.7 " ] && c=0
check concurrent_dispatch $c "rcA=$(cat "$D/a.rc" 2>/dev/null) rcB=$rc tags=$(tags)"

# version_ahead_of_tag: main's VERSION was bumped above the last tag by hand;
# that version is released, not the one after it.
for ev in push workflow_dispatch; do
  setup "version_ahead_$ev"
  ( cd "$D/seed" && sed -i 's/^VERSION="0.1.5"/VERSION="0.1.6"/' scripts/k3d-gpu && git commit -qam "bump to 0.1.6" && git push -q origin HEAD:main )
  runner r; release r "$ev"; rc=$?; m=$(main)
  c=1; [ "$rc" -eq 0 ] && [ "$(tagsha aur-v0.1.6)" = "$m" ] && [ "$(tags)" = "aur-v0.1.5 aur-v0.1.6 " ] \
    && o show "$m:packaging/aur/PKGBUILD" | grep -q '^pkgver=0.1.6' && c=0
  check "version_ahead_of_tag ($ev)" $c "rc=$rc tags=$(tags) $(tail -1 "$D/r.log")"
done

# orphan_release_commit: a release commit whose tag never landed (a push that
# GitHub applied only in part) is tagged in place by the next push run.
setup orphan_release_commit
( cd "$D/seed" && sed -i 's/0\.1\.5/0.1.6/' scripts/k3d-gpu packaging/aur/PKGBUILD packaging/aur/.SRCINFO \
  && git commit -qam "release: aur-v0.1.6 [skip release]" && git push -q origin HEAD:main )
orphan=$(main); runner r; release r push; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(main)" = "$orphan" ] && [ "$(tagsha aur-v0.1.6)" = "$orphan" ] && c=0
check orphan_release_commit $c "rc=$rc tags=$(tags) $(tail -1 "$D/r.log")"

# skip_marker_in_body: [skip release] in the commit body is honoured on push.
setup skip_marker_in_body
( cd "$D/seed" && echo "x" >> scripts/k3d-gpu && git commit -qa -m "install: pin" -m "[skip release]" && git push -q origin HEAD:main )
before=$(main); runner r; release r push; rc=$?
c=1; [ "$rc" -eq 0 ] && [ "$(main)" = "$before" ] && [ "$(tags)" = "aur-v0.1.5 " ] && c=0
check skip_marker_in_body $c "rc=$rc tags=$(tags)"

# no_env_command_hook: the release script runs no command named by the environment.
if grep -n 'BEFORE_PUSH' "$REL"; then fail "no_env_command_hook: env-named command in $REL"
else ok "no_env_command_hook"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

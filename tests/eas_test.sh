#!/usr/bin/env bash
# Checks for integrations/eas without a cluster. With no argument every section
# runs; name sections to run only those (tests/eas_test.sh overlay).
#
#   overlay  integrations/eas/overlay.py, the k3s/k3d patch overlay, on synthetic
#            git repos built from verbatim EAS files (tests/fixtures/eas/trees,
#            EAS main 97f6157 and release-1.2 5c27fb3):
#            - the fixtures are byte-identical to upstream (blob shas in SOURCES)
#            - overlay.json resolves every patch, upstream drafts first-class, no copies
#            - main: 0002 and 0006 apply, 0003 only for nri-balloons, 0004 only on
#              more than one node, 0005 never; the recorded `verified` cases hold
#            - patches already in the tree: drop-merged (git apply -R --check)
#            - the fix done another way with the knob present: drop-fixed
#            - the fix done another way with the knob renamed: STOP
#            - the hunk moved as open EAS #50/#52 move it: STOP naming #50
#            - release-1.2: 0006 variant b applies, 0006a and 0005 do not
#            - --apply changes the checkout only when no row STOPs, and a re-run is a no-op
#            - every decision prints as `fact -> choice [source]`; --explain gives permalinks
# Needs bash, git, python3.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAILS=0
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# check <label> <want> <got>
check() {
    if [ "$2" = "$3" ]; then ok "$1"; else fail "$1: want [$2], got [$3]"; fi
}

# has <label> <needle> <haystack>
has() {
    if grep -qF -- "$2" <<< "$3"; then ok "$1"; else fail "$1: no [$2] in: $3"; fi
}

section_overlay() {
    local OVERLAY="${ROOT}/integrations/eas/overlay.py"
    local SPEC="${ROOT}/integrations/eas/overlay.json"
    local FIX="${ROOT}/tests/fixtures/eas/trees"
    local GH="https://github.com/intel/enterprise-ai-solutions"
    local out got want r before

    # Synthetic repos: no caller git config, fixed identities and dates.
    export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
        GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@invalid \
        GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@invalid \
        GIT_AUTHOR_DATE='2026-10-29T12:00:00Z' GIT_COMMITTER_DATE='2026-10-29T12:00:00Z'

    # mkrepo <tree> <dir>: a git repo whose one commit is a fixture tree.
    mkrepo() {
        mkdir -p "$2" && cp -R "${FIX}/$1/." "$2/" \
            && git -C "$2" init -q -b main && git -C "$2" add -A && git -C "$2" commit -qm "eas fixture $1"
    }
    commit() { git -C "$1" add -A && git -C "$1" commit -qm "$2"; }
    # patchfile <basename>: the path overlay.json's lookup order gives.
    patchfile() {
        local d
        for d in integrations/eas/patches integrations/eat/upstream/patches; do
            [ -f "${ROOT}/${d}/$1" ] && { echo "${ROOT}/${d}/$1"; return 0; }
        done
        return 1
    }
    # replace <file> <old> <new>: the one exact occurrence of old becomes new.
    replace() {
        python3 - "$@" <<'EOF'
import sys
path, old, new = sys.argv[1:4]
s = open(path).read()
if s.count(old) != 1:
    sys.exit("replace: %d matches of %r in %s" % (s.count(old), old, path))
open(path, "w").write(s.replace(old, new))
EOF
    }
    # brief <repo> <policy> <nodes> [overlay args]: "0002=apply:a 0003=skip ...". The exit
    # status goes to ${WORK}/rc (brief runs in a command substitution), text to d.txt.
    brief() {
        python3 "${OVERLAY}" --eas "$1" --policy "$2" --nodes "$3" --json "${WORK}/d.json" "${@:4}" > "${WORK}/d.txt" 2>&1
        echo "$?" > "${WORK}/rc"
        python3 -c 'import json,sys; print(" ".join("%s=%s" % (d["id"], d["brief"]) for d in json.load(open(sys.argv[1]))["decisions"]))' \
            "${WORK}/d.json" 2>/dev/null
    }
    # jq_ <expr over the decisions JSON as d>
    jq_() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "${WORK}/d.json" "$1"; }

    # 1. Fixtures are byte-identical to upstream and nothing else is in the trees.
    local tree blob path f listed=0 bad=0
    while read -r tree blob path; do
        case "${tree}" in ''|'#'*) continue ;; esac
        listed=$((listed + 1))
        if [ "${tree}" = - ]; then f="${FIX}/${path}"; else f="${FIX}/${tree}/${path}"; fi
        got=$(git hash-object "${f}" 2>/dev/null)
        [ "${got}" = "${blob}" ] || { bad=$((bad + 1)); fail "fixture ${tree}/${path}: blob ${got:-missing}, upstream ${blob}"; }
    done < "${FIX}/SOURCES"
    [ "${bad}" -eq 0 ] && ok "all ${listed} fixture files match their upstream blob shas"
    got=$(cd "${FIX}" && find . -type f ! -name SOURCES | sed 's#^\./##; s#^\(97f6157\|5c27fb3\)/#\1 #; /^LICENSE$/s#^#- #' | sort)
    want=$(awk '!/^#/ && NF==3 {print $1" "$3}' "${FIX}/SOURCES" | sort)
    check "the fixture trees hold exactly the files SOURCES lists" "${want}" "${got}"
    has "the fixtures carry EAS's Apache-2.0 LICENSE" "Apache License" "$(head -3 "${FIX}/LICENSE")"

    # 2. overlay.json: every patch resolves, in order, and no draft is copied.
    out=$(python3 - "${ROOT}" "${SPEC}" <<'EOF'
import json, os, re, sys
root, spec = sys.argv[1], json.load(open(sys.argv[2]))
assert spec["schema"] == "k3d-gpu.eas-overlay/v1", spec["schema"]
dirs = spec["patch_dirs"]
assert dirs == ["integrations/eas/patches", "integrations/eat/upstream/patches"], dirs
ids = [r["id"] for r in spec["rows"]]
assert ids == sorted(set(ids)), ids
for r in spec["rows"]:
    assert r["needed_when"] in ("always", "eas_owns_nri", "multi_node", "never"), r
    assert r["needed_when"] in spec["rules"], r["needed_when"]
    assert r["variants"], r["id"]
    for v in r["variants"]:
        for f in v["files"]:
            hits = [d for d in dirs if os.path.isfile(os.path.join(root, d, f))]
            assert len(hits) == 1, (f, hits)
    for p in r["fixed_probe"] + r["moved"]:
        re.compile(p["regex"])
names = [n for d in dirs for n in os.listdir(os.path.join(root, d)) if n.endswith(".patch")]
assert len(names) == len(set(names)), "a patch is in both dirs"
for v in spec["verified"]:
    assert os.path.isdir(os.path.join(root, v["fixture"])), v["fixture"]
    for c in v["cases"]:
        assert sorted(c["expect"]) == ids, c
print("ok")
EOF
)
    check "overlay.json: rows, rules, patch lookup, probes and verified cases are well-formed" ok "${out}"

    # 3. The recorded verified cases hold on the byte-identical fixture trees.
    mkrepo 97f6157 "${WORK}/main" || fail "mkrepo main"
    mkrepo 5c27fb3 "${WORK}/r12" || fail "mkrepo release-1.2"
    while IFS='|' read -r fixture policy nodes want; do
        r="${WORK}/main"; [ "${fixture##*/}" = 5c27fb3 ] && r="${WORK}/r12"
        got=$(brief "${r}" "${policy}" "${nodes}")
        check "verified ${fixture##*/} ${policy} nodes=${nodes}" "${want}" "${got}"
    done < <(python3 -c '
import json, sys
for v in json.load(open(sys.argv[1]))["verified"]:
    for c in v["cases"]:
        print("|".join([v["fixture"], c["policy"], str(c["nodes"]), " ".join("%s=%s" % kv for kv in sorted(c["expect"].items()))]))
' "${SPEC}")

    # 4. EAS main on the desktop (one node, k3d-gpu's balloons policy kept): the expected set.
    got=$(brief "${WORK}/main" best-effort 1)
    check "main best-effort 1 node: 0002 apply, 0003/0004/0005 skip, 0006 apply a" \
        "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:a" "${got}"
    check "main: exit 0" 0 "$(cat "${WORK}/rc")"
    check "main kubelet-static 1 node: 0003 still skips (EAS installs balloons only for nri-balloons)" \
        "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:a" "$(brief "${WORK}/main" kubelet-static 1)"
    check "main nri-balloons 1 node: 0003 applies" \
        "0002=apply:a 0003=apply:a 0004=skip 0005=skip 0006=apply:a" "$(brief "${WORK}/main" nri-balloons 1)"
    check "main best-effort 2 nodes: 0004 applies" \
        "0002=apply:a 0003=skip 0004=apply:a 0005=skip 0006=apply:a" "$(brief "${WORK}/main" best-effort 2)"
    check "main: 0002 resolves to the upstream draft" "${ROOT}/integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch" \
        "$(cd "${ROOT}" && realpath "$(jq_ 'd["decisions"][0]["files"][0]')")"
    check "main: 0002 names the generator knob it needs" istio_platform "$(jq_ 'd["decisions"][0]["knob"]')"

    # Text: one `fact -> choice [source]` line per row, sources at the checkout's sha.
    local sha sha7
    sha=$(git -C "${WORK}/main" rev-parse HEAD); sha7=${sha:0:7}
    out=$(python3 "${OVERLAY}" --eas "${WORK}/main" --policy best-effort --nodes 1)
    check "text: five decision lines" 5 "$(grep -c -- ' -> ' <<< "${out}")"
    check "text: every decision line is fact -> choice [source]" 0 \
        "$(grep -v '^#' <<< "${out}" | grep -cvE '^.+ -> (apply|skip|drop-merged|drop-fixed|STOP)([: ].*)? \[[^]]+\]$')"
    has "text: 0006 apply cites the line it changes" \
        "-> apply 0006-eas-envoy-control-plane-affinity.patch [eas@${sha7} roles/envoy_gateway/tasks/controller.yaml:154]" "${out}"
    has "text: 0003 skip cites its rule" "-> skip [overlay.json 0003 needed_when=eas_owns_nri]" "${out}"
    # 0002's header says line 59 (cut on an older main); at 97f6157 the hunk lands
    # after `istio_enabled: true`, line 68.
    check "fixture: istio_enabled is line 68 of main's global_config.yaml" "istio_enabled: true" \
        "$(sed -n 68p "${WORK}/main/configs/defaults/global_config.yaml")"
    has "text: 0002 apply cites where the hunk lands at this commit, not the header's line" \
        "-> apply 0002-eas-istio-global-platform.patch [eas@${sha7} configs/defaults/global_config.yaml:69]" "${out}"
    out=$(python3 "${OVERLAY}" --eas "${WORK}/main" --policy best-effort --nodes 1 --explain)
    has "--explain: permalink at the full sha" "[${GH}/blob/${sha}/roles/envoy_gateway/tasks/controller.yaml#L154]" "${out}"
    has "--explain: a skip states its rule" "kubernetes_cpu_policy is nri-balloons" "${out}"

    # 5. release-1.2: 0006 variant b; 0006a and 0005 do not apply there.
    got=$(brief "${WORK}/r12" best-effort 1)
    check "release-1.2: 0006 variant b applies" "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:b" "${got}"
    check "release-1.2: 0006 variant a does not apply" False "$(jq_ 'd["decisions"][4]["variants"][0]["applies"]')"
    check "release-1.2: 0006 variant b applies" True "$(jq_ 'd["decisions"][4]["variants"][1]["applies"]')"
    check "release-1.2: 0005 does not apply" False "$(jq_ 'd["decisions"][3]["variants"][0]["applies"]')"
    check "release-1.2: 0006 b is the overlay-only patch" "${ROOT}/integrations/eas/patches/0006-release-1.2-envoy-control-plane-affinity.patch" \
        "$(cd "${ROOT}" && realpath "$(jq_ 'd["decisions"][4]["files"][0]')")"
    out=$(python3 "${OVERLAY}" --eas "${WORK}/r12" --policy best-effort --nodes 1)
    has "release-1.2 text: 0006 says why b" "variant a does not apply, variant b does" "${out}"
    sha=$(git -C "${WORK}/r12" rev-parse HEAD)
    has "release-1.2 text: 0006 b cites install.yaml" \
        "-> apply 0006-release-1.2-envoy-control-plane-affinity.patch [eas@${sha:0:7} roles/envoy_gateway/tasks/install.yaml:194]" "${out}"
    check "fixture: line 194 of release-1.2 install.yaml is the selector comment 0006 b replaces" \
        "# nodeSelector keys on the standard control-plane label (empty string is" \
        "$(sed -n 194p "${WORK}/r12/roles/envoy_gateway/tasks/install.yaml")"
    has "release-1.2 text: 0002 lands after istio_enabled (line 61 there)" \
        "-> apply 0002-eas-istio-global-platform.patch [eas@${sha:0:7} configs/defaults/global_config.yaml:62]" "${out}"

    # 6. Patches already upstream: drop-merged.
    cp -R "${WORK}/main" "${WORK}/merged"
    if ! { git -C "${WORK}/merged" apply "$(patchfile 0002-eas-istio-global-platform.patch)" \
               "$(patchfile 0003-eas-pin-nri-balloons-chart.patch)" "$(patchfile 0004-eas-byo-node-topology-labels.patch)" \
               "$(patchfile 0006-eas-envoy-control-plane-affinity.patch)" \
           && commit "${WORK}/merged" "upstream carries the overlay"; }; then
        fail "pre-apply the patches"
    fi
    check "patches already in the tree: drop-merged" \
        "0002=drop-merged:a 0003=drop-merged:a 0004=drop-merged:a 0005=skip 0006=drop-merged:a" \
        "$(brief "${WORK}/merged" nri-balloons 3)"
    check "drop-merged: exit 0" 0 "$(cat "${WORK}/rc")"
    sha=$(git -C "${WORK}/merged" rev-parse HEAD)
    has "drop-merged: cites the merged line" "-> drop-merged [eas@${sha:0:7} configs/defaults/global_config.yaml:69]" "$(cat "${WORK}/d.txt")"
    check "drop-merged: that line is 0002's comment" '# istio_platform: ""                      # k3d | k3s | microk8s | minikube | gke | openshift' \
        "$(sed -n 69p "${WORK}/merged/configs/defaults/global_config.yaml")"

    # 7. The fix done differently, knob present: drop-fixed.
    # 0002 through set_values with the same knob; the patch's context is gone.
    cp -R "${WORK}/main" "${WORK}/fixed"
    replace "${WORK}/fixed/roles/istio/tasks/install.yaml" \
        $'        dependency_update: true\n        wait: "{{ helm_wait }}"' \
        $'        dependency_update: true\n        set_values:\n          - value: "global.platform={{ istio_platform }}"\n        wait: "{{ helm_wait }}"'
    replace "${WORK}/fixed/roles/istio/defaults/main.yaml" $'istio_wait_timeout: "300s"\n' $'istio_wait_timeout: "300s"\nistio_platform: ""\n'
    # 0006 with a matchExpressions Exists term and no nodeSelector at all.
    replace "${WORK}/fixed/roles/envoy_gateway/tasks/controller.yaml" \
        $'                nodeSelector:\n                  node-role.kubernetes.io/control-plane: ""\n' \
        $'                affinity:\n                  nodeAffinity:\n                    requiredDuringSchedulingIgnoredDuringExecution:\n                      nodeSelectorTerms:\n                        - matchExpressions:\n                            - key: node-role.kubernetes.io/control-plane\n                              operator: Exists\n'
    # 0003 with a chart_version of upstream's own.
    replace "${WORK}/fixed/roles/nri_cpu_balloons/tasks/install_advanced.yaml" \
        $'    release_namespace: "{{ nri_cpu_balloons.namespace }}"\n    create_namespace: true\n    values_files:\n      - "{{ nri_tmp_dir }}/nri-values.yaml"' \
        $'    release_namespace: "{{ nri_cpu_balloons.namespace }}"\n    chart_version: "{{ nri_cpu_balloons.helm.chart_version }}"\n    create_namespace: true\n    values_files:\n      - "{{ nri_tmp_dir }}/nri-values.yaml"'
    commit "${WORK}/fixed" "istio, envoy and balloons fixed another way"
    check "fixed another way, knob present: drop-fixed" \
        "0002=drop-fixed 0003=drop-fixed 0004=skip 0005=skip 0006=drop-fixed" "$(brief "${WORK}/fixed" nri-balloons 1)"
    check "drop-fixed: exit 0" 0 "$(cat "${WORK}/rc")"
    sha=$(git -C "${WORK}/fixed" rev-parse HEAD)
    has "drop-fixed: 0002 cites where upstream wires global.platform" \
        "-> drop-fixed [eas@${sha:0:7} roles/istio/tasks/install.yaml:79]" "$(cat "${WORK}/d.txt")"
    has "drop-fixed: 0006 cites the affinity term" \
        "-> drop-fixed [eas@${sha:0:7} roles/envoy_gateway/tasks/controller.yaml:178]" "$(cat "${WORK}/d.txt")"

    # 0006 affinity added but the exact "" selector kept: still broken on k3s, STOP.
    cp -R "${WORK}/main" "${WORK}/both"
    replace "${WORK}/both/roles/envoy_gateway/tasks/controller.yaml" \
        $'                  node-role.kubernetes.io/control-plane: ""\n' \
        $'                  node-role.kubernetes.io/control-plane: ""\n                affinity:\n                  nodeAffinity:\n                    requiredDuringSchedulingIgnoredDuringExecution:\n                      nodeSelectorTerms:\n                        - matchExpressions:\n                            - key: node-role.kubernetes.io/control-plane\n                              operator: Exists\n'
    commit "${WORK}/both" "envoy: add affinity, keep the selector"
    got=$(brief "${WORK}/both" best-effort 1)
    check "affinity next to the exact \"\" selector is not a fix: STOP" "0006=STOP" "$(tr ' ' '\n' <<< "${got}" | grep '^0006=')"

    # 8. The fix done differently, knob renamed: STOP, nothing to write the old knob to.
    cp -R "${WORK}/main" "${WORK}/renamed"
    replace "${WORK}/renamed/roles/istio/tasks/install.yaml" \
        $'        dependency_update: true\n        wait: "{{ helm_wait }}"' \
        $'        dependency_update: true\n        set_values:\n          - value: "global.platform={{ istio_cni_platform }}"\n        wait: "{{ helm_wait }}"'
    commit "${WORK}/renamed" "istio: istio_cni_platform"
    got=$(brief "${WORK}/renamed" best-effort 1)
    check "knob renamed: 0002 STOP" "0002=STOP 0003=skip 0004=skip 0005=skip 0006=apply:a" "${got}"
    check "knob renamed: exit 3" 3 "$(cat "${WORK}/rc")"
    out=$(cat "${WORK}/d.txt")
    has "knob renamed: names the missing knob" "istio_platform" "${out}"
    has "knob renamed: says regenerate" "regenerate 0002" "${out}"
    # Also when the patch itself would still apply on top: two wirings of one value.
    cp -R "${WORK}/main" "${WORK}/renamed2"
    replace "${WORK}/renamed2/roles/istio/defaults/main.yaml" $'istio_wait_timeout: "300s"\n' \
        $'istio_wait_timeout: "300s"\nistio_values: {global: {platform: "{{ istio_cni_platform }}"}}\n'
    commit "${WORK}/renamed2" "istio: istio_values"
    got=$(brief "${WORK}/renamed2" best-effort 1)
    check "knob renamed while 0002 still applies: STOP, not a second wiring" "0002=STOP" "${got%% *}"

    # 9. The hunk moved the way open EAS #50/#52 move it: STOP naming #50, nothing applied.
    cp -R "${WORK}/main" "${WORK}/pr50"
    replace "${WORK}/pr50/roles/envoy_gateway/tasks/controller.yaml" \
        $'- name: "envoy_gateway | Create EnvoyProxy config (DaemonSet on control-plane nodes)"\n  kubernetes.core.k8s:' \
        $'- name: "envoy_gateway | Create EnvoyProxy config ({{ _envoy_gateway_proxy_shape }})"\n  vars:\n    _eg_k8s_provider:\n      controlplane_daemonset:\n        envoyDaemonSet:\n          pod:\n            nodeSelector:\n              node-role.kubernetes.io/control-plane: ""\n            tolerations:\n              - key: node-role.kubernetes.io/control-plane\n                operator: Exists\n                effect: NoSchedule\n  kubernetes.core.k8s:'
    replace "${WORK}/pr50/roles/envoy_gateway/tasks/controller.yaml" \
        $'          kubernetes:\n            envoyDaemonSet:\n              pod:\n                nodeSelector:\n                  node-role.kubernetes.io/control-plane: ""\n                tolerations:\n                  - key: node-role.kubernetes.io/control-plane\n                    operator: Exists\n                    effect: NoSchedule\n' \
        $'          kubernetes: "{{ _eg_k8s_provider[_envoy_gateway_proxy_shape] }}"\n'
    commit "${WORK}/pr50" "platform layer"
    got=$(brief "${WORK}/pr50" best-effort 1)
    check "#50 layout: 0006 STOP" "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=STOP" "${got}"
    check "#50 layout: exit 3" 3 "$(cat "${WORK}/rc")"
    out=$(cat "${WORK}/d.txt")
    has "#50 layout: names the upstream PR" "${GH}/pull/50" "${out}"
    has "#50 layout: says regenerate" "regenerate 0006" "${out}"
    python3 "${OVERLAY}" --eas "${WORK}/pr50" --policy best-effort --nodes 1 --apply > /dev/null 2>&1
    check "#50 layout --apply: exit 3" 3 "$?"
    check "#50 layout --apply: nothing applied, 0002 included" "" "$(git -C "${WORK}/pr50" status --porcelain)"
    # Once a k3s entry lands in #50's vars layout: drop-fixed.
    mkdir -p "${WORK}/pr50/roles/envoy_gateway/vars"
    printf -- '---\n_envoy_gateway_proxy_shape: controlplane_affinity\n' > "${WORK}/pr50/roles/envoy_gateway/vars/k3s.yaml"
    commit "${WORK}/pr50" "envoy_gateway: k3s vars"
    check "#50 layout plus vars/k3s.yaml: drop-fixed" "0006=drop-fixed" "$(brief "${WORK}/pr50" best-effort 1 | tr ' ' '\n' | grep '^0006=')"

    # 10. Other moves: a known one (EAS #61 for 0002's context), and an unknown one.
    cp -R "${WORK}/main" "${WORK}/pr61"
    replace "${WORK}/pr61/configs/defaults/global_config.yaml" \
        $'velero_enabled: false                     # opt-in: backup/restore. Requires CSI-snapshot storage\n' \
        $'velero_enabled: false                     # opt-in: backup/restore. Requires CSI-snapshot storage\nheadlamp_enabled: false                   # opt-in: Kubernetes web UI\n'
    commit "${WORK}/pr61" "headlamp"
    got=$(brief "${WORK}/pr61" best-effort 1)
    check "#61 context: 0002 STOP" "0002=STOP" "${got%% *}"
    has "#61 context: names the upstream PR" "${GH}/pull/61" "$(cat "${WORK}/d.txt")"
    cp -R "${WORK}/main" "${WORK}/moved"
    replace "${WORK}/moved/roles/istio/tasks/install.yaml" \
        $'        values_files: "{{ [istio_helm_default_values_file] }}"\n        dependency_update: true' \
        $'        values_files: "{{ [istio_helm_default_values_file] + istio_extra_values_files }}"\n        dependency_update: true'
    commit "${WORK}/moved" "istio: extra values files (#123)"
    got=$(brief "${WORK}/moved" best-effort 1)
    check "unknown move: 0002 STOP" "0002=STOP" "${got%% *}"
    has "unknown move: names the last upstream commit on the patch's files" \
        "$(git -C "${WORK}/moved" rev-parse --short=7 HEAD) istio: extra values files (#123)" "$(cat "${WORK}/d.txt")"

    # 11. --apply: only at HEAD, all or nothing, idempotent.
    cp -R "${WORK}/main" "${WORK}/app"
    python3 "${OVERLAY}" --eas "${WORK}/app" --policy best-effort --nodes 1 --apply > "${WORK}/a.txt" 2>&1
    check "--apply: exit 0" 0 "$?"
    want=$'configs/defaults/global_config.yaml\nroles/envoy_gateway/tasks/controller.yaml\nroles/istio/defaults/main.yaml\nroles/istio/tasks/install.yaml\nroles/istio/tasks/upgrade.yaml'
    check "--apply: exactly 0002 and 0006a's files change" "${want}" "$(git -C "${WORK}/app" diff --name-only | sort)"
    has "--apply: says what it applied" "applied 0002-eas-istio-global-platform.patch 0006-eas-envoy-control-plane-affinity.patch" "$(cat "${WORK}/a.txt")"
    before=$(git -C "${WORK}/app" diff)
    python3 "${OVERLAY}" --eas "${WORK}/app" --policy best-effort --nodes 1 --apply > "${WORK}/a.txt" 2>&1
    check "--apply again: exit 0" 0 "$?"
    has "--apply again: a no-op" "already applied" "$(cat "${WORK}/a.txt")"
    check "--apply again: the tree is unchanged" "${before}" "$(git -C "${WORK}/app" diff)"
    check "decisions read HEAD, not the patched working tree" "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:a" \
        "$(brief "${WORK}/app" best-effort 1)"
    # A re-run whose facts now pick one more patch: that one is applied, the rest kept.
    python3 "${OVERLAY}" --eas "${WORK}/app" --policy nri-balloons --nodes 1 --apply > "${WORK}/a.txt" 2>&1
    check "--apply re-run with nri-balloons: exit 0" 0 "$?"
    has "--apply re-run: applies only 0003" "applied 0003-eas-pin-nri-balloons-chart.patch to" "$(cat "${WORK}/a.txt")"
    has "--apply re-run: keeps 0002 and 0006" \
        "already applied in ${WORK}/app: 0002-eas-istio-global-platform.patch 0006-eas-envoy-control-plane-affinity.patch" "$(cat "${WORK}/a.txt")"
    check "--apply re-run: 0003's files join the diff" "${want}"$'\nroles/nri_cpu_balloons/defaults/main.yaml\nroles/nri_cpu_balloons/tasks/install_advanced.yaml' \
        "$(git -C "${WORK}/app" diff --name-only | sort)"
    cp -R "${WORK}/main" "${WORK}/dirty"
    replace "${WORK}/dirty/roles/istio/tasks/install.yaml" $'        dependency_update: true\n        wait: "{{ helm_wait }}"\n        timeout: "{{ istio_wait_timeout }}"\n      register: helm_install_result' \
        $'        wait: "{{ helm_wait }}"\n        timeout: "{{ istio_wait_timeout }}"\n      register: helm_install_result'
    python3 "${OVERLAY}" --eas "${WORK}/dirty" --policy best-effort --nodes 1 --apply > "${WORK}/a.txt" 2>&1
    check "--apply on a conflicting local edit: exit 3" 3 "$?"
    check "--apply on a conflicting local edit: no other file touched" roles/istio/tasks/install.yaml "$(git -C "${WORK}/dirty" diff --name-only)"
    python3 "${OVERLAY}" --eas "${WORK}/merged" --rev HEAD~1 --policy best-effort --nodes 1 --apply > /dev/null 2>&1
    check "--apply with --rev other than HEAD: exit 2" 2 "$?"
    python3 "${OVERLAY}" --eas "${WORK}/merged" --rev HEAD~1 --policy best-effort --nodes 1 --json "${WORK}/d.json" > /dev/null 2>&1
    check "--rev reads that commit without a checkout" "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:a" \
        "$(python3 -c 'import json,sys; print(" ".join("%s=%s" % (d["id"], d["brief"]) for d in json.load(open(sys.argv[1]))["decisions"]))' "${WORK}/d.json")"

    # 12. Inputs it refuses.
    python3 "${OVERLAY}" --eas "${WORK}/main" --policy static --nodes 1 > /dev/null 2>&1
    check "an unknown kubernetes_cpu_policy: exit 2" 2 "$?"
    python3 "${OVERLAY}" --eas "${WORK}/main" --policy best-effort --nodes 0 > /dev/null 2>&1
    check "nodes 0: exit 2" 2 "$?"
    mkdir -p "${WORK}/notgit"
    python3 "${OVERLAY}" --eas "${WORK}/notgit" --policy best-effort --nodes 1 > /dev/null 2>&1
    check "not a git checkout: exit 2" 2 "$?"
    python3 - "${SPEC}" "${WORK}/bad.json" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
s["rows"][0]["variants"][0]["files"] = ["0099-missing.patch"]
json.dump(s, open(sys.argv[2], "w"))
EOF
    python3 "${OVERLAY}" --spec "${WORK}/bad.json" --eas "${WORK}/main" --policy best-effort --nodes 1 > "${WORK}/a.txt" 2>&1
    check "a patch file that is in no patch dir: exit 2" 2 "$?"
    has "a missing patch is named" "0099-missing.patch" "$(cat "${WORK}/a.txt")"

    # 13. --check-verified and --watch (what apply-check.sh --overlay runs on real clones).
    sha=$(git -C "${WORK}/main" rev-parse HEAD)
    python3 - "${SPEC}" "${WORK}/v.json" "${sha}" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
s["verified"] = [{"profile": "fixture", "rev": sys.argv[3], "fixture": "-", "cases": [
    {"policy": "best-effort", "nodes": 1, "expect": {"0002": "apply:a", "0003": "skip", "0004": "skip", "0005": "skip", "0006": "apply:a"}}]}]
s["watch"] = [{"ref": "HEAD", "what": "the fixture"}, {"ref": "pull/999/head", "what": "absent"}]
json.dump(s, open(sys.argv[2], "w"))
EOF
    out=$(python3 "${OVERLAY}" --spec "${WORK}/v.json" --eas "${WORK}/main" --check-verified 2>&1)
    check "--check-verified: exit 0 when the recorded decisions hold" 0 "$?"
    has "--check-verified: prints the case" "ok   fixture ${sha:0:7} best-effort nodes=1" "${out}"
    replace "${WORK}/v.json" '"0006": "apply:a"' '"0006": "apply:b"'
    out=$(python3 "${OVERLAY}" --spec "${WORK}/v.json" --eas "${WORK}/main" --check-verified 2>&1)
    check "--check-verified: exit 1 when a decision moved" 1 "$?"
    has "--check-verified: names the row that moved" "0006 want apply:b, got apply:a" "${out}"
    out=$(python3 "${OVERLAY}" --spec "${WORK}/v.json" --eas "${WORK}/main" --watch 2>&1)
    check "--watch: exit 0" 0 "$?"
    has "--watch: decisions at a ref" "-> apply 0006-eas-envoy-control-plane-affinity.patch" "${out}"
    has "--watch: an absent ref is reported" "pull/999/head: not in this clone" "${out}"

    # 14. pins.json (when present): every pinned EAS sha has a verified overlay entry.
    if [ -f "${ROOT}/integrations/eas/pins.json" ]; then
        out=$(python3 - "${ROOT}/integrations/eas/pins.json" "${SPEC}" <<'EOF'
import json, sys
def shas(o):
    if isinstance(o, dict):
        if o.get("repo", "").endswith("enterprise-ai-solutions") and o.get("sha"):
            yield o["sha"]
        for v in o.values():
            yield from shas(v)
    elif isinstance(o, list):
        for v in o:
            yield from shas(v)
pinned = set(shas(json.load(open(sys.argv[1]))))
verified = {v["rev"] for v in json.load(open(sys.argv[2]))["verified"]}
print(" ".join(sorted(pinned - verified)) or "ok")
EOF
)
        check "every EAS sha in pins.json has a verified overlay entry" ok "${out}"
    else
        ok "pins.json not present yet; the verified-pins check runs once it is"
    fi

    # 15. Committed executable (eas up runs it directly).
    out=$(git -C "${ROOT}" ls-files -s -- integrations/eas/overlay.py)
    [ -n "${out}" ] || fail "integrations/eas/overlay.py is not in git"
    while read -r mode _ _ path; do
        [ -n "${path:-}" ] || continue
        if [ "${mode}" = 100755 ]; then ok "executable in git: ${path}"; else fail "not executable in git (${mode}): ${path}"; fi
    done < <(git -C "${ROOT}" ls-files -s -- 'integrations/eas/*.py')
}

SECTIONS=(overlay)
[ "$#" -gt 0 ] && SECTIONS=("$@")
for s in "${SECTIONS[@]}"; do
    if declare -F "section_${s}" > /dev/null; then
        echo "## ${s}"
        "section_${s}"
    else
        fail "no section ${s}"
    fi
done

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]

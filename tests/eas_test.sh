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
#            - 0006: an Exists term counts only when no file of the role keeps the
#              exact "" selector (any quoting), and a #50-layout vars/k3s.yaml only
#              when it selects a shape other than controlplane_daemonset
#            - the hunk moved as open EAS #50/#52 move it: STOP naming #50
#            - a STOP names a `moved` hint only when the hint watches a file whose
#              hunks fail, else the last commit on the failing files
#            - release-1.2: 0006 variant b applies, 0006a and 0005 do not
#            - --apply changes the checkout only when no row STOPs, and a re-run is a no-op
#            - every decision prints as `fact -> choice [source]`; --explain gives permalinks
#   config   integrations/eas/eas-config.py on golden fixtures, tests/fixtures/eas/config/<case>/:
#            facts.json and the args file go in; expected/ holds what must come out,
#            byte for byte: k3d-gpu-overrides.yaml, global_config.edit.yaml,
#            models.edit.yaml, repos.<layer>.yaml and choices.txt (also its stdout).
#            A case with expected/refusal.txt must exit 3, print exactly that on stderr
#            and write no file. Also: the generator runs on the Python standard library
#            alone (python3 -S), its outputs parse as YAML of the declared shape, a
#            floating ref is refused, malformed facts exit 2, a refusal or a second run
#            leaves no stale file of an earlier run, a KV cache lowered to fit's carries
#            fit's --max-model-len, NFD is left to EI only under the release name EI's
#            role looks for, --explain prints permalinks, every source it can cite is
#            a permalink at a full commit SHA, and pins.json has the k3d-gpu.eas-pins/v1
#            shape.
# Needs bash, git, python3 (with PyYAML, for the config section's shape checks only).

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
    # d.json is removed first, so a run that writes none prints nothing instead of the
    # previous run's decisions.
    brief() {
        rm -f "${WORK}/d.json"
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
        re.compile(p.get("absent") or "")
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
    # The same with the selector in flow style, its key quoted: still not a fix.
    cp -R "${WORK}/main" "${WORK}/flow"
    replace "${WORK}/flow/roles/envoy_gateway/tasks/controller.yaml" \
        $'                nodeSelector:\n                  node-role.kubernetes.io/control-plane: ""\n' \
        $'                nodeSelector: {"node-role.kubernetes.io/control-plane": ""}\n                affinity:\n                  nodeAffinity:\n                    requiredDuringSchedulingIgnoredDuringExecution:\n                      nodeSelectorTerms:\n                        - matchExpressions:\n                            - key: node-role.kubernetes.io/control-plane\n                              operator: Exists\n'
    commit "${WORK}/flow" "envoy: flow-style selector next to the affinity"
    got=$(brief "${WORK}/flow" best-effort 1)
    check "affinity next to a quoted-key \"\" selector is not a fix: STOP" "0006=STOP" "$(tr ' ' '\n' <<< "${got}" | grep '^0006=')"
    # An Exists term in another file of the role does not fix the EnvoyProxy, whose
    # exact "" selector is still in controller.yaml: 0006 still applies.
    cp -R "${WORK}/main" "${WORK}/elsewhere"
    printf -- '---\n- name: "envoy_gateway | some other control-plane workload"\n  vars:\n    _affinity:\n      nodeAffinity:\n        requiredDuringSchedulingIgnoredDuringExecution:\n          nodeSelectorTerms:\n            - matchExpressions:\n                - key: node-role.kubernetes.io/control-plane\n                  operator: Exists\n' \
        > "${WORK}/elsewhere/roles/envoy_gateway/tasks/other.yaml"
    commit "${WORK}/elsewhere" "envoy: another workload with an Exists term"
    got=$(brief "${WORK}/elsewhere" best-effort 1)
    check "an Exists term in another file, the \"\" selector kept: 0006 still applies" "0006=apply:a" \
        "$(tr ' ' '\n' <<< "${got}" | grep '^0006=')"

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
    # A k3s entry in #50's vars layout that keeps controlplane_daemonset, the shape
    # with the exact "" selector, is not a fix: still the STOP naming #50.
    mkdir -p "${WORK}/pr50/roles/envoy_gateway/vars"
    printf -- '---\n_envoy_gateway_proxy_shape: controlplane_daemonset\n' > "${WORK}/pr50/roles/envoy_gateway/vars/k3s.yaml"
    commit "${WORK}/pr50" "envoy_gateway: k3s vars, kubeadm shape"
    got=$(brief "${WORK}/pr50" best-effort 1)
    check "#50 layout plus vars/k3s.yaml on controlplane_daemonset: 0006 STOP" "0006=STOP" "$(tr ' ' '\n' <<< "${got}" | grep '^0006=')"
    has "#50 layout plus vars/k3s.yaml on controlplane_daemonset: names #50" "${GH}/pull/50" "$(cat "${WORK}/d.txt")"
    # Once a k3s entry selects another shape: drop-fixed.
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
    # A `moved` hint names its PR only when the file it watches is one whose hunk
    # fails: here headlamp_enabled exists away from 0002's context (the hunk still
    # applies) and the hunk that fails is in install.yaml, changed after it.
    cp -R "${WORK}/main" "${WORK}/blame"
    printf 'headlamp_enabled: false\n' >> "${WORK}/blame/configs/defaults/global_config.yaml"
    commit "${WORK}/blame" "headlamp, away from 0002's context"
    replace "${WORK}/blame/roles/istio/tasks/install.yaml" \
        $'        values_files: "{{ [istio_helm_default_values_file] }}"\n        dependency_update: true' \
        $'        values_files: "{{ [istio_helm_default_values_file] + istio_extra_values_files }}"\n        dependency_update: true'
    commit "${WORK}/blame" "istio: extra values files (#124)"
    replace "${WORK}/blame/configs/defaults/global_config.yaml" $'headlamp_enabled: false\n' $'headlamp_enabled: true\n'
    commit "${WORK}/blame" "headlamp on"
    got=$(brief "${WORK}/blame" best-effort 1)
    check "hint off the failing file: 0002 STOP" "0002=STOP" "${got%% *}"
    has "hint off the failing file: names the commit that changed the failing file" \
        "$(git -C "${WORK}/blame" rev-parse --short=7 HEAD~1) istio: extra values files (#124)" "$(cat "${WORK}/d.txt")"
    check "hint off the failing file: does not name #61" 0 "$(grep -c "pull/61" "${WORK}/d.txt")"

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
    rm -f "${WORK}/d.json"
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
    python3 - "${SPEC}" "${WORK}/bad.json" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
s["rows"][4]["fixed_probe"][0]["absent"] = "(unclosed"
json.dump(s, open(sys.argv[2], "w"))
EOF
    python3 "${OVERLAY}" --spec "${WORK}/bad.json" --eas "${WORK}/main" --policy best-effort --nodes 1 > "${WORK}/a.txt" 2>&1
    check "an absent regex that does not compile: exit 2" 2 "$?"
    has "the bad absent regex is named" "'(unclosed'" "$(cat "${WORK}/a.txt")"

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

section_config() {
    local EAS="${ROOT}/integrations/eas" FIXTURES="${ROOT}/tests/fixtures/eas/config"
    local case dir name args out rc f got want shape
    # The generator on the standard library only (-S: no site-packages).
    gen() { python3 -S "${EAS}/eas-config.py" "$@"; }

    for dir in "${FIXTURES}"/*/; do
        case=$(basename "${dir}")
        out="${WORK}/out/${case}"
        read -r -a args < "${dir}/args"
        gen --facts "${dir}/facts.json" --out "${out}" "${args[@]}" \
            > "${WORK}/${case}.stdout" 2> "${WORK}/${case}.stderr" && rc=0 || rc=$?
        if [ -f "${dir}/expected/refusal.txt" ]; then
            if [ "${rc}" -eq 3 ] && cmp -s "${dir}/expected/refusal.txt" "${WORK}/${case}.stderr"; then
                ok "${case}: refused, with the expected reasons"
            else
                fail "${case}: want exit 3 and expected/refusal.txt on stderr (rc=${rc})"
                diff "${dir}/expected/refusal.txt" "${WORK}/${case}.stderr" | sed 's/^/  | /'
            fi
            if [ -z "$(find "${out}" -type f 2>/dev/null)" ]; then
                ok "${case}: a refusal writes no file"
            else
                fail "${case}: a refusal wrote $(find "${out}" -type f | tr '\n' ' ')"
            fi
            continue
        fi
        if [ "${rc}" -ne 0 ]; then
            fail "${case}: exit ${rc}: $(cat "${WORK}/${case}.stderr")"
            continue
        fi
        got=$(cd "${out}" 2>/dev/null && find . -type f | sort | tr '\n' ' ')
        want=$(cd "${dir}/expected" && find . -type f | sort | tr '\n' ' ')
        if [ "${got}" = "${want}" ]; then ok "${case}: writes exactly ${want}"; else fail "${case}: wrote ${got}, want ${want}"; fi
        for f in "${dir}"/expected/*; do
            name=$(basename "${f}")
            if cmp -s "${f}" "${out}/${name}"; then
                ok "${case}: ${name}"
            else
                fail "${case}: ${name} differs from the golden"
                diff "${f}" "${out}/${name}" | sed 's/^/  | /'
            fi
        done
        if cmp -s "${dir}/expected/choices.txt" "${WORK}/${case}.stdout"; then
            ok "${case}: stdout is choices.txt"
        else
            fail "${case}: stdout is not choices.txt"
            diff "${dir}/expected/choices.txt" "${WORK}/${case}.stdout" | sed 's/^/  | /'
        fi
        # Shape: overrides flat (scalars, or lists of strings), booleans typed; the
        # edits and the manifest rewrite in their declared form; every rev a full SHA.
        if shape=$(python3 - "${out}" <<'PY' 2>&1
import glob, os, re, sys
import yaml
out = sys.argv[1]
ov = yaml.safe_load(open(os.path.join(out, "k3d-gpu-overrides.yaml")))
assert isinstance(ov, dict) and ov, "overrides: not a mapping"
for k, v in ov.items():
    assert isinstance(v, (str, bool)) or (isinstance(v, list) and all(isinstance(x, str) for x in v)), f"overrides {k}: {v!r}"
    if k.endswith("_enabled"):
        assert isinstance(v, bool), f"overrides {k} is not a boolean: {v!r}"
gc = yaml.safe_load(open(os.path.join(out, "global_config.edit.yaml")))
assert gc == {"existing_kubernetes": "/kube/config"}, f"global_config edit: {gc!r}"
me = yaml.safe_load(open(os.path.join(out, "models.edit.yaml")))
assert set(me) == {"defaults", "models"} and isinstance(me["defaults"]["require_amx"], bool), f"models edit: {me!r}"
for m in me["models"]:
    assert isinstance(m["name"], str) and all(isinstance(v, str) for v in m["env"].values()), f"models edit entry: {m!r}"
    assert set(m) <= {"name", "env", "args_append"}, f"models edit entry: {m!r}"
    # fit's KV cache holds a request only up to fit's context: a lowered KV cache always
    # comes with that --max-model-len, or vLLM takes the model's own context and stops.
    if "VLLM_CPU_KVCACHE_SPACE" in m["env"]:
        a = m.get("args_append") or []
        assert len(a) == 2 and a[0] == "--max-model-len" and a[1].isdigit(), f"KV cache without --max-model-len: {m!r}"
(rf,) = glob.glob(os.path.join(out, "repos.*.yaml"))
rp = yaml.safe_load(open(rf))
for r in rp["repos"]:
    assert set(r) == {"layer", "url", "rev"} and re.fullmatch(r"[0-9a-f]{40}", r["rev"]), f"repos row: {r!r}"
    assert r["url"].startswith("https://github.com/intel/"), f"repos url: {r!r}"
PY
); then
            ok "${case}: outputs parse as YAML of the declared shape"
        else
            fail "${case}: output shape: ${shape}"
        fi
    done

    # A floating ref never reaches the installer: only a profile name or a full SHA.
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/float" \
        --layer agent-toolkit --ei-ref main > /dev/null 2> "${WORK}/float.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 2 ] && grep -q 'not a commit SHA' "${WORK}/float.err" && [ ! -e "${WORK}/float" ]; then
        ok "a floating --ei-ref is refused before anything is written"
    else
        fail "a floating --ei-ref: rc=${rc}: $(cat "${WORK}/float.err")"
    fi
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/badprofile" \
        --eas-ref v9.9.9 > /dev/null 2> "${WORK}/badprofile.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 2 ] && grep -q 'not a commit SHA' "${WORK}/badprofile.err"; then
        ok "an --eas-ref that is neither a profile nor a SHA is refused"
    else
        fail "an unknown --eas-ref: rc=${rc}: $(cat "${WORK}/badprofile.err")"
    fi
    # A full SHA is accepted as is and named in the manifest rewrite.
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/sha" --layer agent-toolkit \
        --ei-ref 0123456789abcdef0123456789abcdef01234567 > "${WORK}/sha.out" 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] && grep -q 'rev: "0123456789abcdef0123456789abcdef01234567"' "${WORK}/sha/repos.agent-toolkit.yaml" \
       && grep -q '^--ei-ref 0123456789abcdef0123456789abcdef01234567 -> repos.agent-toolkit.yaml inference rev' "${WORK}/sha.out"; then
        ok "--ei-ref <sha> sets the inference rev and says so"
    else
        fail "--ei-ref <sha> (rc=${rc}): $(cat "${WORK}/sha.out")"
    fi
    # A refusal into a directory a previous run wrote leaves none of that run's files, and
    # a run for another layer leaves no stale manifest rewrite behind.
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/reuse" --layer agent-toolkit > /dev/null 2>&1
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/reuse" --layer inference > /dev/null 2>&1
    got=$(cd "${WORK}/reuse" && find . -type f | sort | tr '\n' ' ')
    if [ "${got}" = "./choices.txt ./global_config.edit.yaml ./k3d-gpu-overrides.yaml ./models.edit.yaml ./repos.inference.yaml " ]; then
        ok "a second run for another layer replaces the first run's files"
    else
        fail "a second run for another layer left: ${got}"
    fi
    gen --facts "${FIXTURES}/servicelb-disabled/facts.json" --out "${WORK}/reuse" --layer agent-toolkit > /dev/null 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 3 ] && [ -z "$(find "${WORK}/reuse" -type f)" ]; then
        ok "a refusal removes the files a previous run left in --out"
    else
        fail "a refusal after a run (rc=${rc}) left: $(find "${WORK}/reuse" -type f | tr '\n' ' ')"
    fi
    # A full EAS SHA is a custom profile: EI and EAT stay on the default profile's pins.
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/eassha" --layer agent-toolkit \
        --eas-ref 89abcdef0123456789abcdef0123456789abcdef > "${WORK}/eassha.out" 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] && grep -q '^# k3d-gpu eas-config: profile custom, layer agent-toolkit' "${WORK}/eassha.out" \
       && grep -q '^# eas intel/enterprise-ai-solutions 89abcdef0123456789abcdef0123456789abcdef$' "${WORK}/eassha.out" \
       && grep -q '^pin ei main (profile main) -> ' "${WORK}/eassha.out"; then
        ok "--eas-ref <sha> is a custom profile with the default EI and EAT pins"
    else
        fail "--eas-ref <sha> (rc=${rc}): $(cat "${WORK}/eassha.out")"
    fi
    # A k3s.io/node-args annotation that is not a JSON list is an input error.
    python3 - "${FIXTURES}/desktop-i9-13900T/facts.json" "${WORK}/badargs.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1]))
f["nodes"][0]["annotations"]["k3s.io/node-args"] = "--disable servicelb"
json.dump(f, open(sys.argv[2], "w"))
PY
    gen --facts "${WORK}/badargs.json" --out "${WORK}/badargs" > /dev/null 2> "${WORK}/badargs.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 2 ] && grep -q 'k3s.io/node-args is not JSON' "${WORK}/badargs.err" && [ ! -e "${WORK}/badargs" ]; then
        ok "a k3s.io/node-args that is not JSON exits 2 and writes nothing"
    else
        fail "malformed k3s.io/node-args: rc=${rc}: $(cat "${WORK}/badargs.err")"
    fi
    # fit sizes the KV cache for its own --max-model-len; a lowered KV cache carries that
    # context (16384 here, not a constant), and a fit document without one is malformed.
    python3 - "${FIXTURES}/desktop-i9-13900T/facts.json" "${WORK}/ctx16k.json" "${WORK}/noctx.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1]))
doc = f["fit"]["Qwen/Qwen2.5-Coder-7B-Instruct"]
doc["flags"] = ["--max-model-len=16384", "--max-num-seqs=1", "VLLM_CPU_KVCACHE_SPACE=1"]
json.dump(f, open(sys.argv[2], "w"))
doc["flags"] = ["--max-num-seqs=1", "VLLM_CPU_KVCACHE_SPACE=1"]
json.dump(f, open(sys.argv[3], "w"))
PY
    gen --facts "${WORK}/ctx16k.json" --out "${WORK}/ctx16k" --layer agent-toolkit > /dev/null 2> "${WORK}/ctx16k.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] && shape=$(python3 - "${WORK}/ctx16k/models.edit.yaml" <<'PY' 2>&1
import sys, yaml
m = yaml.safe_load(open(sys.argv[1]))["models"]
assert m == [{"name": "qwen2-5-coder-7b", "env": {"VLLM_CPU_KVCACHE_SPACE": "1"},
              "args_append": ["--max-model-len", "16384"]}], m
PY
    ); then
        ok "a lowered KV cache carries fit's own --max-model-len"
    else
        fail "fit at --max-model-len=16384 (rc=${rc}): ${shape:-} $(cat "${WORK}/ctx16k.err")"
    fi
    gen --facts "${WORK}/noctx.json" --out "${WORK}/noctx" --layer agent-toolkit > /dev/null 2> "${WORK}/noctx.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 2 ] && grep -q 'no --max-model-len' "${WORK}/noctx.err" && [ ! -e "${WORK}/noctx" ]; then
        ok "a fit KV cache without --max-model-len exits 2 and writes nothing"
    else
        fail "a fit KV cache without --max-model-len: rc=${rc}: $(cat "${WORK}/noctx.err")"
    fi
    # EI's NFD role skips only a Helm release named node-feature-discovery. NFD installed
    # the way NFD's own docs name it (release nfd) is not that, so EI would add a second.
    python3 - "${FIXTURES}/desktop-i9-13900T/facts.json" "${WORK}/nfdhelm.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1]))
(d,) = [d for d in f["daemonsets"] if d["namespace"] == "node-feature-discovery"]
d["annotations"] = {"meta.helm.sh/release-name": "nfd", "meta.helm.sh/release-namespace": "node-feature-discovery"}
json.dump(f, open(sys.argv[2], "w"))
PY
    gen --facts "${WORK}/nfdhelm.json" --out "${WORK}/nfdhelm" --layer inference > "${WORK}/nfdhelm.out" 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] && grep -qx 'nfd_enabled: false' "${WORK}/nfdhelm/k3d-gpu-overrides.yaml" \
       && grep -q '^DaemonSet node-feature-discovery/nfd-worker from Helm release nfd -> nfd_enabled: false' "${WORK}/nfdhelm.out"; then
        ok "NFD from a Helm release other than node-feature-discovery: nfd_enabled false"
    else
        fail "NFD from Helm release nfd (rc=${rc}): $(grep -i nfd "${WORK}/nfdhelm.out")"
    fi

    # Facts of another schema are an input error, not a refusal.
    printf '{"schema": "k3d-gpu.eas-facts/v0", "cluster": "x", "nodes": []}\n' > "${WORK}/old.json"
    gen --facts "${WORK}/old.json" --out "${WORK}/old" > /dev/null 2> "${WORK}/old.err" && rc=0 || rc=$?
    if [ "${rc}" -eq 2 ] && grep -q 'k3d-gpu.eas-facts/v1' "${WORK}/old.err"; then
        ok "facts of another schema exit 2 and name the expected schema"
    else
        fail "facts of another schema: rc=${rc}: $(cat "${WORK}/old.err")"
    fi

    # --explain: the same choices, each source as a full permalink.
    gen --facts "${FIXTURES}/desktop-i9-13900T/facts.json" --out "${WORK}/explain" --layer agent-toolkit --explain \
        > "${WORK}/explain.out" 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] \
       && grep -qF '[https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1342-L1358]' "${WORK}/explain.out" \
       && [ "$(grep -c '^[^#].* -> ' "${WORK}/explain.out")" -eq "$(grep -c '^[^#].* -> ' "${FIXTURES}/desktop-i9-13900T/expected/choices.txt")" ] \
       && ! grep -E '^[^#].* -> ' "${WORK}/explain.out" | grep -qvE '\[https://[^] ]+\]$' \
       && cmp -s "${FIXTURES}/desktop-i9-13900T/expected/choices.txt" "${WORK}/explain/choices.txt"; then
        ok "--explain prints every source as a permalink; choices.txt keeps the short form"
    else
        fail "--explain (rc=${rc}): $(head -20 "${WORK}/explain.out")"
    fi

    # Every source the generator can cite is a GitHub permalink at a full commit SHA.
    if gen --list-sources > "${WORK}/sources" 2>&1 && [ -s "${WORK}/sources" ] \
       && ! grep -vE '^[a-z0-9_]+ https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(blob|tree)/[0-9a-f]{40}/[^ #]+(#L[0-9]+(-L[0-9]+)?)?$' "${WORK}/sources"; then
        ok "--list-sources: $(wc -l < "${WORK}/sources") sources, each a permalink at a full SHA"
    else
        fail "--list-sources: $(grep -vE '^[a-z0-9_]+ https://github\.com/[^ ]+/(blob|tree)/[0-9a-f]{40}/' "${WORK}/sources" | head -5)"
    fi

    # pins.json: k3d-gpu.eas-pins/v1, flat rows; each profile carries eas, ei and eat.
    if shape=$(python3 - "${EAS}/pins.json" <<'PY' 2>&1
import json, re, sys
d = json.load(open(sys.argv[1]))
assert d["schema"] == "k3d-gpu.eas-pins/v1", d.get("schema")
assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["checked"]), d.get("checked")
rows = d["pins"]
assert rows, "no pins"
for r in rows:
    assert set(r) == {"profile", "name", "repo", "ref", "sha", "date", "source"}, r
    assert all(isinstance(v, str) and v for v in r.values()), r
    assert re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", r["sha"]), r
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]), r
    assert r["source"].startswith("https://"), r
keys = [(r["profile"], r["name"]) for r in rows]
assert len(keys) == len(set(keys)), "duplicate (profile, name)"
profiles = {r["profile"] for r in rows if r["name"] in ("eas", "ei", "eat")}
assert d["default_profile"] in profiles, d["default_profile"]
for p in profiles:
    assert {r["name"] for r in rows if r["profile"] == p} >= {"eas", "ei", "eat"}, p
    assert all(re.fullmatch(r"[0-9a-f]{40}", r["sha"]) for r in rows if r["profile"] == p), p
print(f"{len(rows)} rows, profiles {sorted(profiles)}")
PY
); then
        ok "pins.json: ${shape}"
    else
        fail "pins.json: ${shape}"
    fi
}

SECTIONS=(overlay config)
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

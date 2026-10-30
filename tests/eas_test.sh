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
#            - the same decisions with TMPDIR (the scratch index) on another
#              filesystem than the checkout
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
#   cli      `k3d-gpu eas up|down|status|check`, eas.sh (the in-bastion driver) and the
#            bastion, against stub docker, kubectl, k3d, git, yq and `k3d-gpu fit`, with the
#            real eas-config.py (so the facts it gathers are the facts the generator reads)
#            and a recording overlay.py:
#            - up --dry-run prints the choices and the overlay decisions and makes no
#              docker or kubectl change
#            - up creates the cluster with `k3d-gpu up` when it is absent
#            - the bastion run: host network, root with the state handed back to this
#              uid:gid, state dir rw, kubeconfig ro, secrets by name only
#            - eas.sh order: clone at the SHA, overlay, manifest pins, init, the edits,
#              install with the overrides last; never configure
#            - refs: a profile and a SHA go as given, any other ref through git ls-remote
#            - no secret value in an argument list, a log, choices.txt or a state file
#            - refusals stop before any change
#            - down clears AIGatewayRoute finalizers before teardown platform --force
#            - status prints the layer, pins, overlay, EAS status, gateway, pods not Ready
#            - sandbox up refuses when EAS's sandbox-router-deployment exists
#            - the bastion pins what pins.json's bastion profile names
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
    # The scratch index can sit on another filesystem than the checkout: a tmpfs /tmp
    # with the state dir on disk, or the bastion's own /tmp with the state dir bind-mounted.
    local xdev="" d
    for d in /dev/shm /var/tmp; do
        [ -d "${d}" ] && [ -w "${d}" ] || continue
        [ "$(stat -c %d "${d}")" != "$(stat -c %d "${WORK}")" ] || continue
        xdev=$(mktemp -d "${d}/eas-test-xdev.XXXXXX") && break
    done
    if [ -n "${xdev}" ]; then
        got=$(TMPDIR="${xdev}" brief "${WORK}/main" best-effort 1)
        check "main with the scratch index on another filesystem (TMPDIR ${d}): the same decisions" \
            "0002=apply:a 0003=skip 0004=skip 0005=skip 0006=apply:a" "${got}"
        check "main with the scratch index on another filesystem: exit 0" 0 "$(cat "${WORK}/rc")"
        rm -rf "${xdev}"
    else
        echo "skip no writable /dev/shm or /var/tmp on another filesystem than ${WORK}: the cross-filesystem case did not run"
    fi

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

section_cli() {
    local B="${WORK}/cli/bin" K="${WORK}/cli/k" S="${WORK}/cli/s" LOG="${WORK}/cli/log"
    local EAS_DIR="${ROOT}/integrations/eas" LAUNCHER="${ROOT}/scripts/k3d-gpu"
    local SECRET="hf_SECRET_7f3a9c" EAS_SHA="97f6157d6a72f7173c698c474d4e51d2c79f1fd6"
    local EI_SHA="3e8a24486b6ee812adf2bf452f8ba5751c16c8bd" EAT_SHA="036b66234801e9bd950afd90830821859780d668"
    local STATE="${S}/k3d-gpu-state/eas-easc"
    mkdir -p "${B}" "${K}/eas" "${K}/eat" "${S}/fx" "${S}/home" "${S}/host/proc/sys/fs/inotify"

    # Helpers named apart from the other sections' (has there takes the needle first).
    cli_eq()    { if [ "$2" = "$3" ]; then ok "$1"; else fail "$1: got '$2', want '$3'"; fi; }
    cli_has()   { if grep -Fq -- "$3" <<< "$2"; then ok "$1"; else fail "$1: missing '$3' in: $(head -c 700 <<< "$2")"; fi; }
    cli_lacks() { if grep -Fq -- "$3" <<< "$2"; then fail "$1: unexpected '$3'"; else ok "$1"; fi; }
    # cli_before <name> <haystack> <first> <second>: <first> occurs, and before <second>.
    cli_before() {
        local a b
        a=$(grep -nF -- "$3" <<< "$2" | head -1 | cut -d: -f1)
        b=$(grep -nF -- "$4" <<< "$2" | head -1 | cut -d: -f1)
        if [ -n "${a}" ] && [ -n "${b}" ] && [ "${a}" -lt "${b}" ]; then ok "$1"; else fail "$1: '$3' (line ${a:-none}) not before '$4' (line ${b:-none})"; fi
    }

    # The integration dir: the real files, eas-config.py included (the facts this section
    # gathers must be what it reads), with overlay.py replaced by a recorder (the overlay
    # section tests it on real EAS trees).
    cp "${EAS_DIR}"/eas.sh "${EAS_DIR}"/eas-config.py "${EAS_DIR}"/pins.json "${EAS_DIR}"/overlay.json \
       "${EAS_DIR}"/bastion.Dockerfile "${EAS_DIR}"/requirements.lock "${EAS_DIR}"/images.yaml "${K}/eas/"
    cat > "${K}/eas/overlay.py" <<'EOF'
import os, sys
a = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("overlay %s\n" % " ".join(a))
if os.environ.get("STUB_OVERLAY_STOP"):
    print("STOP: the overlay does not fit eas@97f6157; regenerate the rows above that say STOP. Nothing was applied.", file=sys.stderr)
    sys.exit(3)
print("# overlay at eas@97f6157 (HEAD), kubernetes_cpu_policy=%s, nodes=%s" % (a[a.index("--policy") + 1], a[a.index("--nodes") + 1]))
print("0002 istio global.platform -> apply [stub]\n0006 envoy control-plane affinity -> apply (variant a) [stub]")
EOF
    cat > "${K}/eat/eat-check.sh" <<'EOF'
echo "eat-check mode=${EAT_MODE} kubeconfig=${EAT_KUBECONFIG} domain=${EAT_EAS_DOMAIN} model=${EAT_MODEL}" >> "${STUB_LOG}"
echo "EAT check passed (${EAT_MODE})."
EOF

    # ---- stubs: every call is one line in ${STUB_LOG} -------------------------------------
    cat > "${B}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${STUB_LOG}"
case "$*" in
    "info "*) echo '["name=seccomp,profile=builtin","name=cgroupns"]' ;;
    "exec k3d-easc-server-0 test -S /var/run/nri/nri.sock") exit 0 ;;
    "exec k3d-easc-server-0 crictl info") echo '{"config": {"cni": {"binDirs": ["/usr/bin"], "confDir": "/var/lib/rancher/k3s/agent/etc/cni/net.d"}}}' ;;
    "image inspect "*) [ -e "${STUB_STATE}/image-present" ] ;;
    "build "*) touch "${STUB_STATE}/image-present" ;;
    "run "*) echo "bastion: ran" ;;
    *) echo "docker stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
    cat > "${B}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${STUB_LOG}"
case "$*" in
    "cluster get "*) [ -e "${STUB_STATE}/cluster" ] ;;
    "kubeconfig get "*) cat "${STUB_STATE}/kubeconfig-src" ;;
esac
EOF
    cat > "${B}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB_LOG}"
F="${STUB_STATE}/fx"
case "$*" in
    *"config view --minify --flatten"*) echo "apiVersion: v1 # minified for ${STUB_CLUSTER}" ;;
    *"get nodes -o json"*) cat "${F}/nodes.json" ;;
    *"get nodes"*) echo "node/k3d-easc-server-0" ;;
    *"get daemonsets -A -o json"*) cat "${F}/daemonsets.json" ;;
    *"get namespaces -o json"*) cat "${F}/namespaces.json" ;;
    *"get crd sandboxes.agents.x-k8s.io -o name"*) echo 'Error from server (NotFound)' >&2; exit 1 ;;
    *"version -o json"*) echo '{"serverVersion": {"gitVersion": "v1.37.1+k3s1"}}' ;;
    *"get aigatewayroutes.aigateway.envoyproxy.io -A"*) printf 'llm-inference aigw-qwen2-5-coder-7b\nllm-inference aigw-qwen3guard\n' ;;
    *"-n agent-sandbox-system get deployment sandbox-router-deployment -o name"*)
        [ -n "${STUB_EAS_SANDBOX:-}" ] || { echo 'Error from server (NotFound)' >&2; exit 1; }
        echo deployment.apps/sandbox-router-deployment ;;
    *"-n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway"*) echo 172.18.0.2 ;;
    *"get pods -A -o json"*) cat "${F}/pods.json" ;;
    *" patch "*|*" apply "*|*" delete "*) ;;
    *) echo "kubectl stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
    cat > "${B}/git" <<'EOF'
#!/usr/bin/env bash
echo "git $*" >> "${STUB_LOG}"
if [ "$1" = ls-remote ]; then
    case "$4" in
        release-1.1) printf '170eb3abb1956de0787727f21e356aef2b13ff24\trefs/heads/release-1.1\n' ;;
        v9.9.9)      printf '1111111111111111111111111111111111111111\trefs/tags/v9.9.9\n2222222222222222222222222222222222222222\trefs/tags/v9.9.9^{}\n' ;;
    esac
    exit 0
fi
if [ "$1" = clone ]; then
    # A checkout whose installer records its argv; init seeds env/<env> as the real one does.
    d=$4
    mkdir -p "${d}/.git" "${d}/configs/repos"
    : > "${d}/configs/repos/repos.agent-toolkit.yaml"
    : > "${d}/configs/repos/repos.inference.yaml"
    cat > "${d}/es_auto_installer.sh" <<'EOS'
#!/usr/bin/env bash
echo "installer $*" >> "${STUB_LOG}"
if [ "$1" = init ]; then
    mkdir -p env/k3d-gpu && : > env/k3d-gpu/global_config.yaml && : > env/k3d-gpu/models.yaml && : > env/k3d-gpu/.solutions.yaml
fi
EOS
    chmod +x "${d}/es_auto_installer.sh"
fi
exit 0
EOF
    # yq: records its argv and the strenv() values; answers the row listings eas.sh reads
    # from eas-config.py's files (one `key: "value"` per line, as it writes them).
    cat > "${B}/yq" <<'EOF'
#!/usr/bin/env python3
import os, re, sys
a = sys.argv[1:]
env = " ".join("%s=%s" % (k, os.environ[k]) for k in ("L", "U", "R", "F", "N", "A") if k in os.environ and "strenv(%s)" % k in " ".join(a))
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("yq %s%s\n" % (" ".join(a), " [" + env + "]" if env else ""))
if "-o=json" in a:
    text = open(a[-1]).read()
    if ".args_append" in a[-2]:
        m = re.search(r"args_append: (\[.*\])", text)
        print(m.group(1) if m else "[]")
    else:   # the catalog entry's args: one the edit replaces, one it keeps
        print('["--block-size","128","--max-model-len","131072","--tensor-parallel-size","1"]')
elif "-r" in a:
    text = open(a[-1]).read()
    if "@tsv" in a[a.index("-r") + 1]:
        for m in re.finditer(r'- layer: "([^"]*)"\n\s+url: "([^"]*)"\n\s+rev: "([^"]*)"', text):
            print("\t".join(m.groups()))
    elif ".models[].name" in a:
        for m in re.finditer(r'^  - name: "([^"]*)"', text, re.M):
            print(m.group(1))
EOF
    cat > "${B}/self" <<'EOF'
#!/usr/bin/env bash
echo "self $*" >> "${STUB_LOG}"
case "$1" in
    up) touch "${STUB_STATE}/cluster" ;;
    fit)
        case "$3" in
            Qwen/Qwen2.5-Coder-14B-Instruct) echo '{"schema": "k3d-gpu.fit/v1", "fits": false, "summary": "fit: does not fit this host", "flags": []}'; exit 3 ;;
            meta-llama/Llama-3.1-8B-Instruct) exit 2 ;;
            *) printf '{"schema": "k3d-gpu.fit/v1", "fits": true, "summary": "fit: %s on CPU via vllm", "flags": ["--max-model-len=32768", "--max-num-seqs=4", "VLLM_CPU_KVCACHE_SPACE=12"]}\n' "$3" ;;
        esac ;;
esac
EOF
    chmod +x "${B}"/*

    # ---- fixtures: a k3d-gpu cluster on a 32-CPU, 125 GiB hybrid desktop --------------------
    cat > "${S}/fx/nodes.json" <<'EOF'
{"items": [{"metadata": {"name": "k3d-easc-server-0",
  "labels": {"feature.node.kubernetes.io/cpu-cpuid.AVXVNNI": "true", "k3d-gpu.io/hybrid-cpu": "true",
             "node-role.kubernetes.io/control-plane": "true"},
  "annotations": {"k3s.io/node-args": "[\"server\", \"--disable\", \"traefik\", \"--tls-san\", \"0.0.0.0\"]"}},
  "status": {"allocatable": {"cpu": "32", "memory": "131419028Ki", "gpu.intel.com/xe": "1", "pods": "110"}}}]}
EOF
    cat > "${S}/fx/daemonsets.json" <<'EOF'
{"items": [
 {"metadata": {"namespace": "kube-system", "name": "nri-resource-policy-balloons",
   "labels": {"app.kubernetes.io/managed-by": "Helm"},
   "annotations": {"meta.helm.sh/release-name": "nri-resource-policy-balloons",
                   "objectset.rio.cattle.io/owner-gvk": "k3s.cattle.io/v1, Kind=Addon", "objectset.rio.cattle.io/owner-name": "nri-balloons"}}},
 {"metadata": {"namespace": "node-feature-discovery", "name": "nfd-worker", "labels": {"app": "nfd"},
   "annotations": {"objectset.rio.cattle.io/owner-gvk": "k3s.cattle.io/v1, Kind=Addon", "objectset.rio.cattle.io/owner-name": "node-feature-discovery"}}}]}
EOF
    echo '{"items": [{"metadata": {"name": "default"}}, {"metadata": {"name": "kube-system"}}, {"metadata": {"name": "node-feature-discovery"}}]}' > "${S}/fx/namespaces.json"
    cp "${S}/fx/namespaces.json" "${S}/fx/namespaces.ok"
    cat > "${S}/fx/pods.json" <<'EOF'
{"items": [
 {"metadata": {"namespace": "litellm", "name": "litellm-0"}, "status": {"phase": "Running", "containerStatuses": [{"ready": true}]}},
 {"metadata": {"namespace": "llm-inference", "name": "vllm-qwen-0"}, "status": {"phase": "Pending", "containerStatuses": [{"ready": false, "state": {"waiting": {"reason": "ContainerCreating"}}}]}},
 {"metadata": {"namespace": "kube-system", "name": "helm-install-x"}, "status": {"phase": "Succeeded"}}]}
EOF
    echo "apiVersion: v1 # easc" > "${S}/kubeconfig-src"
    mkdir -p "${S}/k3d-gpu-state"
    cp "${S}/kubeconfig-src" "${S}/k3d-gpu-state/kubeconfig-easc"
    touch "${S}/cluster"
    printf 'MemTotal: 131500000 kB\nMemAvailable: 120000000 kB\n' > "${S}/host/proc/meminfo"
    echo 128 > "${S}/host/proc/sys/fs/inotify/max_user_instances"
    echo 1048576 > "${S}/host/proc/sys/fs/inotify/max_user_watches"

    # cli <fn> [args...]: a launcher function in a subshell, the stubs first on PATH, the
    # launcher's own `up` and `fit` replaced by the self stub.
    cli() {
        (export PATH="${B}:${PATH}" STUB_LOG="${LOG}" STUB_STATE="${S}" STUB_CLUSTER=easc HOME="${S}/home" \
            K3D_GPU_STATE_DIR="${S}/k3d-gpu-state" K3D_GPU_CLUSTER=easc K3D_GPU_EAS_DIR="${K}/eas" \
            K3D_GPU_SHARE="${ROOT}/share"
         unset KUBECONFIG K3D_GPU_KUBECONFIG K3D_GPU_CONTEXT
         # shellcheck disable=SC1090
         source <(sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}")
         # shellcheck disable=SC2034
         SYSROOT="${S}/host"
         qs_self() { echo "${B}/self"; }
         "$@") 2>&1
    }
    local out rc log

    # ---- up --dry-run -------------------------------------------------------------------------
    : > "${LOG}"
    out=$(cli cmd_eas up --agent-toolkit --dry-run); rc=$?
    log=$(cat "${LOG}")
    cli_eq    "cli: dry run exits 0" "${rc}" 0
    cli_has   "cli: dry run prints eas-config's choices" "${out}" "ServiceLB on (no --disable servicelb in k3s.io/node-args) -> metallb_enabled: false"
    cli_has   "cli: the facts carry the baked balloons policy to eas-config" "${out}" "-> kubernetes_cpu_policy: best-effort (one NRI policy per node)"
    cli_has   "cli: the facts carry the node's CNI bin_dirs to eas-config" "${out}" 'containerd CNI bin_dirs ["/usr/bin"] -> istio_platform: k3d'
    cli_has   "cli: the facts carry the fit documents to eas-config" "${out}" "qwen2-5-coder-7b needs 99 GiB and k3d-gpu fit says it fits -> llm_services_autodeploy_models: [qwen2-5-coder-7b]"
    cli_has   "cli: the inotify warning reaches the user" "${out}" "WARN: fs.inotify.max_user_instances 128 < 8192"
    cli_has   "cli: dry run prints the overlay decisions" "${out}" "0006 envoy control-plane affinity -> apply (variant a)"
    cli_has   "cli: the overlay gets eas-config's cpu policy and the node count" "${log}" \
        "overlay --eas ${STATE}/eas --policy best-effort --nodes 1 --json ${STATE}/overlay-decisions.json"
    cli_lacks "cli: dry run: the overlay is not applied" "${log}" "--apply"
    cli_has   "cli: dry run says nothing was applied" "${out}" "Dry run: nothing applied"
    if grep -E '^docker ' <<< "${log}" | grep -Ev '^docker (info --format|exec k3d-easc-server-0 (test -S /var/run/nri/nri.sock|crictl info)$)' | grep -q .; then
        fail "cli: dry run: a docker call that is not a read: $(grep -E '^docker ' <<< "${log}" | grep -Ev '^docker (info|exec)' | head -1)"
    else
        ok "cli: dry run: every docker call is a read (info, exec test, exec crictl info)"
    fi
    if grep -E '^kubectl ' <<< "${log}" | grep -Eqv ' (get|version|config view) '; then
        fail "cli: dry run: a kubectl call that is not a read: $(grep -E '^kubectl ' <<< "${log}" | grep -Ev ' (get|version|config view) ' | head -1)"
    else
        ok "cli: dry run: every kubectl call is a read"
    fi
    cli_lacks "cli: dry run: no installer call" "${log}" "installer "
    cli_eq    "cli: state dir is 0700" "$(stat -c %a "${STATE}")" 700
    cli_eq    "cli: the bastion's kubeconfig is 0600" "$(stat -c %a "${STATE}/kubeconfig")" 600
    cli_has   "cli: the bastion's kubeconfig is this cluster's context alone" "${log}" "kubectl --context k3d-easc config view --minify --flatten"
    cli_has   "cli: eas-config writes its five files" "$(ls "${STATE}")" "repos.agent-toolkit.yaml"
    local facts
    facts=$(python3 -c 'import json, sys
f = json.load(open(sys.argv[1]))
print(sorted(f), f["nri_socket"], f["containerd_cni_bin_dirs"], f["agent_sandbox"], f["docker_rootless"], sorted(f["host"]),
      sorted(f["nodes"][0]), sorted(f["daemonsets"][0]), f["namespaces"], f["fit"]["meta-llama/Llama-3.1-8B-Instruct"],
      f["fit"]["Qwen/Qwen2.5-Coder-14B-Instruct"]["fits"])' "${STATE}/facts.json" 2>&1)
    cli_eq    "cli: facts.json has the k3d-gpu.eas-facts/v1 shape eas-config.py documents" "${facts}" \
        "['agent_sandbox', 'cluster', 'containerd_cni_bin_dirs', 'daemonsets', 'docker_rootless', 'fit', 'host', 'namespaces', 'nodes', 'nri_socket', 'schema', 'server_version'] True ['/usr/bin'] None False ['inotify_max_user_instances', 'inotify_max_user_watches', 'mem_available_kib'] ['allocatable', 'annotations', 'labels', 'name'] ['annotations', 'labels', 'name', 'namespace'] ['default', 'kube-system', 'node-feature-discovery'] None False"
    cli_has   "cli: fit runs per eas-config candidate, vLLM on the CPU" "${log}" "self fit --model Qwen/Qwen2.5-Coder-7B-Instruct --engine vllm --device cpu --json"
    cli_has   "cli: pins.resolved.json records eas-config's commits" "$(cat "${STATE}/pins.resolved.json")" "\"sha\": \"${EAS_SHA}\""

    # ---- refs -----------------------------------------------------------------------------------
    : > "${LOG}"
    out=$(cli cmd_eas up --dry-run --eas-ref v1.2.0-rc.1); rc=$?
    cli_eq    "cli: --eas-ref v1.2.0-rc.1 (a profile) runs" "${rc}" 0
    cli_has   "cli: a profile goes to eas-config as given" "${out}" "# k3d-gpu eas-config: profile v1.2.0-rc.1, layer inference"
    cli_has   "cli: the rc profile's EAS commit drives the overlay" "${out}" "enterprise-ai-solutions@5c27fb3"
    cli_lacks "cli: a profile needs no ls-remote" "$(cat "${LOG}")" "git ls-remote"
    : > "${LOG}"
    out=$(cli cmd_eas up --dry-run --agent-toolkit --ei-ref release-1.1 --eat-ref v9.9.9); rc=$?
    cli_eq    "cli: refs resolve" "${rc}" 0
    cli_has   "cli: a branch resolves through git ls-remote" "$(cat "${LOG}")" "git ls-remote -- https://github.com/intel/enterprise-inference release-1.1 release-1.1^{}"
    cli_has   "cli: the resolved commit is printed with its ref" "${out}" "ei: intel/enterprise-inference@170eb3a (release-1.1, git ls-remote"
    cli_has   "cli: eas-config gets the commit, not the ref" "${out}" "# ei intel/enterprise-inference 170eb3abb1956de0787727f21e356aef2b13ff24"
    cli_has   "cli: an annotated tag resolves to its commit" "$(cat "${STATE}/repos.agent-toolkit.yaml")" 'rev: "2222222222222222222222222222222222222222"'
    : > "${LOG}"
    out=$(cli cmd_eas up --dry-run --eas-ref 0123456789abcdef0123456789abcdef01234567); rc=$?
    cli_has   "cli: a full SHA is taken as given" "${out}" "eas: intel/enterprise-ai-solutions@0123456 (as given)"
    cli_lacks "cli: a full SHA needs no ls-remote" "$(cat "${LOG}")" "git ls-remote"
    out=$(cli cmd_eas up --dry-run --ei-ref no-such-branch); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'has no branch or tag no-such-branch' <<< "${out}"; then ok "cli: an unknown ref stops"; else fail "cli: an unknown ref: rc=${rc} ${out}"; fi

    # ---- refusals ---------------------------------------------------------------------------------
    # cli_refused <name> <message> <cmd_eas args...>: non-zero, the message, and no change.
    cli_refused() {
        local name=$1 msg=$2 o r
        shift 2
        : > "${LOG}"
        o=$(cli cmd_eas "$@"); r=$?
        if [ "${r}" -ne 0 ] && grep -qF -- "${msg}" <<< "${o}" \
           && ! grep -Eq '^docker (run|build)|^kubectl .* (patch|apply|delete|create) |^installer ' "${LOG}"; then
            ok "cli: refused before any change: ${name}"
        else
            fail "cli: refused before any change: ${name} (rc=${r}): $(head -c 600 <<< "${o}") | $(grep -E '^docker (run|build)|^installer' "${LOG}" | head -2)"
        fi
    }
    cli_refused "--layer erag (eas-config's reason)" "chatqna_intel_gpu is EXPERIMENTAL" up --layer erag
    cli_refused "--layer erag reads as a refusal" "refused (above); nothing was changed" up --layer erag
    cli_refused "an unknown layer" "--layer must be inference, agent-toolkit or erag" up --layer platform
    cli_refused "an unknown option" "unknown eas up option: --nope" up --nope
    cli_refused "a ref that is an option" "not a ref or model name: '-x'" up --eas-ref -x
    cli_refused "a ref with shell characters" "not a ref or model name" up --ei-ref 'main;id'
    cli_refused "no subcommand" "usage: k3d-gpu eas up" ""
    cli_refused "down with an option" "eas down takes no options" down --force
    echo '{"items": [{"metadata": {"name": "genai-gateway"}}, {"metadata": {"name": "ingress-nginx"}}]}' > "${S}/fx/namespaces.json"
    cli_refused "EAT 1.x present (eas-config refuses)" "both stacks would own LiteLLM" up --agent-toolkit
    cp "${S}/fx/namespaces.ok" "${S}/fx/namespaces.json"
    STUB_OVERLAY_STOP=1 cli_refused "an overlay STOP" "the overlay stops at this EAS commit" up --dry-run
    rm "${S}/cluster"
    cli_refused "a dry run on an absent cluster" "a dry run reads a running cluster" up --dry-run
    touch "${S}/cluster"
    mv "${K}/eas/overlay.py" "${K}/eas/overlay.py.off"
    cli_refused "an incomplete integration dir" "overlay.py missing" up
    mv "${K}/eas/overlay.py.off" "${K}/eas/overlay.py"

    # ---- up: the cluster, the bastion and its run ------------------------------------------------
    rm -f "${S}/cluster" "${S}/image-present"
    : > "${LOG}"
    out=$(HF_TOKEN="${SECRET}" HTTPS_PROXY=http://proxy.test:3128 cli cmd_eas up --agent-toolkit -- -e '{"llm_services_autodeploy_models": []}'); rc=$?
    log=$(cat "${LOG}")
    cli_eq     "cli: up exits 0" "${rc}" 0
    cli_has    "cli: up creates the absent cluster with 'k3d-gpu up'" "${log}" "self up"
    cli_before "cli: the cluster comes before the facts" "${log}" "self up" "get nodes -o json"
    local tag runline
    tag=$(cat "${EAS_DIR}/bastion.Dockerfile" "${EAS_DIR}/requirements.lock" | sha256sum | cut -c1-16)
    cli_has    "cli: the bastion is built when absent, tagged by Dockerfile + lock" "${log}" \
        "docker build -q -t k3d-gpu-eas-bastion:${tag} -f ${K}/eas/bastion.Dockerfile ${K}/eas"
    runline=$(grep '^docker run' <<< "${log}")
    cli_has    "cli: bastion: host network" "${runline}" "--network host"
    cli_has    "cli: bastion: root (ensure_sudo), the state handed back to this uid:gid" "${runline}" "--user 0:0"
    cli_has    "cli: bastion: EAS_OWNER is this uid:gid" "${runline}" "-e EAS_OWNER=$(id -u):$(id -g)"
    cli_has    "cli: bastion: state dir rw" "${runline}" "-v ${STATE}:/eas-state "
    cli_has    "cli: bastion: kubeconfig ro" "${runline}" "-v ${STATE}/kubeconfig:/kube/config:ro"
    cli_has    "cli: bastion: integrations/ ro, as overlay.py's patch lookup needs" "${runline}" "-v ${K}:/k3d-gpu/integrations:ro"
    cli_has    "cli: bastion: the EAS commit eas-config resolved" "${runline}" "-e EAS_SHA=${EAS_SHA}"
    cli_has    "cli: bastion: eas-config's cpu policy and the node count for the overlay" "${runline}" "-e EAS_CPU_POLICY=best-effort -e EAS_NODES=1"
    cli_has    "cli: bastion: HF_TOKEN by name only" "${runline}" "-e HF_TOKEN "
    cli_has    "cli: bastion: a proxy by name only" "${runline}" "-e HTTPS_PROXY "
    cli_has    "cli: bastion: eas.sh up with the layer and the extra args" "${runline}" \
        "k3d-gpu-eas-bastion:${tag} bash /k3d-gpu/integrations/eas/eas.sh up agent-toolkit -- -e {\"llm_services_autodeploy_models\": []}"
    cli_has    "cli: up prints the gateway with its --resolve line" "${out}" "curl -k --resolve litellm.solutions.ai:443:172.18.0.2 https://litellm.solutions.ai/v1/models"
    local leak
    leak=$(grep -rlF -- "${SECRET}" "${S}" "${LOG}" 2>/dev/null; grep -F -- "${SECRET}" <<< "${out}")
    cli_eq     "cli: no secret value in argv, logs, choices, state files or output" "${leak}" ""
    cli_has    "cli: the image pins go to the state dir" "$(cat "${STATE}/k3d-gpu-images.yaml" 2>/dev/null)" "agent_sandbox_router_image:"
    cli_eq     "cli: the installed layer is recorded" "$(cat "${STATE}/layer" 2>/dev/null)" agent-toolkit
    : > "${LOG}"
    out=$(cli cmd_eas up --agent-toolkit); rc=$?
    cli_lacks  "cli: a present cluster is not recreated" "$(cat "${LOG}")" "self up"
    cli_lacks  "cli: a present bastion is reused, not rebuilt" "$(cat "${LOG}")" "docker build"
    : > "${LOG}"
    out=$(cli cmd_eas up); rc=$?
    cli_eq     "cli: up of the inference layer exits 0" "${rc}" 0
    cli_lacks  "cli: the inference layer has no LiteLLM line" "${out}" "litellm."
    cli_has    "cli: the inference layer prints the gateway" "${out}" "gateway: eg-gateway at 172.18.0.2"
    mkdir -p "${STATE}/eas/env/k3d-gpu"
    out=$(cli cmd_eas check); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'run .k3d-gpu eas up --agent-toolkit. first' <<< "${out}"; then
        ok "cli: check refuses without the agent toolkit (no LiteLLM)"
    else
        fail "cli: check without the agent toolkit: rc=${rc} ${out}"
    fi
    cli cmd_eas up --agent-toolkit >/dev/null

    # ---- eas.sh in the bastion: its steps, in order ------------------------------------------------
    local ES="${WORK}/cli/es"
    mkdir -p "${ES}"
    command cp -f "${STATE}"/k3d-gpu-overrides.yaml "${STATE}"/global_config.edit.yaml "${STATE}"/models.edit.yaml \
        "${STATE}"/repos.agent-toolkit.yaml "${ES}/"
    : > "${LOG}"
    out=$(PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_SHA="${EAS_SHA}" EAS_CPU_POLICY=best-effort EAS_NODES=1 \
        EAS_VENV="${WORK}/cli/venv" bash "${K}/eas/eas.sh" up agent-toolkit -- -e x=1 2>&1); rc=$?
    log=$(cat "${LOG}")
    cli_eq     "cli: eas.sh up exits 0" "${rc}" 0
    cli_has    "cli: eas.sh clones EAS" "${log}" "git clone -q https://github.com/intel/enterprise-ai-solutions ${ES}/eas"
    cli_has    "cli: eas.sh checks out the SHA, clean" "${log}" "git -C ${ES}/eas checkout -q --force --detach ${EAS_SHA}"
    cli_before "cli: clone, then checkout" "${log}" "git clone" "checkout -q --force --detach"
    cli_before "cli: checkout, then clean" "${log}" "checkout -q --force --detach" "git -C ${ES}/eas clean -q -fd"
    cli_before "cli: checkout, then the overlay, applied" "${log}" "checkout -q --force --detach" \
        "overlay --eas ${ES}/eas --policy best-effort --nodes 1 --json ${ES}/overlay-decisions.json --apply"
    cli_has    "cli: overlay.txt holds the decisions" "$(cat "${ES}/overlay.txt")" "0002 istio global.platform -> apply"
    cli_before "cli: the overlay, then the manifest pins" "${log}" "overlay --eas" \
        "yq -i (.repos[] | select(.layer == strenv(L) and .url == strenv(U)) | .rev) = strenv(R) ${ES}/eas/configs/repos/repos.agent-toolkit.yaml [L=inference U=https://github.com/intel/enterprise-inference R=${EI_SHA}]"
    cli_has    "cli: the EAT repo is pinned too" "${log}" "[L=agent-toolkit U=https://github.com/intel/enterprise-agent-toolkit R=${EAT_SHA}]"
    cli_before "cli: the manifest pins, then init" "${log}" "strenv(R) ${ES}/eas/configs/repos" "installer init"
    cli_has    "cli: init <layer> --env k3d-gpu" "${log}" "installer init agent-toolkit --env k3d-gpu"
    cli_before "cli: init, then global_config.edit.yaml" "${log}" "installer init" \
        "yq -i . *= load(strenv(F)) ${ES}/eas/env/k3d-gpu/global_config.yaml [F=${ES}/global_config.edit.yaml]"
    cli_before "cli: init, then models.edit.yaml defaults" "${log}" "installer init" \
        "yq -i .defaults *= load(strenv(F)).defaults ${ES}/eas/env/k3d-gpu/models.yaml [F=${ES}/models.edit.yaml]"
    cli_has    "cli: the picked model's catalog entry is edited by name" "${log}" "[F=${ES}/models.edit.yaml N=qwen2-5-coder-7b]"
    cli_has    "cli: the model's env merges into its catalog entry" "${log}" \
        ".env *= ((load(strenv(F)).models[] | select(.name == strenv(N))).env // {}) ${ES}/eas/env/k3d-gpu/models.yaml"
    cli_has    "cli: args_append follows the catalog args, replacing the flag it names" "${log}" \
        '.args = (strenv(A) | from_yaml) '"${ES}"'/eas/env/k3d-gpu/models.yaml [N=qwen2-5-coder-7b A=["--block-size", "128", "--tensor-parallel-size", "1", "--max-model-len", "32768"]]'
    cli_lacks  "cli: args_append is not written into the catalog entry as a key" "${log}" \
        ") *= (load(strenv(F)).models[]"
    cli_before "cli: the edits, then install" "${log}" "load(strenv(F)).defaults" "installer install"
    cli_has    "cli: install with the image pins, then the overrides, then the extra args" "${log}" \
        "installer install agent-toolkit --env k3d-gpu -- -e @${ES}/k3d-gpu-images.yaml -e @${ES}/k3d-gpu-overrides.yaml -e x=1"
    cli_lacks  "cli: configure is never called" "${log}" "installer configure"
    cli_eq     "cli: .venv links to the baked venv" "$(readlink "${ES}/eas/.venv")" "${WORK}/cli/venv"
    : > "${LOG}"
    PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_SHA="${EAS_SHA}" EAS_CPU_POLICY=best-effort EAS_NODES=1 \
        EAS_VENV="${WORK}/cli/venv" bash "${K}/eas/eas.sh" up agent-toolkit >/dev/null 2>&1
    cli_has    "cli: a second up re-inits with --upgrade (moved pins)" "$(cat "${LOG}")" "installer init agent-toolkit --env k3d-gpu --upgrade"
    cli_lacks  "cli: a second up does not clone again" "$(cat "${LOG}")" "git clone"
    out=$(PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_SHA=main bash "${K}/eas/eas.sh" up agent-toolkit 2>&1); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'EAS_SHA must be a full commit SHA' <<< "${out}"; then ok "cli: eas.sh refuses a floating EAS ref"; else fail "cli: eas.sh floating ref: rc=${rc} ${out}"; fi
    sed -i 's/rev: "3e8a/rev: "main-3e8a/' "${ES}/repos.agent-toolkit.yaml"
    out=$(PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_SHA="${EAS_SHA}" EAS_CPU_POLICY=best-effort EAS_NODES=1 \
        EAS_VENV="${WORK}/cli/venv" bash "${K}/eas/eas.sh" up agent-toolkit 2>&1); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'is not a commit SHA' <<< "${out}"; then ok "cli: eas.sh refuses a manifest rev that is not a SHA"; else fail "cli: eas.sh manifest rev: rc=${rc} ${out}"; fi
    : > "${LOG}"
    PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_VENV="${WORK}/cli/venv" bash "${K}/eas/eas.sh" down >/dev/null 2>&1
    cli_has    "cli: eas.sh down: teardown platform, forced (no terminal)" "$(cat "${LOG}")" "installer teardown platform --env k3d-gpu --force"
    : > "${LOG}"
    PATH="${B}:${PATH}" STUB_LOG="${LOG}" EAS_STATE="${ES}" EAS_VENV="${WORK}/cli/venv" bash "${K}/eas/eas.sh" status >/dev/null 2>&1
    cli_has    "cli: eas.sh status" "$(cat "${LOG}")" "installer status --env k3d-gpu"

    # ---- down ----------------------------------------------------------------------------------------
    mkdir -p "${STATE}/eas/env/k3d-gpu"
    : > "${LOG}"
    out=$(cli cmd_eas down); rc=$?
    log=$(cat "${LOG}")
    cli_eq     "cli: down exits 0" "${rc}" 0
    cli_has    "cli: down clears each AIGatewayRoute's finalizers (EAS #71)" "${log}" \
        'kubectl --context k3d-easc -n llm-inference patch aigatewayroute aigw-qwen2-5-coder-7b --type=merge -p {"metadata":{"finalizers":[]}}'
    cli_has    "cli: down clears the guard model's route too" "${log}" "patch aigatewayroute aigw-qwen3guard"
    cli_before "cli: finalizers cleared before teardown" "${log}" "patch aigatewayroute aigw-qwen3guard" "eas.sh down"
    cli_has    "cli: down keeps the cluster" "${out}" "the cluster stays"
    cli_lacks  "cli: down does not delete the cluster" "${log}" "cluster delete"

    # ---- status --------------------------------------------------------------------------------------
    echo agent-toolkit > "${STATE}/layer"
    command cp -f "${ES}/overlay.txt" "${STATE}/overlay.txt"   # what eas.sh up writes in the bastion
    : > "${LOG}"
    out=$(cli cmd_eas status); rc=$?
    cli_eq    "cli: status exits 0" "${rc}" 0
    cli_has   "cli: status: the layer" "${out}" "Layer: agent-toolkit"
    cli_has   "cli: status: pins" "${out}" "eas: intel/enterprise-ai-solutions@97f6157 (profile main)"
    cli_has   "cli: status: overlay decisions" "${out}" "0006 envoy control-plane affinity -> apply (variant a)"
    cli_has   "cli: status: EAS's own status" "$(cat "${LOG}")" "eas.sh status"
    cli_has   "cli: status: the gateway with its --resolve line" "${out}" "--resolve litellm.solutions.ai:443:172.18.0.2"
    cli_has   "cli: status: a pod not Ready" "${out}" "llm-inference/vllm-qwen-0: ContainerCreating"
    cli_lacks "cli: status: a Ready pod is not listed" "${out}" "litellm/litellm-0"
    cli_lacks "cli: status: a finished pod is not listed" "${out}" "helm-install-x"

    # ---- check ---------------------------------------------------------------------------------------
    : > "${LOG}"
    out=$(cli cmd_eas check --model qwen2-5-coder-7b); rc=$?
    cli_eq  "cli: check exits 0" "${rc}" 0
    cli_has "cli: check runs eat-check.sh in eas mode on this cluster's kubeconfig and domain" "$(cat "${LOG}")" \
        "eat-check mode=eas kubeconfig=${STATE}/kubeconfig domain=solutions.ai model=qwen2-5-coder-7b"

    # ---- nothing installed ---------------------------------------------------------------------------
    rm -rf "${STATE}/eas"
    cli_refused "down with nothing installed" "nothing installed by 'k3d-gpu eas up'" down
    cli_refused "status with nothing installed" "nothing installed by 'k3d-gpu eas up'" status

    # ---- sandbox up next to EAS's agent sandbox --------------------------------------------------------
    : > "${LOG}"
    out=$(STUB_EAS_SANDBOX=1 cli sandbox_up); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q "belongs to EAS's agent toolkit" <<< "${out}" && ! grep -q ' apply ' "${LOG}"; then
        ok "cli: sandbox up refuses when sandbox-router-deployment exists, before any apply"
    else
        fail "cli: sandbox up next to EAS: rc=${rc} ${out}"
    fi

    # ---- the launcher's dispatcher and help ------------------------------------------------------------
    out=$(K3D_GPU_STATE_DIR="${S}/k3d-gpu-state" HOME="${S}/home" "${LAUNCHER}" eas 2>&1); rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'usage: k3d-gpu eas up' <<< "${out}"; then ok "cli: dispatcher: 'eas' reaches cmd_eas"; else fail "cli: dispatcher: rc=${rc} ${out}"; fi
    cli_has "cli: help documents eas up" "$("${LAUNCHER}" help)" "eas up [--agent-toolkit] [--model NAME]"

    # ---- the bastion: its pins are pins.json's bastion profile -----------------------------------------
    local k3s kc want got
    k3s=$(sed -n 's/^ARG K3S_TAG="\{0,1\}v\([0-9]*\.[0-9]*\)\..*/\1/p' "${ROOT}/Dockerfile")
    kc=$(sed -n 's/^ARG KUBECTL_VERSION=v\([0-9]*\.[0-9]*\)\..*/\1/p' "${EAS_DIR}/bastion.Dockerfile")
    if [ -n "${k3s}" ] && [ "${kc}" = "${k3s}" ]; then ok "cli: bastion kubectl minor ${kc} is the K3S_TAG minor"; else fail "cli: bastion kubectl minor '${kc}' vs K3S_TAG minor '${k3s}'"; fi
    want=$(python3 - "${EAS_DIR}/pins.json" <<'PY'
import json, sys
rows = {r["name"]: r for r in json.load(open(sys.argv[1]))["pins"] if r["profile"] == "bastion"}
print("FROM ubuntu:%s@sha256:%s kubectl %s helm %s yq %s kubernetes.core %s"
      % (rows["ubuntu"]["ref"], rows["ubuntu"]["sha"], rows["kubectl"]["ref"], rows["helm"]["ref"], rows["yq"]["ref"],
         rows["kubernetes.core"]["ref"]))
PY
)
    got="$(grep -m1 '^FROM ' "${EAS_DIR}/bastion.Dockerfile") kubectl $(sed -n 's/^ARG KUBECTL_VERSION=//p' "${EAS_DIR}/bastion.Dockerfile") helm $(sed -n 's/^ARG HELM_VERSION=//p' "${EAS_DIR}/bastion.Dockerfile") yq $(sed -n 's/^ARG YQ_VERSION=//p' "${EAS_DIR}/bastion.Dockerfile") kubernetes.core $(sed -n 's/^ARG KUBERNETES_CORE_VERSION=//p' "${EAS_DIR}/bastion.Dockerfile")"
    cli_eq "cli: bastion.Dockerfile pins what pins.json's bastion profile names" "${got}" "${want}"
    cli_eq "cli: bastion FROM is the EAT bastion's digest" \
        "$(grep -m1 '^FROM ' "${EAS_DIR}/bastion.Dockerfile")" "$(grep -m1 '^FROM ' "${ROOT}/integrations/eat/bastion.Dockerfile")"
    want=$(python3 -c 'import json, sys
print(next(r["sha"] for r in json.load(open(sys.argv[1]))["pins"] if r["profile"] == "main" and r["name"] == "eas"))' "${EAS_DIR}/pins.json")
    cli_eq "cli: requirements.lock is for the pinned EAS commit" "$(head -1 "${EAS_DIR}/requirements.lock")" "# enterprise-ai-solutions@${want} requirements.txt"
    cli_eq "cli: requirements.in names the same commit" "$(head -1 "${EAS_DIR}/requirements.in")" "$(head -1 "${EAS_DIR}/requirements.lock")"
    if python3 - "${EAS_DIR}/pins.json" "${EAS_DIR}/requirements.lock" <<'PY'
import json, re, sys
lock = open(sys.argv[2]).read()
for r in json.load(open(sys.argv[1]))["pins"]:
    if r["profile"] == "bastion" and r["repo"].startswith("pypi.org/project/"):
        name = r["repo"].rsplit("/", 1)[1]
        block = re.search(r"(?ms)^%s==%s \\\n(.*?)(?=^\S)" % (re.escape(name), re.escape(r["ref"])), lock + "\nEND")
        assert block and "--hash=sha256:" + r["sha"] in block.group(1), (name, r["ref"])
PY
    then ok "cli: requirements.lock pins each pins.json Python package at its version, with its wheel's sha256"
    else fail "cli: requirements.lock and pins.json's bastion Python rows disagree"; fi
    if grep -E '^[A-Za-z0-9_.-]+==' "${EAS_DIR}/requirements.lock" | grep -qv -- ' \\$'; then
        fail "cli: requirements.lock has a pin without hashes"
    else
        ok "cli: every pin in requirements.lock carries hashes"
    fi
    if grep -E '^ARG [A-Z_]*_(SHA256|VERSION)=' "${EAS_DIR}/bastion.Dockerfile" | grep -Eqv '=(v?[0-9.]+|[0-9a-f]{64})$'; then
        fail "cli: bastion.Dockerfile has a version or sha256 ARG that is not exact"
    else
        ok "cli: bastion.Dockerfile pins every download by version and sha256"
    fi
    cli_eq "cli: every bastion download is checked" "$(grep -c 'sha256sum -c -' "${EAS_DIR}/bastion.Dockerfile")" 4
    while read -r mode _ _ path; do
        if [ "${mode}" = 100755 ]; then ok "cli: executable in git: ${path}"; else fail "cli: not executable in git (${mode}): ${path}"; fi
    done < <(git -C "${ROOT}" ls-files -s -- 'integrations/eas/*.sh')
    if python3 - "${EAS_DIR}/known-issues.json" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1]))["issues"]
keys = {"id", "upstream", "symptom", "workaround", "source", "checked"}
assert rows and all(set(r) == keys and all(isinstance(v, str) and v for v in r.values()) for r in rows), rows
assert len({r["id"] for r in rows}) == len(rows)
PY
    then ok "cli: known-issues.json rows are flat {id, upstream, symptom, workaround, source, checked}"
    else fail "cli: known-issues.json shape"; fi
}

SECTIONS=(overlay config cli)
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

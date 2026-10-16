#!/usr/bin/env bash
# Runs enterprise-ai-solutions' kubernetes role (BYO install branch, and the
# managed-cluster apply_node_topology include) on localhost against a stub
# kubernetes.core collection: k8s_info returns a canned Node list, k8s records
# each label patch. DENY=k8s_info or DENY=k8s before a check makes that module
# fail, as it does for a kubeconfig without node RBAC. No cluster needed.
#
# Needs ansible-playbook and ansible-galaxy on PATH (ansible-core 2.15+) and the
# Python jmespath module (for community.general.json_query).
#
# Usage: integrations/eat/upstream/checks/eas-byo-topology.sh <eas-checkout>
# Exit 0 when every case matches. Unpatched upstream fails "BYO 3 nodes".

set -uo pipefail

EAS=${1:?usage: $0 <enterprise-ai-solutions checkout>}
[ -d "${EAS}/roles/kubernetes" ] || { echo "not an EAS checkout: ${EAS}" >&2; exit 2; }
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
FAILS=0

C="${WORK}/coll/ansible_collections/kubernetes/core"
mkdir -p "${C}/plugins/modules" "${C}/meta"
printf 'namespace: kubernetes\nname: core\nversion: 0.0.0\nreadme: README.md\nauthors: [stub]\n' > "${C}/galaxy.yml"
echo 'requires_ansible: ">=2.15"' > "${C}/meta/runtime.yml"
cat > "${C}/plugins/modules/k8s_info.py" <<'EOF'
#!/usr/bin/python
import json, os
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(api_version=dict(), kind=dict(), name=dict(),
                  namespace=dict(), label_selectors=dict(type='list')),
                  supports_check_mode=True)
if os.environ.get('STUB_DENY') == 'k8s_info':
    m.fail_json(msg='nodes is forbidden (stub)')
with open(os.environ['STUB_NODES']) as f:
    m.exit_json(changed=False, resources=json.load(f))
EOF
cat > "${C}/plugins/modules/k8s.py" <<'EOF'
#!/usr/bin/python
import json, os
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(state=dict(), kind=dict(), name=dict(),
                  definition=dict(type='dict'), api_version=dict(),
                  namespace=dict(), src=dict()))
if os.environ.get('STUB_DENY') == 'k8s':
    m.fail_json(msg='nodes is forbidden (stub)')
with open(os.environ['STUB_PATCHES'], 'a') as f:
    labels = m.params['definition']['metadata']['labels']
    f.write('%s=%s\n' % (m.params['name'], labels['workload-class']))
m.exit_json(changed=True)
EOF
ansible-galaxy collection install --force community.general -p "${WORK}/coll" \
    > "${WORK}/galaxy.log" 2>&1 </dev/null \
    || { cat "${WORK}/galaxy.log" >&2; echo "ansible-galaxy could not install community.general" >&2; exit 2; }

export ANSIBLE_ROLES_PATH="${EAS}/roles" ANSIBLE_COLLECTIONS_PATH="${WORK}/coll"
export ANSIBLE_LOCALHOST_WARNING=false ANSIBLE_INVENTORY_UNPARSED_WARNING=false

# k3s labels servers node-role.kubernetes.io/control-plane=true; agents get none.
SERVER='{"metadata":{"name":"server-0","labels":{"node-role.kubernetes.io/control-plane":"true"}}}'
agent() { printf '{"metadata":{"name":"agent-%s","labels":{}}}' "$1"; }
echo "[${SERVER}]" > "${WORK}/one.json"
echo "[${SERVER},$(agent 0),$(agent 1)]" > "${WORK}/three.json"
# agent-1 already carries a workload-class label set by hand.
HAND='{"metadata":{"name":"agent-1","labels":{"workload-class":"gpu"}}}'
echo "[${SERVER},$(agent 0),${HAND}]" > "${WORK}/hand.json"

for tasks in install apply_node_topology; do
    cat > "${WORK}/${tasks}.yml" <<EOF
- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: kubernetes
        tasks_from: ${tasks}
EOF
done

# check <label> <tasks file> <nodes json> <want patches> <extra-vars...>
# want "playbook-fails" expects the playbook to exit non-zero.
check() {
    local label=$1 tasks=$2 nodes=$3 want=$4 got
    shift 4
    : > "${WORK}/patches"
    if ! STUB_NODES="${WORK}/${nodes}" STUB_PATCHES="${WORK}/patches" STUB_DENY="${DENY:-}" \
        ansible-playbook -i localhost, -c local "${WORK}/${tasks}.yml" "$@" \
        > "${WORK}/out" 2>&1 </dev/null; then
        if [ "${want}" = "playbook-fails" ]; then
            printf 'ok   %-30s [playbook failed]\n' "${label}"
            return
        fi
        printf 'FAIL %-30s playbook failed\n' "${label}"
        tail -15 "${WORK}/out"
        FAILS=$((FAILS + 1))
        return
    fi
    got=$(sort "${WORK}/patches" | tr '\n' ' ')
    if [ "${got}" = "${want}" ]; then
        printf 'ok   %-30s [%s]\n' "${label}" "${got}"
    else
        printf 'FAIL %-30s [%s] want [%s]\n' "${label}" "${got}" "${want}"
        FAILS=$((FAILS + 1))
    fi
}

ALL="agent-0=inference agent-1=inference server-0=platform "
check "BYO 1 node, topology on"    install one.json   "" -e existing_kubernetes=/k -e node_topology_enabled=true
check "BYO 3 nodes, topology on"   install three.json "${ALL}" -e existing_kubernetes=/k -e node_topology_enabled=true
check "BYO 3 nodes, topology off"  install three.json "" -e existing_kubernetes=/k -e node_topology_enabled=false
check "managed single-node"        apply_node_topology three.json "" -e _k8s_needs_ssh=false
check "managed multi-node"         apply_node_topology three.json "${ALL}" -e _k8s_needs_ssh=true
check "BYO 3 nodes, one hand label" install hand.json "agent-0=inference server-0=platform " -e existing_kubernetes=/k -e node_topology_enabled=true
DENY=k8s_info check "BYO, no node list RBAC"  install three.json "" -e existing_kubernetes=/k -e node_topology_enabled=true
DENY=k8s check "BYO, no node patch RBAC"      install three.json "" -e existing_kubernetes=/k -e node_topology_enabled=true
DENY=k8s check "managed, no node patch RBAC"  apply_node_topology three.json "playbook-fails" -e _k8s_needs_ssh=true

[ "${FAILS}" -eq 0 ]

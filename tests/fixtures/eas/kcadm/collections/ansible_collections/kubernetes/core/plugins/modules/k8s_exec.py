#!/usr/bin/python
# WANT_JSON
# kubernetes.core.k8s_exec, which execs shlex.split(command) in the container with no shell
# and no stdin (kubernetes.core 6.3.0 plugins/modules/k8s_exec.py#L192-L198), here as a
# local process: /opt/keycloak/bin/kcadm.sh is KCADM_FAKE_BIN (kcadm.py), and the
# environment carries what the Keycloak operator sets in the pod from keycloak-admin-secret
# (KC_BOOTSTRAP_ADMIN_USERNAME, KC_BOOTSTRAP_ADMIN_PASSWORD).
import json
import os
import shlex
import subprocess
import sys

args = json.load(open(sys.argv[1]))
cmd = args["command"].replace("/opt/keycloak/bin/kcadm.sh", os.environ["KCADM_FAKE_BIN"])
env = {k: v for k, v in os.environ.items() if k != "KCADM_FAKE_PASSWORD"}
env.update(KC_BOOTSTRAP_ADMIN_USERNAME="admin", KC_BOOTSTRAP_ADMIN_PASSWORD=os.environ["KCADM_FAKE_PASSWORD"])
p = subprocess.run(shlex.split(cmd), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
print(json.dumps({"changed": True, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                  "failed": p.returncode != 0}))

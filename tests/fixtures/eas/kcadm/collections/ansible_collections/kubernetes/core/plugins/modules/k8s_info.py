#!/usr/bin/python
# WANT_JSON
# kubernetes.core.k8s_info as EI's keycloak_config role sees it on a fresh install: no
# keycloak-client-secret yet, a ready Keycloak, a finished realm import, and
# keycloak-admin-secret holding KCADM_FAKE_PASSWORD (base64, as the API returns it).
import base64
import json
import os
import sys

args = json.load(open(sys.argv[1]))
kind, name = args.get("kind"), args.get("name")


def b64(s):
    return base64.b64encode(s.encode()).decode()


if kind == "Secret" and name == "keycloak-admin-secret":
    found = [{"data": {"username": b64("admin"), "password": b64(os.environ["KCADM_FAKE_PASSWORD"])}}]
elif kind == "Secret":
    found = []
elif kind == "Keycloak":
    found = [{"metadata": {"name": "keycloak"}}]
elif kind == "StatefulSet":
    found = [{"status": {"readyReplicas": 1}}]
elif kind == "KeycloakRealmImport":
    found = [{"status": {"conditions": [{"type": "Done", "status": "True"}]}}]
elif kind == "Pod":
    found = [{"metadata": {"name": "keycloak-0"}}]
else:
    print(json.dumps({"failed": True, "msg": "k8s_info stub: unexpected %s %s" % (kind, name)}))
    sys.exit(1)
print(json.dumps({"changed": False, "api_found": True, "resources": found}))

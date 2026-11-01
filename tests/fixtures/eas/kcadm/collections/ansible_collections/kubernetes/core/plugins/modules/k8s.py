#!/usr/bin/python
# WANT_JSON
# kubernetes.core.k8s for EI's keycloak_config role. The KeycloakRealmImport sets the
# client's secret in "Keycloak" (KCADM_STATE/secret, which kcadm.py also reads and
# writes) and adds it to the sampler's patterns (KCADM_PATTERN); the keycloak-client-secret
# Secret records whether it stores the secret Keycloak holds (KCADM_STATE/stored).
import json
import os
import sys

args = json.load(open(sys.argv[1]))
d = args.get("definition") or {}
state = os.environ["KCADM_STATE"]
if d.get("kind") == "KeycloakRealmImport":
    secret = d["spec"]["realm"]["clients"][0]["secret"]
    with open(os.environ["KCADM_PATTERN"], "a") as f:
        f.write(secret + "\n")
    with open(os.path.join(state, "secret"), "w") as f:
        f.write(secret)
elif d.get("kind") == "Secret":
    with open(os.path.join(state, "secret")) as f:
        held = f.read()
    with open(os.path.join(state, "stored"), "w") as f:
        f.write("match" if d["stringData"]["client-secret"] == held else "mismatch")
print(json.dumps({"changed": True}))

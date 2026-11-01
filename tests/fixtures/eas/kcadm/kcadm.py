#!/usr/bin/env python3
# A stand-in for the Keycloak pod's /opt/keycloak/bin/kcadm.sh, for tests/eas_test.sh
# section kcadm: the five calls EI's keycloak_config role makes, against a "Keycloak" whose
# admin is KC_BOOTSTRAP_ADMIN_USERNAME / KC_BOOTSTRAP_ADMIN_PASSWORD and whose client
# secret is KCADM_STATE/secret.
#   config credentials   the password from --password, else KC_CLI_PASSWORD (Keycloak
#                        26.6.0 BaseConfigCredentialsCmd.java#L104-L110); records where it
#                        came from and whether it is right in KCADM_STATE/auth
#   get clients          the client's UUID
#   update clients/ID    -s secret=VALUE sets the secret
#   create clients/ID/client-secret   a new secret, added to the sampler's patterns
#   get clients/ID/client-secret      {"type": "secret", "value": ...} on stdout
# Each call that carries or makes a secret lasts as long as a kcadm JVM start, the window
# in which a ps sample can see its argv.
import json
import os
import secrets
import sys
import time

state = os.environ["KCADM_STATE"]
a = sys.argv[1:]


def opt(name):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None


def put(name, value):
    with open(os.path.join(state, name), "w") as f:
        f.write(value)


if a[:2] == ["config", "credentials"]:
    password, source = opt("--password"), "argv"
    if password is None:
        password, source = os.environ.get("KC_CLI_PASSWORD"), "env"
    good = (password == os.environ.get("KC_BOOTSTRAP_ADMIN_PASSWORD")
            and opt("--user") == os.environ.get("KC_BOOTSTRAP_ADMIN_USERNAME"))
    put("auth", "%s %s" % (source, "ok" if good else "bad"))
    time.sleep(2)
    sys.exit(0 if good else 1)
if len(a) > 1 and a[1].startswith("clients/") and a[1].endswith("/client-secret"):
    if a[0] == "create":
        value = "kc-" + secrets.token_hex(16)
        with open(os.environ["KCADM_PATTERN"], "a") as f:
            f.write(value + "\n")
        put("secret", value)
        time.sleep(1)
        sys.exit(0)
    if a[0] == "get":
        with open(os.path.join(state, "secret")) as f:
            print(json.dumps({"type": "secret", "value": f.read()}, indent=2))
        sys.exit(0)
if a[:2] == ["get", "clients"]:
    print("6f1c2b0e-8d4a-4f3e-9b7a-2c5d1e0f9a84")
    sys.exit(0)
if len(a) > 1 and a[0] == "update" and a[1].startswith("clients/"):
    s = opt("-s") or ""
    if s.startswith("secret="):
        put("secret", s[len("secret="):])
    time.sleep(2)
    sys.exit(0)
print("kcadm.py: unexpected: %s" % " ".join(a[:2]), file=sys.stderr)
sys.exit(2)

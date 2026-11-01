"""A stand-in for scripts/images-lock's registry_digest, for tests/watchdog_test.sh.

hack/watchdog loads registry_digest from the file WATCHDOG_IMAGES_LOCK names; the
test points that at this file. $STUB_REGISTRY names a file of lines
"<name>:<tag> [status]": a listed reference without a status resolves to a fixed
digest, one with a status (e.g. 503) raises the way images-lock does for that HTTP
status, and an unlisted one raises HTTP 404. Every lookup is appended to
$STUB_REGISTRY.log.
"""

import os


def registry_digest(name, tag):
    ref = "%s:%s" % (name, tag)
    with open(os.environ["STUB_REGISTRY"] + ".log", "a") as f:
        f.write(ref + "\n")
    with open(os.environ["STUB_REGISTRY"]) as f:
        rows = dict((ln.split() + [""])[:2] for ln in f if ln.strip())
    if ref not in rows:
        raise ValueError("%s: HTTP 404 from https://stub/v2/%s/manifests/%s" % (ref, name, tag))
    if rows[ref]:
        raise ValueError("%s: HTTP %s from https://stub/v2/%s/manifests/%s" % (ref, rows[ref], name, tag))
    return "sha256:" + "0" * 64

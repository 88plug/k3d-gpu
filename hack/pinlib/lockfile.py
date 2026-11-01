"""Rewrite images.lock after a bump without re-checking digests nobody moved.

`scripts/images-lock update` re-resolves every digest and stops on the first one a
registry has re-pointed, so one drifted digest anywhere blocks every bump (#220).
refresh() rebuilds the lock from the repo's own pins (images-lock's collect()),
keeps the old lock's digest for every unchanged k3s system image, and asks a
registry only for the k3s system images of a new K3S_TAG. When images-lock has
`update --only`, that is used instead.
"""

import os
import subprocess
import urllib.parse

from . import http, oci


def supports_only(root):
    with open(os.path.join(root, "scripts", "images-lock"), encoding="utf-8") as f:
        return "--only" in f.read()


def refresh(root, touched=()):
    il = oci.images_lock()
    lock_path = os.path.join(root, "images.lock")
    if supports_only(root):
        r = subprocess.run([os.path.join(root, "scripts", "images-lock"), "--root", root, "update", "--only"]
                           + sorted(touched or ["images.lock"]), capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("scripts/images-lock update --only: %s" % (r.stderr or r.stdout).strip()[-600:])
        return
    found, k3s_tag = il.collect(root)
    old = il.read_lock(lock_path)
    old_by = {(e["name"], e["tag"]): e for e in old["images"]}
    src = il.k3s_source(k3s_tag)
    if old["k3s_tag"] == k3s_tag:
        k3s = [(e["name"], e["tag"], e["digest"]) for e in old["images"] if il.k3s_source(k3s_tag) in e["sources"]]
    else:
        url = il.K3S_IMAGES_URL % urllib.parse.quote(k3s_tag.replace("-k3s", "+k3s"))
        k3s = []
        for line in http.get_text(url).splitlines():
            if line.strip():
                name, tag, digest = il.parse_ref(line.strip())
                k3s.append((name, tag, digest))
    for name, tag, digest in k3s:
        e = found.setdefault((name, tag), {"digest": digest, "sources": set()})
        e["sources"].add(src)
    images = []
    for (name, tag), e in sorted(found.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        digest = e["digest"] or (old_by.get((name, tag)) or {}).get("digest") or oci.digest(name, tag)
        images.append({"name": name, "tag": tag, "digest": digest, "sources": sorted(e["sources"])})
    lock = {"schema": il.LOCK_SCHEMA, "k3s_tag": k3s_tag, "images": images}
    with open(lock_path + ".tmp", "w", encoding="utf-8") as f:
        f.write(il.dumps(lock))
    os.replace(lock_path + ".tmp", lock_path)
    problems = il.check(root, lock_path)
    if problems:
        raise RuntimeError("images.lock after the bump: " + "; ".join(problems[:5]))

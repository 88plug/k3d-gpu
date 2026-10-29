#!/usr/bin/env python3
"""hubcheck.py B3_MODEL_DIR [TREE_JSON]: compare B3's pulled files (b3-records.sh's files.txt and
sha256.txt, H50o amendment O1 item 2) with the Hugging Face Hub at the revision the clone's HEAD
names (git.txt). Every Hub file must be there with the Hub's size, and every LFS file with the Hub's
sha256; files the pull added (graph.pbtxt, the server's config.json) are listed. TREE_JSON: a saved
copy of https://huggingface.co/api/models/<repo>/tree/<rev>?recursive=1 (default: read it now).
Prints one line per file and a summary; exits 1 on any mismatch. Records only; it gates nothing."""
import json
import os
import re
import sys
import urllib.request

REPO = "OpenVINO/gemma-4-26b-a4b-it-int4-ov"


def main(d, tree=None):
    git = open(os.path.join(d, "git.txt")).read()
    rev = re.search(r"^refs/heads/main ([0-9a-f]{40})$", git, re.M).group(1)
    if tree:
        hub = json.load(open(tree))
    else:
        url = "https://huggingface.co/api/models/%s/tree/%s?recursive=1" % (REPO, rev)
        with urllib.request.urlopen(url, timeout=60) as r:
            hub = json.load(r)
    hub = {f["path"]: f for f in hub if f["type"] == "file"}
    pre = "./%s/" % REPO
    sha = dict(reversed(line.split(None, 1)) for line in open(os.path.join(d, "sha256.txt")))
    sha = {p.strip(): h for p, h in sha.items()}
    size = {}
    for line in open(os.path.join(d, "files.txt")):
        s, p = line.split(None, 1)
        size[p.strip()] = int(s)
    bad = 0
    print("revision %s (%s, git.txt)" % (rev, REPO))
    for n, f in sorted(hub.items()):
        p = pre + n
        if p not in size:
            print("MISSING %s" % n)
            bad += 1
            continue
        ok = size[p] == f["size"]
        lfs = (f.get("lfs") or {}).get("oid")
        if lfs:
            ok = ok and sha.get(p) == lfs
        bad += not ok
        print("%s %s %d%s" % ("ok " if ok else "BAD", n, size[p], " lfs " + lfs if lfs else ""))
    extra = sorted(p for p in size if not (p.startswith(pre) and p[len(pre):] in hub))
    print("not on the Hub (written by the pull or the chart): %s" % ", ".join(extra))
    print("%d Hub files, %d LFS, %d mismatched or missing" % (len(hub), sum(bool(f.get("lfs")) for f in hub.values()), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))

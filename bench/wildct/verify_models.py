#!/usr/bin/env python3
"""verify_models.py DIR [--skip FILE]... REPO@COMMIT...: check every downloaded file of each pinned snapshot
against the Hub (EXPERIMENTS.md "Wildct pre-registration", rule W6). For each file the Hub
lists at that commit and that exists under DIR/REPO: the local sha256 must equal the Hub's LFS
sha256 (LFS files), or the local size must equal the Hub's size (small git files, whose Hub
id is a git blob id). Prints one line per file and a summary; exits 1 on any mismatch or on a
listed LFS file that is missing locally, unless named by --skip (a file deliberately not
downloaded, e.g. the GGUF repo's mmproj for a text-only arm). Needs huggingface_hub (hfvenv)."""
import hashlib
import os
import sys

from huggingface_hub import HfApi


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def main(d, specs, skip=()):
    api, bad = HfApi(), 0
    for spec in specs:
        repo, rev = spec.split("@")
        info = api.model_info(repo, revision=rev, files_metadata=True)
        print(f"# {repo} @ {rev} -> {info.sha}", flush=True)
        for s in info.siblings:
            p = os.path.join(d, repo, s.rfilename)
            if not os.path.exists(p):
                if s.rfilename in skip:
                    print(f"skipped {repo}/{s.rfilename} (not downloaded on purpose)")
                elif s.lfs:
                    print(f"MISSING {repo}/{s.rfilename}")
                    bad += 1
                continue
            if s.lfs:
                got = sha256(p)
                ok = got == s.lfs.sha256
                print(f"{'ok' if ok else 'MISMATCH'} {got} {s.size} {repo}/{s.rfilename}", flush=True)
            else:
                got = os.path.getsize(p)
                ok = got == s.size
                print(f"{'ok' if ok else 'MISMATCH'} size={got} {repo}/{s.rfilename}", flush=True)
            bad += not ok
    print("VERIFY", "ok" if bad == 0 else f"{bad} bad")
    return 1 if bad else 0


if __name__ == "__main__":
    args, skip = sys.argv[2:], []
    while args and args[0] == "--skip":
        skip.append(args[1])
        args = args[2:]
    sys.exit(main(sys.argv[1], args, skip))

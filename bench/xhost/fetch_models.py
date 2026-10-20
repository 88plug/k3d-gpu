#!/usr/bin/env python3
"""fetch_models.py DIR REPO@COMMIT... [--draft TARGET=DRAFT]: download Hugging Face model
snapshots at pinned commits into DIR/REPO (the layout bench/hetero-spec/h34.sh and
bench/shared-prefix/run.sh read). Prints REPO COMMIT PATH.

--draft TARGET=DRAFT also copies DIR/DRAFT into DIR/TARGET/<DRAFT with / as ->, without the
download cache: the place OVMS 2026.4.1 `--pull` looks for a Fast Draft, so the agent manifest's
pull container serves the pinned draft offline (EXPERIMENTS.md, H47 and H49 amendment P1, item 6).
Needs huggingface_hub (1.21.0 in ~/k3dgpu-xhost/hfvenv on crescent)."""
import argparse
import shutil

from huggingface_hub import snapshot_download

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("specs", nargs="+", metavar="REPO@COMMIT")
ap.add_argument("--draft", action="append", default=[], metavar="TARGET=DRAFT")
a = ap.parse_args()
for spec in a.specs:
    repo, rev = spec.split("@")
    print(repo, rev, snapshot_download(repo, revision=rev, local_dir=f"{a.dir}/{repo}"), flush=True)
for pair in a.draft:
    target, draft = pair.split("=")
    dst = f"{a.dir}/{target}/{draft.replace('/', '-')}"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(f"{a.dir}/{draft}", dst, ignore=shutil.ignore_patterns(".cache", ".git"))
    print("draft", draft, "->", dst, flush=True)

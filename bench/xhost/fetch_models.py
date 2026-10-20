#!/usr/bin/env python3
"""fetch_models.py DIR REPO@COMMIT...: download Hugging Face model snapshots at pinned commits
into DIR/REPO (the layout bench/hetero-spec/h34.sh and bench/shared-prefix/run.sh read).
Needs huggingface_hub (1.21.0 in ~/k3dgpu-xhost/hfvenv on crescent). Prints REPO COMMIT PATH."""
import sys

from huggingface_hub import snapshot_download

for spec in sys.argv[2:]:
    repo, rev = spec.split("@")
    print(repo, rev, snapshot_download(repo, revision=rev, local_dir=f"{sys.argv[1]}/{repo}"), flush=True)

#!/usr/bin/env python3
"""Print the k3d clusters of the wildct campaign (k3dgpu-w-*), space-separated.

Reads `k3d cluster list -o json` on stdin. Any other cluster on the host (the box owner's
own) is never selected.
"""
import json
import re
import sys

print(" ".join(c["name"] for c in json.load(sys.stdin) if re.match(r"k3dgpu-w-", c["name"])))

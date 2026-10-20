#!/usr/bin/env python3
"""Print the k3d clusters of the cross-host campaign (k3dgpu-x-*), space-separated.

Reads `k3d cluster list -o json` on stdin. Clusters of any other campaign on the host
(k3dgpu-e-*, k3dgpu-a-*, k3dgpu-h5*) are never selected.
"""
import json
import re
import sys

print(" ".join(c["name"] for c in json.load(sys.stdin) if re.match(r"k3dgpu-x-", c["name"])))

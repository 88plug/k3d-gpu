#!/usr/bin/python
# kubernetes.core.k8s_info's result for Secret litellm/litellm-master-key, its master_key
# the fake key in ARGV_FAKE_KEY (base64, as the API returns Secret data).
import base64
import json
import os

key = os.environ["ARGV_FAKE_KEY"].encode()
print(json.dumps({"changed": False, "api_found": True,
                  "resources": [{"data": {"master_key": base64.b64encode(key).decode()}}]}))

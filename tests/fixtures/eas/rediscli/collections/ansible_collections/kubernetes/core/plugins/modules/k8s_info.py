#!/usr/bin/python
# WANT_JSON
# kubernetes.core.k8s_info as EAT's redis_stack validate sees it: one Running
# redis-stack-server pod.
import json
import sys

args = json.load(open(sys.argv[1]))
if args.get("kind") == "Pod":
    found = [{"metadata": {"name": "redis-stack-server-0"}, "status": {"phase": "Running"}}]
else:
    print(json.dumps({"failed": True, "msg": "k8s_info stub: unexpected %s" % args.get("kind")}))
    sys.exit(1)
print(json.dumps({"changed": False, "api_found": True, "resources": found}))

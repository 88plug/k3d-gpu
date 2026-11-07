#!/usr/bin/python
# WANT_JSON
# kubernetes.core.k8s_exec, which execs shlex.split(command) in the container with no shell
# and no stdin (kubernetes.core 6.3.0 plugins/modules/k8s_exec.py#L192-L198), here as a
# local process: redis-cli is bin/redis-cli (first on PATH), and the environment carries the
# REDIS_PASSWORD that EAT's redis-stack-server chart sets in the pod from the
# redis-stack-server-credentials Secret (templates/redis-stack-server.yaml#L57-L61).
import json
import os
import shlex
import subprocess
import sys

args = json.load(open(sys.argv[1]))
env = {k: v for k, v in os.environ.items() if k != "REDISCLI_FAKE_PASSWORD"}
env["REDIS_PASSWORD"] = os.environ["REDISCLI_FAKE_PASSWORD"]
p = subprocess.run(shlex.split(args["command"]), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
print(json.dumps({"changed": True, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                  "failed": p.returncode != 0}))

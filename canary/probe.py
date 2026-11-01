#!/usr/bin/env python3
"""The canary's runtime checks against a serving engine (canary/run.sh starts it).

  probe.py --base URL --api /v1 --name NAME --golden FILE --timeout S --out FILE
           [--container NAME] [--version-path P --version-key K --version-regex R]
           [--expect-version V]

1. Ready: GET {api}/models until it lists NAME, for up to --timeout seconds; with
   --container, stop early once `docker inspect` says the container exited.
2. One completion: POST {api}/completions, the golden's prompt, temperature 0, top_k 1,
   seed 0, max_tokens 8. Hard: HTTP 200, finish_reason stop or length,
   usage.completion_tokens > 0 and the text matches the golden's regex.
3. One streamed completion: at least 2 chunks with choices, then data: [DONE] last.
4. The version the server reports (GET --version-path, JSON key --version-key, read with
   --version-regex) equals --expect-version, when both are known.
5. Exact: the text equals the golden's exact text (null: none recorded). A mismatch keeps
   hard and clears exact.

Writes {ready, ready_s, exited, exit_code, hard, exact, text, version, stream_chunks,
failures, notes} to --out. Python standard library only.
"""

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request

MAX_TOKENS = 8


def http(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def container_state(name):
    """(running, exit code) of a container, (True, None) when docker cannot tell."""
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name],
                             capture_output=True, text=True, timeout=30).stdout.split()
        return out[0] == "true", int(out[1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return True, None


def wait_ready(a, res):
    start = time.monotonic()
    while True:
        try:
            code, body = http(a.base + a.api + "/models", timeout=10)
            if code == 200 and a.name in [m.get("id") for m in json.loads(body).get("data", [])]:
                res["ready"], res["ready_s"] = True, round(time.monotonic() - start, 1)
                return True
        except (OSError, ValueError, AttributeError):
            pass
        if a.container:
            running, rc = container_state(a.container)
            if not running:
                res["exited"], res["exit_code"] = True, rc
                res["failures"].append("the engine exited (code %s) before it listed %s" % (rc, a.name))
                return False
        if time.monotonic() - start >= a.timeout:
            res["failures"].append("never ready: %s%s/models did not list %s within %ds" % (a.base, a.api, a.name, a.timeout))
            return False
        time.sleep(min(2.0, max(0.2, a.timeout / 20)))


def request(a, golden, stream):
    body = {"model": a.name, "prompt": golden["prompt"], "max_tokens": MAX_TOKENS, "temperature": 0, "top_k": 1, "seed": 0}
    if stream:
        body["stream"] = True
    return body


def complete(a, golden, res):
    code, raw = http(a.base + a.api + "/completions", request(a, golden, False), timeout=300)
    if code != 200:
        res["failures"].append("completion: HTTP %d %s" % (code, raw[:200].decode(errors="replace")))
        return
    try:
        doc = json.loads(raw)
        choice = doc["choices"][0]
        text = choice.get("text") or ""
        fin = choice.get("finish_reason")
        tokens = (doc.get("usage") or {}).get("completion_tokens") or 0
    except (ValueError, KeyError, IndexError, TypeError) as e:
        res["failures"].append("completion: unreadable body (%s): %s" % (e, raw[:200].decode(errors="replace")))
        return
    res["text"] = text
    if fin not in ("stop", "length"):
        res["failures"].append("completion: finish_reason %r, not stop or length" % fin)
    if tokens <= 0:
        res["failures"].append("completion: usage.completion_tokens is %r" % tokens)
    if not re.search(golden["regex"], text):
        res["failures"].append("completion: text %r does not match /%s/" % (text, golden["regex"]))


def stream(a, golden, res):
    req = urllib.request.Request(a.base + a.api + "/completions", data=json.dumps(request(a, golden, True)).encode(),
                                 headers={"Content-Type": "application/json"})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:
                line = line.decode(errors="replace").strip()
                if line.startswith("data:"):
                    events.append(line[5:].strip())
    except urllib.error.HTTPError as e:
        res["failures"].append("stream: HTTP %d" % e.code)
        return
    except OSError as e:
        res["failures"].append("stream: %s" % e)
        return
    chunks = 0
    for ev in events:
        if ev == "[DONE]":
            continue
        try:
            doc = json.loads(ev)
        except ValueError:
            res["failures"].append("stream: a chunk is not JSON: %r" % ev[:120])
            return
        if "error" in doc:
            res["failures"].append("stream: error chunk %s" % json.dumps(doc["error"])[:200])
            return
        if doc.get("choices"):
            chunks += 1
    res["stream_chunks"] = chunks
    if chunks < 2:
        res["failures"].append("stream: %d chunks with choices, want at least 2" % chunks)
    if not events or events[-1] != "[DONE]":
        res["failures"].append("stream: did not end with data: [DONE]")


def version(a, res):
    if not a.version_path:
        return
    code, raw = http(a.base + a.version_path, timeout=30)
    try:
        v = str(json.loads(raw)[a.version_key])
    except (ValueError, KeyError, TypeError):
        res["failures"].append("version: GET %s answered %d without %s" % (a.version_path, code, a.version_key))
        return
    m = re.search(a.version_regex, v) if a.version_regex else None
    res["version"] = m.group("v") if m else v
    if not a.expect_version:
        res["notes"].append("server version %s (the pin has no tag to compare)" % v)
    elif res["version"] != a.expect_version:
        res["failures"].append("version: the server reports %s, the pinned tag says %s" % (v, a.expect_version))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", required=True)
    p.add_argument("--api", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--golden", required=True)
    p.add_argument("--timeout", type=float, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--container")
    p.add_argument("--version-path", default="")
    p.add_argument("--version-key", default="version")
    p.add_argument("--version-regex", default="")
    p.add_argument("--expect-version", default="")
    a = p.parse_args()
    with open(a.golden, encoding="utf-8") as f:
        golden = json.load(f)
    res = {"ready": False, "ready_s": None, "exited": False, "exit_code": None, "hard": False, "exact": False,
           "text": None, "version": None, "stream_chunks": 0, "failures": [], "notes": []}
    if wait_ready(a, res):
        complete(a, golden, res)
        stream(a, golden, res)
        version(a, res)
        res["hard"] = not res["failures"]
        res["exact"] = res["hard"] and golden.get("exact") is not None and res["text"] == golden["exact"]
        if res["hard"] and golden.get("exact") is None:
            res["notes"].append("no exact golden recorded")
        elif res["hard"] and not res["exact"]:
            res["notes"].append("exact: %r is not the golden %r" % (res["text"], golden["exact"]))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()

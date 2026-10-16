"""Runs one example against the stub endpoint and checks the tool-call round trip.

    python check.py langgraph        # runs langgraph/agent.py

Passes when the example exits 0 and prints 42, and the stub saw: every request
on /v3/chat/completions for the served model with Qwen3 thinking off, the
multiply tool offered, and the tool's result (42) sent back as a tool message.
"""

import json
import os
import subprocess
import sys
import tempfile

import stub_openai

HERE = os.path.dirname(os.path.abspath(__file__))


def main(example):
    fd, log = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    httpd = stub_openai.serve(0, log)
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}{stub_openai.PREFIX}"
    env = dict(os.environ, K3D_GPU_AGENT_BASE_URL=base_url,
               K3D_GPU_AGENT_MODEL=stub_openai.MODEL)
    proc = subprocess.run([sys.executable, "agent.py"], cwd=os.path.join(HERE, example),
                          env=env, capture_output=True, text=True, timeout=300)
    httpd.shutdown()
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    with open(log) as f:
        reqs = [json.loads(line) for line in f]

    failures = []

    def expect(ok, what):
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    chats = [r for r in reqs if r["method"] == "POST"]
    expect(proc.returncode == 0, f"{example}/agent.py exits 0 (got {proc.returncode})")
    expect("42" in proc.stdout, "final answer printed contains 42")
    expect(len(chats) >= 2, f"at least two chat requests (got {len(chats)})")
    expect(all(r["path"].split("?")[0] == "/v3/chat/completions" for r in chats),
           "every request used /v3/chat/completions: " + ", ".join(sorted({r["path"] for r in chats})))
    expect(all(r["body"].get("model") == stub_openai.MODEL for r in chats),
           f"every request asked for model {stub_openai.MODEL}")
    expect(all((r["body"].get("chat_template_kwargs") or {}).get("enable_thinking") is False
               for r in chats), "every request sent chat_template_kwargs.enable_thinking=false")
    expect(any(stub_openai._tool_name(r["body"].get("tools")) for r in chats),
           "the multiply tool was offered")
    expect(any(m.get("role") == "tool" and "42" in stub_openai._text(m.get("content"))
               for r in chats for m in r["body"].get("messages") or []),
           "the tool result 42 went back as a tool message")
    os.unlink(log)
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check.py <example-dir>")
    sys.exit(main(sys.argv[1]))

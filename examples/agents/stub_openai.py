"""Stub of the k3d-gpu agent endpoint (OVMS's OpenAI API under /v3), stdlib only.

It answers like Qwen3 behind OVMS with the hermes3 tool parser: given a
`multiply` tool it returns one tool call, multiply(a=6, b=7); once the tool's
result comes back it answers with that result. Every request is appended to a
JSON-lines log so tests can check what each framework actually sent.

    python stub_openai.py --port 8000 --log /tmp/requests.jsonl
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "Qwen3-4B-int4-ov"
PREFIX = "/v3"
TOOL_ARGS = {"a": 6, "b": 7}


def _tool_name(tools):
    for tool in tools or []:
        name = tool.get("function", {}).get("name", "")
        if "multiply" in name.lower():
            return name
    return None


def _text(content):
    if isinstance(content, list):  # content parts
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def reply_for(body):
    """Returns (content, tool_calls) for a chat completion request."""
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return f"6 times 7 is {_text(last.get('content')).strip()}.", None
    name = _tool_name(body.get("tools"))
    if name:
        call = {
            "id": f"call_{int(time.time() * 1000)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(TOOL_ARGS)},
        }
        return None, [call]
    return "Hello from the k3d-gpu stub.", None


class Handler(BaseHTTPRequestHandler):
    server_version = "k3d-gpu-stub/1"

    def log_message(self, fmt, *args):  # keep stdout for the example's output
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, body):
        with self.server.lock, open(self.server.log_path, "a") as f:
            f.write(json.dumps({"method": self.command, "path": self.path, "body": body}) + "\n")

    def do_GET(self):
        self._record(None)
        if self.path.rstrip("/") == f"{PREFIX}/models":
            return self._json(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "created": 0, "owned_by": "OVMS"}]})
        return self._json(404, {"error": f"no route {self.path}; the API lives under {PREFIX}"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._record(None)
            return self._json(400, {"error": "invalid JSON"})
        self._record(body)
        if self.path.split("?")[0] != f"{PREFIX}/chat/completions":
            return self._json(404, {"error": f"no route {self.path}; the API lives under {PREFIX}"})
        if body.get("model") != MODEL:
            return self._json(404, {"error": f"model {body.get('model')!r} not found; serving {MODEL!r}"})
        content, tool_calls = reply_for(body)
        if body.get("stream"):
            return self._stream(body, content, tool_calls)
        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self._json(200, {
            "id": "chatcmpl-stub", "object": "chat.completion", "created": int(time.time()),
            "model": MODEL,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    def _stream(self, body, content, tool_calls):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta, finish=None, **extra):
            obj = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                   "created": int(time.time()), "model": MODEL,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())

        chunk({"role": "assistant", "content": ""})
        if tool_calls:
            chunk({"tool_calls": [dict(c, index=i) for i, c in enumerate(tool_calls)]})
            chunk({}, "tool_calls")
        else:
            chunk({"content": content})
            chunk({}, "stop")
        if (body.get("stream_options") or {}).get("include_usage"):
            self.wfile.write(("data: " + json.dumps({
                "id": "chatcmpl-stub", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": MODEL, "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def serve(port, log_path):
    """Starts the stub in a daemon thread; returns the server (port 0 picks one)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.log_path = log_path
    httpd.lock = threading.Lock()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log", default="requests.jsonl")
    args = ap.parse_args()
    serve(args.port, args.log)
    print(f"stub listening on http://127.0.0.1:{args.port}{PREFIX}", flush=True)
    threading.Event().wait()

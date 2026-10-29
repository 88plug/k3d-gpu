#!/usr/bin/env python3
"""A fake OpenVINO Model Server for tests: OpenAI /v3 chat completions whose speed
is set by the deployed config.

  tests/fake_ovms.py <port> [manifest.yaml]

Per-token delay comes from the manifest the launcher last applied (the stub
kubectl saves it), so a tuned config really is faster or slower than the
default, deterministically:
  base 2.0 ms/token; PERFORMANCE_HINT THROUGHPUT x0.5; KV_CACHE_PRECISION u8 x0.8;
  k3d-gpu.io/cores: efficient x1.5. FAKE_OVMS_FLAT=1 ignores the config (a null server).
Every answer contains the quality probes' expected strings, so quality is 5/5.
"""

import http.server
import json
import os
import socketserver
import sys
import time

ANSWER = "391 canberra 366 au 643"


def delay_for(manifest_text):
    d = 0.002
    if os.environ.get("FAKE_OVMS_FLAT") == "1":
        return d
    if '"PERFORMANCE_HINT":"THROUGHPUT"' in manifest_text:
        d *= 0.5
    if '"KV_CACHE_PRECISION":"u8"' in manifest_text:
        d *= 0.8
    if "k3d-gpu.io/cores: efficient" in manifest_text:
        d *= 1.5
    return d


class Handler(http.server.BaseHTTPRequestHandler):
    delay = 0.002
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200 if self.path.startswith("/v2/health") else 404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = int(body.get("max_tokens", 8))
        if not body.get("stream"):
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": ANSWER}}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 5}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(obj):
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()
        send({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]})
        for i in range(n):
            time.sleep(self.delay)
            # OVMS sets finish_reason ("length" at max_tokens) on the last token's chunk
            send({"choices": [{"delta": {"content": "tok "}, "finish_reason": "length" if i == n - 1 else None}]})
        send({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": n}})
        self.wfile.write(b"data: [DONE]\n\n")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def serve(port, delay):
    Handler.delay = delay
    srv = Server(("127.0.0.1", port), Handler)
    return srv


if __name__ == "__main__":
    text = open(sys.argv[2]).read() if len(sys.argv) > 2 else ""
    serve(int(sys.argv[1]), delay_for(text)).serve_forever()

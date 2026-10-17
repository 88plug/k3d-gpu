#!/usr/bin/env python3
"""OpenAI-compatible stub that checks a bearer key (tests/remote_test.sh).

  tests/openai_auth_stub.py --key sk-test-x [--models a,b] [--log FILE]

Serves GET /v1/models and POST /v1/chat/completions (non-streaming) when the
Authorization header is "Bearer <key>", 401 otherwise. --log appends one line
per request: method, path and whether the key matched (never the key itself).
Prints "listening <port>" once ready.
"""

import argparse
import http.server
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--key", required=True)
    ap.add_argument("--models", default="zai-org/GLM-5.3")
    ap.add_argument("--log")
    a = ap.parse_args()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def handle_any(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            good = self.headers.get("Authorization") == f"Bearer {a.key}"
            if a.log:
                with open(a.log, "a") as f:
                    f.write(f"{method} {self.path} auth={'ok' if good else 'bad'} "
                            f"max_tokens={body.get('max_tokens')} temperature={body.get('temperature')}\n")
            if not good:
                return self.reply(401, {"error": {"message": "invalid api key"}})
            if method == "GET" and self.path == "/v1/models":
                return self.reply(200, {"object": "list", "data": [{"id": m} for m in a.models.split(",")]})
            if method == "POST" and self.path == "/v1/chat/completions":
                return self.reply(200, {"choices": [{"index": 0, "finish_reason": "stop",
                                                     "message": {"role": "assistant", "content": "OK"}}],
                                        "usage": {"completion_tokens": 1}})
            self.reply(404, {"error": {"message": "not found"}})

        def do_GET(self):
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"listening {httpd.server_address[1]}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

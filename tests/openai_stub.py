#!/usr/bin/env python3
"""Tiny OpenAI-compatible streaming chat stub for testing bench/llm_load.py.

Serves POST <prefix>/chat/completions (--prefix, default /v1,/v3) and streams
the way OpenVINO Model Server's continuous-batching servables do: a role-only
first chunk with content null, one chunk per token, a usage chunk with empty
choices (when stream_options.include_usage is set), then data: [DONE].

  tests/openai_stub.py --port 0 --tokens 16 --ttft 0.05 --itl 0.01

Prints "listening <port>" once ready. --no-usage drops the usage chunk;
--close sends the stream without chunked encoding and closes the connection.
"""

import argparse
import http.server
import json
import sys
import time


def make_handler(opts):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0" if opts.close else "HTTP/1.1"

        def log_message(self, *a):
            pass

        def send_chunk(self, data):
            raw = f"data: {data}\n\n".encode()
            if opts.close:
                self.wfile.write(raw)
            else:
                self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
            self.wfile.flush()

        def do_POST(self):
            if self.path not in [f"{x}/chat/completions" for x in opts.prefix.split(",")]:
                self.send_error(404)
                return
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not req.get("stream"):
                self.send_error(400, "stub only streams")
                return
            n = min(opts.tokens, req.get("max_tokens") or opts.tokens)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if not opts.close:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            base = {"object": "chat.completion.chunk", "created": int(time.time()), "model": req.get("model")}
            time.sleep(opts.ttft)
            self.send_chunk(json.dumps({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]}))
            for i in range(n):
                if i:
                    time.sleep(opts.itl)
                fin = "length" if i == n - 1 else None
                self.send_chunk(json.dumps({**base, "choices": [{"index": 0, "delta": {"content": f"tok{i} "}, "finish_reason": fin}]}))
            if (req.get("stream_options") or {}).get("include_usage") and not opts.no_usage:
                self.send_chunk(json.dumps({**base, "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": n, "total_tokens": 7 + n}}))
            self.send_chunk("[DONE]")
            if not opts.close:
                self.wfile.write(b"0\r\n\r\n")

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--tokens", type=int, default=16)
    p.add_argument("--ttft", type=float, default=0.05)
    p.add_argument("--itl", type=float, default=0.01)
    p.add_argument("--no-usage", action="store_true")
    p.add_argument("--close", action="store_true")
    p.add_argument("--prefix", default="/v1,/v3", help="API roots served, comma list")
    opts = p.parse_args()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", opts.port), make_handler(opts))
    print(f"listening {srv.server_address[1]}", flush=True)
    sys.stdout.close()
    srv.serve_forever()


if __name__ == "__main__":
    main()

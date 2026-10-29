#!/usr/bin/env python3
"""Tiny OpenAI-compatible streaming chat stub for testing bench/llm_load.py.

Serves POST <prefix>/chat/completions (--prefix, default /v1,/v3) and streams
the way OpenVINO Model Server's continuous-batching servables do: a role-only
first chunk with content null, one chunk per token, a usage chunk with empty
choices (when stream_options.include_usage is set), then data: [DONE].

  tests/openai_stub.py --port 0 --tokens 16 --ttft 0.05 --itl 0.01

Prints "listening <port>" once ready. --no-usage drops the usage chunk;
--close sends the stream without chunked encoding and closes the connection.
--die-at N sends N tokens, then an error chunk and [DONE] with no finish_reason,
the way vLLM's server ends a stream when its engine dies mid-decode.
--cut-at N sends N tokens and closes the connection: no finish_reason, no usage,
no [DONE] (a server process that is killed mid-stream; use with --close).
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
            if opts.eos_at:
                n = min(n, opts.eos_at)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if not opts.close:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            base = {"object": "chat.completion.chunk", "created": int(time.time()), "model": req.get("model")}
            time.sleep(opts.ttft)
            self.send_chunk(json.dumps({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]}))
            stop = opts.die_at or opts.cut_at
            if stop:
                for i in range(min(n, stop)):
                    self.send_chunk(json.dumps({**base, "choices": [{"index": 0, "delta": {"content": f"tok{i} "}, "finish_reason": None}]}))
                if opts.cut_at:
                    self.close_connection = True
                    return
                # vLLM's create_streaming_error_response body, the same object its HTTP 500 carries
                self.send_chunk(json.dumps({"error": {"message": "EngineCore encountered an issue.", "type": "InternalServerError", "param": None, "code": 500}}))
                self.send_chunk("[DONE]")
                if not opts.close:
                    self.wfile.write(b"0\r\n\r\n")
                return
            for i in range(n):
                if i:
                    time.sleep(opts.itl)
                fin = "length" if i == n - 1 else None
                key = "reasoning" if i < opts.reasoning else "content"
                self.send_chunk(json.dumps({**base, "choices": [{"index": 0, "delta": {key: f"tok{i} "}, "finish_reason": fin}]}))
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
    p.add_argument("--reasoning", type=int, default=0, help="first N tokens in delta.reasoning (Ollama v0.40.5 thinking)")
    p.add_argument("--eos-at", type=int, default=0, help="stop after N tokens whatever max_tokens/ignore_eos ask")
    p.add_argument("--die-at", type=int, default=0, help="send N tokens, then an error chunk and [DONE] (engine death)")
    p.add_argument("--cut-at", type=int, default=0, help="send N tokens, then close the connection (server killed)")
    p.add_argument("--prefix", default="/v1,/v3", help="API roots served, comma list")
    opts = p.parse_args()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", opts.port), make_handler(opts))
    print(f"listening {srv.server_address[1]}", flush=True)
    sys.stdout.close()
    srv.serve_forever()


if __name__ == "__main__":
    main()

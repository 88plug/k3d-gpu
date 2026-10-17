"""Recording proxy: one hop between an agent and its OpenAI-compatible LLM server.

Every arm sends its LLM traffic through this same hop, so turn latency is measured
at one place for every engine. Per LLM request it appends one JSON line:

  ts_send, ttft_s, e2e_s   send time (proxy receipt), first byte of ANY delta
                           (content, reasoning or tool-call: a tool-only reply still
                           gets a TTFT), end of the response body
  path, status, error, stream, model
  last_user                text of the last user message (task attribution)
  tools                    names of the tools sent with the request
  tool_calls               [{"name", "arguments", "valid", "reason"}] returned
  tool_source              structured | content | none
  tool_valid               every call valid (agent_trace.validate_calls), or None
  usage, completion_tokens, prompt_tokens, cached_tokens

Other requests (GET /models, health) are forwarded and not logged. Requests beyond
--max-requests get HTTP 429 and a "capped" line: the hard request cap of a paid arm.

Python 3 standard library only.
"""
import http.client
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agent_trace import calls_from_content, validate_calls  # noqa: E402

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
LLM_SUFFIXES = ("/chat/completions", "/completions", "/responses")
TIMEOUT_S = 120.0


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def last_user(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            return _text(m.get("content"))
    if isinstance(body.get("prompt"), str):
        return body["prompt"]
    return None


def tool_names(tools):
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        out.append((fn or t).get("name") if isinstance(fn or t, dict) else None)
    return out


class Recorder:
    """Assembles one response (SSE or JSON) into the logged record."""

    def __init__(self, t0):
        self.t0 = t0
        self.t_first = None
        self.content = ""
        self.acc = {}
        self.usage = None
        self.buf = b""

    def _mark(self):
        if self.t_first is None:
            self.t_first = time.perf_counter()

    def _delta(self, choice):
        d = choice.get("delta") or {}
        got = False
        if d.get("content"):
            self.content += d["content"]
            got = True
        if d.get("reasoning_content") or d.get("reasoning"):
            got = True
        for tc in d.get("tool_calls") or []:
            slot = self.acc.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
            fn = tc.get("function") or {}
            slot["name"] += fn.get("name") or ""
            slot["arguments"] += fn.get("arguments") or ""
            got = True
        if choice.get("text"):  # /v1/completions stream
            self.content += choice["text"]
            got = True
        if got:
            self._mark()

    def feed_sse(self, data):
        """Feed raw stream bytes; call as they arrive so the TTFT stamp is exact."""
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("usage"):
                self.usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                self._delta(choice)

    def feed_json(self, raw):
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        self.usage = obj.get("usage") or self.usage
        for choice in obj.get("choices") or []:
            msg = choice.get("message") or {}
            self.content += _text(msg.get("content")) or choice.get("text") or ""
            for i, tc in enumerate(msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                self.acc[i] = {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}

    def calls(self):
        return [self.acc[k] for k in sorted(self.acc)]


def score_calls(calls, content, tools):
    """(calls with per-call validity, tool_source, all-valid or None)."""
    source = "structured" if calls else "none"
    if not calls:
        calls = calls_from_content(content)
        source = "content" if calls else "none"
    if not calls:
        return [], source, None
    out, allv = [], True
    for c in calls:
        ok, why = validate_calls([c], tools or []) if tools else (False, "no tools declared")
        allv = allv and ok
        out.append({"name": c.get("name"), "arguments": c.get("arguments"), "valid": ok, "reason": why})
    return out, source, allv


class Proxy:
    def __init__(self, upstream, log_path, max_requests=None, include_usage=True, api_key=None,
                 timeout=TIMEOUT_S):
        u = urllib.parse.urlsplit(upstream.rstrip("/"))
        self.scheme, self.netloc, self.base = u.scheme, u.netloc, u.path
        self.log_path = log_path
        self.max_requests = max_requests
        self.include_usage = include_usage
        self.api_key = api_key
        self.timeout = timeout
        self.lock = threading.Lock()
        self.count = 0
        self.tag = None

    def write(self, rec):
        rec["tag"] = self.tag
        with self.lock, open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def take_slot(self):
        with self.lock:
            if self.max_requests is not None and self.count >= self.max_requests:
                return False
            self.count += 1
            return True

    def connection(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.netloc, timeout=self.timeout)


def make_handler(px):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/_bench/stats":
                return self._send(200, {"requests": px.count, "tag": px.tag})
            self._forward(None)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if self.path == "/_bench/tag":
                px.tag = json.loads(raw or b"{}").get("tag")
                return self._send(200, {"tag": px.tag})
            self._forward(raw)

        def _forward(self, raw):
            path = self.path
            llm = raw is not None and urllib.parse.urlsplit(path).path.endswith(LLM_SUFFIXES)
            body = None
            if llm:
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = None
            rec = None
            if llm and isinstance(body, dict):
                if not px.take_slot():
                    px.write({"ts_send": time.time(), "path": path, "status": 429, "error": "capped",
                              "last_user": last_user(body)})
                    return self._send(429, {"error": {"message": "request cap reached"}})
                if px.include_usage and body.get("stream") and "stream_options" not in body:
                    body["stream_options"] = {"include_usage": True}
                    raw = json.dumps(body).encode()
                rec = {"ts_send": time.time(), "path": path, "stream": bool(body.get("stream")),
                       "model": body.get("model"), "last_user": last_user(body),
                       "tools": tool_names(body.get("tools"))}
            t0 = time.perf_counter()
            r = Recorder(t0)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            if px.api_key:
                headers["Authorization"] = f"Bearer {px.api_key}"
            conn = px.connection()
            st = {"status": None, "err": None, "logged": False}

            def finish():
                """Log the record once, before the client sees the end of the body."""
                if rec is None or st["logged"]:
                    return
                st["logged"] = True
                t_end = time.perf_counter()
                calls, source, allv = score_calls(r.calls(), r.content, (body or {}).get("tools"))
                u = r.usage or {}
                status = st["status"]
                rec.update({
                    "status": status,
                    "error": st["err"] or (None if status and status < 400 else f"HTTP {status}"),
                    "ttft_s": None if r.t_first is None else round(r.t_first - t0, 6),
                    "e2e_s": round(t_end - t0, 6),
                    "content": r.content[:4000], "tool_calls": calls, "tool_source": source, "tool_valid": allv,
                    "usage": r.usage, "prompt_tokens": u.get("prompt_tokens"),
                    "completion_tokens": u.get("completion_tokens"),
                    "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                })
                px.write(rec)

            try:
                conn.request(self.command, px.base + path, body=raw, headers=headers)
                resp = conn.getresponse()
                st["status"] = resp.status
                sse = "text/event-stream" in (resp.getheader("Content-Type") or "")
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                if sse:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                        if not chunk:
                            break
                        r.feed_sse(chunk)
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        self.wfile.flush()
                    finish()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    data = resp.read()
                    r.feed_json(data)
                    finish()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            except (OSError, http.client.HTTPException) as e:
                st["err"] = f"{type(e).__name__}: {e}"
                finish()
                if st["status"] is None:
                    try:
                        self._send(502, {"error": {"message": st["err"]}})
                    except OSError:
                        pass
            finally:
                conn.close()
                finish()

    return Handler


def serve(px, host="127.0.0.1", port=0):
    """Start the proxy in a daemon thread; returns the server (server_address has the port)."""
    srv = http.server.ThreadingHTTPServer((host, port), make_handler(px))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def read_log(path):
    """Records of a proxy log. Lines that are not JSON objects are skipped: `kubectl logs`
    of the in-cluster proxy interleaves its stderr messages, and a line still being
    written when the log is read is incomplete."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out

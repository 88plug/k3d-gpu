"""Node daemon: poll every energy counter on a fixed tick, keep fresh edges,
serve per-span bounds over HTTP for the collector-side processor.

    GET /v1/domains                      -> ["rapl.package-0", "nvidia.0", ...]
    GET /v1/bounds?domain=D&start=S&end=E  (unix seconds)
                                         -> {"lo": J, "hi": J, "measured_fraction": f}
                                            or 202 {"pending": true}

Raw counter edges are not served. Since CVE-2020-8694 (Platypus) Linux makes
RAPL energy_uj root-only; republishing its 10 ms edges would hand that signal
to every pod that can reach the port.

With --token-file every request needs "Authorization: Bearer <token>".

Run: python3 -m joule_ledger.daemon --listen 127.0.0.1:9810 --token-file /run/token
"""

import argparse
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .ledger import FreshCounter
from .sampler import Nvml, rapl_zones, read_rapl

KEEP_S = 600.0


class Ledger:
    def __init__(self, sysroot, latency, nvml_lib):
        self.lock = threading.Lock()
        self.readers, self.counters = {}, {}
        for name, (path, rng) in rapl_zones(sysroot).items():
            self.readers[name] = lambda p=path: read_rapl(p)
            self.counters[name] = FreshCounter(latency=latency, quantum=1e-6, wrap=rng / 1e6)
        nv = Nvml(nvml_lib)
        for i in range(len(nv.handles)):
            if nv.energy(i) is not None:
                self.readers[f"nvidia.{i}"] = lambda i=i: nv.energy(i)
                self.counters[f"nvidia.{i}"] = FreshCounter(latency=latency, quantum=1e-3)

    def tick(self):
        now = time.time()
        with self.lock:
            for name, read in self.readers.items():
                try:
                    v = read()
                except OSError:
                    continue
                if v is None:
                    continue
                c = self.counters[name]
                c.observe(now, v)
                c.trim(now - KEEP_S)


def make_server(ledger, listen, token=None):
    host, port = listen.rsplit(":", 1)
    want = f"Bearer {token}".encode() if token else None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if want is not None and not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), want):
                return self._send(401, {"error": "unauthorized"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with ledger.lock:
                if u.path == "/v1/domains":
                    return self._send(200, sorted(ledger.counters))
                c = ledger.counters.get(q.get("domain", ""))
                if c is None:
                    return self._send(404, {"error": "unknown domain"})
                try:
                    if u.path == "/v1/bounds":
                        b = c.bounds(float(q["start"]), float(q["end"]))
                        if b is None:
                            return self._send(202, {"pending": True})
                        lo, hi = b
                        return self._send(200, {"lo": lo, "hi": hi,
                                                "measured_fraction": lo / hi if hi > 0 else 1.0})
                except (KeyError, ValueError):
                    return self._send(400, {"error": "bad query"})
            return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, int(port)), H)


def read_token(path):
    if not path:
        return None
    with open(path) as f:
        tok = f.read().strip()
    if not tok:
        raise SystemExit(f"--token-file {path} is empty")
    return tok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sysroot", default="/")
    ap.add_argument("--tick", type=float, default=0.010)
    ap.add_argument("--latency", type=float, default=0.025,
                    help="bound on counter publish latency, s (measure it: live probe P2)")
    ap.add_argument("--nvml", default="libnvidia-ml.so.1")
    ap.add_argument("--listen", default="127.0.0.1:9810")
    ap.add_argument("--token-file", default="",
                    help="file holding a bearer token every request must present")
    args = ap.parse_args()
    ledger = Ledger(args.sysroot, args.latency, args.nvml)
    srv = make_server(ledger, args.listen, read_token(args.token_file))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    nxt = time.monotonic()
    while True:
        ledger.tick()
        nxt += args.tick
        time.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    main()

"""Node daemon: poll every energy counter on a fixed tick, keep fresh edges,
serve them over HTTP for the collector-side processor.

    GET /v1/domains                      -> ["rapl.package-0", "nvidia.0", ...]
    GET /v1/bounds?domain=D&start=S&end=E  (unix seconds)
                                         -> {"lo": J, "hi": J, "measured_fraction": f}
                                            or 202 {"pending": true}
    GET /v1/edges?domain=D&since=S       -> [[a, b, joules], ...]

Run: python3 -m joule_ledger.daemon --listen 0.0.0.0:9810
"""

import argparse
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


def serve(ledger, listen):
    host, port = listen.rsplit(":", 1)

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
                    if u.path == "/v1/edges":
                        since = float(q.get("since", 0))
                        return self._send(200, [[e.a, e.b, e.value] for e in c.edges if e.b >= since])
                except (KeyError, ValueError):
                    return self._send(400, {"error": "bad query"})
            return self._send(404, {"error": "not found"})

    ThreadingHTTPServer((host, int(port)), H).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sysroot", default="/")
    ap.add_argument("--tick", type=float, default=0.010)
    ap.add_argument("--latency", type=float, default=0.025,
                    help="bound on counter publish latency, s (measure it: live probe P2)")
    ap.add_argument("--nvml", default="libnvidia-ml.so.1")
    ap.add_argument("--listen", default="0.0.0.0:9810")
    args = ap.parse_args()
    ledger = Ledger(args.sysroot, args.latency, args.nvml)
    threading.Thread(target=serve, args=(ledger, args.listen), daemon=True).start()
    nxt = time.monotonic()
    while True:
        ledger.tick()
        nxt += args.tick
        time.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    main()

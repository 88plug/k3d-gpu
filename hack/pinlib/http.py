"""HTTP for the pin sources: retries, Retry-After, GitHub auth, recorded fixtures.

PINLIB_FIXTURES=<file or dir>  serve every request from recorded JSON instead of
                               the network: {"<url>": {"status": 200, "headers":
                               {...}, "json": ... | "body": "..."}}, or a list of
                               such records served in order (the last repeats); a
                               URL with no recording fails (tests stay offline)
PINLIB_RECORD=<file>           also write every response fetched to <file>, in
                               the same format (for making fixtures)
GH_TOKEN / GITHUB_TOKEN        sent to api.github.com only
PINLIB_HTTP_TIMEOUT, PINLIB_HTTP_TRIES
                               seconds per attempt (30) and attempts (4)
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.request

UA = "k3d-gpu-pins/1"
TIMEOUT = float(os.environ.get("PINLIB_HTTP_TIMEOUT") or 30)  # seconds per attempt
TRIES = int(os.environ.get("PINLIB_HTTP_TRIES") or 4)
_fixtures = None


class HTTPError(Exception):
    def __init__(self, url, status, headers=None, body=b""):
        super().__init__("HTTP %d from %s" % (status, url))
        self.url, self.status, self.headers, self.body = url, status, headers or {}, body


class Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status, self.headers, self.body = url, status, headers, body

    def json(self):
        return json.loads(self.body)

    def text(self):
        return self.body.decode("utf-8", "replace")


def _load_fixtures():
    global _fixtures
    if _fixtures is None:
        path, out = os.environ.get("PINLIB_FIXTURES"), {}
        files = []
        if path and os.path.isdir(path):
            files = [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.endswith(".json")]
        elif path:
            files = [path]
        for f in files:
            with open(f, encoding="utf-8") as fh:
                out.update(json.load(fh))
        _fixtures = out
    return _fixtures


_served = {}


def reset_fixtures():
    global _fixtures
    _fixtures = None
    _served.clear()


def _from_fixture(url):
    rec = _load_fixtures().get(url)
    if rec is None:
        raise HTTPError(url, 599, {}, b"no recorded response (PINLIB_FIXTURES)")
    if isinstance(rec, list):  # responses in order (a 401 challenge, then the answer); the last repeats
        n = _served.get(url, 0)
        _served[url] = n + 1
        rec = rec[min(n, len(rec) - 1)]
    body = rec["body"].encode() if "body" in rec else json.dumps(rec.get("json")).encode()
    headers = {k.lower(): v for k, v in rec.get("headers", {}).items()}
    if rec.get("status", 200) >= 400:
        raise HTTPError(url, rec["status"], headers, body)
    return Response(url, rec.get("status", 200), headers, body)


def _record(url, resp):
    path = os.environ.get("PINLIB_RECORD")
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    rec = {"status": resp.status, "headers": {k: v for k, v in resp.headers.items() if k in ("link", "www-authenticate", "docker-content-digest")}}
    try:
        rec["json"] = json.loads(resp.body)
    except ValueError:
        rec["body"] = resp.text()
    data[url] = rec
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def _github_token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def request(url, headers=None, method="GET", tries=None):
    """GET/HEAD url. Retries network errors, 429 and 5xx (honouring Retry-After,
    capped at 60 s); raises HTTPError for any other status >= 400."""
    if os.environ.get("PINLIB_FIXTURES"):
        return _from_fixture(url)
    h = {"User-Agent": UA}
    if url.startswith("https://api.github.com/"):
        h["Accept"] = "application/vnd.github+json"
        tok = _github_token()
        if tok:
            h["Authorization"] = "Bearer " + tok
    h.update(headers or {})
    delay = 3
    tries = tries or TRIES
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                resp = Response(url, r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
                _record(url, resp)
                return resp
        except urllib.error.HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            if (e.code == 429 or e.code >= 500) and attempt < tries - 1:
                wait = delay
                ra = hdrs.get("retry-after", "")
                if ra.isdigit():
                    wait = min(int(ra), 60)
                time.sleep(wait)
                delay *= 2
                continue
            raise HTTPError(url, e.code, hdrs, e.read() if hasattr(e, "read") else b"") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
            if attempt == tries - 1:
                raise HTTPError(url, 598, {}, str(e).encode()) from None
            time.sleep(delay)
            delay *= 2
    raise HTTPError(url, 598)


def get_json(url, headers=None):
    return request(url, headers).json()


def get_text(url, headers=None):
    return request(url, headers).text()

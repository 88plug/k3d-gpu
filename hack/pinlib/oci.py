"""OCI registries: tag lists and digests.

Digests go through scripts/images-lock's registry code (imported by path), so the
lock and the pins read a registry the same way. Tag lists use the same anonymous
pull-token flow, following the Link header across pages. A 429 or 5xx is retried
with backoff (Retry-After honoured) by pinlib.http.
"""

import os
import re
import time
import urllib.parse
import importlib.machinery
import importlib.util

from . import http

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_lock = None


def load_path(name, path):
    """A Python file (any name, no .py needed) as a module."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def images_lock():
    """scripts/images-lock as a module."""
    global _lock
    if _lock is None:
        _lock = load_path("images_lock", os.path.join(ROOT, "scripts", "images-lock"))
    return _lock


def split(name):
    """(registry host, repository path) of a normalized image name."""
    host, repo = name.split("/", 1)
    return images_lock().REGISTRY_HOSTS.get(host, host), repo


def normalize(ref):
    """(name, tag, digest) with docker.io names expanded (library/ for official images)."""
    return images_lock().parse_ref(ref)


_tokens = {}


def _auth_get(url, repo):
    """GET url with an anonymous bearer token when the registry asks for one."""
    headers = {}
    key = url.split("/v2/")[0] + " " + repo
    if key in _tokens:
        headers["Authorization"] = "Bearer " + _tokens[key]
    try:
        return http.request(url, headers)
    except http.HTTPError as e:
        challenge = e.headers.get("www-authenticate", "")
        if e.status != 401 or not challenge.lower().startswith("bearer "):
            raise
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        query = {k: params[k] for k in ("service", "scope") if k in params}
        query.setdefault("scope", "repository:%s:pull" % repo)
        body = http.get_json(params["realm"] + "?" + urllib.parse.urlencode(query))
        _tokens[key] = body.get("token") or body.get("access_token")
        headers["Authorization"] = "Bearer " + _tokens[key]
        return http.request(url, headers)


def tags(name, max_pages=200):
    """Every tag of an image name (registry tags/list, all pages)."""
    host, repo = split(name)
    base = "https://%s" % host
    url = "%s/v2/%s/tags/list?n=1000" % (base, repo)
    out = []
    for _ in range(max_pages):
        r = _auth_get(url, repo)
        out += r.json().get("tags") or []
        link = r.headers.get("link", "")
        m = re.search(r"<([^>]+)>\s*;\s*rel=\"?next\"?", link)
        if not m:
            return out
        nxt = m.group(1)
        url = nxt if nxt.startswith("http") else base + nxt
    raise ValueError("%s: more than %d pages of tags" % (name, max_pages))


def digest(name, tag):
    """The registry's manifest digest of name:tag (the index when there is one)."""
    if os.environ.get("PINLIB_FIXTURES"):
        host, repo = split(name)
        r = http.request("https://%s/v2/%s/manifests/%s" % (host, repo, tag), method="HEAD")
        d = r.headers.get("docker-content-digest", "")
        if not re.match(r"^sha256:[0-9a-f]{64}$", d):
            raise ValueError("%s:%s: no digest" % (name, tag))
        return d
    delay = 5
    for attempt in range(4):
        try:
            return images_lock().registry_digest(name, tag)
        except ValueError as e:
            if attempt < 3 and re.search(r"HTTP (429|5\d\d)", str(e)):
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise ValueError("%s:%s: no digest" % (name, tag))


def published(name, tag):
    """When name:tag was pushed (Docker Hub's tag_last_pushed), or None."""
    host, repo = split(name)
    if host != "registry-1.docker.io":
        return None
    try:
        doc = http.get_json("https://hub.docker.com/v2/repositories/%s/tags/%s" % (repo, urllib.parse.quote(tag)))
    except (http.HTTPError, ValueError):
        return None
    return doc.get("tag_last_pushed") or doc.get("last_updated")


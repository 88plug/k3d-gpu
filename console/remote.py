#!/usr/bin/env python3
"""remote.py: k3d-gpu against any OpenAI-compatible endpoint instead of a local model.

One resolver feeds every command (scripts/k3d-gpu calls this file):

  base URL  --remote URL > OPENAI_BASE_URL > OPENAI_API_BASE (legacy) > OPENAI_HOST (+ /v1)
  key       --api-key-file FILE > OPENAI_API_KEY        (never a command-line value)
  model     --model > OPENAI_MODEL > the only id GET /models lists

The base URL is normalized (normalize_url): a bare host gets /v1, a trailing
/chat/completions is dropped, userinfo, queries and plain http to a non-local
host are refused, so the key never crosses the network in clear text.

Requests go only to the configured host: redirects are not followed at all, so
the Authorization header can never be replayed to a host a 3xx names.

  remote.py check   [--max-tokens N] [--json]   GET /models + one tiny chat completion
  remote.py show                                what resolves, no network
  remote.py models                              GET /models, one id per line
  remote.py resolve [--probe]                   "base<TAB>model<TAB>origin" for the launcher ("-": no model)
  remote.py secret --namespace NS               apply Secret k3d-gpu-llm (kubectl, manifest on its stdin)
  remote.py chat-ui MANIFEST                    Open WebUI manifest reading that Secret
  remote.py sandbox-profile MANIFEST [--addrs]  sandbox profile whose egress is only the endpoint
  remote.py addrs                               the endpoint host's addresses (sandbox drift check)
  remote.py exec -- CMD...                      run CMD with OPENAI_API_KEY in its environment

K3D_GPU_REMOTE_ADDRS=a,b pins the host's addresses for sandbox-profile and addrs
instead of resolving it.

Every subcommand takes --remote URL, --model M and --api-key-file FILE.
Exit codes: 0 ok, 2 configuration or usage, 3 key rejected, 4 endpoint error.
Python 3 standard library only.
"""

import argparse
import base64
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SECRET = "k3d-gpu-llm"
MAX_TOKENS_CAP = 64
TEMPERATURE = 0.6  # never 0: shared fleets serve sampling, and greedy is a different code path
LOCAL_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local")
EXIT_CONFIG, EXIT_AUTH, EXIT_ENDPOINT = 2, 3, 4


class RemoteError(Exception):
    def __init__(self, msg, code=EXIT_CONFIG):
        super().__init__(msg)
        self.code = code


# ---- resolution ---------------------------------------------------------------

def is_local_host(host):
    host = (host or "").lower().strip("[]")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_url(raw):
    """(normalized base URL, warnings). Raises RemoteError on a URL a key must not go to."""
    raw = (raw or "").strip()
    if not raw:
        raise RemoteError("empty endpoint URL")
    u = urllib.parse.urlsplit(raw)
    scheme = u.scheme.lower()
    if scheme not in ("http", "https") or not raw.lower().startswith(scheme + "://"):
        raise RemoteError(f"endpoint must be an http(s):// URL, got {raw!r}")
    if u.username is not None or u.password is not None:
        raise RemoteError("endpoint URL must not carry user:password@; the key goes in OPENAI_API_KEY")
    if u.query or u.fragment:
        raise RemoteError(f"endpoint URL must not have a query or fragment: {raw!r}")
    if not u.hostname:
        raise RemoteError(f"endpoint URL has no host: {raw!r}")
    try:
        port = u.port
    except ValueError:
        raise RemoteError(f"endpoint URL has a bad port: {raw!r}")
    if scheme == "http" and not is_local_host(u.hostname):
        raise RemoteError(f"plain http to {u.hostname} would send the key in clear text; use https "
                          "(http is accepted for loopback and in-cluster *.svc hosts only)")
    host = u.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port else "")
    path = u.path.rstrip("/")
    warnings = []
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
        warnings.append("dropped /chat/completions from the endpoint: it takes the API root (…/v1)")
    if not path:
        path = "/v1"
    return f"{scheme}://{netloc}{path}", warnings


def resolve_base(flag, env):
    """(base URL, origin) by precedence --remote > OPENAI_BASE_URL > OPENAI_API_BASE > OPENAI_HOST."""
    for origin, value in (("--remote", flag), ("OPENAI_BASE_URL", env.get("OPENAI_BASE_URL")),
                          ("OPENAI_API_BASE", env.get("OPENAI_API_BASE")),
                          ("OPENAI_HOST", env.get("OPENAI_HOST"))):
        if value and value.strip():
            return normalize_url(value)[0], origin
    raise RemoteError("no remote endpoint: pass --remote URL or set OPENAI_BASE_URL "
                      "(or OPENAI_API_BASE, or OPENAI_HOST without /v1)")


def load_key(path, env):
    """(key, source). A key file is either the bare key or KEY=VALUE lines with
    OPENAI_API_KEY; it is parsed, never sourced, and must not be readable by others."""
    if path:
        try:
            st = os.stat(path)
        except OSError as e:
            raise RemoteError(f"--api-key-file {path}: {e.strerror}")
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RemoteError(f"--api-key-file {path} is readable by others (mode {stat.S_IMODE(st.st_mode):04o}); "
                              f"chmod 0600 {path}")
        with open(path) as f:
            text = f.read()
        key = None
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        for ln in lines:
            name, sep, value = ln.partition("=")
            if sep and name.strip().removeprefix("export ").strip() == "OPENAI_API_KEY":
                key = value.strip().strip("'\"")
        if key is None and len(lines) == 1 and "=" not in lines[0]:
            key = lines[0]
        if not key:
            raise RemoteError(f"--api-key-file {path} holds no key (a bare key, or an OPENAI_API_KEY= line)")
        return key, path
    key = env.get("OPENAI_API_KEY")
    if key and key.strip():
        return key.strip(), "OPENAI_API_KEY"
    return None, None


def mask(key):
    if not key:
        return "none"
    if len(key) < 12:
        return "…"
    return ("sk-…" if key.startswith("sk-") else "…") + key[-4:]


class Endpoint:
    def __init__(self, a, env, key="need"):
        """key: "need" (a remote host must have one), "load" (read it if set), or
        "none" (never read: commands that only render manifests or resolve names)."""
        self.base, self.origin = resolve_base(a.remote, env)
        self.host = urllib.parse.urlsplit(self.base).hostname
        self.port = urllib.parse.urlsplit(self.base).port or (443 if self.base.startswith("https") else 80)
        self.key, self.key_source = load_key(a.api_key_file, env) if key != "none" else (None, None)
        if key == "need" and not self.key and not is_local_host(self.host):
            raise RemoteError(f"remote endpoint {self.base} needs a key: set OPENAI_API_KEY or pass --api-key-file FILE")
        self.model = a.model or (env.get("OPENAI_MODEL") or "").strip() or None
        self.model_source = "--model" if a.model else ("OPENAI_MODEL" if self.model else None)
        handlers = [_NoRedirect(self.host)]
        if is_local_host(self.host):
            # Plain http to loopback or *.svc: never through an HTTP_PROXY, which
            # would see the Authorization header in clear text.
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)
        # K3D_GPU_REMOTE_ADDRS pins the host's addresses (sandbox egress) instead of DNS.
        self.pinned_addrs = (env.get("K3D_GPU_REMOTE_ADDRS") or "").strip() or None

    def scrub(self, text):
        return text.replace(self.key, mask(self.key)) if self.key else text

    def call(self, method, path, body=None, timeout=60):
        """(HTTP status, parsed JSON or None, seconds). Raises RemoteError when unreachable."""
        url = self.base + path
        assert urllib.parse.urlsplit(url).hostname == self.host
        req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        t0 = time.monotonic()
        try:
            with self._opener.open(req, timeout=timeout) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        except RemoteError:
            raise
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise RemoteError(self.scrub(f"{self.host}: unreachable ({reason})"), EXIT_ENDPOINT)
        dt = time.monotonic() - t0
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"_text": self.scrub(raw[:200].decode("utf-8", "replace"))}
        return status, data, dt

    def models(self):
        status, data, dt = self.call("GET", "/models", timeout=30)
        if status in (401, 403):
            raise RemoteError(f"{self.host} rejected the key (HTTP {status} on GET /models); check the key from "
                              f"{self.key_source or 'OPENAI_API_KEY'} ({mask(self.key)})", EXIT_AUTH)
        if status != 200:
            raise RemoteError(f"GET {self.base}/models: HTTP {status} {_detail(data)}", EXIT_ENDPOINT)
        ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        return ids, dt

    def pick_model(self, ids, warn):
        if self.model:
            if self.model not in ids:
                warn(f"model {self.model} is not listed by {self.host} (it lists: {', '.join(ids) or 'nothing'}); "
                     "trying it anyway, some servers route aliases they do not list")
            return self.model, self.model_source
        if len(ids) == 1:
            return ids[0], "auto: the only model served"
        if not ids:
            raise RemoteError(f"{self.host} lists no model on GET /models; pass --model", EXIT_ENDPOINT)
        raise RemoteError(f"{self.host} serves several models: {', '.join(ids)}; pick one with --model or OPENAI_MODEL")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        to = urllib.parse.urlsplit(newurl).hostname or "?"
        raise RemoteError(f"{self.host} answered HTTP {code} with a redirect to {to}; not followed "
                          f"(the key is only ever sent to {self.host}): fix the endpoint URL", EXIT_ENDPOINT)


def _detail(data):
    if isinstance(data, dict):
        e = data.get("error")
        if isinstance(e, dict):
            return str(e.get("message", ""))[:200]
        return str(e or data.get("_text") or data.get("detail") or "")[:200]
    return ""


# ---- manifests ----------------------------------------------------------------

def q(s):
    return json.dumps(s)


def b64(s):
    return base64.b64encode(s.encode()).decode()


def secret_manifest(ep, namespace, model):
    """Secret k3d-gpu-llm. Keys are env-var names, so `envFrom: secretRef` works.
    The key is base64 data only; base URL and model are annotations too, so tools
    can report them without reading data."""
    return (f"apiVersion: v1\nkind: Secret\nmetadata:\n  name: {SECRET}\n  namespace: {namespace}\n"
            f"  labels:\n    app.kubernetes.io/managed-by: k3d-gpu\n"
            f"  annotations:\n    k3d-gpu.io/base-url: {q(ep.base)}\n    k3d-gpu.io/model: {q(model)}\n"
            f"type: Opaque\ndata:\n"
            f"  OPENAI_BASE_URL: {b64(ep.base)}\n"
            f"  OPENAI_API_KEY: {b64(ep.key or 'none')}\n"
            f"  OPENAI_MODEL: {b64(model)}\n")


def _sub1(pattern, repl, text, what):
    new, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    if n != 1:
        raise RemoteError(f"manifest has no {what} to rewrite (is it k3d-gpu's?)")
    return new


def render_chat_ui(text, base, model):
    """Open WebUI reading the endpoint and key from Secret k3d-gpu-llm. Its env names
    are OPENAI_API_BASE_URL and OPENAI_API_KEY (open-webui backend/open_webui/config.py,
    v0.12.2). ENABLE_PERSISTENT_CONFIG=False: otherwise a connection saved in the data
    volume wins over the env on the next start."""
    def ref(name, key):
        return (f"\\1- name: {name}\n\\1  valueFrom:\n"
                f"\\1    secretKeyRef: {{name: {SECRET}, key: {key}}}\n")
    text = _sub1(r"^(\s*)- name: OPENAI_API_BASE_URL\n\s*value: .*\n", ref("OPENAI_API_BASE_URL", "OPENAI_BASE_URL"),
                 text, "OPENAI_API_BASE_URL")
    text = re.sub(r"^\s*# OVMS runs without an API key.*\n", "", text, flags=re.M)
    text = _sub1(r"^(\s*)- name: OPENAI_API_KEY\n\s*value: .*\n", ref("OPENAI_API_KEY", "OPENAI_API_KEY"),
                 text, "OPENAI_API_KEY")
    text = _sub1(r"^(\s*)- name: DEFAULT_MODELS\n\s*value: .*\n",
                 f"\\1- name: DEFAULT_MODELS\n\\1  value: {q(model)}\n"
                 "\\1- name: ENABLE_PERSISTENT_CONFIG\n\\1  value: \"False\"\n", text, "DEFAULT_MODELS")
    return (f"# Rendered by `k3d-gpu quickstart --remote`: chat against {base}\n"
            f"# (model {model}); endpoint and key come from Secret {SECRET}.\n" + text)


def endpoint_addrs(ep, pinned=None):
    """Sorted addresses of the endpoint host (IPv4 first). Each must be a public address."""
    if pinned:
        addrs = [a.strip() for a in pinned.split(",") if a.strip()]
    else:
        try:
            infos = socket.getaddrinfo(ep.host, ep.port, proto=socket.IPPROTO_TCP)
        except OSError as e:
            raise RemoteError(f"cannot resolve {ep.host}: {e}", EXIT_ENDPOINT)
        addrs = [i[4][0] for i in infos]
    out = set()
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            raise RemoteError(f"--addrs: {a!r} is not an IP address")
        if not ip.is_global:
            raise RemoteError(f"{ep.host} resolves to {ip}, not a public address; the sandbox egress pin is for "
                              "public endpoints (in-cluster endpoints need no egress rule)")
        out.add(ip)
    if not out:
        raise RemoteError(f"{ep.host} resolves to no address", EXIT_ENDPOINT)
    return [str(i) for i in sorted(out, key=lambda i: (i.version, i))]


def render_sandbox(text, host, port, addrs):
    """The sandbox profile with egress to the endpoint only: one rule, a /32 (/128)
    per address of its host, on its port, TCP. No DNS egress: hostAliases pin the
    name to the same addresses, so TLS SNI and Host stay the endpoint's."""
    cidrs = "".join(f"      - ipBlock:\n          cidr: {a}/{32 if ':' not in a else 128}\n" for a in addrs)
    egress = (f"    egress:\n    - to:\n{cidrs}"
              f"      ports:\n      - port: {port}\n        protocol: TCP\n")
    start, end = text.find("\n    egress:\n"), text.find("\n  podTemplate:")
    if start < 0 or end < start:
        raise RemoteError("manifest has no egress block before podTemplate to rewrite (is it k3d-gpu's?)")
    text = text[: start + 1] + egress + text[end + 1:]
    aliases = "".join(f"      - ip: {q(a)}\n        hostnames: [{host}]\n" for a in addrs)
    text = _sub1(r"^(      automountServiceAccountToken: false\n)", f"\\1      hostAliases:\n{aliases}", text,
                 "podTemplate spec")
    text = _sub1(r"^(        env:\n        - name: HOME\n          value: /tmp\n)",
                 f"\\1        envFrom:\n        - secretRef:\n            name: {SECRET}\n", text, "container env")
    text = _sub1(r"^(kind: SandboxTemplate\nmetadata:\n  name: .*\n  namespace: .*\n)",
                 f"\\1  annotations:\n    k3d-gpu.io/remote-host: {q(host)}\n"
                 f"    k3d-gpu.io/remote-addrs: {q(','.join(addrs))}\n", text, "SandboxTemplate metadata")
    return (f"# Rendered by `k3d-gpu sandbox up --remote`: egress is {host}:{port} only "
            f"({', '.join(addrs)}), no DNS.\n" + text)


# ---- commands -----------------------------------------------------------------

def out(*a):
    print(*a, flush=True)


def warn(msg):
    print(f"remote: warn: {msg}", file=sys.stderr, flush=True)


def cmd_check(ep, a):
    if not 1 <= a.max_tokens <= MAX_TOKENS_CAP:
        raise RemoteError(f"--max-tokens must be 1..{MAX_TOKENS_CAP}")
    rep = {"endpoint": ep.base, "origin": ep.origin, "host": ep.host, "key": mask(ep.key),
           "key_source": ep.key_source, "auth": None, "models_latency_s": None, "models": [],
           "model": None, "model_source": None, "chat": None, "warnings": []}

    def note(msg):
        rep["warnings"].append(msg)

    def show():
        if a.json:
            out(json.dumps(rep, indent=2))
            return
        out(f"endpoint  {ep.base} (from {ep.origin})")
        out(f"host      {ep.host}")
        out(f"key       {mask(ep.key)}" + (f" (from {ep.key_source})" if ep.key_source else ""))
        if rep["auth"]:
            lat = rep["models_latency_s"]
            out(f"auth      {rep['auth']}" + (f" (GET /models in {lat:.2f} s)" if lat is not None else ""))
        if rep["models"]:
            out(f"models    {', '.join(rep['models'])}")
        if rep["model"]:
            out(f"model     {rep['model']} ({rep['model_source']})")
        c = rep["chat"]
        if c:
            line = (f"chat      {c['verdict']}: HTTP {c['status']} in {c['latency_s']:.2f} s, "
                    f"finish_reason={c['finish_reason']}, {c['completion_tokens']} completion tokens")
            out(line + (f", content {c['content']!r}" if c["content"] else ", content empty"))
        for w in rep["warnings"]:
            out(f"warn      {w}")

    try:
        ids, dt = ep.models()
    except RemoteError as e:
        rep["auth"] = "rejected" if e.code == EXIT_AUTH else "unknown"
        show()
        raise
    rep.update(auth="ok", models_latency_s=round(dt, 3), models=ids)
    try:
        rep["model"], rep["model_source"] = ep.pick_model(ids, note)
    except RemoteError:
        show()
        raise
    status, data, dt = ep.call("POST", "/chat/completions", {
        "model": rep["model"], "max_tokens": a.max_tokens, "temperature": TEMPERATURE,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}]})
    c = {"status": status, "latency_s": round(dt, 3), "finish_reason": None, "completion_tokens": None,
         "content": None, "reasoning": False, "verdict": "fail"}
    rep["chat"] = c
    choice = ((data.get("choices") or [None])[0] or {}) if isinstance(data, dict) else {}
    if status == 200 and choice:
        msg = choice.get("message") or {}
        c.update(finish_reason=choice.get("finish_reason"),
                 completion_tokens=(data.get("usage") or {}).get("completion_tokens"),
                 content=(msg.get("content") or "").strip()[:80] or None,
                 reasoning=bool(msg.get("reasoning") or msg.get("reasoning_content")))
        if c["content"]:
            c["verdict"] = "ok"
        else:
            c["verdict"] = "warn"
            note(f"the reply is empty: finish_reason={c['finish_reason']} after {c['completion_tokens']} tokens"
                 + (", all reasoning (the model thinks before it answers); raise --max-tokens up to "
                    f"{MAX_TOKENS_CAP}" if c["reasoning"] else ""))
        show()
        return 0
    show()
    code = EXIT_AUTH if status in (401, 403) else EXIT_ENDPOINT
    raise RemoteError(f"POST {ep.base}/chat/completions: HTTP {status} {_detail(data)}", code)


def cmd_show(ep, a):
    out(f"endpoint  {ep.base} (from {ep.origin})")
    out(f"host      {ep.host}")
    out(f"key       {mask(ep.key)}" + (f" (from {ep.key_source})" if ep.key_source else ""))
    out(f"model     {ep.model or 'auto (the only id GET /models lists)'}"
        + (f" (from {ep.model_source})" if ep.model_source else ""))
    return 0


def cmd_models(ep, a):
    ids, _ = ep.models()
    for i in ids:
        out(i)
    return 0


def cmd_resolve(ep, a):
    model, origin = ep.model, ep.model_source
    if a.probe:
        ids, _ = ep.models()
        model, origin = ep.pick_model(ids, warn)
    out(f"{ep.base}\t{model or '-'}\t{ep.origin}")  # "-": no model yet (tabs would collapse an empty field)
    return 0


def need_model(ep, a):
    if getattr(a, "probe", False):
        ids, _ = ep.models()
        return ep.pick_model(ids, warn)[0]
    if not ep.model:
        raise RemoteError("no model: pass --model (or OPENAI_MODEL), or --probe to ask GET /models")
    return ep.model


KUBECTL_APPLY = ["kubectl", "apply", "--server-side", "--force-conflicts", "--field-manager=k3d-gpu", "-f", "-"]


def cmd_secret(ep, a):
    """Applies Secret k3d-gpu-llm: the manifest goes to kubectl on its stdin and
    server-side, so no last-applied-configuration annotation (which `kubectl
    describe` prints) copies the key into the object's metadata."""
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?", a.namespace or ""):
        raise RemoteError(f"--namespace {a.namespace!r} is not a namespace name")
    manifest = secret_manifest(ep, a.namespace, need_model(ep, a))
    try:
        p = subprocess.run(KUBECTL_APPLY, input=manifest, text=True, stdout=subprocess.DEVNULL)
    except OSError as e:
        raise RemoteError(f"kubectl: {e.strerror}")
    if p.returncode != 0:
        raise RemoteError(f"kubectl apply of Secret {a.namespace}/{SECRET} failed (exit {p.returncode})")
    return 0


def read_manifest(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        raise RemoteError(f"{path}: {e.strerror}")


def cmd_chat_ui(ep, a):
    sys.stdout.write(render_chat_ui(read_manifest(a.manifest), ep.base, need_model(ep, a)))
    return 0


def cmd_sandbox_profile(ep, a):
    if ep.base.startswith("http://"):
        raise RemoteError("the sandbox egress pin is for https endpoints; an in-cluster endpoint needs no egress rule")
    addrs = endpoint_addrs(ep, a.addrs or ep.pinned_addrs)
    sys.stdout.write(render_sandbox(read_manifest(a.manifest), ep.host, ep.port, addrs))
    return 0


def cmd_addrs(ep, a):
    out(",".join(endpoint_addrs(ep, a.addrs or ep.pinned_addrs)))
    return 0


def cmd_exec(ep, a):
    """Runs a command with the resolved key as OPENAI_API_KEY in its environment
    (capacity, bench --vs remote): the key never passes through stdout or argv."""
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not argv:
        raise RemoteError("exec needs a command after --")
    if not ep.key:
        raise RemoteError("no key configured")
    env = dict(os.environ, OPENAI_API_KEY=ep.key)
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as e:
        raise RemoteError(f"{argv[0]}: {e.strerror}")


COMMANDS = {
    "check": (cmd_check, "need"), "show": (cmd_show, "load"), "models": (cmd_models, "need"),
    "resolve": (cmd_resolve, "need"), "secret": (cmd_secret, "need"), "chat-ui": (cmd_chat_ui, "none"),
    "sandbox-profile": (cmd_sandbox_profile, "none"), "addrs": (cmd_addrs, "none"), "exec": (cmd_exec, "need"),
}


def parse(argv):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--remote", metavar="URL", help="endpoint base URL (overrides OPENAI_BASE_URL)")
    common.add_argument("--model", help="model id (overrides OPENAI_MODEL)")
    common.add_argument("--api-key-file", metavar="FILE", help="file with the key (overrides OPENAI_API_KEY)")
    ap = argparse.ArgumentParser(prog="k3d-gpu remote", description="Any OpenAI-compatible endpoint.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", parents=[common], help="GET /models plus one tiny chat completion")
    p.add_argument("--max-tokens", type=int, default=16)
    p.add_argument("--json", action="store_true")
    sub.add_parser("show", parents=[common], help="what resolves, no network")
    sub.add_parser("models", parents=[common], help="GET /models")
    p = sub.add_parser("resolve", parents=[common])
    p.add_argument("--probe", action="store_true", help="GET /models: check the key and pick the model")
    p = sub.add_parser("secret", parents=[common])
    p.add_argument("--namespace", required=True)
    p.add_argument("--probe", action="store_true")
    p = sub.add_parser("chat-ui", parents=[common])
    p.add_argument("manifest")
    p = sub.add_parser("sandbox-profile", parents=[common])
    p.add_argument("manifest")
    p.add_argument("--addrs", help="comma-separated addresses instead of resolving the host")
    p = sub.add_parser("addrs", parents=[common])
    p.add_argument("--addrs", help=argparse.SUPPRESS)
    p = sub.add_parser("exec", parents=[common], help="run a command with OPENAI_API_KEY set")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    return ap.parse_args(argv)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    try:
        a = parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_CONFIG
    fn, key = COMMANDS[a.cmd]
    try:
        return fn(Endpoint(a, env, key), a)
    except RemoteError as e:
        print(f"remote: {e}", file=sys.stderr, flush=True)
        return e.code


if __name__ == "__main__":
    sys.exit(main())

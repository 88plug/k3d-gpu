"""Where hack/vllm-recipes reads its inputs: the vLLM versions this tree pins, and the
pinned recipes pair and vLLM tags fetched with git (sparse, partial, depth 1).

VLLM_RECIPES_SRC=<dir> replaces the network with a local tree of the same shape
(tests): <dir>/site (the recipes repo files), <dir>/recipes (vLLM's recipes/) and
<dir>/vllm/v<version> (each tag's sources, or its registries plus flags.txt).
"""

import ast
import os
import re
import subprocess

from .artifact import RECIPES_REPO, VLLM_REPO, GenError, read_lock

SITE_FILES = ("taxonomy.yaml", "sources.lock.json", "LICENSE", "strategies/single_node_tp.yaml")
VLLM_FILES = ("vllm/tool_parsers/__init__.py", "vllm/reasoning/__init__.py", "vllm/engine/arg_utils.py")
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise GenError("versions: %s" % e) from None


def _one(text, rx, what):
    m = re.search(rx, text, re.M)
    if not m:
        raise GenError("versions: %s not found" % what)
    return m.group(1)


def versions(root):
    """The vLLM versions k3d-gpu pins for a vLLM consumer, sorted: the agent's images
    (console/plan.py AGENT_VLLM_VERSION, the llm-scaler one), the AMX test image
    (scripts/k3d-gpu, the vllm-cpu canary engine) and the EAT overlay's vLLM CPU tag
    (integrations/eat/vllm-values.yaml). canary/engines.toml's derived checks prove
    each constant against its image."""
    plan = _read(root, "console/plan.py")
    found = set()
    for n in ast.parse(plan).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "AGENT_VLLM_VERSION"
                                             for t in n.targets):
            found.update(ast.literal_eval(n.value).values())
    if not found:
        raise GenError("versions: console/plan.py has no AGENT_VLLM_VERSION")
    found.add(_one(plan, r'"([0-9]+\.[0-9]+\.[0-9]+)" if backend == "llm-scaler"',
                   "console/plan.py llm-scaler version"))
    found.add(_one(_read(root, "scripts/k3d-gpu"),
                   r'^AMX_TEST_IMAGE="\$\{K3D_GPU_AMX_TEST_IMAGE:-vllm/vllm-openai-cpu:v([0-9]+\.[0-9]+\.[0-9]+)@',
                   "scripts/k3d-gpu AMX_TEST_IMAGE"))
    found.add(_one(_read(root, "integrations/eat/vllm-values.yaml"),
                   r'^  repository: vllm/vllm-openai-cpu\n  tag: "v([0-9]+\.[0-9]+\.[0-9]+)@',
                   "integrations/eat/vllm-values.yaml image.tag"))
    bad = [v for v in found if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(v))]
    if bad:
        raise GenError("versions: not a vLLM release: %s" % bad)
    return sorted(found, key=lambda v: [int(x) for x in v.split(".")])


def _git(cwd, *args):
    try:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, **GIT_ENV)).stdout
    except subprocess.CalledProcessError as e:
        raise GenError("git %s: %s" % (" ".join(args[:3]), (e.stderr or "").strip()[-300:])) from None


def _sparse(dest, repo, ref, paths):
    """dest holds `paths` of github.com/<repo> at ref (a commit or refs/tags/<t>); returns
    the commit id."""
    os.makedirs(dest, exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "fetch", "-q", "--depth=1", "--filter=blob:none", "https://github.com/%s" % repo, ref)
    _git(dest, "checkout", "-q", "FETCH_HEAD", "--", *paths)
    return _git(dest, "rev-parse", "FETCH_HEAD").strip()


def fetch(dest, recipes_commit, vers):
    """(recipes dir, site dir, vLLM sources dir) for a generate() run, fetched under dest."""
    local = os.environ.get("VLLM_RECIPES_SRC")
    if local:
        return os.path.join(local, "recipes"), os.path.join(local, "site"), os.path.join(local, "vllm")
    site = os.path.join(dest, "site")
    _sparse(site, RECIPES_REPO, recipes_commit, SITE_FILES)
    vllm_commit, tree = read_lock(site)
    vtree = os.path.join(dest, "vllm-recipes")
    _sparse(vtree, VLLM_REPO, vllm_commit, ["recipes", "LICENSE"])
    got = _git(vtree, "rev-parse", "FETCH_HEAD:recipes").strip()
    if got != tree:
        raise GenError("%s@%s recipes/ is tree %s; sources.lock.json locks %s" % (VLLM_REPO, vllm_commit, got, tree))
    with open(os.path.join(vtree, "LICENSE"), encoding="utf-8") as f:
        if "Apache License" not in f.read(2000):
            raise GenError("%s@%s LICENSE is not the Apache License" % (VLLM_REPO, vllm_commit))
    for v in vers:
        vdir = os.path.join(dest, "vllm", "v" + v)
        os.makedirs(vdir)
        _git(vdir, "init", "-q")
        _git(vdir, "fetch", "-q", "--depth=1", "--filter=blob:none", "https://github.com/%s" % VLLM_REPO,
             "refs/tags/v%s" % v)
        clis = [p for p in _git(vdir, "ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", "vllm/entrypoints").split()
                if p.endswith("/cli_args.py")]
        _git(vdir, "checkout", "-q", "FETCH_HEAD", "--", *VLLM_FILES, *clis)
    return os.path.join(vtree, "recipes"), site, os.path.join(dest, "vllm")


def latest_recipes_commit():
    """vllm-project/recipes main, through pinlib's HTTP client (GH_TOKEN, PINLIB_FIXTURES)."""
    from pinlib import http  # hack/pinlib, imported here only: generate needs no network code
    return http.get_json("https://api.github.com/repos/%s/commits/main" % RECIPES_REPO)["sha"]

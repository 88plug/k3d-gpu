"""BFCL v4 (bfcl-eval 2025.12.17) on a fixed 100-id subset, S100 (stdlib only).

Two modes, both pinned to bfcl-eval==2025.12.17 (gorilla f7cf7359):

  board      registry "Qwen/Qwen3-4B-Instruct-2507-FC", the handler behind the
             leaderboard row (QwenFCHandler): BFCL applies the chat template itself
             and calls /v1/completions at LOCAL_SERVER_ENDPOINT:LOCAL_SERVER_PORT.
             The server's tool parser is NOT exercised. Calibration (H40).
  server-fc  registry "k3d-gpu/server-fc", registered at launch (bfcl_launch.py)
             with BFCL's own OpenAICompletionsHandler, is_fc_model=True and
             underscore_to_dot=True (that handler renames '.' to '_' in tool names,
             so the checker must map them back). /v1/chat/completions with `tools`
             at OPENAI_BASE_URL: the server's tool parser IS exercised (H41).

The run goes in chunks of CHUNK ids in S100's seeded run order, so an arm cut by
its budget has completed a prefix of the run order. A rerun skips ids already in
the result files (resumable). Scoring uses BFCL's own `evaluate --partial-eval`
and reads per-id pass/fail back from its score files.
"""
import json
import os
import random
import subprocess
import sys
import time

VERSION = "2025.12.17"
WHEEL_SHA256 = "8555bc9407a56682ceb7d969e87eb724f6b679deb0ef05114d9c6e786406b103"
GORILLA = "f7cf7359b7ac615a0b294831c5ba2bc95ee4a000"
STRATA = {
    "simple_python": 5, "simple_java": 5, "simple_javascript": 5,
    "multiple": 15, "parallel": 15, "parallel_multiple": 15, "irrelevance": 15,
    "live_simple": 15, "live_multiple": 10,
}
SEED = 40
CHUNK = 10
REGISTRY = {"board": "Qwen/Qwen3-4B-Instruct-2507-FC", "server-fc": "k3d-gpu/server-fc"}
# Leaderboard row "Qwen3-4B-Instruct-2507 (FC)", https://gorilla.cs.berkeley.edu/data_overall.csv
# read 2026-10-17. Non-Live Simple is the board's one simple column for all three languages.
BOARD = {
    "simple": 75.50, "multiple": 93.50, "parallel": 92.50, "parallel_multiple": 90.00,
    "irrelevance": 84.93, "live_simple": 79.07, "live_multiple": 76.16,
}
GROUP = {"simple_python": "simple", "simple_java": "simple", "simple_javascript": "simple"}
HERE = os.path.dirname(os.path.abspath(__file__))
S100_PATH = os.path.join(HERE, "s100.json")


def category_of(test_id):
    """'live_simple_3-2-0' -> 'live_simple'; 'simple_python_12' -> 'simple_python'."""
    return test_id.rsplit("_", 1)[0]


def build_s100(ids_by_category, seed=SEED):
    """Stratified pick (seeded, per category in STRATA order) and a seeded run order."""
    rng = random.Random(seed)
    picked = {}
    for cat, k in STRATA.items():
        pool = sorted(ids_by_category[cat], key=_natural)
        if len(pool) < k:
            raise ValueError(f"{cat}: {len(pool)} ids, need {k}")
        picked[cat] = sorted(rng.sample(pool, k), key=_natural)
    order = [i for cat in STRATA for i in picked[cat]]
    rng.shuffle(order)
    return {"bfcl_eval": VERSION, "gorilla": GORILLA, "seed": seed, "strata": STRATA,
            "ids_by_category": picked, "run_order": order}


def _natural(test_id):
    tail = test_id.rsplit("_", 1)[1]
    return [int(x) if x.isdigit() else x for x in tail.replace("-", " ").split()]


def dataset_ids(data_dir):
    """{category: [ids]} and {file: sha256} from BFCL's data directory."""
    import hashlib
    ids, sums = {}, {}
    for cat in STRATA:
        path = os.path.join(data_dir, f"BFCL_v4_{cat}.json")
        with open(path, "rb") as f:
            raw = f.read()
        sums[os.path.basename(path)] = hashlib.sha256(raw).hexdigest()
        ids[cat] = [json.loads(line)["id"] for line in raw.decode().splitlines() if line.strip()]
    return ids, sums


def load_s100(path=S100_PATH):
    with open(path) as f:
        return json.load(f)


def expected(s100=None):
    """Board-weighted expected accuracy (percent) on S100."""
    s100 = s100 or load_s100()
    tot = sum(STRATA.values())
    return sum(STRATA[c] * BOARD[GROUP.get(c, c)] for c in STRATA) / tot


def ids_file(ids):
    """Content of test_case_ids_to_generate.json for these ids."""
    out = {}
    for i in ids:
        out.setdefault(category_of(i), []).append(i)
    return out


def env_for(mode, proxy_url, served_model=None, root=None):
    """Environment for a bfcl subprocess pointed at the recording proxy."""
    host_port = proxy_url.split("://", 1)[-1].rstrip("/")
    host, _, port = host_port.partition(":")
    port = port.split("/")[0] or "80"
    env = {"BFCL_PROJECT_ROOT": root or ""}
    if mode == "board":
        env.update(LOCAL_SERVER_ENDPOINT=host, LOCAL_SERVER_PORT=port)
    elif mode == "server-fc":
        env.update(OPENAI_BASE_URL=f"http://{host}:{port}/v1", OPENAI_API_KEY="EMPTY",
                   K3D_BFCL_SERVED_MODEL=served_model or "")
    else:
        raise ValueError(f"mode must be one of {sorted(REGISTRY)}")
    return env


def generate_argv(mode, python="python3", threads=4):
    launch = os.path.join(HERE, "bfcl_launch.py")
    return [python, launch, "generate", "--model", REGISTRY[mode], "--run-ids",
            "--skip-server-setup", "--num-threads", str(threads), "--result-dir", "result"]


def evaluate_argv(mode, python="python3"):
    launch = os.path.join(HERE, "bfcl_launch.py")
    return [python, launch, "evaluate", "--model", REGISTRY[mode], "--partial-eval",
            "--result-dir", "result", "--score-dir", "score"]


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _files(root, kind, mode):
    d = os.path.join(root, kind, REGISTRY[mode].replace("/", "_"))
    out = []
    for dp, _, fs in os.walk(d):
        out += [os.path.join(dp, f) for f in fs if f.endswith(f"_{kind}.json")]
    return out


def done_ids(root, mode):
    return {e["id"] for p in _files(root, "result", mode) for e in _jsonl(p)}


def ingest(root, mode):
    """{id: 1 pass / 0 fail} for every id that has a result, from BFCL's score files.
    A score file's first line is the header; later lines are failed entries."""
    have = done_ids(root, mode)
    failed, scored = set(), set()
    for p in _files(root, "score", mode):
        rows = _jsonl(p)
        if not rows:
            continue
        cat = os.path.basename(p)[len("BFCL_v4_"):-len("_score.json")]
        scored.add(cat)
        failed |= {r["id"] for r in rows[1:] if "id" in r}
    return {i: (0 if i in failed else 1) for i in have if category_of(i) in scored}


def summarize(passed, s100=None):
    """Overall and per-category accuracy (percent) on the completed ids."""
    s100 = s100 or load_s100()
    per = {}
    for i, v in passed.items():
        g = GROUP.get(category_of(i), category_of(i))
        per.setdefault(g, []).append(v)
    cats = {g: {"n": len(v), "acc": 100 * sum(v) / len(v), "board": BOARD.get(g)} for g, v in sorted(per.items())}
    n = len(passed)
    return {"n": n, "acc": 100 * sum(passed.values()) / n if n else None, "categories": cats,
            "expected": round(expected(s100), 2)}


def run(mode, root, proxy_url, budget_s, served_model=None, python="python3", threads=4,
        runner=subprocess.run, clock=time.monotonic, log=print, limit=None):
    """Generate S100 in run-order chunks until done or the budget ends, then score.

    Returns {"completed": [ids], "stopped": "done" | "budget" | "error: ...", "passed": {id: 0/1}}.
    A generate run that exits non-zero without writing the chunk stops the run as an
    error (a broken venv or endpoint is never read as an empty score)."""
    s100 = load_s100()
    os.makedirs(root, exist_ok=True)
    env = dict(os.environ, **env_for(mode, proxy_url, served_model, root))
    t_end = clock() + budget_s
    stopped = "done"
    order = s100["run_order"][:limit] if limit else s100["run_order"]
    for k in range(0, len(order), CHUNK):
        todo = [i for i in order[k:k + CHUNK] if i not in done_ids(root, mode)]
        if not todo:
            continue
        left = t_end - clock()
        if left <= 0:
            stopped = "budget"
            break
        with open(os.path.join(root, "test_case_ids_to_generate.json"), "w") as f:
            json.dump(ids_file(todo), f, indent=1)
        log(f"bfcl: {mode} chunk {k // CHUNK + 1}/{(len(order) + CHUNK - 1) // CHUNK} ({len(todo)} ids, {left:.0f}s left)")
        try:
            p = runner(generate_argv(mode, python, threads), cwd=root, env=env, timeout=left, check=False)
        except subprocess.TimeoutExpired:
            stopped = "budget"
            break
        rc = getattr(p, "returncode", 0)
        missing = [i for i in todo if i not in done_ids(root, mode)]
        if missing:
            stopped = f"error: bfcl generate exit {rc}, {len(missing)} of {len(todo)} ids not written"
            break
    p = runner(evaluate_argv(mode, python), cwd=root, env=env, check=False)
    if getattr(p, "returncode", 0) and stopped == "done":
        stopped = f"error: bfcl evaluate exit {p.returncode}"
    passed = ingest(root, mode)
    # Only an unbroken prefix of the run order counts, so a cut arm is a random prefix.
    prefix = []
    for i in order:
        if i not in passed:
            break
        prefix.append(i)
    return {"completed": prefix, "stopped": stopped, "passed": {i: passed[i] for i in prefix}}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="BFCL S100 helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("s100", help="rebuild s100.json from a bfcl-eval data directory")
    p.add_argument("--data", required=True, help="bfcl_eval/data from the pinned wheel")
    p.add_argument("--out", default=S100_PATH)
    sub.add_parser("expected", help="board-weighted expected S100 accuracy")
    o = ap.parse_args(argv)
    if o.cmd == "s100":
        ids, sums = dataset_ids(o.data)
        s = build_s100(ids)
        s["dataset_sha256"] = sums
        s["wheel_sha256"] = WHEEL_SHA256
        with open(o.out, "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
        print(f"wrote {o.out}: {len(s['run_order'])} ids")
    else:
        print(f"{expected():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

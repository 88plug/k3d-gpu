#!/usr/bin/env python3
"""A stand-in for `gh` that answers from fixture files (tests/bot_merge_test.sh).

State lives under $FIX/gh:
  pulls.json            REST pull objects (number, title, user, head, base, labels,
                        created_at, draft, state, merged)
  files/<n>.json        REST pull files ([{filename}])
  reviews/<n>.json      REST reviews
  comments/<n>.json     REST issue comments (POST and PATCH edit this file)
  runs.json             `gh run list --json` rows, plus "workflow" (the file name)
  artifacts/<id>/<name>/  files `gh run download <id> -n <name>` extracts
  issues.json           [{number, title, state, labels: [{name}]}]
  merge_rc              exit code sequence for `gh pr merge` (last one repeats)
Calls are logged to $FIX/gh/calls; dispatches to $FIX/gh/dispatches, merges to
$FIX/gh/merges, label additions to $FIX/gh/labels.

A successful merge marks the pull merged and writes the squash commit onto the
bare origin's main ($FIX/origin.git): main's tree merged with the head's, authored
by github-actions[bot], committed at $BOT_MERGE_NOW.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIX = Path(os.environ["FIX"])
G = FIX / "gh"


def load(p, default):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default


def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1))


def log(name, line):
    with open(G / name, "a") as f:
        f.write(line + "\n")


def pop(name, default):
    p = G / name
    if not p.exists():
        return default
    seq = p.read_text().split()
    i_path = G / (name + ".i")
    i = int(i_path.read_text()) if i_path.exists() else 0
    i_path.write_text(str(i + 1))
    return seq[min(i, len(seq) - 1)]


def emit(obj, jq_expr):
    """Print obj as gh does: --jq output through jq (strings raw), else JSON."""
    text = json.dumps(obj)
    if jq_expr is None:
        print(text)
        return 0
    r = subprocess.run(["jq", "-c", "-r", jq_expr], input=text, text=True, capture_output=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def flags(argv, with_value):
    """Split argv into positionals and a {flag: [values]} map."""
    pos, opts = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in with_value:
            opts.setdefault(a, []).append(argv[i + 1])
            i += 2
        elif a.startswith("-"):
            opts.setdefault(a, []).append(True)
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, opts


def one(opts, *names):
    for n in names:
        if n in opts:
            return opts[n][-1]
    return None


def pull(n):
    for p in load(G / "pulls.json", []):
        if p["number"] == n:
            return p
    return None


def set_pull(n, **kv):
    pulls = load(G / "pulls.json", [])
    for p in pulls:
        if p["number"] == n:
            p.update(kv)
    save(G / "pulls.json", pulls)


# ---- gh api ---------------------------------------------------------------
def api(argv):
    pos, opts = flags(argv, {"-X", "--method", "-f", "--raw-field", "-F", "--field",
                             "-q", "--jq", "-H", "--header", "--input"})
    endpoint = pos[0].lstrip("/")
    method = (one(opts, "-X", "--method") or "GET").upper()
    fields = {}
    for kv in opts.get("-f", []) + opts.get("--raw-field", []):  # raw: taken as given
        k, v = kv.split("=", 1)
        fields.setdefault(k, []).append(v)
    for kv in opts.get("-F", []) + opts.get("--field", []):  # typed: @path reads a file
        k, v = kv.split("=", 1)
        if v.startswith("@"):
            v = Path(v[1:]).read_text() if v != "@-" else sys.stdin.read()
        fields.setdefault(k, []).append(v)
    if "-f" in opts or "-F" in opts or "--field" in opts or "--raw-field" in opts:
        if method == "GET" and "-X" not in opts and "--method" not in opts:
            method = "POST"
    jq_expr = one(opts, "-q", "--jq")
    path, _, query = endpoint.partition("?")
    parts = path.split("/")
    # repos/<owner>/<repo>/...
    rest = parts[3:]
    if rest[:1] == ["pulls"] and len(rest) == 1:
        q = dict(kv.split("=", 1) for kv in query.split("&") if kv)
        pulls = [p for p in load(G / "pulls.json", [])
                 if q.get("state", "open") in ("all", p.get("state", "open"))
                 and q.get("base", p["base"]["ref"]) == p["base"]["ref"]]
        return emit(pulls, jq_expr)
    if rest[:1] == ["pulls"] and len(rest) == 2:
        p = pull(int(rest[1]))
        if p is None:
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            return 1
        return emit(p, jq_expr)
    if rest[:1] == ["pulls"] and rest[2] == "files":
        return emit(load(G / "files" / f"{rest[1]}.json", []), jq_expr)
    if rest[:1] == ["pulls"] and rest[2] == "reviews":
        return emit(load(G / "reviews" / f"{rest[1]}.json", []), jq_expr)
    if rest[:1] == ["issues"] and len(rest) == 3 and rest[2] == "comments":
        cpath = G / "comments" / f"{rest[1]}.json"
        comments = load(cpath, [])
        if method == "POST":
            ids = [c["id"] for f in (G / "comments").glob("*.json") for c in load(f, [])]
            c = {"id": max(ids, default=1000) + 1, "user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": fields["body"][-1]}
            comments.append(c)
            save(cpath, comments)
            log("calls", f"comment-post {rest[1]}")
            return emit(c, jq_expr)
        return emit(comments, jq_expr)
    if rest[:2] == ["issues", "comments"] and method == "PATCH":
        cid = int(rest[2])
        for f in (G / "comments").glob("*.json"):
            comments = load(f, [])
            for c in comments:
                if c["id"] == cid:
                    c["body"] = fields["body"][-1]
                    save(f, comments)
                    log("calls", f"comment-patch {f.stem} {cid}")
                    return emit(c, jq_expr)
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        return 1
    if rest[:1] == ["issues"] and len(rest) == 3 and rest[2] == "labels" and method == "POST":
        n = int(rest[1])
        names = fields.get("labels[]", [])
        p = pull(n)
        have = [lab["name"] for lab in p["labels"]]
        set_pull(n, labels=p["labels"] + [{"name": x} for x in names if x not in have])
        for x in names:
            log("labels", f"{n} {x}")
        return emit([{"name": x} for x in have + names], jq_expr)
    print(f"bot_merge_fake_gh: unhandled api {method} {endpoint}", file=sys.stderr)
    return 99


# ---- gh run ---------------------------------------------------------------
def wf_match(run, wf):
    return wf in (run.get("workflow"), run.get("workflowName"))


def run_list(argv):
    pos, opts = flags(argv, {"-w", "--workflow", "-c", "--commit", "-b", "--branch", "-e", "--event",
                             "-s", "--status", "-u", "--user", "--created", "-L", "--limit",
                             "--json", "-q", "--jq"})
    runs = load(G / "runs.json", [])
    wf = one(opts, "-w", "--workflow")
    sha = one(opts, "-c", "--commit")
    br = one(opts, "-b", "--branch")
    ev = one(opts, "-e", "--event")
    st = one(opts, "-s", "--status")
    created = one(opts, "--created")
    log("calls", "run-list " + " ".join(f"{k}={v}" for k, v in
                                       (("w", wf), ("c", sha), ("b", br), ("e", ev), ("s", st), ("created", created)) if v))
    out = []
    for r in runs:
        if wf and not wf_match(r, wf):
            continue
        if sha and r["headSha"] != sha:
            continue
        if br and r["headBranch"] != br:
            continue
        if ev and r["event"] != ev:
            continue
        if st and st not in (r["status"], r.get("conclusion")):
            continue
        if created:
            assert created.startswith(">="), created
            if r["createdAt"] < created[2:]:
                continue
        out.append(r)
    out.sort(key=lambda r: r["createdAt"], reverse=True)
    out = out[: int(one(opts, "-L", "--limit") or 20)]
    fields = (one(opts, "--json") or "").split(",")
    out = [{k: r.get(k) for k in fields} for r in out]
    return emit(out, one(opts, "-q", "--jq"))


def run_download(argv):
    pos, opts = flags(argv, {"-n", "--name", "-D", "--dir", "-p", "--pattern"})
    rid = pos[0]
    names = opts.get("-n", []) + opts.get("--name", [])
    dest = Path(one(opts, "-D", "--dir") or ".")
    log("calls", f"run-download {rid} {' '.join(names)}")
    src = G / "artifacts" / rid
    found = [n for n in names if (src / n).is_dir()]
    if not found:
        print("no valid artifacts found to download", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for f in (src / found[0]).iterdir():
        shutil.copy(f, dest / f.name)
    return 0


# ---- gh pr ----------------------------------------------------------------
HELP = "      --match-head-commit SHA   Commit SHA that the pull request head must match to allow merge\n"


def git_origin(*args, env=None):
    return subprocess.run(["git", f"--git-dir={FIX / 'origin.git'}", *args], text=True,
                          capture_output=True, check=True, env=env).stdout.strip()


def pr_merge(argv):
    if "--help" in argv:
        sys.stdout.write((G / "merge_help").read_text() if (G / "merge_help").exists() else HELP)
        return 0
    pos, opts = flags(argv, {"--match-head-commit", "-t", "--subject", "-b", "--body", "-F", "--body-file"})
    n = int(pos[0])
    sha = one(opts, "--match-head-commit")
    subj = one(opts, "-t", "--subject")
    body = one(opts, "-b", "--body")
    log("merges", " ".join(argv).replace("\n", "\\n"))
    p = pull(n)
    rc = int(pop("merge_rc", "0"))
    if rc != 0:
        print("GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)", file=sys.stderr)
        return rc
    if sha != p["head"]["sha"]:
        print("GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)", file=sys.stderr)
        return 1
    tree = git_origin("merge-tree", "--write-tree", "main", sha).splitlines()[0]
    when = os.environ["BOT_MERGE_NOW"] + " +0000"
    env = dict(os.environ, GIT_AUTHOR_NAME="github-actions[bot]",
               GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
               GIT_COMMITTER_NAME="GitHub", GIT_COMMITTER_EMAIL="noreply@github.com",
               GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    commit = git_origin("commit-tree", tree, "-p", "main", "-m", subj, "-m", body, env=env)
    git_origin("update-ref", "refs/heads/main", commit)
    set_pull(n, state="closed", merged=True)
    return 0


def pr_view(argv):
    pos, opts = flags(argv, {"--json", "-q", "--jq"})
    p = pull(int(pos[0]))
    obj = {"state": "MERGED" if p.get("merged") else p.get("state", "open").upper(),
           "headRefOid": p["head"]["sha"], "headRefName": p["head"]["ref"]}
    return emit(obj, one(opts, "-q", "--jq"))


# ---- gh issue / workflow / repo -------------------------------------------
def issue_list(argv):
    pos, opts = flags(argv, {"--label", "-l", "--state", "-s", "--json", "-L", "--limit", "-q", "--jq"})
    label = one(opts, "--label", "-l")
    state = one(opts, "--state", "-s") or "open"
    issues = [i for i in load(G / "issues.json", [])
              if (state == "all" or i.get("state", "open") == state)
              and (label is None or any(lab["name"] == label for lab in i.get("labels", [])))]
    fields = (one(opts, "--json") or "number,title").split(",")
    return emit([{k: i.get(k) for k in fields} for i in issues], one(opts, "-q", "--jq"))


def workflow_run(argv):
    pos, opts = flags(argv, {"-f", "--raw-field", "-F", "--field", "-r", "--ref"})
    kv = opts.get("-f", []) + opts.get("--raw-field", []) + opts.get("-F", []) + opts.get("--field", [])
    log("dispatches", " ".join([pos[0], "ref=" + str(one(opts, "-r", "--ref"))] + kv))
    rc = int(pop("dispatch_rc", "0"))
    if rc == 0:
        print(f"https://github.com/o/r/actions/runs/9{len((G / 'dispatches').read_text().splitlines())}")
    return rc


def main(argv):
    G.mkdir(parents=True, exist_ok=True)
    log("trace", ("gh " + " ".join(argv)).replace("\n", "\\n"))
    if "-R" in argv or "--repo" in argv:
        print("bot_merge_fake_gh: -R is not expected", file=sys.stderr)
        return 98
    head = argv[:2]
    if argv[:1] == ["api"]:
        return api(argv[1:])
    if head == ["run", "list"]:
        return run_list(argv[2:])
    if head == ["run", "download"]:
        return run_download(argv[2:])
    if head == ["pr", "merge"]:
        return pr_merge(argv[2:])
    if head == ["pr", "view"]:
        return pr_view(argv[2:])
    if head == ["issue", "list"]:
        return issue_list(argv[2:])
    if head == ["workflow", "run"]:
        return workflow_run(argv[2:])
    print(f"bot_merge_fake_gh: unhandled {' '.join(argv)}", file=sys.stderr)
    return 99


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

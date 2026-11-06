#!/usr/bin/env python3
"""A stand-in for the gh CLI, for tests/ci_alarm_test.sh and tests/watchdog_test.sh.

Install it on PATH as `gh` (a symlink or a one-line wrapper). State lives in the
directory $FAKE_GH:

  routes.tsv   <regex> TAB <response file or -> TAB <exit code> [TAB <stderr text>
               [TAB <times>]]
               The first line whose regex matches the arguments joined with single
               spaces (re.search) answers: the file's bytes on stdout, the exit code,
               the stderr text. A route with <times> answers that many calls, then
               is passed over (routes.used counts). Routes come before the built-in
               commands below, so a route can make `issue list` fail the way a
               repository with issues disabled does. Blank lines and lines starting
               with # are skipped.
  issues.json  the issue store the built-in issue commands read and write
  labels.json  label names `label create` added
  calls.log    every invocation, the arguments joined with single spaces
  dispatches.log   every `workflow run`: "<file> ref=<ref> [k=v ...]"
  has_issues   "false": the repository has Issues disabled (`api repos/R` says so,
               and the issue and label commands fail as GitHub's do)
  git.json     the objects and refs the git data API below reads and writes:
               {"refs": {"heads/B": sha}, "commits": {sha: {tree, parents, message}},
                "trees": {sha: {path: text}}}
  ref_race     N: the next N ref updates find the branch moved by another writer
               (a foreign commit lands first), so they fail as GitHub's do (HTTP 422)
  commit_comments.json, statuses.json   what the commit comment and status calls posted

Built in (shapes as gh 2.88 prints them for --json):
  api repos/R                      {full_name, has_issues, default_branch: main}
  api repos/R/commits/main         {sha: $FAKE_GH_HEAD (default 40 x "c")}
  api repos/R/git/ref/heads/B, git/commits/SHA, contents/PATH?ref=X (base64)
  api -X POST repos/R/git/trees | git/commits | git/refs, -X PATCH git/refs/heads/B
                                   (JSON on stdin: --input -)
  api -X POST repos/R/commits/SHA/comments -f body=..., repos/R/statuses/SHA -f ...
  issue list [--label L] [--state open|closed|all] [--limit N] [--json F]
  issue create --title T (--body B | --body-file F) [--label L]... [--assignee A]...
  issue comment N (--body B | --body-file F)
  issue edit N [--title T] [--body B | --body-file F]
  issue close N [--comment C] [--reason R]
  label create NAME [--force] [--color C] [--description D]
  workflow run FILE [--ref R] [-f k=v]...

FAKE_GH_NOW (ISO 8601, default 2026-10-29T13:00:00Z) stamps createdAt.
Anything else exits 1 with "fake gh: no route for: <args>" on stderr.
"""

import base64
import hashlib
import json
import os
import re
import sys

STATE = os.environ["FAKE_GH"]
NOW = os.environ.get("FAKE_GH_NOW", "2026-10-29T13:00:00Z")
REPO = os.environ.get("GH_REPO", "88plug/k3d-gpu")


def path(name):
    return os.path.join(STATE, name)


def load(name, default):
    try:
        with open(path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, obj):
    with open(path(name), "w") as f:
        json.dump(obj, f, indent=1)


def opts(args, multi=()):
    """Parse --flag value pairs (and -R/-f/-F short forms) into a dict and positionals."""
    alias = {"-R": "--repo", "-f": "--raw-field", "-F": "--field", "-b": "--body", "-t": "--title",
             "-l": "--label", "-a": "--assignee", "-L": "--limit", "-s": "--state", "-c": "--comment",
             "-r": "--reason", "-d": "--description"}
    flags = {"--force"}
    out, pos, i = {}, [], 0
    while i < len(args):
        a = alias.get(args[i], args[i])
        if a in flags:
            out[a] = True
            i += 1
        elif a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            out.setdefault(k, []).append(v) if k in multi else out.__setitem__(k, v)
            i += 1
        elif a.startswith("-") and i + 1 < len(args):
            out.setdefault(a, []).append(args[i + 1]) if a in multi else out.__setitem__(a, args[i + 1])
            i += 2
        else:
            pos.append(args[i])
            i += 1
    return out, pos


def body_of(o):
    if "--body-file" in o:
        with open(o["--body-file"]) as f:
            return f.read()
    return o.get("--body", "")


def find(issues, num):
    for it in issues:
        if it["number"] == int(num):
            return it
    sys.stderr.write("GraphQL: Could not resolve to an issue or pull request with the number of %s.\n" % num)
    sys.exit(1)


def comment(it, body):
    it["comments"].append({"author": {"login": "github-actions"}, "authorAssociation": "NONE",
                           "body": body, "createdAt": NOW})


def has_issues():
    try:
        with open(path("has_issues")) as f:
            return f.read().strip() != "false"
    except FileNotFoundError:
        return True


def not_found():
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    return 1


def oid(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def git_api(method, rest, body):
    """The git data API and the contents API over git.json."""
    g = load("git.json", {"refs": {}, "commits": {}, "trees": {}})
    if method == "GET" and rest[:3] == ["git", "ref", "heads"]:
        sha = g["refs"].get("heads/" + "/".join(rest[3:]))
        if sha is None:
            return not_found()
        print(json.dumps({"ref": "refs/heads/" + "/".join(rest[3:]), "object": {"sha": sha, "type": "commit"}}))
        return 0
    if method == "GET" and rest[:2] == ["git", "commits"]:
        c = g["commits"].get(rest[2])
        if c is None:
            return not_found()
        print(json.dumps({"sha": rest[2], "tree": {"sha": c["tree"]}, "parents": [{"sha": x} for x in c["parents"]],
                          "message": c["message"]}))
        return 0
    if method == "GET" and rest[:1] == ["contents"]:
        p, _, q = "/".join(rest[1:]).partition("?")
        ref = dict(kv.split("=", 1) for kv in q.split("&") if kv).get("ref", "main")
        commit = g["refs"].get("heads/" + ref, ref)
        c = g["commits"].get(commit)
        if c is None or p not in g["trees"][c["tree"]]:
            return not_found()
        text = g["trees"][c["tree"]][p]
        print(json.dumps({"path": p, "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}))
        return 0
    if method == "POST" and rest == ["git", "trees"]:
        files = dict(g["trees"][body["base_tree"]]) if body.get("base_tree") else {}
        for e in body["tree"]:
            files[e["path"]] = e["content"]
        sha = oid(files)
        g["trees"][sha] = files
        save("git.json", g)
        print(json.dumps({"sha": sha}))
        return 0
    if method == "POST" and rest == ["git", "commits"]:
        c = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
        sha = oid([c, len(g["commits"])])
        g["commits"][sha] = c
        save("git.json", g)
        print(json.dumps({"sha": sha}))
        return 0
    if rest[:2] == ["git", "refs"] and method in ("POST", "PATCH"):
        name = body["ref"][len("refs/"):] if method == "POST" else "/".join(rest[2:])
        race = load("ref_race", 0)
        if race and name in g["refs"]:
            foreign = {"tree": g["commits"][g["refs"][name]]["tree"], "parents": [g["refs"][name]],
                       "message": "another writer"}
            fsha = oid([foreign, len(g["commits"])])
            g["commits"][fsha] = foreign
            g["refs"][name] = fsha
            save("ref_race", race - 1)
        if method == "POST":
            if name in g["refs"]:
                save("git.json", g)
                sys.stderr.write("gh: Reference already exists (HTTP 422)\n")
                return 1
        else:
            cur, seen, todo = g["refs"].get(name), set(), [body["sha"]]
            while todo:  # the commits new reaches: cur among them is a fast-forward
                x = todo.pop()
                if x in seen or x not in g["commits"]:
                    continue
                seen.add(x)
                todo += g["commits"][x]["parents"]
            if cur is None:
                return not_found()
            if cur not in seen and not body.get("force"):
                save("git.json", g)
                sys.stderr.write("gh: Update is not a fast forward (HTTP 422)\n")
                return 1
        g["refs"][name] = body["sha"]
        save("git.json", g)
        print(json.dumps({"ref": "refs/" + name, "object": {"sha": body["sha"]}}))
        return 0
    return None


def api(args):
    o, pos = opts(args, multi=("--raw-field", "--field"))
    if not pos or not pos[0].startswith("repos/"):
        return None
    parts = pos[0].split("/")
    if "/".join(parts[1:3]) not in (REPO, "{owner}/{repo}"):
        sys.stderr.write("fake gh: api for another repository: %s\n" % pos[0])
        return 1
    rest = parts[3:]
    fields = dict(kv.split("=", 1) for kv in o.get("--raw-field", []))
    for kv in o.get("--field", []):  # -F key=@file reads the file
        k, v = kv.split("=", 1)
        if v.startswith("@"):
            with open(v[1:]) as f:
                v = f.read()
        fields[k] = v
    method = o.get("-X", o.get("--method", "POST" if fields else "GET")).upper()
    body = None
    if o.get("--input") == "-":
        body = json.load(sys.stdin)
    elif o.get("--input"):
        with open(o["--input"]) as f:
            body = json.load(f)
    if not rest and method == "GET":
        print(json.dumps({"full_name": REPO, "has_issues": has_issues(), "default_branch": "main"}))
        return 0
    if rest == ["commits", "main"] and method == "GET":
        print(json.dumps({"sha": os.environ.get("FAKE_GH_HEAD", "c" * 40)}))
        return 0
    if rest[:1] == ["commits"] and rest[2:] == ["comments"] and method == "POST":
        cs = load("commit_comments.json", [])
        cs.append({"commit_id": rest[1], "body": fields["body"], "created_at": NOW})
        save("commit_comments.json", cs)
        print(json.dumps({"html_url": "https://github.com/%s/commit/%s#commitcomment-%d" % (REPO, rest[1], len(cs))}))
        return 0
    if rest[:1] == ["statuses"] and method == "POST":
        st = load("statuses.json", [])
        st.append(dict(fields, sha=rest[1], created_at=NOW))
        save("statuses.json", st)
        print(json.dumps({"state": fields.get("state")}))
        return 0
    return git_api(method, rest, body)


def builtin(args):
    if args[:1] == ["api"]:
        return api(args[1:])
    if args[:1] in (["issue"], ["label"]) and not has_issues():
        sys.stderr.write("the '%s' repository has disabled issues\n" % REPO)
        return 1
    issues = load("issues.json", [])
    if args[:2] == ["issue", "list"]:
        o, _ = opts(args[2:])
        state = o.get("--state", "open")
        out = [it for it in issues
               if (state == "all" or it["state"] == state.upper())
               and ("--label" not in o or any(lb["name"] == o["--label"] for lb in it["labels"]))]
        out.sort(key=lambda it: -it["number"])
        print(json.dumps(out[:int(o.get("--limit", 30))]))
        return 0
    if args[:2] == ["issue", "create"]:
        o, _ = opts(args[2:], multi=("--label", "--assignee"))
        num = max([it["number"] for it in issues] + [0]) + 1
        issues.append({"number": num, "title": o["--title"], "body": body_of(o), "state": "OPEN",
                       "labels": [{"name": n} for n in o.get("--label", [])],
                       "assignees": [{"login": n} for n in o.get("--assignee", [])],
                       "comments": [], "createdAt": NOW})
        save("issues.json", issues)
        print("https://github.com/%s/issues/%d" % (REPO, num))
        return 0
    if args[:2] == ["issue", "comment"]:
        o, pos = opts(args[2:])
        comment(find(issues, pos[0]), body_of(o))
        save("issues.json", issues)
        return 0
    if args[:2] == ["issue", "edit"]:
        o, pos = opts(args[2:])
        it = find(issues, pos[0])
        if "--title" in o:
            it["title"] = o["--title"]
        if "--body" in o or "--body-file" in o:
            it["body"] = body_of(o)
        save("issues.json", issues)
        return 0
    if args[:2] == ["issue", "close"]:
        o, pos = opts(args[2:])
        it = find(issues, pos[0])
        if "--comment" in o:
            comment(it, o["--comment"])
        it["state"] = "CLOSED"
        save("issues.json", issues)
        return 0
    if args[:2] == ["label", "create"]:
        _, pos = opts(args[2:])
        labels = load("labels.json", [])
        if pos[0] not in labels:
            labels.append(pos[0])
        save("labels.json", labels)
        return 0
    if args[:2] == ["workflow", "run"]:
        o, pos = opts(args[2:], multi=("--raw-field", "--field"))
        fields = o.get("--raw-field", []) + o.get("--field", [])
        with open(path("dispatches.log"), "a") as f:
            f.write(" ".join([pos[0], "ref=%s" % o.get("--ref", "")] + fields) + "\n")
        return 0
    return None


def main(args):
    line = " ".join(args)
    with open(path("calls.log"), "a") as f:
        f.write(line + "\n")
    try:
        with open(path("routes.tsv")) as f:
            routes = [ln.rstrip("\n").split("\t") for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        routes = []
    used = load("routes.used", {})
    for i, r in enumerate(routes):
        if re.search(r[0], line):
            if len(r) > 4 and r[4]:
                key = "%d %s" % (i, r[0])
                if used.get(key, 0) >= int(r[4]):
                    continue
                used[key] = used.get(key, 0) + 1
                save("routes.used", used)
            if len(r) > 1 and r[1] not in ("", "-"):
                with open(r[1] if os.path.isabs(r[1]) else path(r[1])) as f:
                    sys.stdout.write(f.read())
            if len(r) > 3:
                sys.stderr.write(r[3] + "\n")
            return int(r[2]) if len(r) > 2 and r[2] else 0
    rc = builtin(args)
    if rc is None:
        sys.stderr.write("fake gh: no route for: %s\n" % line)
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

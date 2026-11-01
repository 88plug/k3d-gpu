#!/usr/bin/env python3
"""A stand-in for the gh CLI, for tests/ci_alarm_test.sh and tests/watchdog_test.sh.

Install it on PATH as `gh` (a symlink or a one-line wrapper). State lives in the
directory $FAKE_GH:

  routes.tsv   <regex> TAB <response file or -> TAB <exit code> [TAB <stderr text>]
               The first line whose regex matches the arguments joined with single
               spaces (re.search) answers: the file's bytes on stdout, the exit code,
               the stderr text. Routes come before the built-in commands below, so a
               route can make `issue list` fail the way a repository with issues
               disabled does. Blank lines and lines starting with # are skipped.
  issues.json  the issue store the built-in issue commands read and write
  labels.json  label names `label create` added
  calls.log    every invocation, the arguments joined with single spaces
  dispatches.log   every `workflow run`: "<file> ref=<ref> [k=v ...]"

Built in (shapes as gh 2.88 prints them for --json):
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


def builtin(args):
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
    for r in routes:
        if re.search(r[0], line):
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

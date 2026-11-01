#!/usr/bin/env python3
"""Workflow checks for tests/ci_wiring_test.sh (alarm, watchdog, release hardening).

Reads every .github/workflows/*.yml with PyYAML and prints one line per check:
"ok   <what>" or "FAIL <what>: <why>". Exit 0 when the checks ran (failures are
lines, not the exit code).

Usage: tests/ops/workflow_checks.py REPO_ROOT
"""

import glob
import os
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = sys.argv[1]
WF = os.path.join(ROOT, ".github", "workflows")
DOCS = {os.path.basename(p): yaml.safe_load(open(p)) for p in sorted(glob.glob(WF + "/*.yml"))}


def say(cond, what, why=""):
    print(("ok   %s" % what) if cond else ("FAIL %s%s" % (what, (": " + str(why)) if why else "")))


def on(d):
    """A workflow's on: block (PyYAML reads the bare key `on` as True)."""
    v = (d or {}).get("on", (d or {}).get(True))
    return v if isinstance(v, dict) else {}


# Every job has timeout-minutes; every workflow has a top-level permissions block.
for f, d in DOCS.items():
    missing = [j for j, v in (d.get("jobs") or {}).items() if not isinstance(v.get("timeout-minutes"), int)]
    say(not missing, "%s: every job has timeout-minutes" % f, "missing in %s" % ", ".join(missing))
    say("permissions" in d, "%s: has a top-level permissions block" % f)
say(DOCS.get("aur-deploy.yml", {}).get("permissions") == {"contents": "read"}, "aur-deploy.yml: permissions contents: read")
# Releases now build in parallel, so the AUR deploy of an older one can come last:
# it must leave a newer AUR version alone.
ad = ((DOCS.get("aur-deploy.yml") or {}).get("jobs") or {}).get("deploy", {}).get("steps") or []
guard = [s for s in ad if s.get("id") == "aur"]
pub = [s for s in ad if str(s.get("uses", "")).startswith("KSXGitHub/github-actions-deploy-aur@")]
say(guard and "sort -V" in guard[0].get("run", "") and "rpc/v5/info" in guard[0].get("run", "")
    and pub and pub[0].get("if") == "steps.aur.outputs.skip != 'true'"
    and ad.index(guard[0]) < ad.index(pub[0]),
    "aur-deploy.yml never moves AUR back to an older version")


def aur_guard(answer, ver):
    """Run the guard step as Actions does (bash -eo pipefail) against a fake curl:
    answer is the AUR version (a JSON reply) or None (curl fails). Returns
    (exit code, GITHUB_OUTPUT text)."""
    with tempfile.TemporaryDirectory() as d:
        reply = '{"resultcount":1,"results":[{"Version":"%s"}]}' % answer if answer else ""
        with open(os.path.join(d, "curl"), "w") as f:
            f.write("#!/bin/sh\n" + ("printf '%%s\\n' '%s'\n" % reply if answer else "exit 22\n"))
        os.chmod(os.path.join(d, "curl"), 0o755)
        out = os.path.join(d, "out")
        open(out, "w").close()
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], VER=ver, GITHUB_OUTPUT=out)
        p = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", guard[0]["run"]],
                           env=env, capture_output=True, text=True)
        return p.returncode, open(out).read()


if guard:
    got = {k: aur_guard(a, "0.1.80") for k, a in
           (("newer", "0.1.81-1"), ("same", "0.1.80-1"), ("older", "0.1.79-1"), ("unreadable", None))}
    say(got["newer"] == (0, "skip=true\n") and got["same"] == (0, "") and got["older"] == (0, "")
        and got["unreadable"] == (0, ""),
        "aur-deploy.yml AUR guard: skips only for a newer AUR version; an unreadable RPC deploys", got)

# No cron fires at minute 0 or 30.
scheduled = {}
for f, d in DOCS.items():
    for c in on(d).get("schedule") or []:
        scheduled.setdefault(f, []).append(c["cron"])
        minute = c["cron"].split()[0]
        bad = not re.fullmatch(r"\d+(,\d+)*", minute) or bool({0, 30} & {int(x) for x in minute.split(",")})
        say(not bad, "%s: cron '%s' avoids minutes 0 and 30" % (f, c["cron"]))

# hack/watchdog discovers every scheduled workflow.
p = subprocess.run([sys.executable, os.path.join(ROOT, "hack", "watchdog"), "--list-scheduled", "--root", ROOT],
                   capture_output=True, text=True)
found = {ln.split()[0] for ln in p.stdout.splitlines() if ln.strip()}
say(p.returncode == 0 and scheduled and found == set(scheduled),
    "hack/watchdog --list-scheduled covers every scheduled workflow",
    "watchdog %s, yaml %s %s" % (sorted(found), sorted(scheduled), p.stderr[-200:]))

# watchdog.yml listens to CI, Docs and the release chain by their exact names, and
# to Canary, Freshness and Bot sweep once those workflows exist.
names = {d.get("name") for d in DOCS.values()}
wd = DOCS.get("watchdog.yml") or {}
listed = (on(wd).get("workflow_run") or {}).get("workflows") or []
want = {"CI", "Auto-update, Build & Release", "Auto-release AUR", "Release packages (deb, rpm, Homebrew)",
        "AUR Deploy", "Docs"} | ({"Canary", "Freshness", "Bot sweep"} & names)
say(set(listed) == want and len(listed) == len(want) and set(listed) <= names,
    "watchdog.yml workflow_run names equal the name: fields of the workflows it watches",
    "listed %s, want %s, no such workflow %s" % (sorted(listed), sorted(want), sorted(set(listed) - names)))
job = (wd.get("jobs") or {}).get("watchdog") or {}
perms = wd.get("permissions") or {}
say(perms.get("issues") == "write" and perms.get("actions") == "write" and perms.get("contents") == "read"
    and wd.get("concurrency") == {"group": "watchdog", "cancel-in-progress": False}
    and "head_branch == 'main'" in str(job.get("if", ""))
    and (on(wd).get("workflow_run") or {}).get("branches") == ["main"]
    and on(wd).get("schedule") == [{"cron": "47 * * * *"}]
    and ((on(wd).get("workflow_dispatch") or {}).get("inputs") or {}).get("simulate_red", {}).get("type") == "boolean",
    "watchdog.yml: hourly at :47, simulate_red, issues+actions write, one queue, workflow_run on main only")
# workflow_run's branches: filter matches a fork's branch named main too (a fork PR
# from <fork>:main), so the job also requires the run to come from this repository.
say("github.event.workflow_run.head_repository.full_name == github.repository" in str(job.get("if", "")),
    "watchdog.yml: a workflow_run from a fork's main does not start the watchdog", job.get("if"))
co = [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith("actions/checkout@")]
say(co and co[0].get("with", {}).get("ref") == "main" and co[0]["with"].get("fetch-depth") == 0
    and any(str(s.get("run", "")).strip() == "hack/watchdog" for s in job.get("steps", [])),
    "watchdog.yml checks out main with every tag and runs hack/watchdog")

# build-and-push.yml: one queue per release version, the nightly in its own, so a
# pending release never replaces another.
bp = DOCS.get("build-and-push.yml") or {}
say(bp.get("concurrency") == {"group": "build-and-push-${{ inputs.release && format('release-{0}', inputs.release) || 'nightly' }}",
                              "cancel-in-progress": False},
    "build-and-push.yml concurrency is keyed by the release version", bp.get("concurrency"))

# The release job builds each variant in up to 3 identical attempts (1 and 2
# continue on error and are followed by a 2 and a 4 min wait; 3 fails the job),
# pushing the versioned tags only; the floating tags are copied after both builds
# from hack/release-tags run at that moment.
steps = ((bp.get("jobs") or {}).get("release") or {}).get("steps") or []
by_id = {s.get("id"): s for s in steps if s.get("id")}
order = [s.get("id") or s.get("name") for s in steps]
for v in ("2604", "2404"):
    a = [by_id.get("b%s_%d" % (v, i)) for i in (1, 2, 3)]
    good = all(a) and all(str(s.get("uses", "")).startswith("docker/build-push-action@") for s in a)
    good = good and [s.get("continue-on-error") for s in a] == [True, True, None]
    good = good and a[0].get("if") is None and a[1].get("if") == "steps.b%s_1.outcome == 'failure'" % v \
        and a[2].get("if") == "steps.b%s_2.outcome == 'failure'" % v
    good = good and all(s["with"].get("tags") == "${{ steps.tags.outputs.v%s }}" % v for s in a)
    good = good and len({yaml.safe_dump(s["with"]) for s in a}) == 1 and len({s.get("uses") for s in a}) == 1
    waits = [s for s in steps if "sleep" in str(s.get("run", "")) and s.get("if") in
             ("steps.b%s_1.outcome == 'failure'" % v, "steps.b%s_2.outcome == 'failure'" % v)]
    good = good and [w["run"].split()[-1] for w in waits] == ["120", "240"]
    good = good and order.index("b%s_1" % v) < order.index(waits[0].get("id") or waits[0].get("name")) \
        < order.index("b%s_2" % v) < order.index(waits[1].get("id") or waits[1].get("name")) < order.index("b%s_3" % v)
    say(good, "release builds the %s variant in 3 identical attempts, 2 and 4 min apart, versioned tags only" % v)
tags = by_id.get("tags") or {}
flo = [s for s in steps if "imagetools create" in str(s.get("run", ""))]
say("release-tags" in str(tags.get("run", "")) and len(flo) == 1 and "release-tags" in flo[0]["run"]
    and "floating" in flo[0]["run"] and "b2404_3" in order and "b2604_3" in order
    and order.index(flo[0].get("id") or flo[0].get("name")) > max(order.index("b2404_3"), order.index("b2604_3")),
    "floating tags come from hack/release-tags at push time, after both builds")
say(any("already published" in str(s.get("run", "")) for s in steps), "release keeps its refusal to overwrite a published version")

# auto-release.yml: its push paths equal hack/release-paths, which covers every file
# nfpm and the Homebrew formula ship (README and license excepted); mode=changed
# runs release_cas.sh as a push, so the path filter applies.
ar = DOCS.get("auto-release.yml") or {}
rp = [ln.split("#")[0].strip() for ln in open(os.path.join(ROOT, "hack", "release-paths"))]
rp = [x for x in rp if x]
say(rp and all(re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*(/\*\*)?", x) for x in rp),
    "hack/release-paths holds only paths and dir/** entries")
say((on(ar).get("push") or {}).get("paths") == rp, "auto-release.yml paths: equals hack/release-paths",
    "yml %s, file %s" % ((on(ar).get("push") or {}).get("paths"), rp))


def covered(src):
    return any(src == r or (r.endswith("/**") and src.startswith(r[:-2])) for r in rp)


nfpm = yaml.safe_load(open(os.path.join(ROOT, "packaging", "nfpm", "nfpm.yaml")))
srcs = [c["src"] for c in nfpm["contents"] if c.get("src")]
formula = open(os.path.join(ROOT, "Formula", "k3d-gpu.rb")).read()
inst = re.sub(r",\s*\n\s*", ", ", formula.split("def install", 1)[1].split("\n  end", 1)[0])
fsrcs = [q for ln in inst.splitlines() if ".install" in ln for q in re.findall(r'"([^"]+)"', ln.split(".install", 1)[1])]
docs_only = {"README.md", "LICENSE.md"}
miss = [x for x in srcs + fsrcs if x not in docs_only and not covered(x)]
say(bool(srcs) and bool(fsrcs) and not miss, "hack/release-paths covers every nfpm src and Formula source",
    "not covered: %s" % miss)
mode = ((on(ar).get("workflow_dispatch") or {}).get("inputs") or {}).get("mode") or {}
say(mode.get("type") == "choice" and mode.get("options") == ["force", "changed"] and mode.get("default") == "force",
    "auto-release.yml workflow_dispatch input mode: force (default) or changed", mode)
rel = (ar.get("jobs") or {}).get("release") or {}
ev = [(s.get("env") or {}).get("EVENT") for s in rel.get("steps", []) if "release_cas.sh" in str(s.get("run", ""))]
say(ev == ["${{ github.event_name == 'workflow_dispatch' && inputs.mode == 'changed' && 'push' || github.event_name }}"],
    "mode=changed runs release_cas.sh as a push (the path filter applies)", "EVENT=%s" % ev)
grp = ((rel.get("concurrency") or {}).get("group") or "")
say("inputs.mode" in grp and rel.get("concurrency", {}).get("cancel-in-progress") is False,
    "auto-release.yml queues each mode separately (a pending forced release is never replaced)", grp)

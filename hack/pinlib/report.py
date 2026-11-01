"""The Freshness dashboard issue body, and the lag tripwire.

render(root, outdated_rows, prs, alarms_text) returns (markdown, lag) where lag is
[{"key": "blind-spot:<pin>", "title", "body"}]: an ours pin behind its upstream
latest for more than 72 h (auto-eligible) or 7 days (review), with no open bot PR
for its group.
"""

import datetime
import difflib
import os
import re
import subprocess

from . import http, ledgerinfo, policy, registry

AUTO_LAG = datetime.timedelta(hours=72)
REVIEW_LAG = datetime.timedelta(days=7)
USES = re.compile(r"^(?P<pre>\s*(?:-\s+)?uses:\s+)(?P<repo>[\w.-]+/[\w.-]+)(?P<path>/[\w./-]+)?@(?P<sha>[0-9a-f]{40})(?P<post>\s*#\s*(?P<tag>\S+).*)?$")


def _age(published, now):
    if not published:
        return None
    try:
        t = datetime.datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return now - t


def _fmt_age(d):
    if d is None:
        return "?"
    return "%dd" % d.days if d.days else "%dh" % (d.seconds // 3600)


def _auto_eligible(row):
    return policy.family_allows(row.get("family"), row.get("update_type"))


def lag_alarms(rows, prs, now):
    open_groups = {re.sub(r"^autobump/", "", p.get("headRefName", "")) for p in prs}
    out = []
    for r in rows:
        # an unconfirmed pin (its second source disagrees) is the blind spot that kept
        # k3s at v1.34.1 for 331 days: it lags as much as one behind
        lagging = r.get("behind") or (r.get("unconfirmed") and r.get("latest") not in (None, r.get("current")))
        if not lagging or r.get("class") != "ours" or r["group"] in open_groups:
            continue
        age = _age(r.get("published_at"), now)
        if age is None:
            continue
        limit = AUTO_LAG if _auto_eligible(r) else REVIEW_LAG
        if age > limit:
            out.append({"key": "blind-spot:%s" % r["id"],
                        "title": "%s is %s behind upstream with no open PR" % (r["id"], _fmt_age(age)),
                        "body": "Pin `%s` (group %s) is at %s; upstream %s was published %s (%s ago, limit %s). "
                                "No autobump/%s PR is open: the bumper is not moving it. Check the Freshness run's "
                                "bump job for this group." % (r["id"], r["group"], r.get("current"), r.get("latest"),
                                                             r.get("published_at"), _fmt_age(age),
                                                             "72 h" if limit == AUTO_LAG else "7 days", r["group"])})
    return out


def actions_patch(root):
    """A unified diff moving every workflow `uses: owner/repo@<sha> # <tag>` to its
    repo's latest release commit, for a human to apply (a bot cannot push workflows)."""
    wf = os.path.join(root, ".github", "workflows")
    latest, patch = {}, []
    for name in sorted(os.listdir(wf)) if os.path.isdir(wf) else []:
        path = os.path.join(wf, name)
        with open(path, encoding="utf-8") as f:
            old = f.read().splitlines(keepends=True)
        new = []
        for line in old:
            m = USES.match(line.rstrip("\n"))
            if not m:
                new.append(line)
                continue
            repo = m.group("repo")
            if repo not in latest:
                try:
                    rel = http.get_json("https://api.github.com/repos/%s/releases/latest" % repo)
                    sha = http.get_json("https://api.github.com/repos/%s/commits/%s" % (repo, rel["tag_name"]))["sha"]
                    latest[repo] = (rel["tag_name"], sha)
                except (http.HTTPError, KeyError, ValueError):
                    latest[repo] = None
            if not latest[repo] or latest[repo][1] == m.group("sha"):
                new.append(line)
                continue
            tag, sha = latest[repo]
            new.append("%s%s%s@%s # %s\n" % (m.group("pre"), repo, m.group("path") or "", sha, tag))
        if new != old:
            patch += difflib.unified_diff(old, new, "a/.github/workflows/" + name, "b/.github/workflows/" + name)
    return "".join(patch)


def _run(root, cmd):
    if not os.access(os.path.join(root, cmd[0]), os.X_OK):
        return None
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode in (0, 1) else None


def pending_arms(root):
    """[(row, globs)] for every PENDING row pins/classes gives runner paths."""
    try:
        verdicts = ledgerinfo.rows(root)
    except (OSError, ValueError):
        return []
    out = {}
    for glob, rs, _ in ledgerinfo.ledger_lines(root):
        for r in rs:
            if verdicts.get(r) == "PENDING":
                out.setdefault(r, []).append(glob)
    return sorted(out.items())


def render(root, rows, prs, alarms, now=None, actions=True):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lag = lag_alarms(rows, prs, now)
    md = ["# Freshness dashboard", "",
          "Updated %s by the Freshness workflow (`hack/pins report`). Every pin of class ours or eat-stock in "
          "`pins/registry.toml` against its upstream's newest stable release." % now.strftime("%Y-%m-%d %H:%M UTC"), ""]
    md += ["## Open alarms", ""]
    md += [alarms.strip() if alarms and alarms.strip() else "None.", ""]
    behind = [r for r in rows if r.get("behind") and r.get("class") == "ours"]
    md += ["## Pins behind (%d)" % len(behind), ""]
    if behind:
        md += ["| Group | Pin | Pinned | Latest | Type | Published | Age |", "|---|---|---|---|---|---|---|"]
        for r in behind:
            md.append("| %s | `%s` | `%s` | `%s` | %s | %s | %s |" % (
                r["group"], r["id"], r.get("current"), r.get("latest"), r.get("update_type"),
                (r.get("published_at") or "?")[:10], _fmt_age(_age(r.get("published_at"), now))))
    else:
        md.append("None.")
    md.append("")
    errs = [r for r in rows if r.get("error") or r.get("unconfirmed")]
    if errs:
        md += ["### Not resolved (%d)" % len(errs), ""]
        for r in errs:
            md.append("- `%s` (%s): %s" % (r["id"], r["group"], r.get("error") or r.get("note")))
        md.append("")
    md += ["## Open bot PRs (%d)" % len(prs), ""]
    if prs:
        md += ["| PR | Group | Verdict | Blocking |", "|---|---|---|---|"]
        for p in prs:
            md.append("| #%s | %s | %s | %s |" % (p.get("number"), re.sub(r"^autobump/", "", p.get("headRefName", "")),
                                                p.get("verdict", "?"), p.get("blocking") or "-"))
    else:
        md.append("None.")
    md.append("")
    eat = [r for r in rows if r.get("class") == "eat-stock"]
    md += ["## EAT stock (always review)", ""]
    if eat:
        for r in eat:
            state = "behind: `%s` -> `%s`" % (r.get("current"), r.get("latest")) if r.get("behind") else (
                "error: %s" % r["error"] if r.get("error") else "current (`%s`)" % r.get("current"))
            md.append("- `%s`: %s" % (r["id"], state))
    else:
        md.append("No eat-stock pins resolved.")
    md.append("")
    md += ["## PENDING arms", "",
           "A PENDING row's arm pins move only by a dated amendment pushed before it runs. Propose one with the "
           "command beside the row (a human dispatch; the PR is never bot-merged).", ""]
    arms = pending_arms(root)
    if arms:
        for row, globs in arms:
            md.append("- %s (`%s`): `gh workflow run freshness.yml -f propose_amendment=%s`" % (row, "`, `".join(globs), row))
    else:
        md.append("No PENDING row has runner paths in `pins/classes` yet.")
    md.append("")
    md += ["## Known issues and stale workarounds", ""]
    ki = _run(root, ["hack/known-issues", "check"])
    md += [ki.strip() if ki and ki.strip() else "`hack/known-issues` is not installed or reported nothing.", ""]
    md += ["## Ledger re-measure queue", ""]
    lf = _run(root, ["hack/ledger-freshness", "--md"])
    # hack/ledger-freshness --md opens with its own "## Ledger re-measure queue" heading
    lf = re.sub(r"\A\s*## Ledger re-measure queue[^\n]*\n", "", lf or "")
    md += [lf.strip() if lf.strip() else "`hack/ledger-freshness` is not installed or reported nothing.", ""]
    md += ["## Actions behind", "",
           "GitHub Actions are pinned by commit SHA and only a human (or Dependabot) moves them: a bot token cannot "
           "push `.github/workflows/`. Apply with `git apply`, then open a PR.", ""]
    patch = actions_patch(root) if actions else ""
    md += (["```diff", patch.rstrip("\n"), "```"] if patch else ["Every action is at its latest release."]) + [""]
    if lag:
        md += ["## Lag tripwire", ""] + ["- %s: %s" % (a["key"], a["title"]) for a in lag] + [""]
    return "\n".join(md), lag


def registry_counts(root):
    reg = registry.load(os.path.join(root, "pins", "registry.toml"))
    return len(reg.get("pin", [])), len(reg.get("groups", {}))

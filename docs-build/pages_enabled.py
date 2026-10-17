"""Print enabled=true when GitHub Pages publishes REPO from Actions, else enabled=false.

Usage: pages_enabled.py OWNER/REPO   (sends GH_TOKEN when set; appends to $GITHUB_OUTPUT)
GET /repos/{repo}/pages answers 404 while Pages is off.
"""
import json
import os
import sys
import urllib.error
import urllib.request


def enabled(repo, token=None, opener=urllib.request.urlopen):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pages",
        headers={"Accept": "application/vnd.github+json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req, timeout=30) as resp:
            site = json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise
    return site.get("build_type") == "workflow"


if __name__ == "__main__":
    on = enabled(sys.argv[1], os.environ.get("GH_TOKEN"))
    line = f"enabled={'true' if on else 'false'}"
    print(line)
    if not on:
        print("::notice::GitHub Pages is not set to publish from Actions; deploy skipped.")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            print(line, file=out)

#!/usr/bin/env python3
"""share.py: `k3d-gpu share`, the opt-in capacity-oracle submission.

  1. collect   `k3d-gpu report --json --no-cluster` (or --report) and one
               `k3d-gpu capacity` run (--capacity, default: the newest
               finished run under $XDG_STATE_HOME/k3d-gpu/capacity).
  2. project   oracle/project.py builds the bundle from an allowlist.
  3. validate  oracle/validate.py, the rules ingest runs, plus a scan for this
               machine's own identifiers (hostname, machine-id, user name, home
               path, MAC addresses, DMI serials and UUID). A rejected bundle is
               printed with the reasons and goes nowhere.
  4. print     every byte of the bundle, its sha256, size and field count.
  5. write     ./k3d-gpu-share-<sha256[:12]>.json, mode 0600 (--out).
  6. stop      unless --submit; --dry-run always stops here.
  7. confirm   --submit needs a terminal and a typed y/yes. There is no --yes.
  8. send      a pull request on github.com/88plug/k3d-gpu adding
               data/oracle/<sha256>.json through your fork, when gh is installed
               and logged in (and --no-gh is not given); otherwise the manual
               pull-request steps are printed. CI validates the file (V1-V12).

Nothing touches the network before step 8, and step 8 runs only after the
answer at step 7.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import project as P  # noqa: E402
import validate as V  # noqa: E402

ORACLE_REPO = "88plug/k3d-gpu"
DATA_DIR = "data/oracle"
NOTICE = ("This bundle carries no identifiers: no hostname, serial numbers, MAC or IP addresses, "
          "user names, paths or timestamps (the week only). A GitHub pull request is public "
          "and shows your GitHub account, so a submission is not anonymous.")
QUESTION = (f"Submit this bundle to github.com/{ORACLE_REPO} as a public pull request under your "
            "GitHub account? [y/N] ")
EXIT_USAGE, EXIT_REJECTED, EXIT_NOT_SENT = 2, 3, 1


def say(msg=""):
    print(msg, flush=True)


def root():
    return os.environ.get("K3D_GPU_SYSROOT", "")


# ---------------------------------------------------------------- collect
def run_report():
    self_ = os.environ.get("K3D_GPU_SELF") or os.path.join(REPO, "scripts", "k3d-gpu")
    out = subprocess.run(["bash", self_, "report", "--json", "--no-cluster"], capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        raise P.ProjectionError(f"k3d-gpu report failed: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def newest_capacity():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    runs = []
    for p in glob.glob(os.path.join(base, "k3d-gpu", "capacity", "*", "capacity.json")):
        try:
            with open(p) as f:
                if json.load(f).get("status") == "ok":
                    runs.append((os.path.getmtime(p), p))
        except (OSError, ValueError):
            continue
    return max(runs)[1] if runs else None


def local_identifiers(r=None):
    """This machine's own identifiers, to prove none reached the bundle."""
    r = root() if r is None else r
    ids = set()

    def add(v):
        v = (v or "").strip()
        if len(v) >= 4 and v.lower() not in ("none", "default string", "to be filled by o.e.m."):
            ids.add(v)

    add(P._first_line(f"{r}/proc/sys/kernel/hostname"))
    add(P._first_line(f"{r}/etc/hostname"))
    for f in ("etc/machine-id", "var/lib/dbus/machine-id"):
        add(P._first_line(f"{r}/{f}"))
    for f in ("product_serial", "board_serial", "chassis_serial", "product_uuid", "board_asset_tag",
              "chassis_asset_tag"):
        add(P._first_line(f"{r}/sys/class/dmi/id/{f}"))
    for p in glob.glob(f"{r}/sys/class/net/*/address"):
        mac = P._first_line(p) or ""
        if mac and mac.strip("0:"):
            add(mac)
    if not r:
        for k in ("USER", "LOGNAME"):
            add(os.environ.get(k))
        try:
            pw = pwd.getpwuid(os.getuid())
            add(pw.pw_name)
            add(pw.pw_dir)
        except KeyError:
            pass
    return sorted(ids)


# ---------------------------------------------------------------- output
def leaf_count(x):
    if isinstance(x, dict):
        return sum(leaf_count(v) for v in x.values())
    if isinstance(x, list):
        return sum(leaf_count(v) for v in x)
    return 1


def pretty(b):
    """Indented JSON with sorted keys; a list of plain values stays on one line,
    so the per-request sample columns read as rows and the file stays small."""
    def enc(x, ind):
        pad, inner = " " * ind, " " * (ind + 1)
        if isinstance(x, dict) and x:
            items = [f"{inner}{json.dumps(k)}: {enc(x[k], ind + 1)}" for k in sorted(x)]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(x, list) and any(isinstance(v, (dict, list)) for v in x):
            return "[\n" + ",\n".join(inner + enc(v, ind + 1) for v in x) + "\n" + pad + "]"
        return json.dumps(x, separators=(", ", ": "), allow_nan=False)
    return enc(b, 0) + "\n"


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "unknown").lower()).strip("-")[:48]


# ---------------------------------------------------------------- send
def confirm(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if not (stdin.isatty() and stdout.isatty()):
        say("share: --submit needs an interactive terminal to ask first; nothing was sent.")
        return False
    stdout.write(QUESTION)
    stdout.flush()
    try:
        answer = stdin.readline().strip().lower()
    except (KeyboardInterrupt, EOFError):
        stdout.write("\n")
        stdout.flush()
        return False
    return answer in ("y", "yes")


class GhUnavailable(RuntimeError):
    """gh is installed but `gh auth status` failed."""


def gh_ready():
    """Path of a logged-in gh; None when gh is not installed. Raises
    GhUnavailable with gh's own message when it is installed but the auth
    check fails (not logged in, or a proxy/TLS problem)."""
    gh = shutil.which("gh")
    if not gh:
        return None
    r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise GhUnavailable((r.stderr or r.stdout).strip()[:400] or f"exit {r.returncode}")
    return gh


def submission_steps(sha, path):
    """The manual pull-request steps, for when gh cannot do them."""
    return [f"  gh repo fork {ORACLE_REPO} --clone && cd k3d-gpu",
            f"  git switch -c oracle-{sha[:12]} && cp {path} {DATA_DIR}/{sha}.json",
            f"  git add {DATA_DIR}/{sha}.json && git commit -m 'oracle: bundle {sha[:12]}'",
            f"  git push -u origin oracle-{sha[:12]} && gh pr create --repo {ORACLE_REPO} --fill"]


def gh_run(gh, *args, stdin=None):
    return subprocess.run([gh, *args], input=stdin, capture_output=True, text=True, check=False)


def gh_out(gh, *args, stdin=None):
    r = gh_run(gh, *args, stdin=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed (exit {r.returncode}): {r.stderr.strip()[:300]}")
    return r.stdout.strip()


POLL_TRIES, POLL_WAIT_S = 10, 3.0


def gh_poll(gh, *args):
    """gh_out, retried: GitHub creates a fork asynchronously, so its refs can
    404 for a few seconds after `gh repo fork` returns."""
    for i in range(POLL_TRIES):
        try:
            return gh_out(gh, *args)
        except RuntimeError:
            if i == POLL_TRIES - 1:
                raise
            time.sleep(POLL_WAIT_S)
    return None


LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")


def send(b, path, text, use_gh):
    """Open a pull request on ORACLE_REPO adding DATA_DIR/<sha256>.json through
    the user's fork: fork (no clone), branch from the fork's default branch,
    put the file with the contents API, then gh pr create. CI validates it.

    Every step can be rerun, so the same command after an interrupted submit
    finishes the job: the branch name and file path come from the content
    hash, an existing branch or identical file is reused, and an open pull
    request from that branch is reported instead of opening a second one."""
    sha = b["content_sha256"]
    try:
        gh = gh_ready() if use_gh else None
    except GhUnavailable as e:
        say(f"share: gh is installed but `gh auth status` failed: {e}")
        say(f"Nothing was sent; the bundle is in {path}. Fix gh (gh auth login) and rerun, or open the "
            "pull request by hand:")
        for line in submission_steps(sha, path):
            say(line)
        return EXIT_NOT_SENT
    if not gh:
        say("gh is not available. Open the pull request by hand:" if use_gh else "Open the pull request by hand:")
        for line in submission_steps(sha, path):
            say(line)
        return 0
    branch, dest = f"oracle-{sha[:12]}", f"{DATA_DIR}/{sha}.json"
    try:
        gh_out(gh, "repo", "fork", ORACLE_REPO, "--clone=false", "--default-branch-only")
        login = gh_out(gh, "api", "user", "--jq", ".login")
        if not LOGIN_RE.fullmatch(login):
            raise RuntimeError(f"unexpected GitHub login {login!r}")
        fork = f"{login}/{ORACLE_REPO.split('/')[1]}"
        base = gh_poll(gh, "api", f"repos/{fork}", "--jq", ".default_branch")
        if not BRANCH_RE.fullmatch(base):
            raise RuntimeError(f"unexpected default branch {base!r}")
        have = gh_run(gh, "api", f"repos/{fork}/git/ref/heads/{branch}", "--jq", ".object.sha")
        if not (have.returncode == 0 and have.stdout.strip()):
            head = gh_poll(gh, "api", f"repos/{fork}/git/ref/heads/{base}", "--jq", ".object.sha")
            made = gh_run(gh, "api", "-X", "POST", f"repos/{fork}/git/refs",
                          "-f", f"ref=refs/heads/{branch}", "-f", f"sha={head}")
            if made.returncode != 0 and "Reference already exists" not in made.stderr + made.stdout:
                raise RuntimeError(f"gh api -X POST failed (exit {made.returncode}): {made.stderr.strip()[:300]}")
        cur = gh_run(gh, "api", f"repos/{fork}/contents/{dest}?ref={branch}", "--jq", ".content")
        on_branch = None
        if cur.returncode == 0 and cur.stdout.strip():
            try:
                on_branch = base64.b64decode("".join(cur.stdout.split())).decode()
            except ValueError:
                on_branch = None
        if on_branch != text:
            payload = {"message": f"oracle: bundle {sha[:12]}", "branch": branch,
                       "content": base64.b64encode(text.encode()).decode()}
            if on_branch is not None:
                payload["sha"] = gh_out(gh, "api", f"repos/{fork}/contents/{dest}?ref={branch}", "--jq", ".sha")
            gh_out(gh, "api", "-X", "PUT", f"repos/{fork}/contents/{dest}", "--input", "-",
                   stdin=json.dumps(payload))
        url = gh_out(gh, "pr", "list", "--repo", ORACLE_REPO, "--head", branch, "--author", login,
                     "--state", "open", "--json", "url", "--jq", ".[0].url // empty")
        if url:
            say(f"share: the pull request is already open: {url}")
            return 0
        title = f"oracle: bundle {sha[:12]} {slug(b['hardware']['cpu']['model'])}"
        body = (f"k3d-gpu share bundle `{sha}` as `{dest}`.\n\n"
                "CI runs oracle/validate.py (V1-V12) on it; a maintainer merges it and regenerates "
                "the leaderboard.\n")
        url = gh_out(gh, "pr", "create", "--repo", ORACLE_REPO, "--base", "main",
                     "--head", f"{login}:{branch}", "--title", title, "--body", body)
    except RuntimeError as e:
        say(f"share: {e}; the bundle is still in {path}. Rerun the same command to resume, or the manual steps:")
        for line in submission_steps(sha, path):
            say(line)
        return EXIT_NOT_SENT
    say(f"share: opened {url}")
    return 0


# ---------------------------------------------------------------- main
def parse(argv):
    ap = argparse.ArgumentParser(prog="k3d-gpu share", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.split("\n\n")[1:]))
    ap.add_argument("--report", help="k3d-gpu report --json output (default: run it, without the cluster)")
    ap.add_argument("--capacity", help="capacity.json of a finished `k3d-gpu capacity` run (default: newest)")
    ap.add_argument("--out", help="bundle file (default ./k3d-gpu-share-<sha12>.json, mode 0600)")
    ap.add_argument("--dry-run", action="store_true", help="print the bundle and stop; write and send nothing")
    ap.add_argument("--submit", action="store_true", help="after a y/N prompt, open a public pull request adding it under data/oracle/")
    ap.add_argument("--no-gh", action="store_true", help="never run gh; print the manual pull-request steps instead")
    ap.add_argument("--server-image", help="NAME@sha256:... when the run did not record the server digest")
    ap.add_argument("--model-id", help="Hugging Face org/name when the served name is not one")
    ap.add_argument("--model-revision", help="40-hex commit of the model weights (k3d-gpu agent status)")
    ap.add_argument("--weights-sha256", help="sha256 of the weights file")
    ap.add_argument("--lever", action="append", help="id=on|off: a lever state you know for this run")
    return ap.parse_args(argv)


def main(argv=None, stdin=None, stdout=None):
    a = parse(argv)
    try:
        cap_path = a.capacity or newest_capacity()
        if not cap_path:
            say("share: no finished capacity run found; run `k3d-gpu capacity` first, or pass --capacity FILE.")
            return EXIT_USAGE
        report = load_json(a.report) if a.report else run_report()
        with open(cap_path) as f:
            cap = json.load(f)
        b = P.project_capacity(report, cap, os.path.dirname(os.path.abspath(cap_path)), root(),
                               version=os.environ.get("K3D_GPU_VERSION") or None,
                               server_image=a.server_image, model_id=a.model_id, revision=a.model_revision,
                               weights_sha256=a.weights_sha256, lever_states=P.parse_levers(a.lever))
    except (P.ProjectionError, OSError, ValueError, KeyError) as e:
        say(f"share: {e}")
        return EXIT_USAGE
    # CI reads the pins from the release tag (validate.release_pin). Use the
    # same source when this checkout has the tag, so a checkout between
    # releases sees the verdict CI will give; otherwise the running script's pins.
    ver = b["tool"]["version"]
    pin = os.environ.get("K3D_GPU_AGENT_IMAGE_PIN")
    tagged = V.release_pin(ver, REPO) if not ver.startswith("git-") else None
    pins = {ver: tagged} if tagged else ({ver: set(pin.split())} if pin and pin.split() else None)
    res = V.validate(b, local_identifiers=local_identifiers(), pins=pins)
    text = pretty(b)
    say(f"# k3d-gpu share: bundle built from {cap_path} and this host's report")
    say(text.rstrip("\n"))
    say(f"sha256: {b['content_sha256']}")
    say(f"size: {len(text.encode())} bytes, {leaf_count(b)} fields")
    say(f"validator: {res.verdict}{' (' + ', '.join(res.tags) + ')' if res.tags else ''}")
    for r in res.reasons:
        say(f"  - {r}")
    say(NOTICE)
    if res.verdict == "reject":
        say("share: the bundle failed validation; it was not written and cannot be submitted.")
        return EXIT_REJECTED
    if a.dry_run:
        say("dry run: nothing written, nothing sent.")
        return 0
    path = a.out or f"k3d-gpu-share-{b['content_sha256'][:12]}.json"
    write_private(path, text)
    say(f"wrote {path} (mode 0600)")
    if not a.submit:
        say("not sent: add --submit to be asked whether to send it.")
        return 0
    if not confirm(stdin, stdout):
        say("not sent.")
        return EXIT_NOT_SENT
    return send(b, path, text, not a.no_gh)


if __name__ == "__main__":
    sys.exit(main())

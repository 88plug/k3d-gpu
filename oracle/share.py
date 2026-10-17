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
  8. send      `gh issue create` on github.com/88plug/k3d-gpu-oracle when gh is
               installed and logged in (and --no-gh is not given); otherwise the
               issue-form URL and the file path are printed. Over 48 KiB the
               bundle does not fit an issue: the pull-request steps are printed.

Nothing touches the network before step 8, and step 8 runs only after the
answer at step 7.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import project as P  # noqa: E402
import validate as V  # noqa: E402

ORACLE_REPO = "88plug/k3d-gpu-oracle"
ISSUE_FORM = f"https://github.com/{ORACLE_REPO}/issues/new?template=submission.yml"
ISSUE_MAX_BYTES = 48 * 1024
NOTICE = ("This bundle carries no identifiers: no hostname, serial numbers, MAC or IP addresses, "
          "user names, paths or timestamps (the week only). A GitHub issue or pull request is public "
          "and shows your GitHub account, so a submission is not anonymous.")
QUESTION = (f"Submit this bundle to github.com/{ORACLE_REPO} as a public issue under your "
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
    answer = stdin.readline().strip().lower()
    return answer in ("y", "yes")


def gh_ready():
    gh = shutil.which("gh")
    if not gh:
        return None
    ok = subprocess.run([gh, "auth", "status"], capture_output=True, check=False).returncode == 0
    return gh if ok else None


def send(b, path, text, use_gh):
    sha = b["content_sha256"]
    size = len(text.encode())
    if size > ISSUE_MAX_BYTES:
        say(f"The bundle is {size} bytes, over the {ISSUE_MAX_BYTES}-byte issue limit this tool uses.")
        say("Send it as a pull request instead (gh repo fork, then add the file and open a PR):")
        say(f"  gh repo fork {ORACLE_REPO} --clone && cd k3d-gpu-oracle")
        say(f"  cp {path} inbox/{sha}.json && git add inbox/{sha}.json && git commit -m 'bundle {sha[:12]}'")
        say(f"  git push && gh pr create --repo {ORACLE_REPO} --title 'bundle {sha[:12]}' --body 'k3d-gpu share'")
        return 0
    gh = gh_ready() if use_gh else None
    if not gh:
        say(f"gh is not available{' (or not logged in)' if use_gh else ''}: open {ISSUE_FORM}")
        say(f"and paste the contents of {path}.")
        return 0
    body = f"k3d-gpu share bundle `{sha}`\n\n```json\n{text}```\n"
    fd, tmp = tempfile.mkstemp(prefix="k3d-gpu-share-", suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        title = f"bundle {sha[:12]} {slug(b['hardware']['cpu']['model'])}"
        rc = subprocess.run([gh, "issue", "create", "--repo", ORACLE_REPO, "--title", title,
                             "--label", "submission", "--body-file", tmp], check=False).returncode
    finally:
        os.unlink(tmp)
    if rc != 0:
        say(f"share: gh issue create failed (exit {rc}); the bundle is still in {path}.")
        return EXIT_NOT_SENT
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
    ap.add_argument("--submit", action="store_true", help="after a y/N prompt, open a public GitHub issue with it")
    ap.add_argument("--no-gh", action="store_true", help="never run gh; print the issue-form URL instead")
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
    pin = os.environ.get("K3D_GPU_AGENT_IMAGE_PIN")
    pins = {b["tool"]["version"]: pin} if pin else None
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

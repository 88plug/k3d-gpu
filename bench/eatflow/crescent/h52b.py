#!/usr/bin/env python3
"""Helpers for h52b.sh (EXPERIMENTS.md "H52 amendment D2").

  h52b.py gate PLAN      print the install plan `k3d-gpu host-setup --plan` saved in PLAN;
                         exit 0 when every command is one of D2 item 2's kinds (the accel-config
                         package, `modprobe vfio-pci`, accel-config work-queue steps, QAT
                         sriov_numvfs writes), else exit 3 and name the others.
  h52b.py reverts OUT    print the reverts host-setup printed in OUT, one per line, in its order
                         (the first block: --apply prints it before its prompt and again after).
"""
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*m")
HEADER = re.compile(r"(?:==> )?Install plan \((\d+) commands?\):")
REVERTS = "DSA/IAA/QAT changes last until reboot"
REVERT = re.compile(r"\s+revert: (.+)")
PACKAGE = "sudo apt-get update && sudo apt-get install -y accel-config"
VFIO = "sudo modprobe vfio-pci"
SRIOV = re.compile(r"echo \d+ \| sudo tee /sys/bus/pci/devices/[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]/sriov_numvfs")
# One accel-config step that configures or enables a device, an engine or a work queue. A step
# that saves, loads, resets or disables a device (the kernel-WQ shrink) is not of this kind.
WQ_STEP = re.compile(r"sudo accel-config (?:config-engine|config-wq|enable-device|enable-wq) "
                     r"(?:dsa|iax)\d+(?:/(?:wq|engine)\d+\.\d+)?(?: --[a-z-]+=[A-Za-z0-9_-]+)*")


def clean(lines):
    return [ANSI.sub("", line.rstrip("\n")) for line in lines]


def read(path):
    with open(path, errors="replace") as f:
        return clean(f)


def plan(lines):
    """(declared count, commands) of the first install plan in lines; (None, []) if none."""
    for i, line in enumerate(lines):
        m = HEADER.fullmatch(line.strip())
        if not m:
            continue
        cmds = []
        for cmd in lines[i + 1:]:
            if not cmd.startswith("    ") or REVERT.fullmatch(cmd):
                break
            cmds.append(cmd.strip())
        return int(m.group(1)), cmds
    return None, []


def allowed(cmd):
    if cmd in (PACKAGE, VFIO) or SRIOV.fullmatch(cmd):
        return True
    return all(WQ_STEP.fullmatch(part) for part in cmd.split(" && "))


def gate(lines):
    """(ok, report lines) for D2 item 2."""
    n, cmds = plan(lines)
    if n is None:
        return False, ["no install plan in the output: nothing to apply"]
    out = [f"{'ok' if allowed(c) else 'NOT ALLOWED'}: {c}" for c in cmds]
    ok = bool(cmds) and len(cmds) == n and all(allowed(c) for c in cmds)
    if len(cmds) != n:
        out.append(f"the header names {n} commands, {len(cmds)} were read")
    out.append(f"gate: {'pass' if ok else 'STOP'} ({len(cmds)} commands)")
    return ok, out


def reverts(lines):
    for i, line in enumerate(lines):
        if REVERTS not in line:
            continue
        cmds = []
        for rev in lines[i + 1:]:
            m = REVERT.fullmatch(rev)
            if not m:
                break
            cmds.append(m.group(1))
        return cmds
    return []


def main(argv):
    if len(argv) != 3 or argv[1] not in ("gate", "reverts"):
        print(__doc__, file=sys.stderr)
        return 2
    lines = read(argv[2])
    if argv[1] == "gate":
        ok, report = gate(lines)
        print("\n".join(report))
        return 0 if ok else 3
    cmds = reverts(lines)
    print("\n".join(cmds))
    return 0 if cmds else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

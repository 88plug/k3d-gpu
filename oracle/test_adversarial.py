#!/usr/bin/env python3
"""test_adversarial.py: `k3d-gpu share` as a first-time user on another Intel box.

Each case simulates one host the maintainer box is not: no docker, no GPU,
an N100, WSL2, a generic login/host name, an interrupted --submit, Ctrl-C at
the prompt. Uses fixtures and stubs only (no cluster, no network).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_oracle as T  # noqa: E402

S, V, P = T.S, T.V, T.P


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


def copy_root(name, dst):
    shutil.copytree(os.path.join(T.FIXTURES, name, "sysroot"), dst, symlinks=True)
    return dst


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


class HostShapes(unittest.TestCase):
    """Projection + validation on hosts unlike the maintainer box."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=T.WORK)

    def bundle_for(self, root):
        rep = T.report_for(root)
        b = T.cap_bundle(report=rep, root=root)
        return b, T.check(b, local_identifiers=S.local_identifiers(root))

    def test_n100_alderlake_n(self):
        root = copy_root("alderlake-n", os.path.join(self.tmp, "n100"))
        b, res = self.bundle_for(root)
        self.assertNotEqual(res.verdict, "reject", res.reasons)

    def test_no_intel_gpu(self):
        root = copy_root("alderlake-n", os.path.join(self.tmp, "nogpu"))
        shutil.rmtree(os.path.join(root, "sys", "class", "drm"), ignore_errors=True)
        b, res = self.bundle_for(root)
        self.assertEqual(b["hardware"]["intel_gpus"], [])
        self.assertNotEqual(res.verdict, "reject", res.reasons)

    def test_wsl2_no_dmi_drm_powercap(self):
        root = copy_root("raptorlake-stale-guc", os.path.join(self.tmp, "wsl"))
        for rel in ("sys/class/drm", "sys/class/dmi", "sys/class/powercap", "sys/devices/system/node"):
            shutil.rmtree(os.path.join(root, rel), ignore_errors=True)
        write(root, "proc/version", "Linux version 5.15.153.1-microsoft-standard-WSL2 (root@1c602f52c2e4) "
                                    "(gcc (GCC) 11.2.0) #1 SMP Fri Mar 29 23:14:13 UTC 2024\n")
        write(root, "proc/sys/kernel/osrelease", "5.15.153.1-microsoft-standard-WSL2\n")
        write(root, "proc/sys/kernel/hostname", "DESKTOP-7Q2K9LM\n")
        b, res = self.bundle_for(root)
        self.assertNotEqual(res.verdict, "reject", res.reasons)
        self.assertNotIn("DESKTOP-7Q2K9LM", json.dumps(b))
        self.assertNotIn("microsoft", json.dumps(b).lower())


class GenericNames(unittest.TestCase):
    """A login or host name that is an ordinary word must not block an honest
    bundle. Fedora CoreOS and Flatcar log in as `core`; the bundle of any
    Intel Core CPU carries "Core(TM)" and the keys p_core_cpus/e_core_cpus."""

    def setUp(self):
        self.b = T.cap_bundle()
        self.assertEqual(T.check(self.b).verdict, "accept")

    def test_login_core_on_a_core_cpu(self):
        old = os.environ.get("USER")
        os.environ["USER"] = "core"
        try:
            ids = S.local_identifiers("")
        finally:
            if old is None:
                os.environ.pop("USER", None)
            else:
                os.environ["USER"] = old
        self.assertIn("core", ids)
        res = T.check(self.b, local_identifiers=ids)
        self.assertNotEqual(res.verdict, "reject", res.reasons)

    def test_generic_hostnames(self):
        for host in ("server", "compute", "node", "hardware"):
            with self.subTest(host=host):
                res = T.check(self.b, local_identifiers=[host])
                self.assertNotEqual(res.verdict, "reject", res.reasons)

    def test_real_identifier_still_blocks(self):
        b = T.reseal({**self.b, "hardware": {**self.b["hardware"], "cpu": {
            **self.b["hardware"]["cpu"], "model": "Intel(R) Core(TM) lab-box-7f3a"}}})
        res = T.check(b, local_identifiers=["lab-box-7f3a"])
        self.assertEqual(res.verdict, "reject", res.reasons)


class NoDocker(unittest.TestCase):
    """share needs report --no-cluster only: no docker, kubectl or k3d."""

    def test_dry_run_without_docker_kubectl_k3d(self):
        tmp = tempfile.mkdtemp(dir=T.WORK)
        farm = os.path.join(tmp, "bin")
        os.makedirs(farm)
        seen = set()
        for d in os.environ["PATH"].split(os.pathsep):
            if not os.path.isdir(d):
                continue
            for n in os.listdir(d):
                if n in seen or n in ("docker", "kubectl", "k3d", "gh", "helm", "nvidia-smi"):
                    continue
                seen.add(n)
                os.symlink(os.path.join(d, n), os.path.join(farm, n))
        env = {**os.environ, "PATH": farm, "K3D_GPU_SYSROOT": T.HOST_ROOT,
               "XDG_STATE_HOME": os.path.join(tmp, "state")}
        r = subprocess.run(["bash", T.LAUNCHER, "share", "--capacity", os.path.join(T.RUN, "capacity.json"),
                            "--server-image", T.AGENT_IMAGE, "--model-id", T.MODEL, "--dry-run"],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertIn("validator: accept", r.stdout)


GH_RESUME = T.GH_STUB.replace(
    '  "api -X PUT "*)',
    '  "api -X POST "*) echo \'{"message":"Reference already exists","status":"422"}\' >&2; exit 1 ;;\n'
    '  "api -X PUT "*)')


class InterruptedSubmit(T.ShareTest):
    """A first --submit died after the branch was created on the fork (Ctrl-C,
    lost Wi-Fi, a proxy reset). Running it again must finish the pull request,
    not tell the user to push a branch that already exists."""

    def setUp(self):
        super().setUp()
        T.TC.stub_bin(self.bin, "gh", GH_RESUME.replace("#!/bin/sh\n", ""))

    def test_rerun_after_branch_exists_opens_the_pr(self):
        rc, out = self.run_tty("y", "--submit")
        self.assertEqual(rc, 0, out)
        self.assertIn("pull/999", out)

    def test_ctrl_c_at_the_prompt_is_clean(self):
        # Ctrl-C at the y/N prompt: no traceback, nothing sent.
        import pty, select, signal, time
        master, slave = pty.openpty()
        p = subprocess.Popen(self.args("--submit"), cwd=self.tmp, env=self.env, stdin=slave, stdout=slave,
                             stderr=slave, close_fds=True)
        os.close(slave)
        out, deadline, sent = b"", time.time() + 120, False
        while time.time() < deadline and p.poll() is None:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    out += os.read(master, 65536)
                except OSError:
                    break
            if not sent and b"[y/N]" in out:
                p.send_signal(signal.SIGINT)
                sent = True
        p.wait(timeout=10)
        while select.select([master], [], [], 0.2)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        os.close(master)
        out = out.decode(errors="replace")
        self.assertTrue(sent, out)
        self.assertNotIn("Traceback", out, out[-700:])
        self.assertEqual([c for c in self.gh_calls() if c != "auth status"], [])


# The inherited ShareTest cases run in test_oracle.py already.
for _n in [n for n in dir(T.ShareTest) if n.startswith("test_")]:
    setattr(InterruptedSubmit, _n, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for bench/h62r (EXPERIMENTS.md "H62r pre-registration"): H62's runner re-pinned to the
GA tags (EAS v1.2.0, EAT release-2.0.0, enterprise-inference v1.1.0). The pins, the guests' QEMU
command lines and cloud-init, the setup gates, D1 as a check (the GA layer manifest names the
commits itself), the documented edit and D2, D3's sibling read (EAS's probe against the guest
kernel's sibling lists, and the reserved cpuset it writes), the pod and cgroup reads behind the
idle-arm check, B5's weight check, the re-run trigger, and the shell scripts against stubs. No VM,
no cluster, no network."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h62r  # noqa: E402

REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
GIB_KIB = 1024 * 1024


def read(*path):
    with open(os.path.join(*path)) as f:
        return f.read()


def run(*argv, stdin=None, env=None):
    return subprocess.run([sys.executable, os.path.join(HERE, "h62r.py"), *argv], input=stdin,
                          capture_output=True, text=True, env=env)


# EAS v1.2.0 (a051f00) configs/repos/repos.agent-toolkit.yaml, lines 12-27, verbatim (the
# agent-toolkit comment carries a trailing space).
MANIFEST = "".join(ln + "\n" for ln in (
    'repos:',
    '  - layer: "inference"',
    '    url: "https://github.com/intel/enterprise-inference"',
    '    dest: "enterprise.ai-inference"',
    '    rev: "d035b1ef1981fbf5084ff05c9a337e620478bb87" # v1.1.0',
    '    deployment_subdir: ""',
    '    config_dir: ""',
    '    default_flavour: ""',
    '    model_catalog: "model_manager/models.yaml"',
    '',
    '  - layer: "agent-toolkit"',
    '    url: "https://github.com/intel/enterprise-agent-toolkit"',
    '    dest: "enterprise.agent-toolkit"',
    '    rev: "7b827e2d95cec826a20c7e3f5a30eb4818ed2992" # release-2.0.0 ',
    '    deployment_subdir: ""',
    '    config_dir: ""',
    '    default_flavour: ""'))

# EAT release-2.0.0 (7b827e2) config.yaml, lines 33-36 (unchanged from 036b662; the file init
# seeds as env/local/config.agent-toolkit.yaml), and the same key commented in a doc block.
CONFIG = textwrap.dedent('''\
    # Model(s) to deploy, from the catalog (env/local/models.yaml)
    # llm_services_autodeploy_models: ["qwen3-coder-30b", "bge-base-en"]
    llm_services_autodeploy_models: ["qwen2-5-coder-14b"]
    redis_stack_enabled: true
    ''')

# enterprise-inference v1.1.0 (d035b1e) model_manager/models.yaml (unchanged from 170eb3a), its
# first entry's commented name line (H62 amendment V3) and the gemma-4-26b entry between two
# others, at the file's indentation.
MODELS = ('models:\n  - name: qwen3-0-6b                    # deploy name + the "model" id used in API requests\n'
          '    model_id: Qwen/Qwen3-0.6B\n\n' + textwrap.indent(textwrap.dedent('''\
      - name: gemma-4-26b-tp2
        model_id: google/gemma-4-26B-A4B-it
        category: llm

      - name: gemma-4-26b
        model_id: google/gemma-4-26B-A4B-it
        category: llm
        runtime: vllm
        server_version: "0.28.0"
        cpu: auto                        # one NUMA node per TP rank, per node
        memory: 128Gi

      - name: qwen3guard
        model_id: Qwen/Qwen3Guard-Gen-0.6B
        category: llm
    '''), "  "))

# The global_config.yaml init seeds (EAS v1.2.0 configs/defaults/global_config.yaml, trimmed):
# no nri_reserved_cpu_list line, so EAS's role default ("") applies.
GLOBAL = textwrap.dedent('''\
    # --- CPU Policy ---
    kubernetes_cpu_policy: "nri-balloons"

    kubernetes_accelerator: "cpu"
    ''')

# thread_siblings_list of each CPU. GUEST: H62's QEMU guest (-smp 96,sockets=1,cores=48,threads=2),
# siblings adjacent, so Linux writes each pair as a range ("0-1"; read in a spike guest of that
# shape, 2026-11-06). HOST: crescent itself (read 2026-11-07T03:36Z: cpu0 "0,48", cpu1 "1,49").
GUEST = {i: f"{i - i % 2}-{i - i % 2 + 1}" for i in range(96)}
HOST = {i: (f"{i},{i + 48}" if i < 48 else f"{i - 48},{i}") for i in range(96)}
NOSMT = {i: str(i) for i in range(96)}
ODD = ",".join(str(i) for i in range(1, 96, 2))


def probe(tpc, logical=96):
    """EAS v1.2.0 roles/nri_cpu_balloons/files/probe-node-topology.sh's first four lines."""
    return f"NRI_NUMA=1\nNRI_LOGICAL={logical}\nNRI_TPC={tpc}\nNRI_PHYSICAL={logical // tpc}\nNRI_PKGNODE={{0:[0]}}\n"


class PinsTest(unittest.TestCase):
    """Every pin the pre-registration names, in one table the scripts read: B5 at the GA tags,
    A1 and the guests as H62 had them (Docker CE at its current patch release)."""

    def test_b5_is_at_the_ga_tags(self):
        p = h62r.PINS
        self.assertEqual((p["eas_tag"], p["eas_sha"]), ("v1.2.0", "a051f00fcb2dd473fe763610471cd509db82c433"))
        self.assertEqual((p["ei_tag"], p["ei_sha"]), ("v1.1.0", "d035b1ef1981fbf5084ff05c9a337e620478bb87"))
        self.assertEqual((p["eat_tag"], p["eat_sha"]), ("release-2.0.0", "7b827e2d95cec826a20c7e3f5a30eb4818ed2992"))
        self.assertEqual(p["eas_repo"], "https://github.com/intel/enterprise-ai-solutions.git")
        self.assertEqual((p["catalog_name"], p["stock_model_id"]), ("gemma-4-26b", "google/gemma-4-26B-A4B-it"))
        self.assertEqual(p["vllm_cpu_image"], "vllm/vllm-openai-cpu:v0.28.0@sha256:"
                                              "197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647")

    def test_a1_weights_and_guests(self):
        p = h62r.PINS
        self.assertEqual(p["qat_repo"], "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized")
        self.assertEqual(p["qat_rev"], "f1e06dc520982d9b9edd76859fdb7ab209449949")
        self.assertEqual(p["node_image"], "cryptoandcoffee/k3d-gpu:0.1.61@sha256:"
                                          "8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c")
        self.assertEqual(p["ovms_image"], "openvino/model_server:2026.4.1-gpu@sha256:"
                                          "a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58")
        self.assertEqual(p["cloud_image_sha256"], "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94")
        self.assertTrue(p["cloud_image_url"].endswith("/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img"))
        self.assertEqual(p["cloud_image_bytes"], 621284352)
        self.assertEqual(p["qemu_base"], "ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c")
        self.assertEqual((p["qemu_pkg"], p["genisoimage_pkg"]), ("1:10.2.1+ds-1ubuntu3.3", "9:1.1.11-5"))
        self.assertEqual(p["docker_ce"], "5:29.9.3-1~ubuntu.24.04~noble")
        self.assertEqual(p["containerd"], "2.4.2-3~ubuntu.24.04~noble")
        self.assertEqual(p["docker_gpg_fpr"], "9DC858229FC7DD38854AE2D88D81803C0EBFCD88")
        self.assertEqual((p["jq"], p["python3_yaml"]), ("1.7.1-3ubuntu0.24.04.2", "6.0.1-2build2"))
        self.assertEqual(h62r.QEMU_IMAGE, "h62r-qemu:1-10.2.1-ds-1ubuntu3.3")

    def test_no_branch_pins_left(self):
        # D1 at rc.1 rewrote two branch revs; the GA manifest names commits, so no branch is pinned.
        self.assertFalse({"ei_branch", "eat_branch"} & set(h62r.PINS))

    def test_cli_prints_one_pin(self):
        r = run("pin", "eas_sha")
        self.assertEqual((r.returncode, r.stdout), (0, "a051f00fcb2dd473fe763610471cd509db82c433\n"))
        self.assertEqual(run("pin", "nope").returncode, 2)

    def test_the_qemu_image_is_built_from_the_pins(self):
        df = read(HERE, "qemu.Dockerfile")
        self.assertIn(f"FROM {h62r.PINS['qemu_base']}\n", df)
        for pkg in ("qemu-system-x86", "qemu-utils"):
            self.assertIn(f"{pkg}={h62r.PINS['qemu_pkg']}", df)
        self.assertIn(f"genisoimage={h62r.PINS['genisoimage_pkg']}", df)

    def test_the_cloud_image_is_checked_before_use(self):
        vm = read(HERE, "vm.sh")
        self.assertIn('P pin cloud_image_sha256', vm)
        self.assertIn('sha256sum -c', vm)


class VmSpecTest(unittest.TestCase):
    def test_two_guests_of_h62s_shape_under_their_own_names(self):
        a, b = h62r.VMS["a"], h62r.VMS["b"]
        self.assertEqual((a["arm"], a["mem_gib"], a["disk_gib"], b["arm"], b["mem_gib"], b["disk_gib"]),
                         ("A1", 96, 200, "B5", 192, 400))
        self.assertEqual(h62r.SMP, "96,sockets=1,cores=48,threads=2")
        self.assertEqual(h62r.VCPUS, 96)
        self.assertEqual((a["name"], a["host"], b["name"], b["host"]), ("h62r-vm-a", "h62r-a", "h62r-vm-b", "h62r-b"))
        # No k3dgpu prefix: hold.sh's cleanup never removes them; another row's serving covariate
        # counts them as foreign load.
        for v in (a, b):
            self.assertFalse(v["name"].startswith("k3dgpu"), v["name"])
        ports = {a["ssh_port"], a["serve_port"], b["ssh_port"], b["serve_port"]}
        self.assertEqual(ports, {18842, 18843, 18851, 18852})
        self.assertFalse(ports & {18822, 18823, 18831, 18832})                 # H62's ports

    def test_memory_gate_is_both_guests_plus_48_gib(self):
        self.assertEqual(h62r.mem_needed_kib(), (96 + 192 + 48) * GIB_KIB)


class QemuArgvTest(unittest.TestCase):
    def test_vm_b(self):
        argv = h62r.qemu_argv("b", "/st", "h62r-qemu:x", uid=1000, gid=1000, kvm_gid=992)
        self.assertEqual(argv[:5], ["docker", "run", "-d", "--name", "h62r-vm-b"])
        for pair in (["--network", "host"], ["--device", "/dev/kvm"], ["--user", "1000:1000"],
                     ["--group-add", "992"], ["-v", "/st:/h62r"], ["-cpu", "host"], ["-m", "192G"],
                     ["-smp", "96,sockets=1,cores=48,threads=2"], ["-machine", "q35,accel=kvm"],
                     ["--label", "io.k3d-gpu.h62r=vm"]):
            self.assertTrue(any(argv[i:i + 2] == pair for i in range(len(argv))), pair)
        net = argv[argv.index("-netdev") + 1]
        self.assertEqual(net, "user,id=n0,hostfwd=tcp:127.0.0.1:18843-:22,hostfwd=tcp:127.0.0.1:18852-:18852")
        self.assertNotIn("0.0.0.0", " ".join(argv))
        self.assertNotIn("-virtfs", argv)
        self.assertIn("file=/h62r/b/disk.qcow2,if=virtio,format=qcow2", argv)
        self.assertIn("file=/h62r/b/seed.iso,if=virtio,format=raw,readonly=on", argv)
        self.assertEqual(argv[argv.index("h62r-qemu:x") + 1], "qemu-system-x86_64")

    def test_vm_a_shares_the_ir_read_only(self):
        argv = h62r.qemu_argv("a", "/st", "img", share="/home/u/ir", uid=1, gid=1, kvm_gid=2)
        self.assertIn("/home/u/ir:/share:ro", argv)
        self.assertEqual(argv[argv.index("-virtfs") + 1],
                         "local,path=/share,mount_tag=h62rshare,security_model=none,readonly=on")
        self.assertEqual(argv[argv.index("-m") + 1], "96G")
        with self.assertRaises(ValueError):
            h62r.qemu_argv("b", "/st", "img", share="/x", uid=1, gid=1, kvm_gid=2)
        with self.assertRaises(ValueError):
            h62r.qemu_argv("a", "/st", "img", uid=1, gid=1, kvm_gid=2)          # A1's IR is required

    def test_cli_one_argument_per_line(self):
        r = run("qemu-argv", "b", "/st", "img", "1000", "1000", "992")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), h62r.qemu_argv("b", "/st", "img", uid=1000, gid=1000, kvm_gid=992))


class CloudInitTest(unittest.TestCase):
    KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample h62r"

    def test_user_data(self):
        u = h62r.user_data("b", self.KEY)
        self.assertTrue(u.startswith("#cloud-config\n"))
        self.assertIn(f"  - {self.KEY}\n", u)
        self.assertIn("ssh_pwauth: false\n", u)
        self.assertIn("package_upgrade: false\n", u)
        self.assertIn("hostname: h62r-b\n", u)
        for unit in ("unattended-upgrades.service", "apt-daily.timer", "apt-daily-upgrade.timer"):
            self.assertIn(unit, u)

    def test_meta_data_and_key_checks(self):
        self.assertEqual(h62r.meta_data("a"), "instance-id: h62r-a\nlocal-hostname: h62r-a\n")
        for bad in ("", "ssh-ed25519 AAAA\nrm -rf /", "not-a-key"):
            with self.assertRaises(ValueError):
                h62r.user_data("a", bad)


class HostFactsTest(unittest.TestCase):
    def test_reads_meminfo_df_and_probes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        mi = os.path.join(tmp, "meminfo")
        with open(mi, "w") as f:
            f.write("MemTotal:       527700000 kB\nMemAvailable:   374341632 kB\n")
        df = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 3600000000 1100000000 2400000000 31% /\n"
        seen = []
        f = h62r.host_facts(mi, df, lambda url: seen.append(url) or "registry.k8s.io" not in url)
        self.assertEqual((f["mem_available_kib"], f["root_used_pct"]), (374341632, 31))
        self.assertEqual(f["reach"], {"huggingface.co": True, "registry-1.docker.io": True, "registry.k8s.io": False})
        self.assertEqual(len(seen), 3)


class IrVerifyTest(unittest.TestCase):
    def test_manifest_check(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        files = {"openvino_model.bin": b"bin" * 10, "openvino_model.xml": b"<xml/>"}
        man = {"files": {}}
        for n, data in files.items():
            with open(os.path.join(tmp, n), "wb") as f:
                f.write(data)
            man["files"][n] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        with open(os.path.join(tmp, "manifest.json"), "w") as f:
            json.dump(man, f)
        r = run("ir-verify", tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 files, 0 bad", r.stdout)
        with open(os.path.join(tmp, "openvino_model.xml"), "ab") as f:
            f.write(b" ")
        r = run("ir-verify", tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 bad", r.stdout)


class SshConfigTest(unittest.TestCase):
    def test_both_guests(self):
        c = h62r.ssh_config("/home/u/k3dgpu-bench/h62r")
        for host, port in (("h62r-a", 18842), ("h62r-b", 18843)):
            blk = c[c.index(f"Host {host}\n"):]
            self.assertIn(f"  Port {port}\n", blk)
        for line in ("  HostName 127.0.0.1\n", "  User ubuntu\n", "  IdentityFile /home/u/k3dgpu-bench/h62r/id_ed25519\n",
                     "  UserKnownHostsFile /home/u/k3dgpu-bench/h62r/known_hosts\n", "  StrictHostKeyChecking accept-new\n",
                     "  ControlMaster auto\n", "  ControlPath /home/u/k3dgpu-bench/h62r/cm-%C\n", "  BatchMode yes\n"):
            self.assertEqual(c.count(line), 2, line)


class GatesTest(unittest.TestCase):
    HOST = {"mem_available_kib": 357 * GIB_KIB, "root_used_pct": 31,
            "reach": {"huggingface.co": True, "registry-1.docker.io": True, "registry.k8s.io": True}}

    def failed(self, phase, facts):
        return [m for ok, m in h62r.gates(phase, facts) if not ok]

    def test_host(self):
        self.assertEqual(self.failed("host", self.HOST), [])
        self.assertEqual(self.failed("host", dict(self.HOST, mem_available_kib=h62r.mem_needed_kib())), [])
        self.assertEqual(len(self.failed("host", dict(self.HOST, mem_available_kib=h62r.mem_needed_kib() - 1))), 1)
        self.assertEqual(self.failed("host", dict(self.HOST, root_used_pct=90)), [])
        self.assertEqual(len(self.failed("host", dict(self.HOST, root_used_pct=91))), 1)
        unreach = dict(self.HOST, reach={"huggingface.co": False, "registry-1.docker.io": True, "registry.k8s.io": True})
        self.assertIn("huggingface.co", self.failed("host", unreach)[0])

    def test_guest_amx_on_every_vcpu(self):
        self.assertEqual(self.failed("guest", {"amx_tile": {"a": 96, "b": 96}, "nproc": {"a": 96, "b": 96}}), [])
        self.assertEqual(len(self.failed("guest", {"amx_tile": {"a": 96, "b": 0}, "nproc": {"a": 96, "b": 96}})), 1)
        self.assertEqual(len(self.failed("guest", {"amx_tile": {"a": 96}, "nproc": {"a": 96}})), 2)   # b unread

    def test_nfd_and_weights(self):
        self.assertEqual(self.failed("nfd", {"amxbf16": "true"}), [])
        self.assertEqual(len(self.failed("nfd", {"amxbf16": None})), 1)
        self.assertEqual(self.failed("weights", {"bad": 0, "missing": 0, "checked": 3}), [])
        self.assertEqual(len(self.failed("weights", {"bad": 1, "missing": 0, "checked": 3})), 1)
        self.assertEqual(len(self.failed("weights", {"bad": 0, "missing": 0, "checked": 0})), 1)
        with self.assertRaises(ValueError):
            h62r.gates("other", {})

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "f.json")
        with open(p, "w") as f:
            json.dump(self.HOST, f)
        r = run("gates", "host", p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        with open(p, "w") as f:
            json.dump(dict(self.HOST, root_used_pct=95), f)
        r = run("gates", "host", p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)


class EasCheckTest(unittest.TestCase):
    """D1 at the GA tags: the layer manifest already names the release notes' commits, with the
    release on each line's comment, so it is checked, not rewritten."""

    def test_the_ga_manifest_passes_unchanged(self):
        self.assertEqual(h62r.eas_check(MANIFEST),
                         ["inference d035b1ef1981fbf5084ff05c9a337e620478bb87 (v1.1.0)",
                          "agent-toolkit 7b827e2d95cec826a20c7e3f5a30eb4818ed2992 (release-2.0.0)"])

    def test_refuses_anything_else(self):
        for bad in (MANIFEST.replace('rev: "d035b1ef', 'rev: "0000000f'),             # another commit
                    MANIFEST.replace("# v1.1.0", "# v1.0.1"),                          # another release named
                    MANIFEST.replace('"7b827e2d95cec826a20c7e3f5a30eb4818ed2992" # release-2.0.0 ',
                                     '"release-2.0.0"'),                               # a branch, as at rc.1
                    MANIFEST.replace('- layer: "inference"', '- layer: "other"'),        # a layer missing
                    MANIFEST + '    rev: "x"\n'):                                       # a second rev in a block
            with self.assertRaises(ValueError):
                h62r.eas_check(bad)

    def test_cli_reads_without_writing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "repos.agent-toolkit.yaml")
        with open(p, "w") as f:
            f.write(MANIFEST)
        r = run("eas-check", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok inference d035b1ef1981fbf5084ff05c9a337e620478bb87 (v1.1.0)", r.stdout)
        self.assertEqual(read(p), MANIFEST)
        with open(p, "w") as f:
            f.write(MANIFEST.replace("# v1.1.0", "# main"))
        self.assertEqual(run("eas-check", p).returncode, 2)
        self.assertEqual(run("eas-pin", p).returncode, 2)                              # no rewrite command


class EasEditTest(unittest.TestCase):
    """The one documented edit (the catalog value) and D2 (gemma-4-26b's weights), as in H62."""

    def test_autodeploy_gemma_only_on_the_live_line(self):
        out = h62r.set_autodeploy(CONFIG, "gemma-4-26b")
        self.assertIn('\nllm_services_autodeploy_models: ["gemma-4-26b"]\n', out)
        self.assertIn('# llm_services_autodeploy_models: ["qwen3-coder-30b", "bge-base-en"]\n', out)
        self.assertEqual(h62r.set_autodeploy(out, "gemma-4-26b"), out)
        with self.assertRaises(ValueError):
            h62r.set_autodeploy(CONFIG.replace("llm_services_autodeploy_models: [\"qwen", "#x: [\"qwen"), "gemma-4-26b")

    def test_d2_moves_only_gemma_4_26b_to_the_qat_weights(self):
        out = h62r.set_model_id(MODELS, "gemma-4-26b", "google/gemma-4-26B-A4B-it",
                                "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized")
        self.assertEqual(sum(1 for a, b in zip(MODELS.splitlines(), out.splitlines()) if a != b), 1)
        self.assertIn("model_id: Qwen/Qwen3-0.6B\n", out)                              # commented name line parsed
        blocks = out.split("\n\n")
        self.assertIn("model_id: google/gemma-4-26B-A4B-it\n", blocks[1])           # the tp2 entry keeps its id
        self.assertIn("model_id: google/gemma-4-26B-A4B-it-qat-q4_0-unquantized\n", blocks[2])
        with self.assertRaises(ValueError):
            h62r.set_model_id(MODELS, "nope", "a", "b")
        with self.assertRaises(ValueError):
            h62r.set_model_id(MODELS, "gemma-4-26b", "google/other", "b")

    def test_cli_edits_an_env_and_prints_the_diffs(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for name, text in (("config.agent-toolkit.yaml", CONFIG), ("models.yaml", MODELS), ("global_config.yaml", GLOBAL)):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(text)
        r = run("eas-edit", tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('+llm_services_autodeploy_models: ["gemma-4-26b"]', r.stdout)
        self.assertIn("+    model_id: google/gemma-4-26B-A4B-it-qat-q4_0-unquantized", r.stdout)
        self.assertEqual(read(tmp, "global_config.yaml"), GLOBAL)                       # not this step's file
        self.assertEqual(run("eas-edit", tmp).stdout, "")


class SiblingReadTest(unittest.TestCase):
    """D3's sibling read. EAS v1.2.0's probe counts the comma fields of cpu0's
    thread_siblings_list. In the guest Linux writes each adjacent sibling pair as a range ("0-1"),
    so the probe reads 1 thread per core and EAS's default 'siblings' profile falls back to
    'standard' (12 reserved CPUs): H62's 76-CPU gemma pod. On crescent's host ("0,48") it reads 2
    and reserves the 48 siblings. The sibling read supplies that reservation through EAS's own
    nri_reserved_cpu_list, and only when the probe and the kernel disagree in that way."""

    def test_guest_where_the_probe_misreads_gets_the_odd_vcpus(self):
        self.assertEqual(h62r.sibling_read(GUEST, probe(1)), ODD)
        self.assertEqual(len(ODD.split(",")), 48)

    def test_host_numbering_needs_nothing(self):
        self.assertEqual(h62r.sibling_read(HOST, probe(2)), "")

    def test_no_smt_needs_nothing(self):
        self.assertEqual(h62r.sibling_read(NOSMT, probe(1)), "")

    def test_any_other_disagreement_stops(self):
        for lists, text in ((NOSMT, probe(2)),                                    # the probe sees SMT the kernel does not
                            ({**GUEST, 95: "95"}, probe(1)),                # uneven threads per core
                            ({i: f"{i - i % 4}-{i - i % 4 + 3}" for i in range(96)}, probe(1)),   # 4 threads per core
                            (GUEST, "NRI_NUMA=1\n"),                              # no NRI_TPC line
                            ({}, probe(1))):                                      # no CPU read
            with self.assertRaises(ValueError):
                h62r.sibling_read(lists, text)

    def test_cli_reads_a_sysfs_cpu_tree(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for i, s in GUEST.items():
            d = os.path.join(tmp, "cpu", f"cpu{i}", "topology")
            os.makedirs(d)
            with open(os.path.join(d, "thread_siblings_list"), "w") as f:
                f.write(s + "\n")
        for extra in ("cpufreq", "cpuidle", "cpu.txt"):                           # siblings of cpuN in sysfs
            os.makedirs(os.path.join(tmp, "cpu", extra), exist_ok=True)
        p = os.path.join(tmp, "probe.txt")
        with open(p, "w") as f:
            f.write(probe(1))
        r = run("sibling-read", os.path.join(tmp, "cpu"), p)
        self.assertEqual((r.returncode, r.stdout), (0, ODD + "\n"), r.stderr)
        with open(p, "w") as f:
            f.write(probe(2))                                                     # the probe agrees: nothing
        self.assertEqual(run("sibling-read", os.path.join(tmp, "cpu"), p).stdout, "")
        with open(p, "w") as f:
            f.write(probe(4))                                                     # it disagrees otherwise: stop
        self.assertEqual(run("sibling-read", os.path.join(tmp, "cpu"), p).returncode, 2)


class ReserveTest(unittest.TestCase):
    """The reserved cpuset goes into env/local/global_config.yaml as nri_reserved_cpu_list, the
    key EAS's docs/customize/nri_cpu_balloons.md documents there (the installer passes
    global_config.yaml to every playbook as extra vars)."""

    def test_appends_the_documented_key(self):
        out = h62r.reserve(GLOBAL, ODD)
        self.assertTrue(out.startswith(GLOBAL))
        self.assertIn(f'\nnri_reserved_cpu_list: "{ODD}"\n', out)
        self.assertEqual(out.count("nri_reserved_cpu_list:"), 1)
        self.assertEqual(h62r.reserve(out, ODD), out)                                   # a second run changes nothing

    def test_fills_the_documented_empty_default(self):
        out = h62r.reserve(GLOBAL + 'nri_reserved_cpu_list: ""\n', ODD)
        self.assertEqual(out, GLOBAL + f'nri_reserved_cpu_list: "{ODD}"\n')

    def test_refuses_another_value_or_a_bad_cpuset(self):
        with self.assertRaises(ValueError):
            h62r.reserve(GLOBAL + 'nri_reserved_cpu_list: "0-3"\n', ODD)
        for bad in ("", "1,3,", "a-b", '1"\nx: y'):
            with self.assertRaises(ValueError):
                h62r.reserve(GLOBAL, bad)

    def test_cli_edits_in_place_and_prints_the_diff(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "global_config.yaml")
        with open(p, "w") as f:
            f.write(GLOBAL)
        r = run("reserve", ODD, p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'+nri_reserved_cpu_list: "{ODD}"', r.stdout)
        self.assertEqual(read(p), h62r.reserve(GLOBAL, ODD))
        self.assertEqual(run("reserve", ODD, p).stdout, "")


def pod(name, uid, ready=True, deleting=False, created="2026-10-30T00:00:00Z", phase="Running"):
    meta = {"name": name, "uid": uid, "creationTimestamp": created}
    if deleting:
        meta["deletionTimestamp"] = "2026-10-30T00:01:00Z"
    return {"metadata": meta, "status": {"phase": phase,
                                         "conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


class PodTest(unittest.TestCase):
    def test_pick_a_new_ready_pod(self):
        old = pod("p0", "u0", deleting=True)
        self.assertIsNone(h62r.pick_pod({"items": [old, pod("p1", "u1", ready=False)]}, {"u0"}))
        self.assertEqual(h62r.pick_pod({"items": [old, pod("p1", "u1")]}, {"u0"}), ("p1", "u1"))
        self.assertIsNone(h62r.pick_pod({"items": [pod("p0", "u0")]}, {"u0"}))
        self.assertEqual(h62r.pick_pod({"items": [pod("p0", "u0")]}, set()), ("p0", "u0"))
        two = {"items": [pod("p1", "u1", created="2026-10-30T00:00:01Z"), pod("p2", "u2", created="2026-10-30T00:00:09Z")]}
        self.assertEqual(h62r.pick_pod(two, set()), ("p2", "u2"))
        self.assertIsNone(h62r.pick_pod({"items": [pod("p1", "u1", phase="Pending")]}, set()))

    def test_cli(self):
        doc = json.dumps({"items": [pod("p0", "u0", deleting=True), pod("p1", "u1")]})
        self.assertEqual(run("uids", stdin=doc).stdout, "u0,u1\n")
        r = run("pick-pod", "u0", stdin=doc)
        self.assertEqual((r.returncode, r.stdout), (0, "p1 u1\n"))
        self.assertEqual(run("pick-pod", "u0,u1", stdin=doc).returncode, 1)

    def test_model_mount(self):
        p = {"spec": {"volumes": [{"name": "home", "emptyDir": {}},
                                  {"name": "model-cache", "persistentVolumeClaim": {"claimName": "model-store"}}],
                      "containers": [{"name": "main", "volumeMounts": [
                          {"name": "home", "mountPath": "/home"},
                          {"name": "model-cache", "mountPath": "/mnt/models", "subPath": "gemma-4-26b"}]}]}}
        self.assertEqual(h62r.model_mount(p, "main"), "/mnt/models")
        with self.assertRaises(ValueError):
            h62r.model_mount(p, "other")


class VerifyScriptTest(unittest.TestCase):
    """B5's weights, read in its pod: every LFS file of the QAT checkpoint at f1e06dc5."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.d = os.path.join(self.tmp, "models")
        os.makedirs(os.path.join(self.d, ".cache", "huggingface", "download"))
        files = {"model-00001.safetensors": b"w1" * 1000, "config.json": b"{}"}
        sib = []
        for n, data in files.items():
            with open(os.path.join(self.d, n), "wb") as f:
                f.write(data)
            with open(os.path.join(self.d, ".cache", "huggingface", "download", n + ".metadata"), "w") as f:
                f.write(h62r.PINS["qat_rev"] + "\netag\n1730000000.0\n")
            s = {"rfilename": n, "size": len(data)}
            if n.endswith(".safetensors"):
                s["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            sib.append(s)
        self.sib = {"sha": h62r.PINS["qat_rev"], "siblings": sib}
        self.script = os.path.join(self.tmp, "verify.py")
        with open(self.script, "w") as f:
            f.write(h62r.verify_script(self.sib))

    def check(self):
        r = subprocess.run([sys.executable, self.script, self.d], capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout.splitlines()[-1])

    def test_matching_weights_pass_and_name_their_commit(self):
        rc, s = self.check()
        self.assertEqual((rc, s["bad"], s["missing"], s["checked"], s["revisions"]), (0, 0, 0, 1, [h62r.PINS["qat_rev"]]))
        self.assertIn("bench/h62r/h62r.py", read(self.script))

    def test_a_changed_weight_fails(self):
        with open(os.path.join(self.d, "model-00001.safetensors"), "ab") as f:
            f.write(b"x")
        self.assertEqual(self.check()[0], 1)


class TriggerTest(unittest.TestCase):
    """H62r's re-run trigger: an EAS release after v1.2.0 (no release candidate), or an EAT
    release after release-2.0.0 (its hotfix of 2.0.0 too)."""

    EAS = ("986847891a835136591b48e08e784250cc275a15\trefs/tags/v1.0.0\n"
           "1d5686e75f8ec0b1edac206e9ca4db7ddde32250\trefs/tags/v1.2.0-rc.1\n"
           "a051f00fcb2dd473fe763610471cd509db82c433\trefs/tags/v1.2.0\n")
    EAT = ("0c726f576595971c53cc45b94e08ba969beb4a4a\trefs/tags/release-1.1.0\n"
           "0c7504eec7224556d87e173aa530c0999dc68925\trefs/tags/release-2.0.0\n"
           "7b827e2d95cec826a20c7e3f5a30eb4818ed2992\trefs/tags/release-2.0.0^{}\n")

    def test_nothing_fired_at_the_pinned_releases(self):
        self.assertEqual(h62r.trigger(self.EAS, self.EAT), {"eas": [], "eat": []})

    def test_a_later_release_fires(self):
        for tag in ("v1.2.1", "v1.3.0", "v2.0.0", "v1.10.0"):
            self.assertEqual(h62r.trigger(self.EAS + f"e\trefs/tags/{tag}\n", self.EAT)["eas"], [tag], tag)
        for tag in ("v1.3.0-rc.1", "v1.2.0", "v1.1.1"):
            self.assertEqual(h62r.trigger(self.EAS + f"e\trefs/tags/{tag}\n", self.EAT)["eas"], [], tag)
        for tag in ("release-2.0.0-hotfix", "release-2.0.1", "release-2.1.0", "v3.0.0"):
            self.assertEqual(h62r.trigger(self.EAS, self.EAT + f"f\trefs/tags/{tag}\n")["eat"], [tag], tag)
        for tag in ("release-2.0.0", "release-1.2.0-hotfix", "release-2.1.0-rc1"):
            self.assertEqual(h62r.trigger(self.EAS, self.EAT + f"f\trefs/tags/{tag}\n")["eat"], [], tag)

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for n, t in (("eas", self.EAS), ("eat", self.EAT + "f\trefs/tags/release-2.0.1\n")):
            with open(os.path.join(tmp, n), "w") as f:
                f.write(t)
        r = run("trigger", os.path.join(tmp, "eas"), os.path.join(tmp, "eat"))
        self.assertEqual((r.returncode, r.stdout), (0, "fired: eat release-2.0.1\n"))


class CampaignMathTest(unittest.TestCase):
    def test_pairs_left(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        self.assertEqual(h62r.pairs_left(tmp), 24)
        with open(os.path.join(tmp, "pairs.jsonl"), "w") as f:
            f.writelines(json.dumps({"j": j, "status": "complete"}) + "\n" for j in range(10))
        self.assertEqual(run("pairs-left", tmp).stdout, "14\n")

    def test_vmtax_is_a_description(self):
        h50 = {"within_budget_success": {"A1": {"k": 19, "n": 24}, "B1": {"k": 21, "n": 24}},
               "attempt_median_s": {"A1": 8.0, "B1": 12.0}}
        mine = {"within_budget_success": {"A1": {"k": 20, "n": 24}, "B5": {"k": 18, "n": 24}},
                "attempt_median_s": {"A1": 9.2, "B5": 15.0}}
        t = h62r.vmtax(h50, mine)
        self.assertEqual(t["a1_guest"], {"success": "20/24", "attempt_median_s": 9.2})
        self.assertAlmostEqual(t["attempt_median_guest_over_host"], 1.15)
        self.assertAlmostEqual(t["b_attempt_median_guest_over_host"], 1.25)
        self.assertEqual(t["gated"], False)


def stub(path, body):
    with open(path, "w") as f:
        f.write("#!/usr/bin/env bash\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class ArmScriptTest(unittest.TestCase):
    """bench/h62r/arm.sh against a stub ssh: the restart wait, the cgroup spec the idle-arm check
    reads, cpustat and the ports."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = os.path.join(self.tmp, "ssh.log")
        stub(os.path.join(self.tmp, "ssh"), textwrap.dedent(f'''\
            cmd="${{@: -1}}"
            echo "$*" >> "{self.log}"
            case "${{cmd}}" in
              *"get pods -l"*)
                n=$(cat "{self.tmp}/n" 2>/dev/null || echo 0)
                f="{self.tmp}/pods-${{n}}.json"; [ -f "$f" ] || f=$(ls "{self.tmp}"/pods-*.json | sort -V | tail -1)
                cat "$f"; echo $(( n + 1 )) > "{self.tmp}/n" ;;
              *"find /sys/fs/cgroup"*) echo /sys/fs/cgroup/kubepods.slice/kubepods-podu1.slice ;;
              *"cat /x/cpu.stat"*) printf 'usage_usec 77\\n' ;;
              *) : ;;
            esac
            '''))
        self.env = dict(os.environ, H62R_SSH=os.path.join(self.tmp, "ssh"), H62R_STATE=self.tmp, H62R_POLL_S="0.1")

    def arm(self, *args):
        return subprocess.run(["bash", os.path.join(HERE, "arm.sh"), *args], capture_output=True, text=True, env=self.env)

    def pods(self, *docs):
        for i, d in enumerate(docs):
            with open(os.path.join(self.tmp, f"pods-{i}.json"), "w") as f:
                json.dump({"items": d}, f)

    def test_one_lookup_per_call(self):
        self.assertEqual(run("arm-fields", "A1").stdout,
                         "a h62r-a 18851 k3d-gpu-agent app=ovms svc/ovms 8000 ovms \n")
        self.assertEqual(h62r.arm_fields("B5")[-1], "llminferenceservice/gemma-4-26b")
        self.assertEqual(read(HERE, "arm.sh").count("P arm"), 1)

    def test_ports_and_unknown_arm(self):
        self.assertEqual(self.arm("A1", "port").stdout, "18851\n")
        self.assertEqual(self.arm("B5", "port").stdout, "18852\n")
        self.assertEqual(self.arm("C9", "port").returncode, 2)

    def test_restart_waits_for_a_new_ready_pod(self):
        self.pods([pod("p0", "u0")], [pod("p0", "u0", deleting=True)], [pod("p1", "u1", ready=False)], [pod("p1", "u1")])
        r = self.arm("B5", "restart", "30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ready: p1 u1", r.stdout)
        log = read(self.log)
        self.assertIn("h62r-b", log)
        self.assertIn("kubectl -n llm-inference delete pod -l app.kubernetes.io/part-of=llminferenceservice,"
                      "app.kubernetes.io/name=gemma-4-26b,kserve.io/component=workload --wait=false", log)

    def test_restart_not_ready_in_time_exits_3(self):
        self.pods([pod("p0", "u0")], [pod("p1", "u1", ready=False)])
        self.assertEqual(self.arm("A1", "restart", "1").returncode, 3)
        self.assertEqual(self.arm("A1", "restart", "0").returncode, 3)

    def test_cgroup_spec_and_cpustat(self):
        self.pods([pod("p1", "u1")])
        r = self.arm("B5", "cgroup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"exec:{os.path.join(HERE, 'arm.sh')} B5 cpustat "
                                   "/sys/fs/cgroup/kubepods.slice/kubepods-podu1.slice\n")
        self.assertEqual(self.arm("B5", "cpustat", "/x").stdout, "usage_usec 77\n")

    def test_forward_restarts_the_guest_port_forward(self):
        r = self.arm("A1", "forward")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = read(self.log)
        self.assertIn("port-forward --address 0.0.0.0 svc/ovms 18851:8000", log)
        self.assertIn("~/h62r/pf-A1.pid", log)
        self.arm("A1", "unforward")
        self.assertIn("kill", read(self.log).splitlines()[-1])


class OnTtyTest(unittest.TestCase):
    """H62 amendment V2, kept: EAS v1.2.0's es_auto_installer.sh is byte-identical to rc.1's, so
    configure still takes no option and its y/N prompt still refuses a stdin that is not a terminal."""

    def test_stdin_is_a_terminal_and_the_reply_answers_the_prompt(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        log = os.path.join(tmp, "out.log")
        r = subprocess.run([sys.executable, os.path.join(HERE, "h62r.py"), "on-tty", log, "y", "bash", "-c",
                            '[ -t 0 ] || { echo notty; exit 3; }; read -rp "Proceed? [y/N] " r; echo "reply=$r"; exit 5'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 5)
        self.assertIn("reply=y", read(log))


class ScriptsTest(unittest.TestCase):
    """The shell scripts: syntax, and what each one is pinned to run."""

    SCRIPTS = ("arm.sh", "vm.sh", "guest-a.sh", "guest-b.sh", "setup.sh", "smoke.sh", "pairs-hold.sh",
               "campaign.sh", "teardown.sh", "upstream.sh", "topology-spike.sh")

    def test_syntax(self):
        for s in self.SCRIPTS:
            r = subprocess.run(["bash", "-n", os.path.join(HERE, s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{s}: {r.stderr}")

    def test_shellcheck_when_present(self):
        if not shutil.which("shellcheck"):
            self.skipTest("no shellcheck")
        r = subprocess.run(["shellcheck", "-S", "warning", *(os.path.join(HERE, s) for s in self.SCRIPTS)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_every_script_is_h62rs_own(self):
        # H62's runner is historical: nothing here reads its scripts, its state or its results.
        for s in self.SCRIPTS + ("arm-A1", "arm-B5", "qemu.Dockerfile", "h62r.py"):
            text = read(HERE, s)
            for old in ("bench/h62/", "/h62/", "results/H62/", "results/H62\n", "smoke/H62\n", "H62_STATE",
                        "h62.py", "k3dgpu-e-h62\n", "h62-vm-", "H62-hold", "H62-smoke"):
                self.assertNotIn(old, text, f"{s}: {old!r}")

    def test_vm_up_runs_the_argv_h62r_py_builds(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(tmp, "id_ed25519"), "w") as f:
            f.write("private\n")
        with open(os.path.join(tmp, "id_ed25519.pub"), "w") as f:
            f.write(CloudInitTest.KEY + "\n")
        env = dict(os.environ, H62R_DRY="1", H62R_STATE=tmp, H62R_KVM_GID="992")
        r = subprocess.run(["bash", os.path.join(HERE, "vm.sh"), "up", "b"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        want = h62r.qemu_argv("b", tmp, h62r.QEMU_IMAGE, uid=os.getuid(), gid=os.getgid(), kvm_gid=992)
        self.assertIn(" ".join(want) + "\n", r.stdout)
        self.assertIn("qemu-img create -f qcow2 -F qcow2 -b /h62r/base.img /h62r/b/disk.qcow2 400G", r.stdout)
        self.assertIn("genisoimage -output /h62r/b/seed.iso -volid cidata -joliet -rock", r.stdout)
        self.assertEqual(read(tmp, "b", "user-data"), h62r.user_data("b", CloudInitTest.KEY))
        self.assertEqual(read(tmp, "ssh_config"), h62r.ssh_config(tmp))

    def test_pairs_hold_is_h50s_hold_with_remote_arms(self):
        sh = read(HERE, "pairs-hold.sh")
        self.assertIn('crescent/hold.sh" "${name}" 2400 env', sh)
        self.assertIn("hold) name=H62r-hold${N} R=${B}/results/H62r guard=600 ;;", sh)
        self.assertIn("smoke) name=H62r-smoke R=${B}/smoke/H62r guard=99999 ;;", sh)
        for item in ("LIVE_MODE=pairs", "LIVE_CAP=2370", 'LIVE_HOLD="${N}"', "LIVE_MODEL_SET=gemma4",
                     'bash "${HERE}/../eatflow/live.sh" H62r', "SERVING_VMS=h62r-vm-a,h62r-vm-b",
                     'LIVE_REMOTE_A="${HERE}/arm-A1"', 'LIVE_REMOTE_B="${HERE}/arm-B5"'):
            self.assertIn(item, sh, item)
        for absent in ("LIVE_MOUNT_B", "LIVE_EAT_CHART", "K3D_GPU_IMAGE"):
            self.assertNotIn(absent, sh, absent)
        for arm in ("A1", "B5"):
            self.assertIn(f'exec "$(dirname "$0")/arm.sh" {arm} "$@"', read(HERE, f"arm-{arm}"))
            self.assertTrue(os.access(os.path.join(HERE, f"arm-{arm}"), os.X_OK))

    def test_guest_b_is_intels_documented_path_at_the_ga_tags(self):
        sh = read(HERE, "guest-b.sh")
        steps = ['git clone -q -b "${TAG}" https://github.com/intel/enterprise-ai-solutions.git',
                 "P eas-check configs/repos/repos.agent-toolkit.yaml",
                 'P on-tty "${R}/configure.log" y ./es_auto_installer.sh configure',
                 "./es_auto_installer.sh init agent-toolkit",
                 "P eas-edit env/local",
                 "sh roles/nri_cpu_balloons/files/probe-node-topology.sh",
                 'P sibling-read /sys/devices/system/cpu "${R}/probe.txt"',
                 'P reserve "${cpus}" env/local/global_config.yaml',
                 "./es_auto_installer.sh install agent-toolkit"]
        pos = [sh.index(s) for s in steps]
        self.assertEqual(pos, sorted(pos), "steps in the registered order")
        self.assertIn('[ "$(git rev-parse HEAD)" = "${SHA}" ]', sh)
        self.assertNotIn("eas-pin", sh)                                               # D1 rewrites nothing now
        self.assertIn("fs.inotify.max_user_instances = 8192", sh)
        self.assertIn("fs.inotify.max_user_watches   = 1048576", sh)
        # global_config.yaml: written only by D3's sibling read, then copied into the records.
        uses = [ln.strip() for ln in sh.splitlines() if "global_config.yaml" in ln]
        self.assertEqual(uses, ['[ -z "${cpus}" ] || P reserve "${cpus}" env/local/global_config.yaml > "${R}/d3-siblings.diff"',
                                "for f in global_config.yaml config.agent-toolkit.yaml config.inference.yaml "
                                "models.yaml inventory/hosts.yaml; do"])
        self.assertIn("HF_TOKEN", sh)
        self.assertNotIn("--only", sh)
        self.assertNotIn("existing_kubernetes", sh)

    def test_guest_b_records_the_sizing_whether_or_not_the_install_passes(self):
        sh = read(HERE, "guest-b.sh")
        i = sh.index('if ! ./es_auto_installer.sh install agent-toolkit > "${R}/install.log" 2>&1; then')
        block = sh[i:sh.index("\nfi\n", i)]
        for f in ("fail-nodes.txt", "fail-pods.txt", "fail-events.txt", "fail-describe.txt"):   # H62 amendment V4
            self.assertIn(f, block)
        self.assertIn("sizing fail", block)
        self.assertTrue(block.rstrip().endswith("exit 1"))
        self.assertIn("sizing ok", sh[i + len(block):])
        sizing = sh[sh.index("sizing() {"):sh.index("\n}\n", sh.index("sizing() {"))]
        for item in ("balloonspolicies.config.nri", "resources.requests.cpu", "sandbox-router-deployment",
                     "describe nodes"):
            self.assertIn(item, sizing, item)
        self.assertNotIn("KUBECONFIG", sh[:i])                                         # set only after the install

    def test_guest_a_deploys_h50s_a1(self):
        sh = read(HERE, "guest-a.sh")
        live = read(REPO, "bench", "eatflow", "live.sh")
        for item in ("K3D_GPU_AGENT_DEVICE=CPU", "K3D_GPU_AGENT_MODEL=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov",
                     "K3D_GPU_AGENT_MODEL_NAME=gemma-4-26b-a4b-it", "K3D_GPU_AGENT_DRAFT=off",
                     "K3D_GPU_AGENT_TOOL_PARSER=gemma4", "K3D_GPU_AGENT_REASONING_PARSER=''", "K3D_GPU_AUTOQUANT=off"):
            self.assertIn(item, sh, item)
        self.assertNotIn("K3D_GPU_AGENT_BACKEND", sh)                                 # H50's and H62's command
        self.assertIn("TGT=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov TGT_DIR=gemma-4-26B-A4B-it-qat-int4-ov A_TOOL=gemma4 A_DRAFT=off", live)
        # A1 stays H50's OVMS 2026.4.1: a deploy commit that renders another image stops here.
        self.assertIn('grep -qF "image: $(P pin ovms_image)" "${R}/agent.yaml"', sh)
        self.assertIn("K3D_GPU_CLUSTER=k3dgpu-e-h62r", sh)
        self.assertIn("h62rshare /mnt/h62r-ir", sh)
        self.assertIn('K3D_GPU_IMAGE="$(P pin node_image)"', sh)
        self.assertIn("eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%", sh)
        for pkg in ("docker-ce", "docker-ce-cli"):
            self.assertIn(f'{pkg}="$(P pin docker_ce)"', sh)
        self.assertIn('containerd.io="$(P pin containerd)"', sh)

    def test_smoke_runs_mains_eas_check_in_vm_b(self):
        sh = read(HERE, "smoke.sh")
        self.assertIn("./es_auto_installer.sh validate agent-toolkit", sh)
        self.assertIn("EAT_MODE=eas EAT_MODEL=${MODEL}", sh)
        self.assertIn("MODEL=$(P pin catalog_name)", sh)
        # PR #260 merged: the check is integrations/eat/eat-check.sh at the deploy commit, beside
        # the integrations/eas/eas-ready.py it reads, both recorded by sha256.
        self.assertIn('CHECK=${REPO}/integrations/eat/eat-check.sh READY=${REPO}/integrations/eas/eas-ready.py', sh)
        self.assertIn("h62r-b:h62r/integrations/eat/", sh)
        self.assertIn("h62r-b:h62r/integrations/eas/", sh)
        self.assertIn('sha256sum "${CHECK}" "${READY}"', sh)
        self.assertRegex(read(REPO, "integrations", "eat", "eat-check.sh"), r"(?m)^\s+eas\)")
        self.assertIn('ready="${HERE}/../eas/eas-ready.py"', read(REPO, "integrations", "eat", "eat-check.sh"))
        self.assertIn('"${HERE}/pairs-hold.sh" 0 smoke', sh)

    def test_campaign_order_caps_and_attempt_bound(self):
        sh = read(HERE, "campaign.sh")
        pos = [sh.index(s) for s in ("ATTEMPTS_MAX=3", "trigger setup", "trap teardown EXIT", '"${HERE}/setup.sh"',
                                     '"${HERE}/smoke.sh"', 'trigger "hold${n}"', '"${HERE}/pairs-hold.sh" "${n}"',
                                     "pairs-report")]
        self.assertEqual(pos, sorted(pos))
        self.assertIn("MAX_HOLDS=8", sh)
        self.assertIn("NOT_DELIVERABLE_MAX=2", sh)
        self.assertIn('h62r.py" pairs-left', sh)
        self.assertIn("--hyp H62r --arm-a A1 --arm-b B5", sh)
        self.assertIn("R=${B}/results/H62r", sh)

    def test_campaign_refuses_a_fourth_setup_attempt(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        res = os.path.join(tmp, "k3dgpu-bench", "results", "H62r")
        os.makedirs(res)
        with open(os.path.join(res, "attempts.txt"), "w") as f:
            f.write("a\nb\nc\n")
        r = subprocess.run(["bash", os.path.join(HERE, "campaign.sh")], capture_output=True, text=True,
                           env=dict(os.environ, HOME=tmp), timeout=60)
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("setup attempt bound", read(res, "campaign.log"))
        self.assertEqual(read(res, "attempts.txt"), "a\nb\nc\n")
        self.assertFalse(os.path.exists(os.path.join(res, "teardown.log")))             # nothing was started

    def campaign_with_stubs(self, reads, holds_left=3):
        """campaign.sh copied beside stub setup, smoke, holds, teardown, h62r.py and upstream.sh.
        `reads` is upstream.sh's answer per call, in order ("ERR" exits 1). Returns the run, the
        results directory and the order of the calls."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        here = os.path.join(tmp, "repo", "bench", "h62r")
        os.makedirs(here)
        shutil.copy(os.path.join(HERE, "campaign.sh"), here)
        calls = os.path.join(tmp, "calls")
        with open(os.path.join(tmp, "reads"), "w") as f:
            f.write("".join(r + "\n" for r in reads))
        stubs = {
            "setup.sh": f'echo setup >> "{calls}"',
            "smoke.sh": f'echo smoke >> "{calls}"',
            "teardown.sh": f'echo teardown >> "{calls}"',
            "pairs-hold.sh": f'echo "hold$1" >> "{calls}"; echo $(( $(cat "{tmp}/left") - 1 )) > "{tmp}/left"',
            "upstream.sh": (f'echo upstream >> "{calls}"; r=$(head -1 "{tmp}/reads"); sed -i 1d "{tmp}/reads"; '
                            '[ "${r}" != ERR ] || { echo "ls-remote failed" >&2; exit 1; }; echo "${r}"'),
        }
        for name, body in stubs.items():
            with open(os.path.join(here, name), "w") as f:
                f.write("#!/usr/bin/env bash\n" + body + "\n")
        with open(os.path.join(here, "h62r.py"), "w") as f:
            f.write(f'import sys\nprint(open("{tmp}/left").read().strip()) if sys.argv[1] == "pairs-left" else None\n')
        with open(os.path.join(tmp, "left"), "w") as f:
            f.write(f"{holds_left}\n")
        r = subprocess.run(["bash", os.path.join(here, "campaign.sh")], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, HOME=tmp, H62R_TRIGGER_RETRY_S="0"))
        res = os.path.join(tmp, "k3dgpu-bench", "results", "H62r")
        order = read(calls).split() if os.path.exists(calls) else []
        return r, res, order

    def test_campaign_reads_the_trigger_before_setup_and_before_each_hold(self):
        # The pre-registration's re-run trigger: it runs before setup and before each hold, and its
        # output goes into the records.
        r, res, order = self.campaign_with_stubs(["fired: none"] * 4)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(order, ["upstream", "setup", "smoke", "upstream", "hold1", "upstream", "hold2",
                                 "upstream", "hold3", "teardown"])
        rec = read(res, "upstream.txt").splitlines()
        self.assertEqual([ln.split(" ", 1)[1] for ln in rec],
                         ["setup fired: none", "hold1 fired: none", "hold2 fired: none", "hold3 fired: none"])

    def test_campaign_stops_when_the_trigger_fires_before_hold_1(self):
        # Fired before setup: nothing starts and no setup attempt is spent.
        r, res, order = self.campaign_with_stubs(["fired: eas v1.2.1"])
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertEqual(order, ["upstream"])
        self.assertFalse(os.path.exists(os.path.join(res, "attempts.txt")))
        self.assertIn("setup fired: eas v1.2.1", read(res, "upstream.txt"))
        # Fired after setup, before hold 1: no hold runs; the guests are torn down.
        r, res, order = self.campaign_with_stubs(["fired: none", "fired: eat release-2.0.1"])
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertEqual(order, ["upstream", "setup", "smoke", "upstream", "teardown"])
        self.assertIn("amendment", read(res, "campaign.log"))

    def test_campaign_finishes_at_its_pins_when_the_trigger_fires_after_hold_1(self):
        r, res, order = self.campaign_with_stubs(["fired: none", "fired: none", "fired: eas v1.2.1", "fired: eas v1.2.1"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c for c in order if c.startswith("hold")], ["hold1", "hold2", "hold3"])
        self.assertIn("hold2 fired: eas v1.2.1", read(res, "upstream.txt"))

    def test_campaign_records_an_unreadable_trigger_after_three_reads(self):
        r, res, order = self.campaign_with_stubs(["fired: none"] + ["ERR"] * 3 + ["fired: none"] * 2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(order[:7], ["upstream", "setup", "smoke", "upstream", "upstream", "upstream", "hold1"])
        self.assertRegex(read(res, "upstream.txt"), r"(?m)^\S+ hold1 unreadable: ls-remote failed$")
        # Unreadable before setup: nothing starts.
        r, res, order = self.campaign_with_stubs(["ERR"] * 3)
        self.assertEqual((r.returncode, order), (11, ["upstream"] * 3), r.stdout + r.stderr)

    def test_setup_caps_and_gates(self):
        sh = read(HERE, "setup.sh")
        self.assertIn('flock -w 7200 9', sh)
        self.assertIn("A_CAP=2700", sh)
        self.assertIn("B_CAP=7200", sh)
        for phase in ("host", "guest", "nfd", "weights"):
            self.assertIn(f"P gates {phase}", sh)
        self.assertIn('put "${vm}" "${HERE}/h62r.py" "${HERE}/guest-${vm}.sh"', sh)

    def test_topology_spike_runs_eas_own_probe_in_h62rs_guest_shape(self):
        sh = read(HERE, "topology-spike.sh")
        self.assertIn("https://raw.githubusercontent.com/intel/enterprise-ai-solutions/${SHA}/roles/nri_cpu_balloons/"
                      "files/probe-node-topology.sh", sh)
        self.assertIn('SHA=$(python3 "${HERE}/h62r.py" pin eas_sha)', sh)
        self.assertIn("print(h62r.SMP)", sh)
        self.assertIn("-machine q35,accel=kvm -cpu host", sh)
        self.assertRegex(sh, r"ALPINE=alpine:[0-9.]+@sha256:[0-9a-f]{64}\n")
        # Both of the probe's reads of thread_siblings_list: its threads per core (line 31) and its
        # sibling list (line 74, which the 'siblings' profile reserves when it reads SMT).
        self.assertIn("NRI_(NUMA|LOGICAL|TPC|PHYSICAL)=", sh)
        self.assertIn("NRI_SIBLINGS=", sh)

    def test_upstream_reads_both_repositories(self):
        sh = read(HERE, "upstream.sh")
        self.assertIn("git ls-remote --tags https://github.com/intel/enterprise-ai-solutions.git", sh)
        self.assertIn("git ls-remote --tags https://github.com/intel/enterprise-agent-toolkit.git", sh)
        self.assertIn('h62r.py" trigger', sh)


if __name__ == "__main__":
    unittest.main(verbosity=1)

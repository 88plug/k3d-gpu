#!/usr/bin/env python3
"""Tests for bench/h62 (EXPERIMENTS.md "H62 pre-registration"): the pins, the two guests'
QEMU command lines and cloud-init, the setup gates, deviation D1 (the layer manifest's
commits) and the documented edits with deviation D2, the pod and cgroup reads behind the
idle-arm check, B5's weight check, the re-run trigger, and the shell scripts against stubs.
No VM, no cluster, no network."""
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
import h62  # noqa: E402

REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
GIB_KIB = 1024 * 1024


def read(*path):
    with open(os.path.join(*path)) as f:
        return f.read()


def run(*argv, stdin=None, env=None):
    return subprocess.run([sys.executable, os.path.join(HERE, "h62.py"), *argv], input=stdin,
                          capture_output=True, text=True, env=env)


# EAS v1.2.0-rc.1 (5c27fb3) configs/repos/repos.agent-toolkit.yaml, lines 12-28, verbatim.
MANIFEST = textwrap.dedent('''\
    repos:
      - layer: "inference"
        url: "https://github.com/intel/enterprise-inference"
        dest: "enterprise.ai-inference"
        rev: "release-1.1" # pin 170eb3abb1956de0787727f21e356aef2b13ff24 (#49) for release
        deployment_subdir: ""
        config_dir: ""
        default_flavour: ""
        model_catalog: "model_manager/models.yaml"

      - layer: "agent-toolkit"
        url: "https://github.com/intel/enterprise-agent-toolkit"
        dest: "enterprise.agent-toolkit"
        rev: "release-2.0.0" # pin 036b66234801e9bd950afd90830821859780d668 (#19) for release
        deployment_subdir: ""
        config_dir: ""
        default_flavour: ""
    ''')

# EAT release-2.0.0 (036b662) config.yaml, lines 33-36 (the file init seeds as
# env/local/config.agent-toolkit.yaml), and the same key commented in a doc block.
CONFIG = textwrap.dedent('''\
    # Model(s) to deploy, from the catalog (env/local/models.yaml)
    # llm_services_autodeploy_models: ["qwen3-coder-30b", "bge-base-en"]
    llm_services_autodeploy_models: ["qwen2-5-coder-14b"]
    redis_stack_enabled: true
    ''')

# enterprise-inference release-1.1 (170eb3a) model_manager/models.yaml, lines 355-366 and
# 405-416, trimmed: the gemma-4-26b entry between two others, at the file's indentation.
MODELS = textwrap.indent(textwrap.dedent('''\
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
    '''), "  ")


class PinsTest(unittest.TestCase):
    """Every pin the pre-registration names, in one table the scripts read."""

    def test_pins_are_the_preregistered_values(self):
        p = h62.PINS
        self.assertEqual(p["eas_tag"], "v1.2.0-rc.1")
        self.assertEqual(p["eas_sha"], "5c27fb39a95eecec41aa4e29a1188984e9c726f3")
        self.assertEqual(p["ei_sha"], "170eb3abb1956de0787727f21e356aef2b13ff24")
        self.assertEqual(p["eat_sha"], "036b66234801e9bd950afd90830821859780d668")
        self.assertEqual(p["cloud_image_sha256"], "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94")
        self.assertTrue(p["cloud_image_url"].endswith("/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img"))
        self.assertEqual(p["cloud_image_bytes"], 621284352)
        self.assertEqual(p["qemu_base"], "ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c")
        self.assertEqual(p["qemu_pkg"], "1:10.2.1+ds-1ubuntu3.3")
        self.assertEqual(p["docker_ce"], "5:29.9.2-1~ubuntu.24.04~noble")
        self.assertEqual(p["containerd"], "2.4.2-3~ubuntu.24.04~noble")
        self.assertEqual(p["docker_gpg_fpr"], "9DC858229FC7DD38854AE2D88D81803C0EBFCD88")
        self.assertEqual(p["node_image"], "cryptoandcoffee/k3d-gpu:0.1.61@sha256:"
                                          "8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c")
        self.assertEqual(p["qat_repo"], "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized")
        self.assertEqual(p["qat_rev"], "f1e06dc520982d9b9edd76859fdb7ab209449949")
        self.assertEqual((p["catalog_name"], p["stock_model_id"]), ("gemma-4-26b", "google/gemma-4-26B-A4B-it"))
        self.assertEqual(p["vllm_cpu_image"], "vllm/vllm-openai-cpu:v0.28.0@sha256:"
                                              "197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647")
        self.assertEqual((p["jq"], p["python3_yaml"]), ("1.7.1-3ubuntu0.24.04.2", "6.0.1-2build2"))
        self.assertEqual(h62.QEMU_IMAGE, "h62-qemu:1-10.2.1-ds-1ubuntu3.3")

    def test_cli_prints_one_pin(self):
        r = run("pin", "eas_sha")
        self.assertEqual((r.returncode, r.stdout), (0, "5c27fb39a95eecec41aa4e29a1188984e9c726f3\n"))
        self.assertEqual(run("pin", "nope").returncode, 2)

    def test_the_qemu_image_is_built_from_the_pins(self):
        df = read(HERE, "qemu.Dockerfile")
        self.assertIn(f"FROM {h62.PINS['qemu_base']}\n", df)
        for pkg in ("qemu-system-x86", "qemu-utils"):
            self.assertIn(f"{pkg}={h62.PINS['qemu_pkg']}", df)
        self.assertIn(f"genisoimage={h62.PINS['genisoimage_pkg']}", df)

    def test_the_cloud_image_is_checked_before_use(self):
        vm = read(HERE, "vm.sh")
        self.assertIn('P pin cloud_image_sha256', vm)
        self.assertIn('sha256sum -c', vm)


class VmSpecTest(unittest.TestCase):
    def test_two_guests_of_one_shape(self):
        a, b = h62.VMS["a"], h62.VMS["b"]
        self.assertEqual((a["arm"], a["mem_gib"], b["arm"], b["mem_gib"]), ("A1", 96, "B5", 192))
        self.assertEqual(h62.SMP, "96,sockets=1,cores=48,threads=2")
        self.assertEqual(h62.VCPUS, 96)
        # The guests carry no k3dgpu prefix: hold.sh's cleanup never removes them, and another
        # row's serving covariate counts them as foreign load.
        for v in (a, b):
            self.assertFalse(v["name"].startswith("k3dgpu"), v["name"])
        self.assertEqual(len({a["ssh_port"], a["serve_port"], b["ssh_port"], b["serve_port"]}), 4)

    def test_memory_gate_is_both_guests_plus_48_gib(self):
        self.assertEqual(h62.mem_needed_kib(), (96 + 192 + 48) * GIB_KIB)


class QemuArgvTest(unittest.TestCase):
    def test_vm_b(self):
        argv = h62.qemu_argv("b", "/st", "h62-qemu:x", uid=1000, gid=1000, kvm_gid=992)
        self.assertEqual(argv[:4], ["docker", "run", "-d", "--name"])
        self.assertEqual(argv[4], "h62-vm-b")
        for pair in (["--network", "host"], ["--device", "/dev/kvm"], ["--user", "1000:1000"],
                     ["--group-add", "992"], ["-v", "/st:/h62"], ["-cpu", "host"], ["-m", "192G"],
                     ["-smp", "96,sockets=1,cores=48,threads=2"], ["-machine", "q35,accel=kvm"]):
            self.assertTrue(any(argv[i:i + 2] == pair for i in range(len(argv))), pair)
        net = argv[argv.index("-netdev") + 1]
        self.assertEqual(net, "user,id=n0,hostfwd=tcp:127.0.0.1:18823-:22,hostfwd=tcp:127.0.0.1:18832-:18832")
        self.assertNotIn("0.0.0.0", " ".join(argv))
        self.assertNotIn("-virtfs", argv)
        self.assertIn("file=/h62/b/disk.qcow2,if=virtio,format=qcow2", argv)
        self.assertIn("file=/h62/b/seed.iso,if=virtio,format=raw,readonly=on", argv)
        self.assertEqual(argv[argv.index("h62-qemu:x") + 1], "qemu-system-x86_64")

    def test_vm_a_shares_the_ir_read_only(self):
        argv = h62.qemu_argv("a", "/st", "img", share="/home/u/ir", uid=1, gid=1, kvm_gid=2)
        self.assertIn("/home/u/ir:/share:ro", argv)
        self.assertEqual(argv[argv.index("-virtfs") + 1],
                         "local,path=/share,mount_tag=h62share,security_model=none,readonly=on")
        self.assertEqual(argv[argv.index("-m") + 1], "96G")
        with self.assertRaises(ValueError):
            h62.qemu_argv("b", "/st", "img", share="/x", uid=1, gid=1, kvm_gid=2)
        with self.assertRaises(ValueError):
            h62.qemu_argv("a", "/st", "img", uid=1, gid=1, kvm_gid=2)          # A1's IR is required

    def test_cli_one_argument_per_line(self):
        r = run("qemu-argv", "b", "/st", "img", "1000", "1000", "992")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), h62.qemu_argv("b", "/st", "img", uid=1000, gid=1000, kvm_gid=992))


class CloudInitTest(unittest.TestCase):
    KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample h62"

    def test_user_data(self):
        u = h62.user_data("b", self.KEY)
        self.assertTrue(u.startswith("#cloud-config\n"))
        self.assertIn(f"  - {self.KEY}\n", u)
        self.assertIn("ssh_pwauth: false\n", u)
        self.assertIn("package_upgrade: false\n", u)
        self.assertIn("hostname: h62-b\n", u)
        # D3: the guest's package state stays the image's; no apt run competes with the install.
        for unit in ("unattended-upgrades.service", "apt-daily.timer", "apt-daily-upgrade.timer"):
            self.assertIn(unit, u)

    def test_meta_data_and_key_checks(self):
        self.assertEqual(h62.meta_data("a"), "instance-id: h62-a\nlocal-hostname: h62-a\n")
        for bad in ("", "ssh-ed25519 AAAA\nrm -rf /", "not-a-key"):
            with self.assertRaises(ValueError):
                h62.user_data("a", bad)


class HostFactsTest(unittest.TestCase):
    """The host gate's facts: MemAvailable, how full / is, and whether the registries answer."""

    def test_reads_meminfo_df_and_probes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        mi = os.path.join(tmp, "meminfo")
        with open(mi, "w") as f:
            f.write("MemTotal:       527700000 kB\nMemAvailable:   374341632 kB\n")
        df = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 3600000000 1100000000 2400000000 31% /\n"
        seen = []
        f = h62.host_facts(mi, df, lambda url: seen.append(url) or "registry.k8s.io" not in url)
        self.assertEqual((f["mem_available_kib"], f["root_used_pct"]), (374341632, 31))
        self.assertEqual(f["reach"], {"huggingface.co": True, "registry-1.docker.io": True, "registry.k8s.io": False})
        self.assertEqual(len(seen), 3)


class IrVerifyTest(unittest.TestCase):
    """A1's IR against its manifest.json (crescent amendment E3 item 4), before VM a sees it."""

    def test_manifest_check(self):
        import hashlib
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
        c = h62.ssh_config("/home/u/k3dgpu-bench/h62")
        for host, port in (("h62-a", 18822), ("h62-b", 18823)):
            blk = c[c.index(f"Host {host}\n"):]
            self.assertIn(f"  Port {port}\n", blk)
        for line in ("  HostName 127.0.0.1\n", "  User ubuntu\n", "  IdentityFile /home/u/k3dgpu-bench/h62/id_ed25519\n",
                     "  UserKnownHostsFile /home/u/k3dgpu-bench/h62/known_hosts\n", "  StrictHostKeyChecking accept-new\n",
                     "  ControlMaster auto\n", "  ControlPath /home/u/k3dgpu-bench/h62/cm-%C\n", "  BatchMode yes\n"):
            self.assertEqual(c.count(line), 2, line)


class GatesTest(unittest.TestCase):
    HOST = {"mem_available_kib": 357 * GIB_KIB, "root_used_pct": 31,
            "reach": {"huggingface.co": True, "registry-1.docker.io": True, "registry.k8s.io": True}}

    def failed(self, phase, facts):
        return [m for ok, m in h62.gates(phase, facts) if not ok]

    def test_host(self):
        self.assertEqual(self.failed("host", self.HOST), [])
        edge = dict(self.HOST, mem_available_kib=h62.mem_needed_kib())
        self.assertEqual(self.failed("host", edge), [])
        low = dict(self.HOST, mem_available_kib=h62.mem_needed_kib() - 1)
        self.assertEqual(len(self.failed("host", low)), 1)
        self.assertEqual(self.failed("host", dict(self.HOST, root_used_pct=90)), [])
        self.assertEqual(len(self.failed("host", dict(self.HOST, root_used_pct=91))), 1)
        unreach = dict(self.HOST, reach={"huggingface.co": False, "registry-1.docker.io": True, "registry.k8s.io": True})
        self.assertIn("huggingface.co", self.failed("host", unreach)[0])

    def test_guest_amx_on_every_vcpu(self):
        good = {"amx_tile": {"a": 96, "b": 96}, "nproc": {"a": 96, "b": 96}}
        self.assertEqual(self.failed("guest", good), [])
        self.assertEqual(len(self.failed("guest", {"amx_tile": {"a": 96, "b": 0}, "nproc": {"a": 96, "b": 96}})), 1)
        self.assertEqual(len(self.failed("guest", {"amx_tile": {"a": 96}, "nproc": {"a": 96}})), 2)   # b unread

    def test_nfd_and_weights(self):
        self.assertEqual(self.failed("nfd", {"amxbf16": "true"}), [])
        self.assertEqual(len(self.failed("nfd", {"amxbf16": None})), 1)
        self.assertEqual(self.failed("weights", {"bad": 0, "missing": 0, "checked": 3}), [])
        self.assertEqual(len(self.failed("weights", {"bad": 1, "missing": 0, "checked": 3})), 1)
        self.assertEqual(len(self.failed("weights", {"bad": 0, "missing": 0, "checked": 0})), 1)
        with self.assertRaises(ValueError):
            h62.gates("other", {})

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


class EasPinTest(unittest.TestCase):
    """Deviation D1: the layer manifest's branch revs become the commits Intel's own comments
    and the rc.1 release notes name; nothing else in the file changes."""

    def test_pins_both_layers_to_the_commented_commits(self):
        out = h62.eas_pin(MANIFEST)
        self.assertIn('rev: "170eb3abb1956de0787727f21e356aef2b13ff24" # H62 D1, was "release-1.1"', out)
        self.assertIn('rev: "036b66234801e9bd950afd90830821859780d668" # H62 D1, was "release-2.0.0"', out)
        diff = [(a, b) for a, b in zip(MANIFEST.splitlines(), out.splitlines()) if a != b]
        self.assertEqual(len(diff), 2)
        self.assertEqual(len(out.splitlines()), len(MANIFEST.splitlines()))
        self.assertEqual(h62.eas_pin(out), out)                          # a second run changes nothing

    def test_refuses_what_it_was_not_written_for(self):
        for bad in (MANIFEST.replace("pin 170eb3ab", "pin 0000000b"),        # Intel's comment names another commit
                    MANIFEST.replace('rev: "release-1.1"', 'rev: "main"'),   # another branch
                    MANIFEST.replace('- layer: "inference"', '- layer: "other"'),
                    MANIFEST + '    rev: "x"\n'):                             # a second rev in a block
            with self.assertRaises(ValueError):
                h62.eas_pin(bad)


class EasEditTest(unittest.TestCase):
    """The one documented edit (the catalog value) and deviation D2 (gemma-4-26b's weights)."""

    def test_autodeploy_gemma_only_on_the_live_line(self):
        out = h62.set_autodeploy(CONFIG, "gemma-4-26b")
        self.assertIn('\nllm_services_autodeploy_models: ["gemma-4-26b"]\n', out)
        self.assertIn('# llm_services_autodeploy_models: ["qwen3-coder-30b", "bge-base-en"]\n', out)
        self.assertEqual(h62.set_autodeploy(out, "gemma-4-26b"), out)
        with self.assertRaises(ValueError):
            h62.set_autodeploy(CONFIG.replace("llm_services_autodeploy_models: [\"qwen", "#x: [\"qwen"), "gemma-4-26b")

    def test_d2_moves_only_gemma_4_26b_to_the_qat_weights(self):
        out = h62.set_model_id(MODELS, "gemma-4-26b", "google/gemma-4-26B-A4B-it",
                               "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized")
        blocks = out.split("\n\n")
        self.assertIn("model_id: google/gemma-4-26B-A4B-it\n", blocks[0])           # the tp2 entry keeps its id
        self.assertIn("model_id: google/gemma-4-26B-A4B-it-qat-q4_0-unquantized\n", blocks[1])
        self.assertIn("model_id: Qwen/Qwen3Guard-Gen-0.6B\n", blocks[2])
        self.assertEqual(sum(1 for a, b in zip(MODELS.splitlines(), out.splitlines()) if a != b), 1)
        with self.assertRaises(ValueError):
            h62.set_model_id(MODELS, "nope", "a", "b")
        with self.assertRaises(ValueError):                                          # the stock id is not there
            h62.set_model_id(MODELS, "gemma-4-26b", "google/other", "b")

    def test_cli_edits_an_env_and_prints_the_diffs(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for name, text in (("config.agent-toolkit.yaml", CONFIG), ("models.yaml", MODELS),
                           ("global_config.yaml", 'base_domain_name: "solutions.ai"\n')):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(text)
        r = run("eas-edit", tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('+llm_services_autodeploy_models: ["gemma-4-26b"]', r.stdout)
        self.assertIn("+    model_id: google/gemma-4-26B-A4B-it-qat-q4_0-unquantized", r.stdout)
        self.assertEqual(read(tmp, "global_config.yaml"), 'base_domain_name: "solutions.ai"\n')   # untouched
        self.assertEqual(run("eas-edit", tmp).stdout, "")                          # idempotent
        p = os.path.join(tmp, "manifest.yaml")
        with open(p, "w") as f:
            f.write(MANIFEST)
        r = run("eas-pin", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('+    rev: "170eb3abb1956de0787727f21e356aef2b13ff24"', r.stdout)


def pod(name, uid, ready=True, deleting=False, created="2026-10-30T00:00:00Z", phase="Running"):
    meta = {"name": name, "uid": uid, "creationTimestamp": created}
    if deleting:
        meta["deletionTimestamp"] = "2026-10-30T00:01:00Z"
    return {"metadata": meta, "status": {"phase": phase,
                                         "conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


class PodTest(unittest.TestCase):
    def test_pick_a_new_ready_pod(self):
        old = pod("p0", "u0", deleting=True)
        self.assertIsNone(h62.pick_pod({"items": [old, pod("p1", "u1", ready=False)]}, {"u0"}))
        self.assertEqual(h62.pick_pod({"items": [old, pod("p1", "u1")]}, {"u0"}), ("p1", "u1"))
        self.assertIsNone(h62.pick_pod({"items": [pod("p0", "u0")]}, {"u0"}))           # the old pod is not new
        self.assertEqual(h62.pick_pod({"items": [pod("p0", "u0")]}, set()), ("p0", "u0"))
        two = {"items": [pod("p1", "u1", created="2026-10-30T00:00:01Z"), pod("p2", "u2", created="2026-10-30T00:00:09Z")]}
        self.assertEqual(h62.pick_pod(two, set()), ("p2", "u2"))
        self.assertIsNone(h62.pick_pod({"items": [pod("p1", "u1", phase="Pending")]}, set()))

    def test_cli(self):
        doc = json.dumps({"items": [pod("p0", "u0", deleting=True), pod("p1", "u1")]})
        self.assertEqual(run("uids", stdin=doc).stdout, "u0,u1\n")
        r = run("pick-pod", "u0", stdin=doc)
        self.assertEqual((r.returncode, r.stdout), (0, "p1 u1\n"))
        self.assertEqual(run("pick-pod", "u0,u1", stdin=doc).returncode, 1)
        self.assertEqual(run("pick-pod", "", stdin=doc).stdout, "p1 u1\n")

    def test_model_mount(self):
        p = {"spec": {"volumes": [{"name": "home", "emptyDir": {}},
                                  {"name": "model-cache", "persistentVolumeClaim": {"claimName": "model-store"}}],
                      "containers": [{"name": "main", "volumeMounts": [
                          {"name": "home", "mountPath": "/home"},
                          {"name": "model-cache", "mountPath": "/mnt/models", "subPath": "gemma-4-26b"}]}]}}
        self.assertEqual(h62.model_mount(p, "main"), "/mnt/models")
        with self.assertRaises(ValueError):
            h62.model_mount(p, "other")
        p["spec"]["volumes"][1]["persistentVolumeClaim"]["claimName"] = "x"
        with self.assertRaises(ValueError):
            h62.model_mount(p, "main")


class VerifyScriptTest(unittest.TestCase):
    """B5's weights, read in its pod: every LFS file of the QAT checkpoint at f1e06dc5, and the
    commit `hf download --local-dir` recorded for each file."""

    def setUp(self):
        import hashlib
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.d = os.path.join(self.tmp, "models")
        os.makedirs(os.path.join(self.d, ".cache", "huggingface", "download"))
        files = {"model-00001.safetensors": b"w1" * 1000, "model-00002.safetensors": b"w2" * 1000, "config.json": b"{}"}
        sib = []
        for n, data in files.items():
            with open(os.path.join(self.d, n), "wb") as f:
                f.write(data)
            meta = os.path.join(self.d, ".cache", "huggingface", "download", n + ".metadata")
            with open(meta, "w") as f:
                f.write(h62.PINS["qat_rev"] + "\netag\n1730000000.0\n")
            s = {"rfilename": n, "size": len(data)}
            if n.endswith(".safetensors"):
                s["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            sib.append(s)
        sib.append({"rfilename": "README.md", "size": 10})                       # not downloaded: noted
        self.sib = {"sha": h62.PINS["qat_rev"], "siblings": sib}
        self.script = os.path.join(self.tmp, "verify.py")
        with open(self.script, "w") as f:
            f.write(h62.verify_script(self.sib))

    def check(self):
        r = subprocess.run([sys.executable, self.script, self.d], capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout.splitlines()[-1])

    def test_matching_weights_pass_and_name_their_commit(self):
        rc, s = self.check()
        self.assertEqual((rc, s["bad"], s["missing"], s["checked"]), (0, 0, 0, 2))
        self.assertEqual(s["revisions"], [h62.PINS["qat_rev"]])
        self.assertEqual(s["want"], h62.PINS["qat_rev"])

    def test_a_changed_or_missing_weight_fails(self):
        with open(os.path.join(self.d, "model-00002.safetensors"), "ab") as f:
            f.write(b"x")
        rc, s = self.check()
        self.assertEqual((rc, s["bad"]), (1, 1))
        os.unlink(os.path.join(self.d, "model-00001.safetensors"))
        rc, s = self.check()
        self.assertEqual((rc, s["missing"]), (1, 1))

    def test_cli_writes_the_script(self):
        p = os.path.join(self.tmp, "sib.json")
        with open(p, "w") as f:
            json.dump(self.sib, f)
        r = run("verify-script", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, h62.verify_script(self.sib))


class TriggerTest(unittest.TestCase):
    """The re-run trigger: EAS v1.2.0 (GA) tagged, or an EAT 2.x release tagged."""

    EAS = ("1d5686e75f8ec0b1edac206e9ca4db7ddde32250\trefs/tags/v1.2.0-rc.1\n"
           "5c27fb39a95eecec41aa4e29a1188984e9c726f3\trefs/tags/v1.2.0-rc.1^{}\n"
           "aaaa\trefs/tags/v1.1.0\n")
    EAT = "bbbb\trefs/tags/release-1.1.0\ncccc\trefs/tags/release-1.0.3-hotfix\n"

    def test_nothing_fired_today(self):
        self.assertEqual(h62.trigger(self.EAS, self.EAT), {"eas": [], "eat": []})

    def test_ga_or_an_eat_2_release_fires(self):
        eas = self.EAS + "dddd\trefs/tags/v1.2.0\ndddd\trefs/tags/v1.2.0^{}\n"
        self.assertEqual(h62.trigger(eas, self.EAT)["eas"], ["v1.2.0"])
        self.assertEqual(h62.trigger(self.EAS + "e\trefs/tags/v1.2.1\n", self.EAT)["eas"], ["v1.2.1"])
        self.assertEqual(h62.trigger(self.EAS + "e\trefs/tags/v1.2.0-rc.2\n", self.EAT)["eas"], [])
        for tag in ("release-2.0.0", "v2.0.0", "release-2.1.0"):
            self.assertEqual(h62.trigger(self.EAS, self.EAT + f"f\trefs/tags/{tag}\n")["eat"], [tag], tag)

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for n, t in (("eas", self.EAS), ("eat", self.EAT + "f\trefs/tags/release-2.0.0\n")):
            with open(os.path.join(tmp, n), "w") as f:
                f.write(t)
        r = run("trigger", os.path.join(tmp, "eas"), os.path.join(tmp, "eat"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "fired: eat release-2.0.0\n")


class CampaignMathTest(unittest.TestCase):
    def test_pairs_left(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        self.assertEqual(h62.pairs_left(tmp), 24)
        with open(os.path.join(tmp, "pairs.jsonl"), "w") as f:
            f.writelines(json.dumps({"j": j, "status": "complete"}) + "\n" for j in range(10))
            f.write('{"j": 10, "sta')                                              # a line still being written
        self.assertEqual(h62.pairs_left(tmp), 14)
        self.assertEqual(run("pairs-left", tmp).stdout, "14\n")

    def test_vmtax_is_a_description(self):
        h50 = {"within_budget_success": {"A1": {"k": 19, "n": 24, "rate": 19 / 24}}, "attempt_median_s": {"A1": 8.0}}
        h62r = {"within_budget_success": {"A1": {"k": 20, "n": 24, "rate": 20 / 24}}, "attempt_median_s": {"A1": 9.2}}
        t = h62.vmtax(h50, h62r)
        self.assertEqual(t["a1_host"], {"success": "19/24", "attempt_median_s": 8.0})
        self.assertEqual(t["a1_guest"], {"success": "20/24", "attempt_median_s": 9.2})
        self.assertAlmostEqual(t["attempt_median_guest_over_host"], 1.15)
        self.assertEqual(t["gated"], False)


def stub(path, body):
    with open(path, "w") as f:
        f.write("#!/usr/bin/env bash\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class ArmScriptTest(unittest.TestCase):
    """bench/h62/arm.sh against a stub ssh that answers from canned files: the restart wait,
    the cgroup spec the idle-arm check reads, cpustat and the ports."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = os.path.join(self.tmp, "ssh.log")
        # The stub logs each remote command, and answers `get pods` from the next pods-N.json.
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
        self.env = dict(os.environ, H62_SSH=os.path.join(self.tmp, "ssh"), H62_STATE=self.tmp, H62_POLL_S="0.1")

    def arm(self, *args):
        return subprocess.run(["bash", os.path.join(HERE, "arm.sh"), *args], capture_output=True, text=True, env=self.env)

    def pods(self, *docs):
        for i, d in enumerate(docs):
            with open(os.path.join(self.tmp, f"pods-{i}.json"), "w") as f:
                json.dump({"items": d}, f)

    def test_one_lookup_per_call(self):
        self.assertEqual(run("arm-fields", "A1").stdout,
                         "a h62-a 18831 k3d-gpu-agent app=ovms svc/ovms 8000 ovms \n")
        self.assertEqual(h62.arm_fields("B5")[-1], "llminferenceservice/gemma-4-26b")
        self.assertEqual(read(HERE, "arm.sh").count("P arm"), 1)          # cpustat runs on every idle read

    def test_ports_and_unknown_arm(self):
        self.assertEqual(self.arm("A1", "port").stdout, "18831\n")
        self.assertEqual(self.arm("B5", "port").stdout, "18832\n")
        self.assertEqual(self.arm("C9", "port").returncode, 2)

    def test_restart_waits_for_a_new_ready_pod(self):
        self.pods([pod("p0", "u0")], [pod("p0", "u0", deleting=True)], [pod("p1", "u1", ready=False)], [pod("p1", "u1")])
        r = self.arm("B5", "restart", "30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ready: p1 u1", r.stdout)
        log = read(self.log)
        self.assertIn("h62-b", log)
        self.assertIn("kubectl -n llm-inference delete pod -l app.kubernetes.io/part-of=llminferenceservice,"
                      "app.kubernetes.io/name=gemma-4-26b,kserve.io/component=workload --wait=false", log)

    def test_restart_not_ready_in_time_exits_3(self):
        self.pods([pod("p0", "u0")], [pod("p1", "u1", ready=False)])
        self.assertEqual(self.arm("A1", "restart", "1").returncode, 3)
        self.assertEqual(self.arm("A1", "restart", "0").returncode, 3)              # no time left at all
        self.assertIn("kubectl -n k3d-gpu-agent delete pod -l app=ovms --wait=false", read(self.log))

    def test_cgroup_spec_and_cpustat(self):
        self.pods([pod("p1", "u1")])
        r = self.arm("B5", "cgroup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"exec:{os.path.join(HERE, 'arm.sh')} B5 cpustat "
                                   "/sys/fs/cgroup/kubepods.slice/kubepods-podu1.slice\n")
        self.assertIn("-name podu1 -o -name '*podu1.slice'", read(self.log))
        r = self.arm("B5", "cpustat", "/x")
        self.assertEqual(r.stdout, "usage_usec 77\n")

    def test_forward_restarts_the_guest_port_forward(self):
        r = self.arm("A1", "forward")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = read(self.log)
        self.assertIn("port-forward --address 0.0.0.0 svc/ovms 18831:8000", log)
        self.assertIn("pf-A1.pid", log)
        self.arm("A1", "unforward")
        self.assertIn("kill", read(self.log).splitlines()[-1])


class ScriptsTest(unittest.TestCase):
    """The shell scripts: syntax, and what each one is pinned to run."""

    SCRIPTS = ("arm.sh", "vm.sh", "guest-a.sh", "guest-b.sh", "setup.sh", "smoke.sh", "pairs-hold.sh",
               "campaign.sh", "teardown.sh", "upstream.sh")

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

    def test_vm_up_runs_the_argv_h62_py_builds(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(tmp, "id_ed25519"), "w") as f:              # a key exists: none is made
            f.write("private\n")
        with open(os.path.join(tmp, "id_ed25519.pub"), "w") as f:
            f.write(CloudInitTest.KEY + "\n")
        env = dict(os.environ, H62_DRY="1", H62_STATE=tmp, H62_KVM_GID="992")
        r = subprocess.run(["bash", os.path.join(HERE, "vm.sh"), "up", "b"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docker run -d --name h62-vm-b --network host --device /dev/kvm", r.stdout)
        self.assertIn("-smp 96,sockets=1,cores=48,threads=2 -m 192G", r.stdout)
        want = h62.qemu_argv("b", tmp, h62.QEMU_IMAGE, uid=os.getuid(), gid=os.getgid(), kvm_gid=992)
        self.assertIn(" ".join(want) + "\n", r.stdout)
        self.assertIn("qemu-img create -f qcow2 -F qcow2 -b /h62/base.img /h62/b/disk.qcow2 400G", r.stdout)
        self.assertIn("genisoimage -output /h62/b/seed.iso -volid cidata -joliet -rock", r.stdout)
        self.assertEqual(read(tmp, "b", "meta-data"), h62.meta_data("b"))
        self.assertEqual(read(tmp, "b", "user-data"), h62.user_data("b", CloudInitTest.KEY))
        self.assertEqual(read(tmp, "ssh_config"), h62.ssh_config(tmp))

    def test_pairs_hold_is_h50s_hold_with_remote_arms(self):
        sh = read(HERE, "pairs-hold.sh")
        self.assertIn('crescent/hold.sh" "${name}" 2400 env', sh)
        self.assertIn("hold) name=H62-hold${N} R=${B}/results/H62 guard=600 ;;", sh)
        for item in ("LIVE_MODE=pairs", "LIVE_CAP=2370", 'LIVE_HOLD="${N}"', "LIVE_MODEL_SET=gemma4",
                     'bash "${HERE}/../eatflow/live.sh" H62', "SERVING_VMS=h62-vm-a,h62-vm-b"):
            self.assertIn(item, sh, item)
        for absent in ("LIVE_MOUNT_B", "LIVE_EAT_CHART", "K3D_GPU_IMAGE"):        # nothing is deployed on the host
            self.assertNotIn(absent, sh, absent)

    def test_live_sh_calls_the_remote_arm_as_one_word(self):
        # LIVE_REMOTE_A names a script and its arm: live.sh calls "${RA}" with the arm as one word,
        # so arm.sh is called through a two-line wrapper per arm.
        for arm in ("A1", "B5"):
            w = read(HERE, f"arm-{arm}")
            self.assertIn(f'exec "$(dirname "$0")/arm.sh" {arm} "$@"', w)
            self.assertTrue(os.access(os.path.join(HERE, f"arm-{arm}"), os.X_OK))
        sh = read(HERE, "pairs-hold.sh")
        self.assertIn('LIVE_REMOTE_A="${HERE}/arm-A1"', sh)
        self.assertIn('LIVE_REMOTE_B="${HERE}/arm-B5"', sh)

    def test_guest_b_is_intels_documented_path(self):
        sh = read(HERE, "guest-b.sh")
        steps = ['git clone -q -b "${TAG}" https://github.com/intel/enterprise-ai-solutions.git',
                 'h62.py" eas-pin configs/repos/repos.agent-toolkit.yaml',
                 "./es_auto_installer.sh configure --force",
                 "./es_auto_installer.sh init agent-toolkit",
                 'h62.py" eas-edit env/local',
                 "./es_auto_installer.sh install agent-toolkit"]
        pos = [sh.index(s) for s in steps]
        self.assertEqual(pos, sorted(pos), "steps in the documented order")
        self.assertIn('[ "$(git rev-parse HEAD)" = "${SHA}" ]', sh)
        # prerequisites.md "Node Kernel Settings", verbatim values.
        self.assertIn("fs.inotify.max_user_instances = 8192", sh)
        self.assertIn("fs.inotify.max_user_watches   = 1048576", sh)
        # global_config.yaml (base_domain_name and the rest) is only copied into the records.
        uses = [ln.strip() for ln in sh.splitlines() if "global_config.yaml" in ln]
        self.assertEqual(uses, ["# base_domain_name stays EAS's default: global_config.yaml is not edited",
                                "for f in global_config.yaml config.agent-toolkit.yaml config.inference.yaml "
                                "models.yaml inventory/hosts.yaml; do"])
        self.assertIn("HF_TOKEN", sh)                    # recorded as set or unset, never printed
        self.assertNotIn("--only", sh)
        self.assertNotIn("existing_kubernetes", sh)

    def test_guest_a_deploys_h50s_a1(self):
        sh = read(HERE, "guest-a.sh")
        live = read(REPO, "bench", "eatflow", "live.sh")
        for item in ("K3D_GPU_AGENT_DEVICE=CPU", "K3D_GPU_AGENT_MODEL=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov",
                     "K3D_GPU_AGENT_MODEL_NAME=gemma-4-26b-a4b-it", "K3D_GPU_AGENT_DRAFT=off",
                     "K3D_GPU_AGENT_TOOL_PARSER=gemma4", "K3D_GPU_AGENT_REASONING_PARSER=''", "K3D_GPU_AUTOQUANT=off"):
            self.assertIn(item, sh, item)
        # The same model set live.sh gave A1 in H50.
        self.assertIn("TGT=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov TGT_DIR=gemma-4-26B-A4B-it-qat-int4-ov A_TOOL=gemma4 A_DRAFT=off", live)
        self.assertIn("ALIAS=google/gemma-4-26B-A4B-it SERVED=gemma-4-26b-a4b-it", live)
        self.assertIn('stage.py" ovms', sh)
        self.assertIn("K3D_GPU_CLUSTER=k3dgpu-e-h62", sh)
        self.assertIn('K3D_GPU_IMAGE="$(P pin node_image)"', sh)
        self.assertIn("eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%", sh)
        for pkg in ("docker-ce", "docker-ce-cli"):
            self.assertIn(f'{pkg}="$(P pin docker_ce)"', sh)
        self.assertIn('containerd.io="$(P pin containerd)"', sh)
        self.assertIn("docker_gpg_fpr", sh)

    def test_smoke_gates_b5_through_eas_itself(self):
        sh = read(HERE, "smoke.sh")
        self.assertIn("./es_auto_installer.sh validate agent-toolkit", sh)
        self.assertIn("EAT_MODE=eas", sh)
        self.assertIn("MODEL=$(P pin catalog_name)", sh)
        self.assertIn("EAT_MODE=eas EAT_MODEL=${MODEL}", sh)
        self.assertIn('"${HERE}/pairs-hold.sh" 0 smoke', sh)
        hold = read(HERE, "pairs-hold.sh")
        self.assertIn("smoke) name=H62-smoke", hold)
        self.assertIn("guard=99999", hold)

    def test_campaign_order_and_caps(self):
        sh = read(HERE, "campaign.sh")
        pos = [sh.index(s) for s in ('"${HERE}/setup.sh"', '"${HERE}/smoke.sh"', '"${HERE}/pairs-hold.sh" "${n}"',
                                     "pairs-report")]
        self.assertEqual(pos, sorted(pos))
        self.assertIn("MAX_HOLDS=8", sh)
        self.assertIn('h62.py" pairs-left', sh)
        self.assertIn("trap teardown EXIT", sh)
        self.assertIn("--hyp H62 --arm-a A1 --arm-b B5", sh)
        self.assertIn("NOT_DELIVERABLE_MAX=2", sh)

    def test_setup_caps_and_gates(self):
        sh = read(HERE, "setup.sh")
        self.assertIn('flock -w 7200 9', sh)
        self.assertIn("A_CAP=2700", sh)          # 45 min
        self.assertIn("B_CAP=7200", sh)          # 120 min
        for phase in ("host", "guest", "nfd", "weights"):
            self.assertIn(f"P gates {phase}", sh)


if __name__ == "__main__":
    unittest.main(verbosity=1)

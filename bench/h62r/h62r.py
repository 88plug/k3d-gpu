#!/usr/bin/env python3
"""h62r.py: the pure parts of the H62r runner (EXPERIMENTS.md "H62r pre-registration"): H62
re-run with B5 at the GA tags (EAS v1.2.0 installing EAT release-2.0.0 and enterprise-inference
v1.1.0), k3d-gpu's CPU stack A1 unchanged, each in its own KVM guest on crescent. It started as a
copy of H62's runner (historical, never edited); the differences are the pins, the guests'
names and ports, D1 as a check, D3's sibling read and the re-run trigger. Python 3 standard
library only, so the same file runs on the host and in either guest.

  h62r.py pin NAME                      one pin of PINS (the shell scripts read every pin here)
  h62r.py qemu-image                    the QEMU image's name
  h62r.py vm VM FIELD                   a field of VMS (VM a or b)
  h62r.py arm ARM FIELD                 a field of ARMS (A1 or B5)
  h62r.py arm-fields ARM                every field arm.sh reads, on one line
  h62r.py ssh-config STATE              the ssh_config of both guests
  h62r.py user-data VM PUBKEY_FILE      the guest's cloud-init user-data
  h62r.py meta-data VM                  its cloud-init meta-data
  h62r.py qemu-argv VM STATE IMAGE UID GID KVM_GID [SHARE]
                                        the `docker run` of the guest's QEMU, one argument per line
  h62r.py host-facts                    the host gate's facts (MemAvailable, / used, reachability), JSON
  h62r.py gates PHASE FACTS_JSON        PASS/FAIL lines of one gate phase (host, guest, nfd,
                                        weights); exit 1 on any FAIL
  h62r.py ir-verify DIR                 A1's IR against its manifest.json; exit 1 on any mismatch
  h62r.py eas-check MANIFEST            D1 at the GA tags: the agent-toolkit layer manifest names the
                                        pinned commits and releases; prints them, writes nothing
  h62r.py eas-edit ENV_DIR              the documented catalog edit and D2, in place; prints the diffs
  h62r.py sibling-read CPU_DIR PROBE    D3's sibling read: the cpuset to reserve when EAS's topology
                                        probe (its output in PROBE) reads one thread per core where the
                                        kernel's thread_siblings_list under CPU_DIR has two; nothing
                                        when they agree; exit 2 on anything else
  h62r.py reserve CPUS GLOBAL_CONFIG    nri_reserved_cpu_list in EAS's global_config.yaml, in place;
                                        prints the diff
  h62r.py uids < PODS_JSON              every pod's uid, comma-separated
  h62r.py pick-pod OLD_UIDS < PODS_JSON "<name> <uid>" of a Ready pod not in OLD_UIDS; exit 1 if none
  h62r.py model-mount CONTAINER < POD_JSON
                                        where CONTAINER mounts the model-store PVC
  h62r.py verify-script SIBLINGS_JSON   a self-contained script that checks a weights directory
                                        against the Hub's file list (run in B5's pod)
  h62r.py trigger EAS_TAGS EAT_TAGS     the re-run trigger, from two `git ls-remote --tags` outputs
  h62r.py on-tty LOG REPLY CMD...       CMD with stdin on a new terminal that is sent REPLY, a newline
                                        and end of input; its output appended to LOG; its exit code
  h62r.py pairs-left PAIRS_DIR          B24 pairs still to run
  h62r.py vmtax H50_ANALYSIS H62R_ANALYSIS
                                        A1 on the host (H50) and in its guest, and H50's B1 on the
                                        host against B5 in its guest, described
"""
import difflib
import glob
import json
import os
import re
import sys

PINS = {
    # B5: EAS's GA release and the solution versions its release notes list (read 2026-11-06).
    # The v1.2.0 layer manifest names both commits itself; EAS release-1.2 is at the same commit.
    "eas_repo": "https://github.com/intel/enterprise-ai-solutions.git",
    "eas_tag": "v1.2.0",
    "eas_sha": "a051f00fcb2dd473fe763610471cd509db82c433",
    "ei_tag": "v1.1.0",
    "ei_sha": "d035b1ef1981fbf5084ff05c9a337e620478bb87",
    "eat_tag": "release-2.0.0",
    "eat_sha": "7b827e2d95cec826a20c7e3f5a30eb4818ed2992",
    "catalog_name": "gemma-4-26b",
    "stock_model_id": "google/gemma-4-26B-A4B-it",
    # D2: the weights both arms serve (crescent amendment M1).
    "qat_repo": "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
    "qat_rev": "f1e06dc520982d9b9edd76859fdb7ab209449949",
    # The image enterprise-inference v1.1.0's catalog names for gemma-4-26b (by tag; the digest it
    # serves, unchanged since 2026-10-29). Recorded, not forced.
    "vllm_cpu_image": "vllm/vllm-openai-cpu:v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647",
    # The guests (D3), as H62 had them: every pin below is still the current one (read 2026-11-06)
    # but Docker CE, at its current patch release.
    "cloud_image_url": "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img",
    "cloud_image_sha256": "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94",
    "cloud_image_bytes": 621284352,
    "qemu_base": "ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c",
    "qemu_pkg": "1:10.2.1+ds-1ubuntu3.3",
    "genisoimage_pkg": "9:1.1.11-5",
    # A1's guest: Docker CE from Docker's apt repository, H50's node image and H50's OVMS.
    "docker_ce": "5:29.9.3-1~ubuntu.24.04~noble",
    "containerd": "2.4.2-3~ubuntu.24.04~noble",
    "docker_gpg_fpr": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
    "python3_yaml": "6.0.1-2build2",          # bench/apb/stage.py in VM a
    "jq": "1.7.1-3ubuntu0.24.04.2",           # integrations/eat/eat-check.sh in VM b (the smoke)
    "node_image": "cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c",
    "ovms_image": "openvino/model_server:2026.4.1-gpu@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58",
}
QEMU_IMAGE = "h62r-qemu:" + re.sub(r"[^A-Za-z0-9_.-]", "-", PINS["qemu_pkg"])

VCPUS = 96
SMP = "96,sockets=1,cores=48,threads=2"   # crescent's topology: 1 socket, 48 cores, 2 threads
HOST_RESERVE_GIB = 48                     # MemAvailable left to the host's own tenants
ROOT_MAX_PCT = 90
REACH = {"huggingface.co": "https://huggingface.co/api/models/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
         "registry-1.docker.io": "https://registry-1.docker.io/v2/",
         "registry.k8s.io": "https://registry.k8s.io/v2/"}
VMS = {
    "a": {"name": "h62r-vm-a", "host": "h62r-a", "arm": "A1", "mem_gib": 96, "disk_gib": 200,
          "ssh_port": 18842, "serve_port": 18851, "share": True},
    "b": {"name": "h62r-vm-b", "host": "h62r-b", "arm": "B5", "mem_gib": 192, "disk_gib": 400,
          "ssh_port": 18843, "serve_port": 18852, "share": False},
}
ARMS = {
    # k3d-gpu's agent OVMS (agent manifest: Deployment ovms, label app=ovms, Service ovms:8000).
    "A1": {"vm": "a", "ns": "k3d-gpu-agent", "selector": "app=ovms", "svc": "svc/ovms", "svc_port": 8000,
           "container": "ovms", "extra": ""},
    # EAS's gemma-4-26b: a KServe LLMInferenceService (enterprise-inference v1.1.0's model_manager
    # and KServe role are 170eb3a's), behind <name>-kserve-workload-svc:8000.
    "B5": {"vm": "b", "ns": "llm-inference",
           "selector": "app.kubernetes.io/part-of=llminferenceservice,app.kubernetes.io/name=gemma-4-26b,"
                       "kserve.io/component=workload",
           "svc": "svc/gemma-4-26b-kserve-workload-svc", "svc_port": 8000, "container": "main",
           "extra": "llminferenceservice/gemma-4-26b"},
}
TASKS = 24                                # B24's task ids, one pair each


def arm_fields(arm):
    """What bench/h62r/arm.sh needs, in one call: VM, guest host, serve port, namespace, selector,
    Service, Service port, container, then the extra object."""
    a = ARMS[arm]
    v = VMS[a["vm"]]
    return [a["vm"], v["host"], v["serve_port"], a["ns"], a["selector"], a["svc"], a["svc_port"], a["container"],
            a["extra"]]


def mem_needed_kib():
    return (sum(v["mem_gib"] for v in VMS.values()) + HOST_RESERVE_GIB) * 1024 * 1024


def host_facts(meminfo="/proc/meminfo", df_text=None, probe=None):
    """MemAvailable (KiB), how full / is (df's percentage) and, per REACH host, whether its URL
    answered over HTTPS at all (any HTTP status counts: a registry answers 401)."""
    import subprocess
    with open(meminfo) as f:
        mem = {k: v for k, _, v in (ln.partition(":") for ln in f)}
    if df_text is None:
        df_text = subprocess.run(["df", "-P", "/"], capture_output=True, text=True).stdout
    pct = int(df_text.splitlines()[-1].split()[4].rstrip("%"))
    if probe is None:
        def probe(url):
            return subprocess.run(["curl", "-sS", "-o", "/dev/null", "--max-time", "20", url],
                                  capture_output=True).returncode == 0
    return {"mem_available_kib": int(mem["MemAvailable"].split()[0]), "root_used_pct": pct,
            "reach": {h: bool(probe(u)) for h, u in REACH.items()}}


def ir_verify(d):
    """(lines, bad) of A1's IR directory against its manifest.json: every listed file's sha256
    and size, and the files the manifest does not list."""
    import hashlib
    files = _load(os.path.join(d, "manifest.json"))["files"]
    out, bad = [], 0
    for n, want in sorted(files.items()):
        p = os.path.join(d, n)
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        ok = h.hexdigest() == want["sha256"] and os.path.getsize(p) == want["bytes"]
        bad += not ok
        out.append(f"{'ok ' if ok else 'BAD'} {n} {h.hexdigest()} {want['bytes']}")
    extra = sorted(set(os.listdir(d)) - set(files) - {"manifest.json"})
    out.append(f"dir {d}; {len(files)} files, {bad} bad; not in the manifest: {extra}")
    return out, bad


def ssh_config(state):
    out = []
    for v in VMS.values():
        out += [f"Host {v['host']}", "  HostName 127.0.0.1", f"  Port {v['ssh_port']}", "  User ubuntu",
                f"  IdentityFile {state}/id_ed25519", "  IdentitiesOnly yes", f"  UserKnownHostsFile {state}/known_hosts",
                "  StrictHostKeyChecking accept-new", "  BatchMode yes", "  ControlMaster auto",
                f"  ControlPath {state}/cm-%C", "  ControlPersist 600", "  ServerAliveInterval 30", ""]
    return "\n".join(out)


def _key(pubkey):
    k = pubkey.strip()
    if not re.fullmatch(r"ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( [^\n]*)?", k):
        raise ValueError("not one ssh public key line")
    return k


def user_data(vm, pubkey):
    """cloud-init for one guest: the harness's key on the default user, no password login, and
    the image's package state kept (D3): no upgrade at boot and no apt timer running alongside the
    installs."""
    return "\n".join([
        "#cloud-config",
        f"hostname: {VMS[vm]['host']}",
        "ssh_pwauth: false",
        "ssh_authorized_keys:",
        f"  - {_key(pubkey)}",
        "package_update: false",
        "package_upgrade: false",
        "runcmd:",
        "  - [systemctl, disable, --now, unattended-upgrades.service, apt-daily.timer, apt-daily-upgrade.timer]",
        ""])


def meta_data(vm):
    h = VMS[vm]["host"]
    return f"instance-id: {h}\nlocal-hostname: {h}\n"


def qemu_argv(vm, state, image, share=None, uid=None, gid=None, kvm_gid=None):
    """The guest's QEMU, in the pinned container: KVM, -cpu host (AMX passes through), the host's
    topology, user-mode networking with the ssh and serve ports on 127.0.0.1 only, a qcow2 overlay
    on the cloud image, and for VM a A1's IR shared read-only."""
    v = VMS[vm]
    if v["share"] and not share:
        raise ValueError(f"VM {vm} needs A1's IR directory to share")
    if share and not v["share"]:
        raise ValueError(f"VM {vm} shares nothing")
    if None in (uid, gid, kvm_gid):
        raise ValueError("uid, gid and the kvm group are required")
    argv = ["docker", "run", "-d", "--name", v["name"], "--network", "host", "--device", "/dev/kvm",
            "--user", f"{uid}:{gid}", "--group-add", str(kvm_gid), "--label", "io.k3d-gpu.h62r=vm",
            "-v", f"{state}:/h62r"]
    if share:
        argv += ["-v", f"{share}:/share:ro"]
    fwd = f"hostfwd=tcp:127.0.0.1:{v['ssh_port']}-:22,hostfwd=tcp:127.0.0.1:{v['serve_port']}-:{v['serve_port']}"
    argv += [image, "qemu-system-x86_64", "-name", v["name"], "-machine", "q35,accel=kvm", "-cpu", "host",
             "-smp", SMP, "-m", f"{v['mem_gib']}G",
             "-drive", f"file=/h62r/{vm}/disk.qcow2,if=virtio,format=qcow2",
             "-drive", f"file=/h62r/{vm}/seed.iso,if=virtio,format=raw,readonly=on",
             "-netdev", f"user,id=n0,{fwd}", "-device", "virtio-net-pci,netdev=n0"]
    if share:
        argv += ["-virtfs", "local,path=/share,mount_tag=h62rshare,security_model=none,readonly=on"]
    return argv + ["-display", "none", "-serial", "stdio", "-monitor", "none"]


def gates(phase, f):
    """[(ok, message)] of one gate phase, from its facts."""
    out = []
    if phase == "host":
        need = mem_needed_kib()
        have = f.get("mem_available_kib")
        out.append((have is not None and have >= need,
                    f"MemAvailable {have} KiB >= {need} KiB (both guests + {HOST_RESERVE_GIB} GiB)"))
        pct = f.get("root_used_pct")
        out.append((pct is not None and pct <= ROOT_MAX_PCT, f"/ used {pct}% <= {ROOT_MAX_PCT}%"))
        reach = f.get("reach") or {}
        for h in REACH:
            out.append((reach.get(h) is True, f"{h} reachable"))
    elif phase == "guest":
        for vm in VMS:
            amx = (f.get("amx_tile") or {}).get(vm)
            out.append((amx == VCPUS, f"VM {vm}: amx_tile on {amx} of {VCPUS} vCPUs"))
            n = (f.get("nproc") or {}).get(vm)
            out.append((n == VCPUS, f"VM {vm}: nproc {n} == {VCPUS}"))
    elif phase == "nfd":
        out.append((f.get("amxbf16") == "true", f"VM b node label cpu-cpuid.AMXBF16={f.get('amxbf16')}"))
    elif phase == "weights":
        ok = f.get("bad") == 0 and f.get("missing") == 0 and (f.get("checked") or 0) > 0
        out.append((ok, f"B5's weights: {f.get('checked')} LFS files checked, {f.get('bad')} differ, "
                        f"{f.get('missing')} missing, against {PINS['qat_repo']}@{PINS['qat_rev']}"))
    else:
        raise ValueError(f"no gate phase {phase}")
    return out


def _diff(old, new, name):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), name, name))


def eas_check(text):
    """D1 at the GA tags. EAS v1.2.0's agent-toolkit layer manifest names each layer's commit
    itself, with its release in the line's comment (the release notes' solution table). Checked,
    never rewritten: each layer must carry one rev line, the pinned commit and the pinned release.
    Returns one line per layer."""
    want = {"inference": (PINS["ei_sha"], PINS["ei_tag"]), "agent-toolkit": (PINS["eat_sha"], PINS["eat_tag"])}
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r'\s*- layer: "', ln)]
    seen = {}
    for k, i in enumerate(starts):
        layer = re.match(r'\s*- layer: "([^"]+)"', lines[i]).group(1)
        if layer not in want:
            continue
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        revs = [lines[j] for j in range(i, end) if re.match(r"\s*rev:", lines[j])]
        if len(revs) != 1:
            raise ValueError(f"layer {layer}: {len(revs)} rev lines, want 1")
        sha, tag = want[layer]
        m = re.fullmatch(r'\s*rev: "([0-9a-f]{40})" # (\S+)\s*', revs[0])
        if not m or (m.group(1), m.group(2)) != (sha, tag):
            raise ValueError(f"layer {layer}: rev line is not {sha} ({tag}): {revs[0].strip()}")
        seen[layer] = f"{layer} {sha} ({tag})"
    if set(seen) != set(want):
        raise ValueError(f"layers missing from the manifest: {sorted(set(want) - set(seen))}")
    return [seen[k] for k in want]


def set_autodeploy(text, name):
    """The documented edit (single-node-deployment.md step 3): the one live
    llm_services_autodeploy_models line names one catalog value."""
    pat = re.compile(r"^llm_services_autodeploy_models:.*$", re.M)
    if len(pat.findall(text)) != 1:
        raise ValueError("want exactly one live llm_services_autodeploy_models line")
    return pat.sub(f'llm_services_autodeploy_models: ["{name}"]', text)


def set_model_id(text, name, old_id, new_id):
    """D2: the model_id of catalog entry `name` (and only that entry) moves from the stock id to
    new_id. Refuses when the entry or its stock id is not there."""
    lines = text.splitlines(True)
    heads = [i for i, ln in enumerate(lines) if re.match(r"\s*- name:\s*\S", ln)]
    for k, i in enumerate(heads):
        # A name line may carry an inline comment (the catalog's first entry does; H62 amendment V3).
        if re.match(r"\s*- name:\s*(\S+)\s*(#.*)?$", lines[i]).group(1) != name:
            continue
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        ids = [j for j in range(i, end) if re.match(r"\s*model_id:", lines[j])]
        if len(ids) != 1:
            raise ValueError(f"{name}: {len(ids)} model_id lines")
        j = ids[0]
        indent, value = re.match(r"(\s*)model_id:\s*(\S+)", lines[j]).groups()
        if value == new_id:
            return text
        if value != old_id:
            raise ValueError(f"{name}: model_id is {value}, not the stock {old_id}")
        lines[j] = f"{indent}model_id: {new_id}\n"
        return "".join(lines)
    raise ValueError(f"no catalog entry {name}")


def eas_edit(env_dir):
    """Both edits on an env directory; returns their diffs ("" when nothing changed)."""
    out = []
    for name, fn in (("config.agent-toolkit.yaml", lambda t: set_autodeploy(t, PINS["catalog_name"])),
                     ("models.yaml", lambda t: set_model_id(t, PINS["catalog_name"], PINS["stock_model_id"],
                                                            PINS["qat_repo"]))):
        p = os.path.join(env_dir, name)
        with open(p) as f:
            old = f.read()
        new = fn(old)
        if new != old:
            with open(p, "w") as f:
                f.write(new)
            out.append(_diff(old, new, name))
    return "".join(out)


def _cpulist(s):
    """The CPU ids of a Linux cpulist ("0-1", "0,48", "1,3,5-7")."""
    out = set()
    for part in s.strip().split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part.strip())
        if not m:
            raise ValueError(f"not a cpulist: {s!r}")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(range(lo, hi + 1))
    return out


def sibling_read(lists, probe_text):
    """D3's sibling read. `lists` maps each CPU id to its thread_siblings_list as the guest kernel
    writes it; `probe_text` is the output of EAS's own topology probe
    (roles/nri_cpu_balloons/files/probe-node-topology.sh) on the same machine.

    The probe reads threads per core as the comma fields of cpu0's list. On crescent's host the
    list is "0,48" (2). In the guest QEMU numbers siblings next to each other and Linux writes the
    pair as a range, "0-1", which the probe reads as 1, so EAS's default 'siblings' profile falls
    back to 'standard' (12 reserved CPUs) and gemma-4-26b's `cpu: auto` took 76 of 96 (H62).
    When the probe reads 1 and the kernel lists 2 threads on every core, the result is the cpuset
    EAS's siblings profile reserves when its probe reads SMT: the higher thread of each core.
    When the probe agrees with the kernel, nothing ("") is reserved by the runner. Anything else
    stops the setup (ValueError)."""
    if not lists:
        raise ValueError("no CPU sibling list read")
    sets = {cpu: _cpulist(s) for cpu, s in lists.items()}
    sizes = {len(s) for s in sets.values()}
    if len(sizes) != 1:
        raise ValueError(f"threads per core differ across CPUs: {sorted(sizes)}")
    kernel = sizes.pop()
    m = re.search(r"(?m)^NRI_TPC=(\d+)\s*$", probe_text)
    if not m:
        raise ValueError("no NRI_TPC line in the probe's output")
    probed = int(m.group(1))
    if probed == kernel:
        return ""
    if (probed, kernel) != (1, 2):
        raise ValueError(f"EAS's probe reads {probed} thread(s) per core, the kernel lists {kernel}")
    sib = sorted(cpu for cpu, s in sets.items() if cpu != min(s))
    if len(sib) * 2 != len(sets):
        raise ValueError(f"{len(sib)} sibling CPUs of {len(sets)}, want half")
    return ",".join(str(c) for c in sib)


def read_sibling_lists(cpu_dir):
    """{cpu id: thread_siblings_list} under a sysfs cpu directory (/sys/devices/system/cpu)."""
    out = {}
    for d in glob.glob(os.path.join(cpu_dir, "cpu[0-9]*")):
        m = re.fullmatch(r"cpu(\d+)", os.path.basename(d))
        p = os.path.join(d, "topology", "thread_siblings_list")
        if m and os.path.isfile(p):
            with open(p) as f:
                out[int(m.group(1))] = f.read().strip()
    return out


def reserve(text, cpus):
    """nri_reserved_cpu_list in EAS's global_config.yaml: the key EAS's
    docs/customize/nri_cpu_balloons.md documents there ("Optional: pin reserved CPUs to a fixed
    cpuset"), read by the nri_cpu_balloons role (the installer passes global_config.yaml to every
    playbook as extra vars). Filled when absent or at its documented empty default; a line with
    another value is refused."""
    if not re.fullmatch(r"[0-9]+([,-][0-9]+)*", cpus):          # EAS's own cpuset check
        raise ValueError(f"not a cpuset: {cpus!r}")
    line = f'nri_reserved_cpu_list: "{cpus}"'
    pat = re.compile(r'^nri_reserved_cpu_list:[ \t]*"?([^"\n]*)"?[ \t]*$', re.M)
    found = pat.findall(text)
    if len(found) > 1:
        raise ValueError("more than one live nri_reserved_cpu_list line")
    if found:
        if found[0] == cpus:
            return text
        if found[0]:
            raise ValueError(f"nri_reserved_cpu_list is already {found[0]!r}")
        return pat.sub(line, text)
    sep = "" if text.endswith("\n") or not text else "\n"
    return (text + sep + "\n# H62r D3's sibling read: the cpuset EAS's 'siblings' profile reserves when its probe\n"
            "# reads this guest's SMT (EXPERIMENTS.md \"H62r pre-registration\")\n" + line + "\n")


def _ready(item):
    st = item.get("status") or {}
    return (st.get("phase") == "Running" and not (item.get("metadata") or {}).get("deletionTimestamp")
            and any(c.get("type") == "Ready" and c.get("status") == "True" for c in st.get("conditions") or []))


def pick_pod(doc, old):
    """(name, uid) of the newest Running, Ready, not-deleting pod whose uid is not in old."""
    new = [i for i in doc.get("items", []) if i["metadata"]["uid"] not in old and _ready(i)]
    if not new:
        return None
    m = max(new, key=lambda i: i["metadata"].get("creationTimestamp", ""))["metadata"]
    return m["name"], m["uid"]


def model_mount(pod, container, claim="model-store"):
    """Where `container` mounts the PVC `claim` (EAS's model store)."""
    vols = {v["name"] for v in pod["spec"].get("volumes", [])
            if (v.get("persistentVolumeClaim") or {}).get("claimName") == claim}
    for c in pod["spec"].get("containers", []):
        if c["name"] == container:
            for m in c.get("volumeMounts", []):
                if m["name"] in vols:
                    return m["mountPath"]
    raise ValueError(f"container {container} mounts no volume of PVC {claim}")


VERIFY = r'''#!/usr/bin/env python3
# Generated by bench/h62r/h62r.py verify-script: DIR against the Hub's file list of
# @REPO@@@REV@ (H62r, B5's weights). Every LFS file's sha256, every other listed file's size, and
# the commit `hf download --local-dir` recorded per file. Last line: a JSON summary.
import glob, hashlib, json, os, sys
SIB = json.loads(@SIB@)
d = sys.argv[1]
bad = missing = checked = 0
for s in SIB:
    p = os.path.join(d, s["rfilename"])
    lfs = s.get("lfs")
    if not os.path.exists(p):
        print(("MISSING " if lfs else "absent  ") + s["rfilename"])
        missing += bool(lfs)
        continue
    if lfs:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        ok = h.hexdigest() == lfs["sha256"]
        checked += 1
        print(("ok  " if ok else "BAD ") + h.hexdigest() + " " + s["rfilename"])
    else:
        ok = os.path.getsize(p) == s.get("size", os.path.getsize(p))
        print(("ok  " if ok else "BAD ") + "size " + str(os.path.getsize(p)) + " " + s["rfilename"])
    bad += not ok
revs = set()
for m in glob.glob(os.path.join(d, ".cache", "huggingface", "download", "**", "*.metadata"), recursive=True):
    with open(m) as f:
        revs.add(f.readline().strip())
print(json.dumps({"want": "@REV@", "revisions": sorted(revs), "checked": checked, "bad": bad, "missing": missing}))
sys.exit(1 if bad or missing else 0)
'''


def verify_script(siblings):
    """A stdlib script that checks a weights directory against `siblings` (the Hub API's
    /api/models/<repo>/revision/<rev>?blobs=true answer); it needs no network."""
    sib = [{k: s[k] for k in ("rfilename", "size", "lfs") if k in s} for s in siblings["siblings"]]
    return (VERIFY.replace("@SIB@", repr(json.dumps(sib))).replace("@REPO@", PINS["qat_repo"])
            .replace("@REV@", PINS["qat_rev"]))


def _tags(ls_remote):
    return {m.group(1) for m in re.finditer(r"refs/tags/([^\s^]+)(?:\^\{\})?\s*$", ls_remote, re.M)}


def trigger(eas_ls_remote, eat_ls_remote):
    """H62r's re-run trigger: an EAS release tag after v1.2.0 (vX.Y.Z, not a release candidate),
    or an EAT release tag after release-2.0.0 (release-X.Y.Z or vX.Y.Z, and EAT's hotfix form
    release-X.Y.Z-hotfix, which follows the release of the same number)."""
    def eas(t):
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", t)
        return bool(m) and tuple(int(x) for x in m.groups()) > (1, 2, 0)

    def eat(t):
        m = re.fullmatch(r"(?:release-|v)(\d+)\.(\d+)\.(\d+)(-hotfix)?", t)
        return bool(m) and (*(int(x) for x in m.groups()[:3]), bool(m.group(4))) > (2, 0, 0, False)
    return {"eas": sorted(t for t in _tags(eas_ls_remote) if eas(t)),
            "eat": sorted(t for t in _tags(eat_ls_remote) if eat(t))}


def pairs_left(pairs_dir, total=TASKS):
    """B24 pairs not yet recorded (complete or void): pairs resume across holds (pairs.next_pair)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eatflow"))
    import pairs  # noqa: E402  (bench/eatflow/pairs.py)
    return max(0, total - pairs.next_pair(pairs_dir))


def vmtax(h50, mine):
    """Described, never gated. A1 on the host (H50's analysis) against A1 in its guest (H62r's):
    the same stack, so the guest alone. And H50's B1 on the host against B5 in its guest: the same
    vLLM CPU v0.28.0 image and QAT weights under another deployment, so the guest and the
    deployment together."""
    def arm(a, name):
        s = (a.get("within_budget_success") or {}).get(name)
        if not s:
            return None
        return {"success": f"{s['k']}/{s['n']}", "attempt_median_s": (a.get("attempt_median_s") or {}).get(name)}

    def ratio(host, guest):
        if host and guest and host["attempt_median_s"] and guest["attempt_median_s"]:
            return round(guest["attempt_median_s"] / host["attempt_median_s"], 3)
        return None
    a_host, a_guest, b_host, b_guest = arm(h50, "A1"), arm(mine, "A1"), arm(h50, "B1"), arm(mine, "B5")
    return {"a1_host": a_host, "a1_guest": a_guest, "attempt_median_guest_over_host": ratio(a_host, a_guest),
            "b1_host": b_host, "b5_guest": b_guest, "b_attempt_median_guest_over_host": ratio(b_host, b_guest),
            "b_note": "B1 (EAT release-1.1.0's chart) and B5 (EAS's KServe deployment) share the vLLM image and "
                      "weights, not the deployment: the guest and the deployment are read together",
            "gated": False}


def on_tty(log, reply, cmd):
    """Run cmd with stdin on a fresh pseudo-terminal, type reply and Enter into it, then end of
    input (^D on a new line), the way a person answers a y/N prompt (H62 amendment V2: EAS's
    `configure` takes no option and refuses a stdin that is not a terminal). Output and errors
    are appended to log. Returns cmd's exit code."""
    import pty
    import subprocess
    master, slave = pty.openpty()
    try:
        with open(log, "ab") as out:
            p = subprocess.Popen(cmd, stdin=slave, stdout=out, stderr=subprocess.STDOUT)
        os.close(slave)
        slave = None
        os.write(master, reply.encode() + b"\n\x04")
        return p.wait()
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)


def _load(path):
    with open(path) as f:
        return json.load(f)


def _edit(path, fn):
    """Apply fn to a file's text in place; returns the diff ("" when nothing changed)."""
    with open(path) as f:
        old = f.read()
    new = fn(old)
    if new == old:
        return ""
    with open(path, "w") as f:
        f.write(new)
    return _diff(old, new, os.path.basename(path))


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "pin":
        if args[0] not in PINS:
            print(f"h62r.py: no pin {args[0]}", file=sys.stderr)
            return 2
        print(PINS[args[0]])
    elif cmd == "qemu-image":
        print(QEMU_IMAGE)
    elif cmd == "vm":
        if args[0] not in VMS or args[1] not in VMS[args[0]]:
            print(f"h62r.py: no VM field {args}", file=sys.stderr)
            return 2
        print(VMS[args[0]][args[1]])
    elif cmd == "arm-fields":
        print(*arm_fields(args[0]))
    elif cmd == "arm":
        if args[0] not in ARMS or args[1] not in ARMS[args[0]]:
            print(f"h62r.py: no arm field {args}", file=sys.stderr)
            return 2
        print(ARMS[args[0]][args[1]])
    elif cmd == "ssh-config":
        sys.stdout.write(ssh_config(args[0]))
    elif cmd == "user-data":
        with open(args[1]) as f:
            sys.stdout.write(user_data(args[0], f.read()))
    elif cmd == "meta-data":
        sys.stdout.write(meta_data(args[0]))
    elif cmd == "qemu-argv":
        vm, state, image, uid, gid, kvm = args[:6]
        print("\n".join(qemu_argv(vm, state, image, share=args[6] if len(args) > 6 else None,
                                  uid=int(uid), gid=int(gid), kvm_gid=int(kvm))))
    elif cmd == "host-facts":
        print(json.dumps(host_facts()))
    elif cmd == "ir-verify":
        lines, bad = ir_verify(args[0])
        print("\n".join(lines))
        return 1 if bad else 0
    elif cmd == "gates":
        res = gates(args[0], _load(args[1]))
        for ok, msg in res:
            print(("PASS " if ok else "FAIL ") + msg)
        return 0 if all(ok for ok, _ in res) else 1
    elif cmd == "eas-check":
        with open(args[0]) as f:
            for line in eas_check(f.read()):
                print("ok " + line)
    elif cmd == "eas-edit":
        sys.stdout.write(eas_edit(args[0]))
    elif cmd == "sibling-read":
        with open(args[1]) as f:
            cpus = sibling_read(read_sibling_lists(args[0]), f.read())
        if cpus:
            print(cpus)
    elif cmd == "reserve":
        sys.stdout.write(_edit(args[1], lambda t: reserve(t, args[0])))
    elif cmd == "uids":
        print(",".join(i["metadata"]["uid"] for i in json.load(sys.stdin).get("items", [])))
    elif cmd == "pick-pod":
        p = pick_pod(json.load(sys.stdin), {u for u in (args[0] if args else "").split(",") if u})
        if not p:
            return 1
        print(*p)
    elif cmd == "model-mount":
        print(model_mount(json.load(sys.stdin), args[0]))
    elif cmd == "verify-script":
        sys.stdout.write(verify_script(_load(args[0])))
    elif cmd == "trigger":
        with open(args[0]) as f, open(args[1]) as g:
            t = trigger(f.read(), g.read())
        fired = [f"{k} {v}" for k in ("eas", "eat") for v in t[k]]
        print("fired: " + (", ".join(fired) if fired else "none"))
    elif cmd == "on-tty":
        return on_tty(args[0], args[1], args[2:])
    elif cmd == "pairs-left":
        print(pairs_left(args[0]))
    elif cmd == "vmtax":
        print(json.dumps(vmtax(_load(args[0]), _load(args[1])), indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, KeyError, IndexError, OSError) as e:
        print(f"h62r.py: {e}", file=sys.stderr)
        sys.exit(2)

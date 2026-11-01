#!/usr/bin/env python3
"""h62.py: the pure parts of the H62 runner (EXPERIMENTS.md "H62 pre-registration"): k3d-gpu's
CPU stack A1 against B5, Intel's documented single-node default (EAS v1.2.0-rc.1 installing
EAT 2.0), each in its own KVM guest on crescent. Python 3 standard library only, so the same
file runs on the host and in either guest.

  h62.py pin NAME                       one pin of PINS (the shell scripts read every pin here)
  h62.py vm VM FIELD                    a field of VMS (VM a or b)
  h62.py arm ARM FIELD                  a field of ARMS (A1 or B5)
  h62.py arm-fields ARM                 every field arm.sh reads, on one line
  h62.py ssh-config STATE               the ssh_config of both guests
  h62.py user-data VM PUBKEY_FILE       the guest's cloud-init user-data
  h62.py meta-data VM                   its cloud-init meta-data
  h62.py qemu-argv VM STATE IMAGE UID GID KVM_GID [SHARE]
                                        the `docker run` of the guest's QEMU, one argument per line
  h62.py host-facts                     the host gate's facts (MemAvailable, / used, reachability), JSON
  h62.py gates PHASE FACTS_JSON         PASS/FAIL lines of one gate phase (host, guest, nfd,
                                        weights); exit 1 on any FAIL
  h62.py ir-verify DIR                  A1's IR against its manifest.json; exit 1 on any mismatch
  h62.py eas-pin MANIFEST               deviation D1, in place: the agent-toolkit layer manifest's
                                        branch revs become the commits its comments name; prints the diff
  h62.py eas-edit ENV_DIR               the documented catalog edit and deviation D2, in place; prints
                                        the diffs (nothing when the env already reads so)
  h62.py uids < PODS_JSON               every pod's uid, comma-separated
  h62.py pick-pod OLD_UIDS < PODS_JSON  "<name> <uid>" of a Ready pod not in OLD_UIDS; exit 1 if none
  h62.py model-mount CONTAINER < POD_JSON
                                        where CONTAINER mounts the model-store PVC
  h62.py verify-script SIBLINGS_JSON    a self-contained script that checks a weights directory
                                        against the Hub's file list (run in B5's pod)
  h62.py trigger EAS_TAGS EAT_TAGS      the re-run trigger, from two `git ls-remote --tags` outputs
  h62.py pairs-left PAIRS_DIR           B24 pairs still to run
  h62.py vmtax H50_ANALYSIS H62_ANALYSIS
                                        A1 on the host (H50) and in its guest (H62), and H50's B1
                                        on the host against B5 in its guest, described
"""
import difflib
import json
import os
import re
import sys

PINS = {
    # B5: EAS's release candidate and the component table of its release notes (read 2026-10-29).
    "eas_repo": "https://github.com/intel/enterprise-ai-solutions.git",
    "eas_tag": "v1.2.0-rc.1",
    "eas_sha": "5c27fb39a95eecec41aa4e29a1188984e9c726f3",
    "ei_branch": "release-1.1",
    "ei_sha": "170eb3abb1956de0787727f21e356aef2b13ff24",
    "eat_branch": "release-2.0.0",
    "eat_sha": "036b66234801e9bd950afd90830821859780d668",
    "catalog_name": "gemma-4-26b",
    "stock_model_id": "google/gemma-4-26B-A4B-it",
    # Deviation D2: the weights both arms serve (crescent amendment M1).
    "qat_repo": "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
    "qat_rev": "f1e06dc520982d9b9edd76859fdb7ab209449949",
    # The image enterprise-inference's catalog names for gemma-4-26b (by tag; the digest it served
    # on 2026-10-29, the same image as crescent amendment E1's B1). Recorded, not forced.
    "vllm_cpu_image": "vllm/vllm-openai-cpu:v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647",
    # The guests (deviation D3): Ubuntu 24.04 (an OS EAS's configure supports), QEMU in a container.
    "cloud_image_url": "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img",
    "cloud_image_sha256": "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94",
    "cloud_image_bytes": 621284352,
    "qemu_base": "ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c",
    "qemu_pkg": "1:10.2.1+ds-1ubuntu3.3",
    "genisoimage_pkg": "9:1.1.11-5",
    # A1's guest: Docker CE from Docker's apt repository, and H50's node image.
    "docker_ce": "5:29.9.2-1~ubuntu.24.04~noble",
    "containerd": "2.4.2-3~ubuntu.24.04~noble",
    "docker_gpg_fpr": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
    "python3_yaml": "6.0.1-2build2",          # bench/apb/stage.py in VM a
    "jq": "1.7.1-3ubuntu0.24.04.2",           # integrations/eat/eat-check.sh in VM b (the smoke)
    "node_image": "cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c",
}
QEMU_IMAGE = "h62-qemu:" + re.sub(r"[^A-Za-z0-9_.-]", "-", PINS["qemu_pkg"])

VCPUS = 96
SMP = "96,sockets=1,cores=48,threads=2"   # crescent's topology: 1 socket, 48 cores, 2 threads
HOST_RESERVE_GIB = 48                     # MemAvailable left to the host's own tenants
ROOT_MAX_PCT = 90
REACH = {"huggingface.co": "https://huggingface.co/api/models/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
         "registry-1.docker.io": "https://registry-1.docker.io/v2/",
         "registry.k8s.io": "https://registry.k8s.io/v2/"}
VMS = {
    "a": {"name": "h62-vm-a", "host": "h62-a", "arm": "A1", "mem_gib": 96, "disk_gib": 200,
          "ssh_port": 18822, "serve_port": 18831, "share": True},
    "b": {"name": "h62-vm-b", "host": "h62-b", "arm": "B5", "mem_gib": 192, "disk_gib": 400,
          "ssh_port": 18823, "serve_port": 18832, "share": False},
}
ARMS = {
    # k3d-gpu's agent OVMS (agent manifest: Deployment ovms, label app=ovms, Service ovms:8000).
    "A1": {"vm": "a", "ns": "k3d-gpu-agent", "selector": "app=ovms", "svc": "svc/ovms", "svc_port": 8000,
           "container": "ovms", "extra": ""},
    # EAS's gemma-4-26b: a KServe LLMInferenceService; KServe v0.18.0 labels its workload pods so
    # (GetWorkloadLabelSelector) and serves them behind <name>-kserve-workload-svc:8000.
    "B5": {"vm": "b", "ns": "llm-inference",
           "selector": "app.kubernetes.io/part-of=llminferenceservice,app.kubernetes.io/name=gemma-4-26b,"
                       "kserve.io/component=workload",
           "svc": "svc/gemma-4-26b-kserve-workload-svc", "svc_port": 8000, "container": "main",
           "extra": "llminferenceservice/gemma-4-26b"},
}
TASKS = 24                                # B24's task ids, one pair each


def arm_fields(arm):
    """What bench/h62/arm.sh needs, in one call (it runs on every idle-arm read): VM, guest host,
    serve port, namespace, selector, Service, Service port, container, then the extra object."""
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
    the image's package state kept (deviation D3): no upgrade at boot and no apt timer running
    alongside the installs."""
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
            "--user", f"{uid}:{gid}", "--group-add", str(kvm_gid), "--label", "io.k3d-gpu.h62=vm",
            "-v", f"{state}:/h62"]
    if share:
        argv += ["-v", f"{share}:/share:ro"]
    fwd = f"hostfwd=tcp:127.0.0.1:{v['ssh_port']}-:22,hostfwd=tcp:127.0.0.1:{v['serve_port']}-:{v['serve_port']}"
    argv += [image, "qemu-system-x86_64", "-name", v["name"], "-machine", "q35,accel=kvm", "-cpu", "host",
             "-smp", SMP, "-m", f"{v['mem_gib']}G",
             "-drive", f"file=/h62/{vm}/disk.qcow2,if=virtio,format=qcow2",
             "-drive", f"file=/h62/{vm}/seed.iso,if=virtio,format=raw,readonly=on",
             "-netdev", f"user,id=n0,{fwd}", "-device", "virtio-net-pci,netdev=n0"]
    if share:
        argv += ["-virtfs", "local,path=/share,mount_tag=h62share,security_model=none,readonly=on"]
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


def eas_pin(text):
    """Deviation D1: in EAS's agent-toolkit layer manifest, each layer's branch rev becomes the
    commit Intel's own comment on that line names (the rc.1 release notes' component table).
    Refuses anything else: another branch, a comment naming another commit, a missing layer."""
    want = {"inference": (PINS["ei_branch"], PINS["ei_sha"]), "agent-toolkit": (PINS["eat_branch"], PINS["eat_sha"])}
    lines = text.splitlines(True)
    starts = [i for i, ln in enumerate(lines) if re.match(r'\s*- layer: "', ln)]
    seen = set()
    for k, i in enumerate(starts):
        layer = re.match(r'\s*- layer: "([^"]+)"', lines[i]).group(1)
        if layer not in want:
            continue
        seen.add(layer)
        branch, sha = want[layer]
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        revs = [j for j in range(i, end) if re.match(r"\s*rev:", lines[j])]
        if len(revs) != 1:
            raise ValueError(f"layer {layer}: {len(revs)} rev lines, want 1")
        j = revs[0]
        indent = re.match(r"(\s*)", lines[j]).group(1)
        pinned = f'{indent}rev: "{sha}" # H62 D1, was "{branch}"\n'
        if lines[j] == pinned:
            continue
        m = re.fullmatch(r'\s*rev: "([^"]+)" # pin ([0-9a-f]{40}) .*\n?', lines[j])
        if not m or m.group(1) != branch or m.group(2) != sha:
            raise ValueError(f"layer {layer}: rev line is not {branch!r} pinned to {sha}: {lines[j].strip()}")
        lines[j] = pinned
    if seen != set(want):
        raise ValueError(f"layers missing from the manifest: {sorted(set(want) - seen)}")
    return "".join(lines)


def set_autodeploy(text, name):
    """The documented edit (single-node-deployment.md step 3): the one live
    llm_services_autodeploy_models line names one catalog value."""
    pat = re.compile(r"^llm_services_autodeploy_models:.*$", re.M)
    if len(pat.findall(text)) != 1:
        raise ValueError("want exactly one live llm_services_autodeploy_models line")
    return pat.sub(f'llm_services_autodeploy_models: ["{name}"]', text)


def set_model_id(text, name, old_id, new_id):
    """Deviation D2: the model_id of catalog entry `name` (and only that entry) moves from the
    stock id to new_id. Refuses when the entry or its stock id is not there."""
    lines = text.splitlines(True)
    heads = [i for i, ln in enumerate(lines) if re.match(r"\s*- name:\s*\S", ln)]
    for k, i in enumerate(heads):
        if re.match(r"\s*- name:\s*(\S+)\s*$", lines[i]).group(1) != name:
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
# Generated by bench/h62/h62.py verify-script: DIR against the Hub's file list of
# @REPO@@@REV@ (H62, B5's weights). Every LFS file's sha256, every other listed file's size, and
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


def _release(tag, pattern, at_least):
    m = re.fullmatch(pattern, tag)
    return bool(m) and tuple(int(x) for x in m.groups()) >= at_least


def trigger(eas_ls_remote, eat_ls_remote):
    """The re-run trigger: an EAS release tag at v1.2.0 or later (vX.Y.Z, not a release
    candidate), or an EAT release tag at 2.0.0 or later (EAT tags its releases release-X.Y.Z and
    its hotfixes release-X.Y.Z-hotfix; a vX.Y.Z tag counts too). Full support may ship under a
    later number than v1.2.x or 2.x, so any later release fires."""
    eas = sorted(t for t in _tags(eas_ls_remote) if _release(t, r"v(\d+)\.(\d+)\.(\d+)", (1, 2, 0)))
    eat = sorted(t for t in _tags(eat_ls_remote)
                 if _release(t, r"(?:release-|v)?(\d+)\.(\d+)\.(\d+)(?:-hotfix)?", (2, 0, 0)))
    return {"eas": eas, "eat": eat}


def pairs_left(pairs_dir, total=TASKS):
    """B24 pairs not yet recorded (complete or void): pairs resume across holds (pairs.next_pair)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eatflow"))
    import pairs  # noqa: E402  (bench/eatflow/pairs.py)
    return max(0, total - pairs.next_pair(pairs_dir))


def vmtax(h50, h62):
    """Described, never gated. A1 on the host (H50's analysis) against A1 in its guest (H62's):
    the same stack, so the guest alone. And H50's B1 on the host against B5 in its guest: the same
    vLLM CPU v0.28.0 image and QAT weights under another deployment, so the guest and the
    deployment together; it shows whether the guest costs vLLM's arm more than OVMS's."""
    def arm(a, name):
        s = (a.get("within_budget_success") or {}).get(name)
        if not s:
            return None
        return {"success": f"{s['k']}/{s['n']}", "attempt_median_s": (a.get("attempt_median_s") or {}).get(name)}

    def ratio(host, guest):
        if host and guest and host["attempt_median_s"] and guest["attempt_median_s"]:
            return round(guest["attempt_median_s"] / host["attempt_median_s"], 3)
        return None
    a_host, a_guest, b_host, b_guest = arm(h50, "A1"), arm(h62, "A1"), arm(h50, "B1"), arm(h62, "B5")
    return {"a1_host": a_host, "a1_guest": a_guest, "attempt_median_guest_over_host": ratio(a_host, a_guest),
            "b1_host": b_host, "b5_guest": b_guest, "b_attempt_median_guest_over_host": ratio(b_host, b_guest),
            "b_note": "B1 (EAT release-1.1.0's chart) and B5 (EAS's KServe deployment) share the vLLM image and "
                      "weights, not the deployment: the guest and the deployment are read together",
            "gated": False}


def _load(path):
    with open(path) as f:
        return json.load(f)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "pin":
        if args[0] not in PINS:
            print(f"h62.py: no pin {args[0]}", file=sys.stderr)
            return 2
        print(PINS[args[0]])
    elif cmd == "qemu-image":
        print(QEMU_IMAGE)
    elif cmd == "vm":
        if args[0] not in VMS or args[1] not in VMS[args[0]]:
            print(f"h62.py: no VM field {args}", file=sys.stderr)
            return 2
        print(VMS[args[0]][args[1]])
    elif cmd == "arm-fields":
        print(*arm_fields(args[0]))
    elif cmd == "arm":
        if args[0] not in ARMS or args[1] not in ARMS[args[0]]:
            print(f"h62.py: no arm field {args}", file=sys.stderr)
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
    elif cmd == "eas-pin":
        with open(args[0]) as f:
            old = f.read()
        new = eas_pin(old)
        if new != old:
            with open(args[0], "w") as f:
                f.write(new)
            sys.stdout.write(_diff(old, new, os.path.basename(args[0])))
    elif cmd == "eas-edit":
        sys.stdout.write(eas_edit(args[0]))
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
        print(f"h62.py: {e}", file=sys.stderr)
        sys.exit(2)

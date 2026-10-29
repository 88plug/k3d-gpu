# wildct resume pack

How to resume the wildct campaign ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Wildct
pre-registration", rules W1-W8, and wildct amendments R1, P1, R2 and R3) once the box owner has
made the kernel modules k3s needs available on wildct (step 1, H9 and H10). This file changes no
rule. Run it top to bottom, except that step 3b (ship `origin/main` to wildct) runs first when the
`repo` on wildct predates `bench/wildct/nfprobe.sh` (H10 runs it). Nothing past step 1 runs until
every step 1 check passes.

## Where things stand (2026-10-29, after `smoke3`)

- **Blocked: k3s still cannot run on wildct.** The box owner loaded `vxlan` and `br_netfilter`
  on 2026-10-29 and kept them across reboots with `/etc/modules-load.d/k3dgpu-wildct.conf`
  (`kernel.modprobe` left empty; the production checks read the same before and after:
  `results/2026-10-29/host/modules.txt`). Step 1 then passed (H9 included), main at `c4407a2c`
  was shipped (step 3b), and the wiring smoke `smoke3` printed every pass marker of step 5. The
  cluster was not working: k3s's network policy controller stopped every ~16 s on "Extension
  comment revision 0 not supported, missing kernel module?", the node container restarted, the
  node carried the `not-ready` taint, and coredns, metrics-server and the NFD pods stayed 0/1
  (`pods.txt`: 10 restarts each). The old pod wait read STATUS only, so it said `yes`. `smoke3`
  is discarded (amendment R3); the pod wait now runs `bench/wildct/ready.sh` (every node Ready,
  every container ready).
- **What the kernel still lacks.** `bench/wildct/nfprobe.sh` (step 1's H10) adds, in a throwaway
  container, one rule per iptables extension and one set per ipset type in its list, with the node
  image's own iptables-nft and ipset. On 2026-10-29 it read
  `NFPROBE missing: NFLOG REJECT comment hash:ip hash:ip,port hash:ip,port,ip hash:ip,port,net
  hash:net limit multiport physdev pkttype statistic` (`results/2026-10-29/runs/nfprobe.txt`).
  Every module behind them is on disk for 6.6.156-2-MANJARO and not loaded. The fix is the box
  owner's, either of:
  - restore `kernel.modprobe` (`/sbin/modprobe`; the kernel then loads what a rule asks for);
  - load the modules and add them to `/etc/modules-load.d/k3dgpu-wildct.conf`:
    `modprobe -a xt_comment xt_statistic xt_multiport xt_limit xt_NFLOG xt_physdev xt_pkttype
    ipt_REJECT ip6t_REJECT ip_set_hash_ip ip_set_hash_net ip_set_hash_ipport ip_set_hash_ipportip
    ip_set_hash_ipportnet`.

  Then step 3b (the `repo` on wildct is `c4407a2c`, without `nfprobe.sh` and `ready.sh`), step 1
  (H10 must read `NFPROBE clean`), and the smoke again, as `smoke4`.
- **Left on wildct by this campaign** (no container, cluster, network or volume):
  - `~/k3dgpu-bench-wildct/`, which holds:
    - `bin/`: k3d v5.9.0, helm v4.3.1, kubectl v1.37.2;
    - `k3d-gpu/`: the 0.1.79 `.deb`, unpacked;
    - `hfvenv/`;
    - `models/` and `h57/models/`: every file sha256-checked against the Hub, `VERIFY ok`
      (`setup/dl.log` also holds the first H57 verify's `VERIFY 1 bad`: the mmproj file,
      not downloaded on purpose; `setup/verify-h57.log` skips it and reads `VERIFY ok`);
    - `h57/models/k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf/` (step 4c, 2026-10-29: Q4_0
      `8eb21d5b...`, 321,145,696 bytes, the i9's bytes) and Od's `graph.pbtxt` (`c57ecf35...`,
      the i9's);
    - `h57/bin/k3d` (the shim) and `h57/weights.sha256`;
    - `repo` -> `repo-c4407a2c2ba8` (and the older archives);
    - `setup/`: `images-before.txt`, `containers-before.txt`, `volumes-before.txt`,
      `networks-before.txt` and the verify logs;
    - `runs/`: `smoke1*`, `smoke2*`, `diag-smoke2*`, `smoke3*` (with `smoke3-node.log`, the
      node's log, and `smoke3-node-excerpt.txt`), `convert/`, `resume-health.txt` (and the
      earlier one, renamed `resume-health.txt.<UTC time>`), `install-0.1.79.log`,
      `setup-images-*.log`, `IMAGE` and `campaign.log`;
    - `diag/diag-up.sh`, the command `diag-smoke2` ran, and `diag/iptprobe-in.sh`, an ad-hoc
      form of `nfprobe.sh`.
  - `/etc/modules-load.d/k3dgpu-wildct.conf` (root, 0644), written with the box owner's approval
    on 2026-10-29. It is the owner's: step 9 does not remove it.
  - Images: `cryptoandcoffee/k3d-gpu:0.1.79`, `ghcr.io/k3d-io/k3d-tools:5.9.0`, the five refs of
    `setup-images.sh`, and `python:3.13-slim@sha256:70729b46...` (the RAPL reader's image until
    2026-10-29). `cryptoandcoffee/k3d-gpu:0.1.71` was removed in step 3c.
- **Never existed.** No inference request. No measurement. `smoke1`, `smoke2`, `diag-smoke2`
  and `smoke3` are discarded and disclosed (amendments R1, R2 and R3).
- **Never touched.** No GPU, nor anything that was on the host before the campaign, apart from
  the two kernel modules and their `modules-load.d` file, which the box owner approved.
- **Newest release on 2026-10-29: k3d-gpu 0.1.79**, installed in step 3c. Step 3a picks the newest
  one again.
  - `.deb` sha256: `3c94881c20ddaca11b1422044f62e2a2b1f01e8b6bb698d4d99e1bb5763830fb`.
  - Node image: `cryptoandcoffee/k3d-gpu:0.1.79@sha256:6e27ff9817bf9398edb20f479dceb5f08273a04a01971b37628a7b2791bdfcfe`.
  - Its launcher and `share/` equal main's at `4a322ccc` and `c4407a2c`, apart from the version
    line.
- **H57wb's start gates were met on 2026-10-29** (`/` 208 GB available, MemAvailable 118 GiB),
  not a reading of the row. If they still are at step 6's row 7, the pack stops there: H57wb has
  no runner on this host.

## Ground rules (W1-W8; this pack adds none)

- **GPUs.** No GPU on wildct is used, the iGPU included. Every cluster runs with
  `K3D_GPU_VENDORS=auto,-nvidia,-intel`. No `--gpus`, no `/dev/dri`, no `/dev/nvidia*`.
  H28w's plain `k3d cluster create` does not read that variable, so `row.sh h28w` passes the two
  disables `up` would (`--disable=intel-gpu-plugin --disable=intel-gpu-dra`) with W3.
- **Production box.**
  - Never stop, start, change or remove anything this campaign did not create.
  - That includes the `feed-my-algorithm-*` containers, `know-my-neighbor-db-1`,
    `proxmox-dc-manager`, `tos-rate-calculator`, `searxng-*`, the two exited `k3d-wtm-*`
    containers, `ollama` and its stores, and the old hung `docker run` / `docker inspect`
    processes.
  - No `docker ... prune`. No `pkill`/`pgrep -f`.
- **CPUs 16-19 stay free.** The treatment runs on 0-15 (H18w's sandboxes: the E-core balloon).
  The load runs on 20-23.
- **One hold at a time.** Each hold runs through `bench/wildct/hold.sh`, which:
  - takes `~/k3dgpu-bench-wildct/live.lock`;
  - checks host health;
  - applies the load and disk gates;
  - runs the command under the RAPL reader, the sampler and the disk watchdog;
  - cleans up (after a command that ran and did not end 0, also the `h28-model` images, as the
    H28w row registers) and writes `runs/NAME/rc`.
- **Every wait has a ceiling and a failure marker.** A step that hits either stops the pack:
  record what you saw and report it. Do not retry blindly. A failed hold counts. Each row may
  repeat a failed hold once, under a new NAME.

## 0. Variables

Mark: **[c]** runs on the controller, from a checkout of this repository. **[w]** runs on wildct
as `andrew`.

```bash
# [c] WSSH: your ssh command for andrew@100.66.38.121 (by IP; through a ControlMaster about 25 s
#     per command, about 50 s for a new session). Every remote call below is wrapped in timeout.
WSSH="ssh -F /dev/null -o ControlPath=<your control path> andrew@100.66.38.121"
```

```bash
# [w] at the top of every wildct script below
W=~/k3dgpu-bench-wildct; R=$W/repo; export PATH=$W/bin:$PATH
PYIMG=python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb   # the RAPL reader's image (hold.sh's RAPL_IMAGE)
```

## 1. Health checks [w], read-only except the probe containers

Run them as one script (`timeout 600 $WSSH bash -s <<'EOF' ... EOF`). Every check must pass.

| # | Command | Pass | Fail: stop and report |
|---|---|---|---|
| H1 | `timeout 30 systemctl show -p ActiveState --value docker containerd` | `active` twice, within 30 s (this call timed out on 2026-10-21) | timeout or "Failed to get properties" |
| H2 | `timeout 30 systemctl is-system-running` | `running` or `degraded`, within 30 s | timeout, `starting`, `stopping`, `maintenance` |
| H3 | `timeout 30 docker info --format '{{.ServerVersion}} live-restore={{.LiveRestoreEnabled}} cgroup={{.CgroupDriver}}/{{.CgroupVersion}}'` | answers (expect `29.6.1 live-restore=false cgroup=systemd/2`; record it) | timeout |
| H4 | `timeout 120 docker run --rm --name k3dgpu-w-probe --privileged --network none --cpuset-cpus 20-23 "$PYIMG" true; echo "probe rc=$?"` (the RAPL reader's exact flags, `true` instead of `sleep`) | `probe rc=0` | `rc=124`: the wedge persists. Run `timeout 60 docker rm -f k3dgpu-w-probe`, then stop |
| H5 | `ps -eo pid,etime,stat,args \| awk '$4 == "runc" && $5 == "init"'` | no line older than 10 min | an old `runc init` remains. Record it; do not kill it (root-owned) |
| H6 | `df -P -BG / \| tail -1; grep MemAvailable /proc/meminfo; cat /proc/loadavg` | `/` available >= 55 GB (15 + H57w's 40) for the whole campaign, >= 18 GB for smoke, H52w and H18w; `load1` <= 12 | below 18 GB: no hold can start. Report |
| H7 | `timeout 60 k3d cluster list -o json \| python3 $R/bench/wildct/clusters.py` | empty | a `k3dgpu-w-*` cluster exists: report (step 2 does not delete clusters) |
| H8 | `flock -n $W/live.lock true && echo lock-free; ps -eo pid,args \| grep '[h]old.sh'` | `lock-free`, no `hold.sh` | a hold is still running |
| H9 | `awk '$1 == "vxlan" \|\| $1 == "br_netfilter" {print $1}' /proc/modules \| sort \| paste -sd' '; echo "modprobe=[$(cat /proc/sys/kernel/modprobe)]"` | `br_netfilter vxlan` (both loaded; record `modprobe=`) | `vxlan` missing: k3s's flannel cannot create its VXLAN device and k3s exits (`smoke2`, 2026-10-29, with `modprobe=[]`: the kernel cannot load it on demand). `br_netfilter` missing: k3s cannot set `net.bridge.bridge-nf-call-iptables`, which Service traffic between pods on one node needs. Either: report to the box owner |
| H10 | `timeout 300 bash $R/bench/wildct/nfprobe.sh "$(cat $W/runs/IMAGE)"; echo "nfprobe rc=$?"` (a throwaway container from the release node image: `--network none`, NET_ADMIN and NET_RAW only, CPUs 20-23; adds one rule per iptables extension in its list, in its own network namespace, and one ipset per set type in its list) | `NFPROBE clean`, `nfprobe rc=0` | `NFPROBE missing: ...`: k3s's iptables rules fail (`smoke3`, 2026-10-29: the network policy controller stopped on `comment` and k3s restarted every ~16 s). Report to the box owner with the modules: `comment` xt_comment, `statistic` xt_statistic, `multiport` xt_multiport, `limit` xt_limit, `NFLOG` xt_NFLOG, `physdev` xt_physdev, `pkttype` xt_pkttype, `REJECT` ipt_REJECT and ip6t_REJECT, `hash:ip` ip_set_hash_ip, `hash:net` ip_set_hash_net, `hash:ip,port` ip_set_hash_ipport, `hash:ip,port,ip` ip_set_hash_ipportip, `hash:ip,port,net` ip_set_hash_ipportnet. `nfprobe rc=2`: no verdict (docker did not answer, or no nf_tables iptables in the image); stop |

Record the whole output in `$W/runs/resume-health.txt` (move an earlier one aside first). Also
copy it back with the records. `runs/IMAGE` is step 3c's; in a campaign that has not reached 3c,
run H10 after it.

## 2. Clean up k3dgpu-w-rapl [w]

```bash
timeout 60 docker rm -f k3dgpu-w-rapl; echo "rm rc=$?"
timeout 30 docker ps -a --filter name=k3dgpu-w- --filter name=k3d-gpu-bench-load --format '{{.Names}} {{.Status}}'; echo "ps rc=$?"
```

- **Pass:** `rm rc=0`, `ps rc=0`, and the listing is empty.
- **Fail** (an rc not 0, 124 included, or the container still listed): stop. The host is not fixed.
- Keep `runs/smoke1*`. They are the disclosed record of the attempt.

## 3. Upgrade to the newest release

Rule W7: the newest k3d-gpu release at the first hold. No hold has run yet, so upgrade now.

**3a [c]. Pick the release and check it matches main.**

```bash
git fetch -q origin --tags
SHA=$(git rev-parse origin/main)
# release_wait SHA: prints V once the newest k3d-gpu release (an `aur-v*` tag; the nightly's
# `<k3s>-ndp<plugin>` releases are not k3d-gpu releases) has a launcher (minus VERSION) and share/
# equal to SHA's. Ceiling 3600 s. Failure marker: auto-release concluded failure on SHA.
release_wait() {
    local sha=$1 t0=$SECONDS v
    while :; do
        v=$(timeout 60 gh release list -R 88plug/k3d-gpu -L 30 --json tagName \
            -q '[.[] | select(.tagName | startswith("aur-v"))][0].tagName')
        if [ -n "$v" ] \
            && diff <(git show "$v:scripts/k3d-gpu" | grep -v '^VERSION=') <(git show "$sha:scripts/k3d-gpu" | grep -v '^VERSION=') > /dev/null \
            && git diff --quiet "$v" "$sha" -- share; then
            echo "${v#aur-v}"; return 0
        fi
        timeout 60 gh run list -R 88plug/k3d-gpu --workflow auto-release.yml --commit "$sha" --json conclusion -q '.[].conclusion' \
            | grep -qx failure && { echo "auto-release failed on $sha" >&2; return 2; }
        [ $(( SECONDS - t0 )) -lt 3600 ] || { echo "no release matching $sha after 3600 s" >&2; return 4; }
        sleep 60; git fetch -q origin --tags
    done
}
V=$(release_wait "$SHA") || { V=; echo "STOP: no release matching $SHA"; }   # 0.1.75 on 2026-10-29
# The node image is pushed after the release: wait for its digest, ceiling 1800 s (15 x 120 s).
DIGEST=
for _ in $(seq 15); do
    [ -n "$V" ] || break
    DIGEST=$(timeout 60 docker buildx imagetools inspect "cryptoandcoffee/k3d-gpu:$V" | awk '/^Digest:/ {print $2; exit}')
    [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] && break
    DIGEST=; sleep 60
done
echo "V=$V DIGEST=$DIGEST"
```

- **Pass:** `V` is set and `DIGEST` is `sha256:` and 64 hex digits.
- **Fail:** either is empty. Stop.

**3b [c]. Ship main as an archive.** The archive is never edited on wildct. Its `COMMIT` file
names the commit.

```bash
D=repo-${SHA:0:12}; T=$(mktemp -d)
mkdir -p "$T/$D" && echo "$SHA" > "$T/$D/COMMIT"
git archive --format=tar --prefix="$D/" "$SHA" > "$T/$D.tar" && tar -rf "$T/$D.tar" -C "$T" "$D/COMMIT"
timeout 600 $WSSH "cd ~/k3dgpu-bench-wildct && rm -rf $D && tar -xf - && ln -sfn $D repo && cat repo/COMMIT" < "$T/$D.tar"
rm -rf "$T"
```

- **Pass:** it prints `$SHA`.

**3c [w]. Install, pull the node image, check the launcher.** Pass `V` and `DIGEST` from 3a.

```bash
timeout 900 bash $R/bench/wildct/install.sh "$V" > $W/runs/install-$V.log 2>&1; echo "install rc=$?"
grep -E '^ok |^checksum|^k3d-gpu ' $W/runs/install-$V.log
IMAGE=cryptoandcoffee/k3d-gpu:$V@$DIGEST
timeout 900 docker pull -q "$IMAGE"; echo "pull rc=$?"
# H52w's plain `up` runs the release's default image by tag: pull the tag and check it is the digest.
timeout 900 docker pull -q "cryptoandcoffee/k3d-gpu:$V" > /dev/null; echo "pull tag rc=$?"
timeout 30 docker image inspect -f '{{join .RepoDigests " "}}' "cryptoandcoffee/k3d-gpu:$V" | grep -qF "@$DIGEST" && echo TAG-DIGEST-MATCH || echo TAG-DIGEST-DIFF
echo "$IMAGE" > $W/runs/IMAGE                     # every later step reads it
diff <(grep -v '^VERSION=' $R/scripts/k3d-gpu) <(grep -v '^VERSION=' $W/k3d-gpu/usr/bin/k3d-gpu) > /dev/null && echo LAUNCHER-MATCH || echo LAUNCHER-DIFF
# The package adds console/, bench/ and images.lock; every file of share/ must be installed, unchanged.
diff -r $R/share $W/k3d-gpu/usr/share/k3d-gpu | grep -v "^Only in $W/k3d-gpu/usr/share/k3d-gpu" | head -3
```

- **Pass, all of:**
  - `install rc=0`;
  - four `ok` lines (k3d, helm.tgz, kubectl, the `.deb`);
  - `k3d-gpu $V`;
  - `pull rc=0` and `pull tag rc=0`;
  - `TAG-DIGEST-MATCH`;
  - `LAUNCHER-MATCH`;
  - an empty share diff.
- **Fail:** `checksum mismatch`, any rc not 0 (124 = timeout), `TAG-DIGEST-DIFF` or
  `LAUNCHER-DIFF`. Stop.
  `hold.sh` would refuse every hold anyway (exit 7).

Remove the old node images this campaign pulled. Only remove an image whose ID is not in
`setup/images-before.txt`. The file's format was not recorded, so an ID counts as listed when its
first 12 hex digits appear anywhere in it, and nothing is removed when the file holds no ID at all:

```bash
if grep -qE '[0-9a-f]{12}' $W/setup/images-before.txt; then
    for t in 0.1.71 0.1.65; do
        id=$(timeout 30 docker image inspect -f '{{.Id}}' cryptoandcoffee/k3d-gpu:$t 2>/dev/null) || continue
        s=${id#sha256:}
        grep -qF "${s:0:12}" $W/setup/images-before.txt && { echo "keep $t (pre-existing)"; continue; }
        timeout 300 docker rmi cryptoandcoffee/k3d-gpu:$t > /dev/null; echo "rmi $t rc=$?"
    done
else
    echo "setup/images-before.txt holds no image ID: remove nothing, report it"
fi
```

## 4. Setup left before the first hold [w]

**4a. Models unchanged since they were verified.** They were verified on 2026-10-21. Nothing
under them may be newer than the verify logs:

```bash
find $W/models $W/h57/models -type f -newer $W/setup/verify-h57.log | head -3   # pass: empty
grep -E '^VERIFY' $W/setup/*.log                                                # pass: VERIFY ok per log
```

- Any file listed: re-run the matching `verify_models.py` line of `bench/wildct/setup-models.sh`
  under `timeout 3600 nice -n 10`. It reads every byte, so run it outside any hold.

**4b. H57w's k3d shim and weights list.**

```bash
install -D -m 0755 $R/bench/h57d/k3d-shim.sh $W/h57/bin/k3d
grep -hE '^ok [0-9a-f]{64} ' $W/setup/verify-h57.log | awk '{print $2 "  " $4}' > $W/h57/weights.sha256
wc -l < $W/h57/weights.sha256     # pass: > 0
```

**4c. Drafter GGUF and Od's graph.pbtxt.** These come from `convert.sh`, under the host lock and
outside `hold.sh`. The cap is 3600 s. It uses the same pid/rc files as a hold, so `wait_hold.sh`
can follow it.

```bash
mkdir -p $W/runs/convert && rm -f $W/runs/convert/rc $W/runs/convert/pid   # a re-run never reads the last rc
setsid nohup bash -c 'echo $$ > '"$W"'/runs/convert/pid; bash '"$R"'/bench/wildct/row.sh h57w-convert > '"$W"'/runs/convert/log 2>&1; echo $? > '"$W"'/runs/convert/rc' > /dev/null 2>&1 < /dev/null &
```

- **Wait:** `hold_wait convert 0` (step 6). Its ceiling is 7200 s: the 3600 s cap (`row.sh`
  runs `convert.sh` under `timeout -k 30 3600`, its lock wait included) plus a free lock.
- **Pass, all of:**
  - rc 0;
  - the log has `drafter GGUF built` (or the GGUF already existed) and `done`;
  - `manifest.json` under `h57/models/k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf/` lists the
    Q4_0 file. Record its sha256. On the i9 it was `8eb21d5b...`, 321,145,696 bytes.
  - Record `graph.pbtxt`'s sha256. On the i9 it was `c57ecf35...`.
- **Fail** (rc not 0, or 124 at the cap): the `docker run --rm` it started may still be
  running. Check
  `timeout 30 docker ps --filter ancestor=ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375acf22d45551b0650af7e96361ad1a3abc65ad2bde215f23488da2711e; echo "ps rc=$?"`
  and the OVMS image (`openvino/model_server@sha256:a928903f...`); a `ps rc` that is not 0 means
  docker did not answer, not that none is running. Stop only those containers.
  Then stop the pack.

## 5. Wiring smoke (`smoke4`)

This is W1's smoke: an `up` with the opt-out and W3, the pod wait, the GPU scan, then `down`.
It sends no inference request. It is discarded and disclosed, and is not a hold of any row.
`smoke2` (2026-10-29) failed at `up`: neither module of step 1's H9 was loaded. `smoke3`
(2026-10-29) printed every pass marker on a cluster whose k3s restarted every ~16 s (H10's
`comment`); the pod wait now needs every node Ready and every container ready
(`bench/wildct/ready.sh`, amendment R3). The next smoke is `smoke4`. A `GPU-SCAN` line is a
verdict only on a non-empty pod list: `gpu_scan.py` exits 1 with no `GPU-SCAN` line on an empty
one (amendment R2).

**[w] launch** (the same function serves every hold below):

```bash
# launch NAME CAP NEED ROW [ARGS]: one hold, detached from the ssh session. row.sh runs every check
# of the row first (IMAGE, the row's arguments, H57w's mode and arms, the k3d shim): a usage error
# inside the hold would be a failed hold, which counts.
launch() {
    [ ! -e "$W/runs/$1" ] || { echo "runs/$1 exists: use a new NAME"; return 1; }
    IMAGE=$(cat $W/runs/IMAGE) WILDCT_HOLD=$1 WILDCT_ROW_CHECK=1 bash "$R/bench/wildct/row.sh" "${@:4}" \
        || { echo "row.sh ${*:4}: not a row it runs; nothing launched"; return 1; }
    IMAGE=$(cat $W/runs/IMAGE) setsid nohup bash "$R/bench/wildct/hold.sh" "$1" "$2" "$3" \
        bash "$R/bench/wildct/row.sh" "${@:4}" > "$W/runs/$1.out" 2>&1 < /dev/null &
    echo "launched $1"
}
launch smoke4 900 3 smoke
```

**[c] wait:** `hold_wait smoke4 900` (step 6).

- **Pass, in `runs/smoke4/out/rc.txt`, all of:**
  - `up rc=0`;
  - `pods ready: yes ...`;
  - `GPU-SCAN clean`;
  - `down rc=0`.
- **Pass, in `up.log`:** nvidia and intel are named as opted out.
- **Pass, in `node-container.txt`:** `gpus=[]` and `restarts=0`.
- **Pass, in `pods.txt`:** every pod `n/n` and Running, or Completed.
- **Fail:** any of these, or rc not 0. Stop and report. Do not start H52w.

## 6. Rows, one hold at a time

**[c] hold_wait.** Every wait goes through this function. Its ceiling is CAP + 7200 s:

- up to 3600 s of `load1` gate;
- up to about 3300 s of bounded steps in `hold.sh`, each bound plus its SIGKILL grace (two health
  checks, the cluster list and leftover delete, the RAPL reader, stopping the command's leftover
  processes, the cleanup), and `-k 30` on CMD;
- about 300 s of slack (polls, ssh).

The lock wait is not included: the lock is free (H8) and holds run one at a time.

```bash
# hold_wait NAME CAP: 0 only when the hold's rc is 0. Failure markers: wait_hold.sh's exits
# 1 (rc not 0), 2 (hold.sh gone without rc), 3 (never started), 4 (ceiling); 3 ssh failures in a row.
hold_wait() {
    local name=$1 ceil=$(( $2 + 7200 )) t0=$SECONDS st fails=0 left
    while :; do
        left=$(( ceil - (SECONDS - t0) ))
        [ "$left" -gt 0 ] || { echo "$name: no rc within $ceil s"; return 4; }
        timeout $(( left + 120 )) $WSSH "bash ~/k3dgpu-bench-wildct/repo/bench/wildct/wait_hold.sh $name $left" < /dev/null; st=$?
        case $st in
            0|1|2|3|4) return "$st" ;;
            *) fails=$(( fails + 1 )); [ "$fails" -lt 3 ] || { echo "$name: ssh failed 3 times (last rc $st)"; return 5; }; sleep 60 ;;
        esac
    done
}
```

**[w] after every hold**, before the next one:

```bash
N=<hold NAME>
cat $W/runs/$N/rc; ls $W/runs/$N/{flagged,disk-abort,cleanup-incomplete,unhealthy} 2>/dev/null; tail -3 $W/runs/campaign.log
ls $W/h57/results/H57w/hold*/{no-hold,flagged,disk-abort} 2>/dev/null                 # H57w holds: the inner hold's markers
timeout 60 k3d cluster list -o json | python3 $R/bench/wildct/clusters.py; echo "list rc=$?"   # pass: empty, list rc=0
timeout 30 docker ps -a --filter name=k3dgpu-w- --filter name=k3d-gpu-bench-load --format '{{.Names}}'; echo "ps rc=$?"  # pass: empty, ps rc=0 (k3dgpu-w-*, k3d-k3dgpu-w-* and load.py's k3d-gpu-bench-load-*)
timeout 30 python3 $R/bench/wildct/strays.py $N; echo "strays rc=$?"                 # pass: empty, strays rc=0 (no process left from the hold)
df -P -BG / | tail -1
```

- **Stop** when `cleanup-incomplete` or `unhealthy` exists, `list rc`, `ps rc` or `strays rc` is
  not 0, a `k3dgpu-w-*` cluster or container or a process of the hold is left, or `/` has less
  than 18 GB available.
- A `flagged` hold is kept and reported as flagged (W1).

**[c] copy the records back** after every hold:

```bash
N=<hold NAME>; D=bench/wildct/results/$(date -u +%F)
mkdir -p "$D"
timeout 900 $WSSH "tar -C ~/k3dgpu-bench-wildct/runs --exclude='kubeconfig*' -cf - $N $N.out campaign.log" \
    | tar -C "$D" -xf -
```

The kubeconfigs stay behind: they hold cluster credentials. H57w's own records live under
`$W/h57/results/H57w/`. Copy that directory the same way after each H57w hold.

**The holds, in order** (each is `launch NAME CAP NEED ROW...` on [w], then `hold_wait NAME CAP`
on [c]):

| # | NAME | CAP | NEED | Command | Pass marker (besides rc 0 and the after-hold checks) |
|---|---|---|---|---|---|
| 1 | `H52w` | 1800 | 3 | `launch H52w 1800 3 h52w` | `out/rc.txt` has every line: `detect`, `doctor`, `up`, `pods ready`, `GPU-SCAN`, `test`, `down`, `inventory`. `h52w.sh` ends 0 whenever it ran to its end, so read the lines too: stop and report if `up rc` or `down rc` is not 0 or `pods ready` is not `yes`. Scored later by H52w's table |
| 2 | `H18w` | 2100 | 3 | `launch H18w 2100 3 h18w` | `out/run-1..3.txt` each has `sandbox_up_rc=0` and a `pip_rc=` line |
| 3 | `H25w` | 1500 | 8 | `launch H25w 1500 8 h25w` | `out/typical/score.json` exists |
| 4 | `H34w` | 1500 | 8 | `launch H34w 1500 8 h34w` | `out/verdict.json` written by `h34.py` |
| 5 | `H28w-prep` | 900 | 35 | `launch H28w-prep 900 35 h28w-prep` | `docker images 'h28-model'` lists r0-r6. Before it, record `timeout 60 docker system df` (build cache) |
| 6 | `H28w` | 1500 | 35 | `launch H28w 1500 35 h28w` | `out/h28-*.csv` has 6 `round,...,scored,typical` lines (report any `VOID`); `docker images 'h28-model'` empty afterwards; record `docker system df` again |
| 7 | H57wb reading | none | none | [w] `bash $R/bench/wildct/row.sh h57wb` (not a hold) | `runs/H57wb/reading.txt`. Expected: INCONCLUSIVE (not run). If both gates are met, stop: H57wb has no runner on this host |
| 8 | `H57w-1` | 4600 | 40 | `launch H57w-1 4600 40 h57w 1 perf` | `$W/h57/results/H57w/hold1/{Od,Ld,Ld-mtp}/status.json` say `measured` |
| 9 | `H57w-2` | 4600 | 40 | `launch H57w-2 4600 40 h57w 2 perf` | as hold 1 |
| 10 | `H57w-3` | 4600 | 40 | `launch H57w-3 4600 40 h57w 3 perf` | as hold 1 |
| 11 | `H57w-4` | 4600 | 40 | `launch H57w-4 4600 40 h57w 4 quality` | `quality.json` in each arm |
| (12) | `H57w-5` | 4600 | 40 | only if hold k (1-4) failed: first `mkdir -p $W/h57/results/H57w/hold5 && echo k > $W/h57/results/H57w/hold5/repeats`, then `launch H57w-5 4600 40 h57w 5 <k's mode> <k's arms in k's order>` | as hold k |

Notes on the holds:

- **H52w.** `row.sh h52w` runs `bench/wildct/h52w.sh $W/runs/H52w/out`, the registered
  procedure. `h52w.sh` sets its own cluster (`k3dgpu-w-h52`), opt-out and W3, and its `up` runs
  the release's default node image (the tag checked in 3c).
- **Arm orders.** The registration fixes them: `bench/h57d/h57d.py arms` gives hold 1 Od Ld
  Ld-mtp, hold 2 Ld Ld-mtp Od, hold 3 Ld-mtp Od Ld, hold 4 Ld Od Ld-mtp.
- **H28w's model images.** `hold.sh` deletes every `h28-model` image after a command that ran and
  did not end 0, as the H28w row registers. A failed `H28w-prep` or `H28w` therefore leaves none,
  so a repeat of `H28w` needs `H28w-prep` again first (a hold, counted toward the 12).
- **H57w's inner gate.** H57d's own `hold.sh` re-checks `load1` <= 12, `/` >= 15 GB and
  MemAvailable >= 20 GiB. A busy inner wait eats the outer 4600 s cap. A hold the cap cuts
  counts as failed.
- **Scoring is not part of this pack.** It happens on [c] from the copied records: H52w by its
  table; H18w, H25w, H34w and H28w by their original scorers;
  `python3 bench/h57d/h57d.py score <records>/H57w` for H57wa and H57wm.

**What a hold's rc means:**

| rc | Meaning |
|---|---|
| 0 | the command ended 0 (H52w: read `out/rc.txt` as its table row says) |
| 4 | lock not taken in 21600 s |
| 5 | disk below 15 + NEED. If the need never fits before the campaign ends, the row is INCONCLUSIVE (not run). For H57w, also the inner hold's own gates (`/` < 15 GB or MemAvailable < 20 GiB; its `no-hold` file) |
| 6 | RAPL reader did not start or gave no reading (`campaign.log` has docker's rc) |
| 7 | launcher differs from the installed release: repeat step 3 |
| 8 | host not healthy (`unhealthy` names it: systemd, `docker info`, `k3d cluster list`, or a leftover cluster delete): back to step 1 and the box owner |
| 124 / 137 / 143 | cap reached / cap reached and SIGKILL needed / signal: a failed hold, which may repeat once |
| other | the command's own exit (`row.sh` 2: a usage error or a bad IMAGE): a failed hold, which may repeat once |

## 7. Gates and caps

| Gate or cap | Value | Where |
|---|---|---|
| Lock | `flock -w 21600 ~/k3dgpu-bench-wildct/live.lock`; H28w and H57w take `inner.lock` inside it | W1 |
| Host health | systemd answers `ActiveState=active` for docker, and `docker info` answers, each within 120 s, after the lock and again just before the hold starts; then `k3d cluster list` and any leftover cluster delete answer; else exit 8 | R1, `hold.sh` |
| Hold start, load | `load1` <= 12, 2-min polls for at most 60 min, then the hold runs flagged | W1 |
| Hold start, disk | `/` >= 15 + NEED GB. NEED: H52w 3, H18w 3, H25w 8, H34w 8, H28w 35, H57w 40 | W6 |
| Disk watchdog | stops CMD when `/` < 15 GB | W6 |
| Launcher | equals the installed release's, apart from `VERSION=`; else exit 7 | W7 |
| RAPL | `k3dgpu-w-rapl` starts and reads `energy_uj` within 120 s; else exit 6 | W4, R1 |
| Command cap | `timeout -k 30 CAP` | W1 |
| Bounded calls | docker and `k3d cluster list` 120 s; cluster deletes 600 s; SIGKILL 10 s after either. A signal ends the lock and load waits at once; during cleanup further signals are ignored | R1 |
| CPUs | treatment 0-15 (H18w sandboxes: E-core balloon); load 20-23; 16-19 never in a campaign cpuset | W2 |
| H57w inside | `timeout -k 30 4500`; arms start only with 900 s left (quality: 600 s); `/` >= 15 GB; MemAvailable >= 20 GiB | H57d R1 |
| H57wb | runs only with `/` >= 85 GB and MemAvailable >= 100 GiB; otherwise INCONCLUSIVE (not run) | H57wb row |
| Hold count | at most 12 row holds (smoke not counted). Each row may repeat a failed hold once. H57wa and H57wm share at most 5 | W order, R1 |
| GPUs | `K3D_GPU_VENDORS=auto,-nvidia,-intel` on every cluster; nothing opens a GPU device | W8 |

## 8. Per-row wall time

The cap is the registered limit on the hold's command. "Recorded" is the same protocol's
duration on the host it first ran on, from its run section in EXPERIMENTS.md. wildct has the
same 16 P-core CPUs as the i9, 8 E-cores rather than 16, and a 35 W PL1.

| Hold | CAP (s) | Inner limit | Recorded on the original host | hold_wait ceiling (s) |
|---|---|---|---|---|
| smoke4 | 900 | `up` 600 s, pod wait 60 polls every 5 s (each poll at most 70 s: two reads of 30 s, each with a 5 s SIGKILL grace), `down` 600 s | H52 (Xeon) `up`: 38 s | 8100 |
| H52w | 1800 | `up` 600 s, pod wait as smoke4, `test` 900 s, `down` 600 s | H52 (Xeon) `up`: 38 s | 9000 |
| H18w | 2100 | 3 runs | H18 (i9) runs 1-3: 230 s | 9300 |
| H25w | 1500 | `HOLD=1440`, one level | H25 run 4 (i9): 608 s; H25x (Xeon): 329 s | 8700 |
| H34w | 1500 | `H34_LEVEL_S` 1200, one level | H34x (Xeon): 549 s | 8700 |
| H28w-prep | 900 | 7 images of 1 GiB | not timed (the runner's comment: about 2 min for 13 images) | 8100 |
| H28w | 1500 | `HOLD=1440`, rounds stop at 13 min | H28 A2 (i9): 977 s | 8700 |
| convert (not a hold) | 3600 | | not recorded | 7200 |
| H57w-1..3 (perf) | 4600 | 4500, arms with 900 s left | H57d (i9) holds 1-3: 4215, 3722, 2290 s | 11800 |
| H57w-4 (quality) | 4600 | 4500, arms with 600 s left | H57d (i9) hold 4: 2441 s | 11800 |

## 9. End of the campaign [w], after every record is copied back and checked

Everything the campaign created is deleted (W6). Nothing else is touched.

- **Clusters and containers:** none left (after-hold checks).
- **Volumes and networks:** compare the current lists against `setup/volumes-before.txt` and
  `setup/networks-before.txt`. Remove only `k3d-k3dgpu-w-*` names.
- **Images:** remove an image only when its ID is not in `setup/images-before.txt` (its first 12
  hex digits, as in step 3c; nothing when the file holds no ID) and it is one of the campaign's
  refs:
  - `cryptoandcoffee/k3d-gpu:$V`;
  - `ghcr.io/k3d-io/k3d-tools:5.9.0` and `ghcr.io/k3d-io/k3d-proxy:5.9.0`;
  - the five refs of `setup-images.sh`;
  - `python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f`,
    the RAPL reader's image until 2026-10-29, pulled by the campaign's setup on 2026-10-20;
  - `h28-model:*`.
  - Use `timeout 300 docker rmi`, one image at a time.
  - The OVMS image was pulled seconds before the snapshot. If its ID is in the list, it cannot
    be told from a pre-existing one: leave it and report it.
- **Build cache from H28w-prep's `docker build`:** compare `docker system df` against the
  pre-prep reading. Report the growth to the box owner, with `docker buildx du --verbose`'s
  record IDs. Remove nothing without their OK: no `prune`.
- **Kernel modules:** the loaded modules and `/etc/modules-load.d/k3dgpu-wildct.conf` stay: they
  are the box owner's (approved 2026-10-29). Name them in the end-of-campaign report, so the owner
  can remove the file.
- **Files:** `rm -rf ~/k3dgpu-bench-wildct`, once the records are in the repository and the
  copied `rc` files and logs have been compared.
- **Last check:** `df -P -BG /` and `timeout 30 docker ps -a --filter name=k3dgpu-w- --filter name=k3d-gpu-bench-load; echo "ps rc=$?"`.
  Report both (a `ps rc` that is not 0 means docker did not answer, not that nothing is left).

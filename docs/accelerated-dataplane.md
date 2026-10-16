# Accelerated agent data plane: DSA, IAA and QAT

Can an agent platform's memory and retrieval tier use the Xeon accelerators
**today**? This covers Redis or Valkey, PostgreSQL with pgvector, the RAG
pipeline, LiteLLM's cache and the TLS edge. The accelerators are DSA (memory
moves), IAA (deflate and analytics) and QAT (crypto and compression). This page
also shows how a k3d-gpu cluster would wire each one.

This is a design and feasibility study, written 2026-10-16. **Nothing here has
run on DSA, IAA or QAT hardware.** The k3d-gpu development host is an
i9-13900T and has none of the three. Each claim is labelled either
**proven elsewhere** (with a citation) or **untested hypothesis**. The probes in
[Probes for a Sapphire Rapids host](#probes-for-a-sapphire-rapids-host) turn
the hypotheses into results.

Example manifests live in [`examples/dataplane/`](../examples/dataplane). They
pass `kubeconform -strict` against Kubernetes 1.34.1. The Envoy configuration
also passes `envoy --mode validate` with `envoyproxy/envoy:contrib-v1.40.0`. See
[Static validation](#static-validation).

## Verdict

| Data-plane piece | DSA | IAA | QAT | Usable today? |
|---|---|---|---|---|
| **Redis / Valkey** (LiteLLM cache) | DTO via `LD_PRELOAD` | none (no compression in Redis) | TLS through an OpenSSL provider | **Hypothesis.** No published Redis+DTO result found as of 2026-10-16. |
| **PostgreSQL + pgvector** | blocked in the server process | not in the server process. `pg_dump` gzip via zlib-accel works; host zswap works | same as IAA | **Backups: proven elsewhere.** Server: no hook. |
| **RAG pipeline** (ingest, chunk, embed) | DTO for large copies | zlib-accel for gzip-heavy steps | zlib-accel / QATzip | **Hypothesis.** No vector store using QPL found as of 2026-10-16. |
| **LiteLLM** | indirect (its Redis) | none | indirect (Envoy/Istio edge) | Needs no change, and gains nothing on its own. |
| **TLS edge and response compression** | n/a | n/a | Envoy QAT key provider and qatzip; Istio gateway; nginx/HAProxy via QAT_Engine | **Proven elsewhere** for HAProxy/nginx. Envoy/Istio are alpha but buildable today. |
| **Memory pressure** (any pod) | n/a | host zswap on `deflate-iaa` | host zswap on `qat_deflate` / `qat_zstd` | **Proven elsewhere** (kernel). Pods need `LimitedSwap`. |

In short: the accelerators reach this tier **from outside the database and
cache engines**. That means interposed libraries (DTO, zlib-accel), the
proxies (Envoy, Istio, HAProxy, nginx) and the kernel (zswap). None of
PostgreSQL, pgvector, Redis, Valkey or LiteLLM has a hook that calls QPL,
QATzip or DML directly. ClickHouse had built-in codecs for these devices
(`DEFLATE_QPL`, `ZSTD_QAT`) and **removed them in 26.1**. Outside the
engines above, RocksDB has an IAA compressor plugin (`intel/iaa-plugin-rocksdb`).

## The container contract

Every workload that touches these devices from user space needs the
following. All of it is checked against source; none of it has been run.

| | DSA / IAA (idxd user work queues) | QAT (SR-IOV VF via vfio-pci) |
|---|---|---|
| Resource | `dsa.intel.com/wq-user-shared` / `-dedicated`, `iaa.intel.com/wq-user-shared` / `-dedicated` | `qat.intel.com/<services>`: `cy` (sym+asym), `dc`, `sym`, `asym`, `asym-dc`, … |
| Device nodes the plugin injects | `/dev/dsa/wqX.Y` or `/dev/iax/wqX.Y`, plus `/dev/char/<maj>:<min>` | `/dev/vfio/<group>` and `/dev/vfio/vfio`; env `QAT<n>=<VF BDF>` |
| Capability | **`CAP_SYS_RAWIO`.** idxd refuses to `mmap` a WQ portal without it on every supported DSA/IAA, because `user_submission_safe = false` for all device IDs (INTEL-SA-01084) | **`CAP_IPC_LOCK`.** vfio type1 skips the `RLIMIT_MEMLOCK` check for the pinned DMA buffers only when the task has it |
| memlock | not needed (SVA, nothing pinned) | without `CAP_IPC_LOCK`, qatlib needs **≥ 16 MiB per VF plus the app's buffers** (Intel's HAProxy image uses 500 MB). Pods inherit dockerd's limit, which is **8 MiB** on this host (`LimitMEMLOCK=8388608`) |
| User | root. The node is root-owned 0600, and added capabilities are not effective for a non-root process | root, for the same reasons (or k3s `--nonroot-devices` plus a raised memlock) |
| Host prerequisites | WQs configured with `accel-config`. **Shared** WQs (which DTO requires) need PASID: `/sys/bus/dsa/devices/dsaN/pasid_enabled` = 1, which means IOMMU scalable mode | QAT PF driver bound, VFs created (`sriov_numvfs`), `vfio-pci` loaded, IOMMU on. Services per PF via `cfg_services` |
| Kernel | DSA user WQs: idxd. QPL needs ≥ 5.18 for IAA 1.0 and ≥ 6.3 for IAA 2.0 | in-tree `qat_4xxx` / `qat_420xx` / `qat_6xxx` |

Notes that change how the manifests look:

- **The DSA/IAA plugin sets no environment variable** naming the allocated
  queue (`pkg/idxd/plugin.go`). A workload has to find it under
  `/dev/dsa` or `/dev/iax`. [`dto-launch.sh`](../examples/dataplane/images/dto-launch.sh)
  does this for DTO.
- **One shared WQ goes to one container by default.** The plugin's
  `-shared-dev-num` flag defaults to 1, and k3d-gpu's
  `share/intel-dsa-plugin.yml` keeps the default. Several DTO pods on one SWQ
  need that flag raised. See [Wiring proposals](#wiring-proposals-for-k3d-gpu).
- **QAT resource names follow `cfg_services` order.** The plugin replaces `;`
  with `-` and maps only `sym;asym`/`asym;sym` to `cy`. So a PF set to
  `asym;dc` advertises `qat.intel.com/asym-dc`, and one set to `dc;asym`
  advertises `qat.intel.com/dc-asym`.
- **Run qatlib in standalone mode inside containers**, with no `qatmgr`. This
  is Intel's preferred mode for containers. Set `QAT_POLICY` to 1 or higher; 0
  is invalid in standalone mode. Intel's QAT_Engine images run with
  `--cap-add=IPC_LOCK`, `--ulimit memlock=524288000`, `QAT_POLICY=1` and
  `seccomp=unconfined`. k3s pods default to `Unconfined` unless the kubelet's
  `seccompDefault` is on.
- **Non-root images drop privileges before the process starts.** The official
  `redis` entrypoint switches to the `redis` user via `gosu`, and the Envoy
  entrypoint switches to uid 101 unless `ENVOY_UID=0`. PostgreSQL refuses to
  run as root at all. All of these lose `CAP_SYS_RAWIO` and `CAP_IPC_LOCK`.

## Components

### Redis / Valkey with DTO (DSA)

| | |
|---|---|
| Library | [intel/DTO](https://github.com/intel/DTO). The last tag is v1.1 (2024-07). `main` @ `fbf41a6` (2026-09-09) adds CMake and optional accel-config/libnuma |
| Status | Intel library, no "GA" label. It intercepts `memcpy`, `memmove`, `memset` and `memcmp` |
| Engines | Redis 8.10.2 (Debian trixie image), Valkey 9.1.2. Neither has DSA code |

How DTO works:

- It only submits to **shared** WQs (`dto.c` rejects any WQ whose `mode` is
  not `shared`).
- It offloads calls of **`DTO_MIN_BYTES` or more**. Smaller calls go to the
  CPU. The code default is 65536 (`dto.c`: `DTO_DEFAULT_MIN_SIZE`); the
  README says 16384.
- Auto-tuning (`DTO_AUTO_ADJUST_KNOBS`) is **on by default**. Under load it
  moves the threshold between 6144 and 65536 bytes (`MIN_DSA_MIN_SIZE`,
  `MAX_DSA_MIN_SIZE`), so `DTO_MIN_BYTES` is only a starting point unless
  auto-tuning is set to 0. The manifest sets it to 0.
- It waits by busy-polling by default (`dto.c`: `wait_method = WAIT_BUSYPOLL`).
  The README contradicts itself: its env list says "yield", its prose says
  "busypoll". The code is authoritative.
- Without accel-config it needs `DTO_WQ_LIST`. Its CMake adds `-march=native`,
  so **build the image on the target CPU**.

Evidence:

- **Proven elsewhere:**
  - DTO speeds up CacheLib's CacheBench. The DSA characterization paper
    (arXiv 2305.02480) offloaded only copies ≥ 8 KB with DTO. Those were
    4.8 % of `memcpy` calls but 96.4 % of the bytes.
  - Redis with **custom** DSA code reached up to 23 % faster RDB saves
    (LightDSA). The same paper notes DTO lacks DSA-specific tuning.
- **Untested hypothesis:** `LD_PRELOAD`ing DTO into Redis cuts the CPU cost
  of large GET/SET values. LiteLLM stores each cached response as a single
  `json.dumps()` string (`litellm/caching/redis_cache.py`). An embedding of
  1536 floats serialises to tens of KB, above the manifest's fixed 16 KiB
  threshold. Chat completions mostly fall below 16 KiB and stay on the CPU.
  With auto-tuning left on, a loaded DSA can push the threshold to 64 KiB
  and keep those embeddings on the CPU too.
- **Risk:** Redis runs its event loop on one thread. A synchronous DSA wait
  still blocks that thread, so the gain is only real if the DSA-plus-CPU
  split finishes a copy sooner than the CPU alone. The A/B probe measures
  exactly that.

Pod spec (full manifest: [`redis-dto.yaml`](../examples/dataplane/redis-dto.yaml),
image: [`redis-dto.Dockerfile`](../examples/dataplane/images/redis-dto.Dockerfile)):

```yaml
containers:
- name: redis
  image: k3d-gpu/redis-dto:8.10.2-dto-fbf41a6   # ENTRYPOINT dto-launch sets DTO_WQ_LIST + LD_PRELOAD
  args: [redis-server, --requirepass, $(REDIS_PASSWORD)]
  env:
  - {name: DTO_MIN_BYTES, value: "16384"}
  - {name: DTO_AUTO_ADJUST_KNOBS, value: "0"}   # keep the threshold fixed (on by default)
  - {name: DTO_USESTDC_CALLS, value: "0"}       # "1" = same binary on CPU only (A/B baseline)
  resources:
    limits: {dsa.intel.com/wq-user-shared: 1}
  securityContext:
    runAsUser: 0
    capabilities: {drop: [ALL], add: [SYS_RAWIO]}
```

**PostgreSQL with DTO is blocked as shipped.** The postmaster refuses root,
and a non-root process has no effective `CAP_SYS_RAWIO`. The only route
would be a file capability on the `postgres` binary (`setcap
cap_sys_rawio+ep`) with the pod's bounding set allowing it, plus device
ownership through `--nonroot-devices`. That is an untested hypothesis, and
the payoff is doubtful. PostgreSQL's hot copies are 8 KB pages, below
DTO's default threshold.

### IAA in the kernel: zswap (and why not zram)

| | |
|---|---|
| Driver | `iaa_crypto`, mainline since **Linux 6.8**. It registers `deflate` with driver name `deflate-iaa` |
| Status | GA in mainline. zswap allocates its compressor through the crypto acomp API (`mm/zswap.c`) |
| zram | **Not possible since Linux 6.12.** zram moved to its own backends (lzo, lz4, zstd, deflate, 842) and no longer goes through the crypto API |
| QAT equivalent | the in-tree QAT driver registers `deflate` (`qat_deflate`) and, since **Linux 7.1**, `zstd` (`qat_zstd`), both at priority 4001 |

- **Proven elsewhere:** the kernel's IAA documentation
  (`Documentation/driver-api/crypto/iaa/iaa-crypto.rst`) uses zswap as the
  reference consumer: `echo deflate-iaa > /sys/module/zswap/parameters/compressor`.
- **Configuration:** the IAA WQs for this must be **kernel**-type WQs bound
  to the `crypto` driver. They cannot also be the user WQs that
  `intel-iaa-plugin` hands to pods. Partition the device, for example
  `iax1/wq1.0` kernel and `iax3/wq3.0` user.
- **Not merged:** the zswap batching series that makes IAA's parallelism
  pay off. Mainline `mm/zswap.c` at v7.3-rc7 has no batching path.
- **Untested hypothesis on k3d:** pods get any of this only if the kubelet
  lets them swap. NodeSwap is GA in Kubernetes 1.34, but the default
  `swapBehavior` is `NoSwap`. k3s already sets `failSwapOn: false` and reads
  kubelet drop-ins from `/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/`.
  [`kubelet-limitedswap.conf`](../examples/dataplane/kubelet-limitedswap.conf)
  turns on `LimitedSwap`. That covers Burstable pods only, such as the
  pgvector StatefulSet. Whether the kubelet inside a k3d node container
  enforces swap limits on its pods' cgroups has not been tested.
- **Hybrid note:** the kernel's idxd table includes **Panther Lake and
  Wildcat Lake IAA** (`IAA_PTL`, `IAA_WCL`). zswap-on-IAA is therefore the one
  item on this page that also applies to a hybrid client laptop running
  k3d-gpu.

There is no pod spec. The pod only needs to be Burstable on a node with
`LimitedSwap`.

### IAA in user space: QPL and zlib-accel

| | |
|---|---|
| QPL | [intel/qpl](https://github.com/intel/qpl) **v1.9.0** (2025-12-10). Not packaged in Debian trixie or Ubuntu 26.04. It `dlopen`s `libaccel-config.so.1` at run time. Since accel-config 3.2, WQs belong to the `dsadev` group |
| zlib-accel | [intel/zlib-accel](https://github.com/intel/zlib-accel) **v1.1.0** (2026-10-01). A zlib shim for `LD_PRELOAD` with QAT (via QATzip), IAA (via QPL) and IGZIP backends, configured by `/etc/zlib-accel.conf` |
| RocksDB | [intel/iaa-plugin-rocksdb](https://github.com/intel/iaa-plugin-rocksdb) v0.3.0 (2023) |

- **Proven elsewhere:**
  - zlib-accel's README lists **`pg_dump --format=directory
    --compress=gzip`** and RocksDB `db_bench` as tested uses. It was tested on
    an AWS m7i.metal-24xl with Ubuntu 22.04 and kernel 6.8.
  - IAA gave the fastest compression and decompression in CMU's DuckDB study
    (ADMS 2025).
- **Limit that decides fit:** QAT and IAA only take a **single `Z_FINISH`
  call carrying the whole input**. Streaming `deflate(Z_NO_FLUSH)` stays on
  software zlib; only IGZIP handles it. The `gz*` file API is intercepted and
  buffered into whole gzip members.
  - In PostgreSQL 18, `pg_dump --format=directory` writes through
    `gzdopen`/`gzwrite`, so it is offloadable.
  - `--format=custom` streams `deflate(..., Z_NO_FLUSH)`, so it is not.
  - This comes from `src/bin/pg_dump/compress_gzip.c`.
- **Untested hypothesis:** RAG ingest steps that inflate gzip-compressed
  sources through zlib in one call per object (Python `zlib.decompress`, for
  example) would offload the same way.

Pod spec (full manifest: [`pgvector-backup.yaml`](../examples/dataplane/pgvector-backup.yaml),
image: [`pg-zlib-accel.Dockerfile`](../examples/dataplane/images/pg-zlib-accel.Dockerfile)):

```yaml
containers:
- name: pgdump
  image: k3d-gpu/pg-zlib-accel:18-zla1.1.0
  command: [/bin/sh, -c]
  args: ["LD_PRELOAD=/usr/local/lib/libzlib-accel.so pg_dump -h pgvector -U postgres -d rag --format=directory --compress=gzip:1 --jobs=4 --file=/backup/rag"]
  resources:
    limits: {iaa.intel.com/wq-user-dedicated: 1}    # or qat.intel.com/dc: 1 with CAP_IPC_LOCK
  securityContext:
    runAsUser: 0
    capabilities: {drop: [ALL], add: [SYS_RAWIO]}
  volumeMounts:
  - {name: zlib-accel-config, mountPath: /etc/zlib-accel.conf, subPath: iaa.conf}
```

### QAT compression: QATzip, QAT-ZSTD-Plugin

| | |
|---|---|
| QATzip | **v2.0.0** (2026-08-14): native ZSTD, LZ4 block and dictionary support. Distro packages: Debian trixie 1.3.1, Ubuntu 26.04 1.3.2 |
| qatlib | **26.08.0** (2026-08-07). Distro packages: Debian trixie 24.02.0, Ubuntu 26.04 26.02.0 |
| QAT-ZSTD-Plugin | **v1.0.0** (2025-10-07). It plugs into zstd's sequence-producer API, so the application has to register it. It is not transparent |

- **Proven elsewhere:** QATzip is how Envoy's `qatzip` compressor and
  zlib-accel's QAT backend reach the hardware.
- **No transparent path for zstd or lz4.** PostgreSQL's `wal_compression`
  (pglz, lz4, zstd) and TOAST (pglz, lz4) call those libraries in-process.
  Using QAT for them would mean patching PostgreSQL to register QAT-ZSTD. That
  is an untested hypothesis, and no upstream proposal was found as of 2026-10-16.

The QAT variant of the zlib-accel pod adds two things to the spec above:

```yaml
  env: [{name: QAT_POLICY, value: "1"}]
  resources: {limits: {qat.intel.com/dc: 1}}
  securityContext: {runAsUser: 0, capabilities: {drop: [ALL], add: [IPC_LOCK]}}
```

### QAT crypto: QAT_Engine / qatprovider for OpenSSL (nginx, HAProxy, Redis TLS)

| | |
|---|---|
| QAT_Engine | **v2.3.0** (2026-10-09). The OpenSSL 3 provider `qatprovider`, plus the legacy engine. Distro packages are 2.0.0: Ubuntu 26.04 ships binaries `qatengine` and `openssl-provider-qat`, and Debian trixie has source `qatengine` 2.0.0-1 |
| asynch_mode_nginx | **v1.0.0** (2025-06-26) |
| Intel images | `dockerfiles/qat_crypto_base` and `dockerfiles/haproxy` (HAProxy 3.4.0) in QAT_Engine. The Docker Hub `intel/qat-crypto-base` tags date from 2023, so build from the Dockerfiles |

- **Proven elsewhere:** HAProxy and nginx terminate TLS on QAT through
  QAT_Engine (Intel's Dockerfiles and run commands in
  `dockerfiles/README.md`).
- **Untested hypothesis:** Redis built with TLS on OpenSSL 3 would offload
  handshakes once `openssl.cnf` loads `qatprovider`. Redis has no QAT code;
  the provider does the work. Agent traffic to Redis is long-lived
  connections, so the handshake share is small. Expect little gain.

Pod spec. This follows Intel's `docker run` line, translated; no manifest is
shipped:

```yaml
containers:
- name: haproxy
  image: k3d-gpu/qat-haproxy:3.4.0-qatengine2.3.0   # built from QAT_Engine dockerfiles/haproxy
  env: [{name: QAT_POLICY, value: "1"}]
  resources: {limits: {qat.intel.com/cy: 1}}
  securityContext: {runAsUser: 0, capabilities: {add: [IPC_LOCK]}}
```

### Envoy: QAT private-key provider and qatzip compressor

| | |
|---|---|
| Envoy | **v1.40.0** (2026-10-13), image `envoyproxy/envoy:contrib-v1.40.0` |
| Extensions | `envoy.tls.key_providers.qat`, `envoy.compression.qatzip.compressor` and `envoy.compression.qatzstd.compressor`. All three are **contrib, status `alpha`** in `contrib/extensions_metadata.yaml` |
| Linking | qatlib, QATzip and QAT-ZSTD are built into the contrib binary (`bazel/deps.yaml`), so the image needs no extra packages |

- **Proven elsewhere:** the extensions ship in the contrib image. This is
  checked here, not only cited. `envoy --mode validate` with the shipped
  config logs `Failed to start QAT device`, falls back and reports `OK`, so
  both extensions are registered.
- **Key types:** the key provider does **RSA only** (sign and decrypt). An
  ECDSA certificate gets nothing from it.
- **Fallback needs the key twice:** with `fallback: true`, the
  `TlsCertificate` must also set `private_key`. Without it, validation fails
  with "Failed to load incomplete private key". This was found by running
  validate.
- **Untested hypothesis:** in an agent platform, the edge compressor helps
  `/v1/embeddings`. Those responses are large, compressible JSON float
  arrays. Streaming chat SSE is small chunks and should stay uncompressed.

Pod spec (full manifest: [`envoy-qat.yaml`](../examples/dataplane/envoy-qat.yaml)):

```yaml
containers:
- name: envoy
  image: envoyproxy/envoy:contrib-v1.40.0
  env:
  - {name: ENVOY_UID, value: "0"}     # keep root: VFIO node is root-owned, IPC_LOCK must stay effective
  - {name: QAT_POLICY, value: "1"}
  resources:
    limits: {qat.intel.com/cy: 1, qat.intel.com/dc: 1}   # or one qat.intel.com/asym-dc
  securityContext: {runAsUser: 0, capabilities: {add: [IPC_LOCK]}}
```

### Istio: QAT private-key provider on the ingress gateway

| | |
|---|---|
| Istio | **1.31.2** (2026-10-16) |
| API | `ProxyConfig.privateKeyProvider.qat {pollDelay, fallback}` (`istio/api` `mesh/v1alpha1/proxy.proto`) |
| Build | `istio/proxy` 1.31.2 links `envoy.tls.key_providers.qat` (and `cryptomb`) into the **default BoringSSL** `proxyv2` build. They are left out only of OpenSSL builds (`OPENSSL_INCOMPATIBLE_CONTRIB_EXTENSIONS`). The `qatzip` compressor is **not** in Istio's contrib allow-list |

- **Untested hypothesis:** an ingress gateway with
  [`istio-gateway-qat.values.yaml`](../examples/dataplane/istio-gateway-qat.values.yaml)
  signs RSA handshakes on QAT. This depends on gateway injection keeping the
  chart's `qat.intel.com/cy` limit and the root `containerSecurityContext` on
  the injected `istio-proxy` container.
- **Not applicable:** Ambient's ztunnel is Rust, not Envoy, so this does not
  apply to it. Waypoints are Envoy and could take the same ProxyConfig; that
  is an untested hypothesis.
- **Chart location:** the istio 1.31 gateway chart is not on the
  `istio-release` GCS index (it stops at 1.30.6) nor at
  `oci://gcr.io/istio-release/charts`. The values here were rendered from the
  `manifests/charts/gateway` tree at tag 1.31.2.

The values snippet:

```yaml
podAnnotations:
  proxy.istio.io/config: |
    privateKeyProvider:
      qat: {pollDelay: 2ms, fallback: true}
resources:
  limits: {qat.intel.com/cy: 1}
containerSecurityContext:
  runAsUser: 0
  runAsNonRoot: false
  capabilities: {drop: [ALL], add: [IPC_LOCK, NET_BIND_SERVICE]}
```

### PostgreSQL and pgvector

| | |
|---|---|
| PostgreSQL | 18.6 (REL_18_6) is current; 19 is at RC1. Image: `pgvector/pgvector:0.8.7-pg18-trixie` |
| pgvector | **0.8.7** |

Why nothing reaches the server process:

- **TOAST** compression methods are `pglz` and `lz4` only (`toast_compression.h`).
  PostgreSQL 19 makes lz4 the default. A zstd-for-TOAST patch (Dec 2025) is
  on pgsql-hackers but not committed. No hardware-backed method has been
  proposed.
- **pgvector types are `STORAGE = external`** (`vector`, `halfvec` and
  `sparsevec` in `sql/vector.sql`). Vector columns go out of line but are
  **never compressed**, so even a future pluggable TOAST compressor would not
  touch them. float32 embeddings compress poorly anyway.
- **`wal_compression`** (pglz, lz4, zstd) runs in-process in software.
- **OpenZFS** supports QAT for gzip compression, checksums and encryption.
  However, it builds against Intel's **out-of-tree** QAT driver source
  (`--with-qat=<driver>/QAT1.x`, `config/kernel.m4`), not the in-tree
  `qat_4xxx` plus vfio stack that the device plugin uses. The two compete for
  the same PF.

What works for this tier:

- **Backups** through zlib-accel, which is proven elsewhere. See the QPL
  section above and [`pgvector-backup.yaml`](../examples/dataplane/pgvector-backup.yaml).
- **zswap under memory pressure**, which is proven in the kernel and an
  untested hypothesis on k3d.

[`pgvector.yaml`](../examples/dataplane/pgvector.yaml) itself carries no
accelerator.

### LiteLLM

v1.105.1. The cache stores JSON strings in Redis (`cache: true`,
`cache_params.type: redis`), with no compression or TLS of its own unless
`REDIS_SSL`. LiteLLM needs no change. It benefits only through its Redis
(DTO) and its edge (Envoy or Istio). See
[`litellm-cache.yaml`](../examples/dataplane/litellm-cache.yaml).

## Wiring proposals for k3d-gpu

What already works:

- `k3d-gpu up` deploys the DSA, IAA and QAT plugins and labels the node.
- It bind-mounts the host's `/dev/char` so the idxd plugin accepts WQs.
- The privileged node container sees `/dev/dsa`, `/dev/iax` and `/dev/vfio`.
- `doctor` flags missing WQs, VFs and `vfio-pci`.

The launcher never changes host configuration, and nothing below changes that.

Proposals (none implemented in this branch):

1. **QAT memlock.** When `qat` is active, add
   `--runtime-ulimit memlock=-1:-1` (k3d v5.9.0 accepts `memlock`).
   - *Why:* non-root qatlib processes, and root ones without `CAP_IPC_LOCK`,
     then have room to pin buffers. The 8 MiB inherited from dockerd is below
     qatlib's 16 MiB-per-VF floor.
   - *Hypothesis:* the k3s-managed containerd passes the node's limit on to
     pods (probe P3).
2. **Opt-in `K3D_GPU_NONROOT_DEVICES=1`.** This adds
   `--k3s-arg=--nonroot-devices@server:*;agent:*`.
   - *Effect:* k3s v1.34.1 sets containerd `device_ownership_from_security_context`,
     so injected device nodes take the pod's uid and gid. That suits Istio's
     uid 1337 and PostgreSQL.
   - *Limit:* it does not grant `CAP_SYS_RAWIO`, so non-root DSA/IAA stays
     blocked.
3. **`K3D_GPU_DSA_SHARED_DEV_NUM`.** Pass it through to the DSA plugin's
   `-shared-dev-num` (and the IAA plugin's) in the baked manifests.
   - *Why:* with the default of 1, a shared WQ serves exactly one container,
     which defeats DTO's model.
4. **`doctor` additions.**
   - Warn when shared user WQs exist but `pasid_enabled` is 0, since DTO and
     shared-WQ QPL will fail.
   - List kernel-type IAA WQs bound to `crypto` and the current zswap
     compressor.
   - Print dockerd's `LimitMEMLOCK` when `qat` is active.
5. **Opt-in `K3D_GPU_SWAP=limited`.** Mount
   `examples/dataplane/kubelet-limitedswap.conf` into
   `/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/` on every node.

## Proven elsewhere vs untested hypothesis

**Proven elsewhere (cited):**

- DTO offloads large `memcpy` for CacheLib/CacheBench (arXiv 2305.02480).
- DSA speeds up Redis RDB saves with custom code (LightDSA, up to 23 %).
- zswap compresses on IAA via `deflate-iaa` (kernel docs, Linux 6.8+).
- zlib-accel offloads `pg_dump --format=directory --compress=gzip` and
  RocksDB `zlib` to QAT/IAA (zlib-accel README).
- HAProxy and nginx terminate TLS on QAT via QAT_Engine (QAT_Engine
  `dockerfiles`).
- Envoy contrib ships the QAT key provider and qatzip compressor. Both are
  registered in `contrib-v1.40.0`, checked here with validate.
- Istio 1.31.2 proxyv2 links the QAT key provider (istio/proxy BUILD) and
  exposes it in ProxyConfig.
- Containers need `SYS_RAWIO` for DSA/IAA user WQs and `IPC_LOCK` for QAT
  (kernel source, plugin demo pods, Intel images).

**Untested hypotheses (all of them on k3d):**

1. DTO in Redis lowers CPU time per large GET/SET, such as LiteLLM's cached
   embeddings.
2. Shared WQs work from a pod in a k3d node: ENQCMD with PASID, as root with
   `SYS_RAWIO`.
3. QPL inside a pod finds only its injected WQ. libaccel-config enumerates
   every WQ in host sysfs, but only the injected `/dev/iax` node exists.
4. qatlib in standalone mode inside a k3d pod opens its injected VF, with
   `IPC_LOCK` and `QAT_POLICY=1`.
5. The k3d node and its pods inherit dockerd's memlock, and
   `--runtime-ulimit` raises it.
6. The Envoy QAT key provider and qatzip offload on real VFs inside k3d.
7. Istio gateway injection keeps the QAT limit and root security context.
8. `LimitedSwap` works for pods inside a k3d node, and zswap-on-IAA then
   compresses their pages.
9. zlib-accel helps RAG ingest (whole-buffer inflate).
10. Redis TLS gains from `qatprovider`.

**Blocked or not applicable:**

- PostgreSQL server-side DSA or IAA or QAT.
- pgvector compression (`STORAGE external`).
- zram on IAA (Linux 6.12+).
- ClickHouse QPL/QAT codecs (removed in 26.1).
- Istio qatzip.
- QAT key provider with ECDSA certificates.

## Probes for a Sapphire Rapids host

Run these in order on a 4th Gen Xeon (or newer) with DSA, IAA and QAT
enabled in BIOS. Each probe states what passes.

**P0. Hardware and kernel.**

```bash
lspci -d 8086:0b25; lspci -d 8086:0cfe; lspci -d 8086:4940   # DSA, IAA (SPR), QAT 4xxx PF
uname -r                                                       # >= 6.8 for iaa_crypto; >= 7.1 for qat_zstd
grep -o 'intel_iommu=[^ ]*' /proc/cmdline                      # expect on,sm_on (or scalable mode default)
cat /sys/bus/dsa/devices/dsa0/pasid_enabled                    # 1 = shared WQs usable from user space
```

Pass: all three devices present and `pasid_enabled` is 1.

**P1. Host queues and VFs.**

DSA shared WQ for DTO (from `intel/DTO accelConfig.sh`):

```bash
accel-config config-engine dsa0/engine0.0 --group-id=0
accel-config config-wq dsa0/wq0.1 --group-id=0 --priority=1 --wq-size=128 \
  --block-on-fault=0 --type=user --name=dto --mode=shared --threshold=127 --driver-name=user
accel-config enable-device dsa0 && accel-config enable-wq dsa0/wq0.1
```

IAA, one user WQ (dedicated) for pods plus one kernel WQ for zswap:

```bash
accel-config config-engine iax1/engine1.0 --group-id=0
accel-config config-wq iax1/wq1.0 --group-id=0 --mode=dedicated --type=user --priority=10 --name=pods --driver-name=user
accel-config enable-device iax1 && accel-config enable-wq iax1/wq1.0
accel-config config-engine iax3/engine3.0 --group-id=0
accel-config config-wq iax3/wq3.0 --group-id=0 --mode=dedicated --type=kernel --priority=10 --name=iaa_crypto --driver-name=crypto
accel-config enable-device iax3 && accel-config enable-wq iax3/wq3.0
```

QAT, two PFs with different services, then VFs:

```bash
echo sym\;asym > /sys/bus/pci/devices/<pf0>/qat/cfg_services
echo dc > /sys/bus/pci/devices/<pf1>/qat/cfg_services
echo 16 > /sys/bus/pci/devices/<pf0>/sriov_numvfs; echo 16 > /sys/bus/pci/devices/<pf1>/sriov_numvfs
modprobe vfio-pci
```

The `cfg_services` write may need the PF to be `down` first (`qat/state`);
see the kernel's `sysfs-driver-qat` ABI.

Pass: `k3d-gpu doctor` prints `ok` for DSA, IAA and QAT.

**P2. Cluster and resources.**

```bash
k3d-gpu up
kubectl get node -o jsonpath='{.items[0].status.allocatable}'
```

Pass: `dsa.intel.com/wq-user-shared: 1`, `iaa.intel.com/wq-user-dedicated: 1`,
`qat.intel.com/cy: 16` and `qat.intel.com/dc: 16` are allocatable.

**P3. Limits inside the node and pods.**

```bash
docker exec k3d-gpu-server-0 sh -c 'ulimit -l'
kubectl run ml --rm -i --image=busybox:1.37 --restart=Never -- sh -c 'ulimit -l'
```

Repeat after recreating the cluster with `--runtime-ulimit memlock=-1:-1`
added to `k3d cluster create`.

Pass: the first value equals dockerd's limit, and the second becomes
`unlimited` with the flag. This decides proposal 1.

**P4. Device access from pods.** Apply `namespace.yaml`, then build and
import both images on this host (see the Dockerfile headers).

Run the upstream demo jobs, edited to `intel/accel-config-demo` built
locally (the Docker Hub tag `0.37.1` does not exist):

```bash
kubectl apply -f https://raw.githubusercontent.com/intel/intel-device-plugins-for-kubernetes/v0.37.1/demo/dsa-accel-config-demo-pod.yaml
```

Pass: `dsa_user_test_runner.sh --skip-config` and
`iaa_user_test_runner.sh` exit 0. Remove `SYS_RAWIO` and expect `EPERM` on
`mmap`.

**P5. DTO in Redis, A/B.** Deploy `redis-dto.yaml` twice: once as is, once
with `DTO_USESTDC_CALLS=1`. Load each with values of 4, 16, 64 and 256 KiB:

```bash
redis-benchmark -h redis-dto -a "$PW" -t set,get -d <size> -n 200000 -c 50 -P 16
```

Record ops/s, p99 and the Redis container's CPU seconds
(`kubectl top` or cgroup `cpu.stat`). Rerun with `DTO_COLLECT_STATS=1` once
to confirm calls ≥ 16 KiB land in the "dsa (success)" columns. This holds
only because the manifest sets `DTO_AUTO_ADJUST_KNOBS=0`; keep it at 0 for
both runs.

Pass: the DTO run uses less CPU per op, or reaches a higher p99-bounded
throughput, at ≥ 64 KiB. At 4 KiB it should match the baseline, because
nothing is offloaded.

**P6. LiteLLM cache path.** With `litellm-cache.yaml` in front of an
embeddings server, send the same `/v1/embeddings` request twice.

Pass: the second response comes from the cache (LiteLLM cache-hit header or
log). P5's histogram, taken during this test, shows the GET offloaded.

**P7. zlib-accel backups.** Load the pgvector DB (for example 1 M rows of
`vector(1536)` plus text chunks). Run `pgdump-qat` and then `pgdump-iaa`, and
compare with the same image without `LD_PRELOAD`.

Pass:
- `/backup/zlib-accel-*.log` shows offloaded calls (`log_level = 2`).
- Wall time and CPU seconds both drop.
- `pg_restore --list` reads every dump.
- Expect the vector table files to barely shrink, since float32 data is
  near-incompressible. The text chunks should shrink.

**P8. Envoy QAT.** Deploy `envoy-qat.yaml` with an RSA-2048 certificate. Load
it with `h2load` or `openssl s_time` using new TLS handshakes only.

Pass:
- The log has no `Failed to start QAT device`.
- Handshakes/s rises and Envoy CPU per handshake falls against the same pod
  with the QAT limits removed (fallback).
- For qatzip, a 1536-float `/v1/embeddings` response comes back
  `content-encoding: gzip` with lower Envoy CPU than the software `gzip`
  compressor.

**P9. Istio gateway.** Install istio-base and istiod 1.31.2, then the gateway
with the values file, and confirm the injected pod spec:

```bash
kubectl get pod -n istio-ingress -o yaml | grep -A3 qat.intel.com
```

Pass: `qat.intel.com/cy` and `runAsUser: 0` survive injection, and the P8
handshake test shows the same gain.

**P10. zswap on IAA and QAT.**

```bash
echo deflate-iaa > /sys/module/zswap/parameters/compressor   # then: qat_deflate, qat_zstd
echo 1 > /sys/module/zswap/parameters/enabled
```

Recreate the cluster with `kubelet-limitedswap.conf` mounted. Run a
Burstable pod that allocates past its memory request, such as pgvector under
a large index build.

Pass:
- `/sys/kernel/debug/zswap/stored_pages` grows.
- `/sys/bus/dsa/drivers/crypto/` stats (with
  `CONFIG_CRYPTO_DEV_IAA_CRYPTO_STATS`) count compressions.
- The pod's `memory.swap.current` is non-zero.

## Static validation

Run here, with no hardware and no cluster:

```bash
# Kubernetes schemas (Envoy, Redis, LiteLLM, pgvector, backups, Istio gateway render)
docker run --rm -v "$PWD/examples/dataplane:/ex:ro" ghcr.io/yannh/kubeconform:v0.8.0 \
  -strict -summary -kubernetes-version 1.34.1 -ignore-filename-pattern 'values' /ex
helm template istio-ingress <istio-1.31.2>/manifests/charts/gateway -n istio-ingress \
  -f examples/dataplane/istio-gateway-qat.values.yaml | \
  docker run --rm -i ghcr.io/yannh/kubeconform:v0.8.0 -strict -summary -kubernetes-version 1.34.1 -
# Envoy config (QAT absent: logs "Failed to start QAT device", then "OK")
docker run --rm -e ENVOY_UID=0 -v <envoy.yaml>:/etc/envoy/envoy.yaml:ro -v <tls dir>:/etc/envoy/tls:ro \
  envoyproxy/envoy:contrib-v1.40.0 --mode validate -c /etc/envoy/envoy.yaml
# Dockerfiles and the launcher script
docker run --rm -i hadolint/hadolint:v2.14.0 hadolint - < examples/dataplane/images/redis-dto.Dockerfile
docker run --rm -i hadolint/hadolint:v2.14.0 hadolint - < examples/dataplane/images/pg-zlib-accel.Dockerfile
docker run --rm -v "$PWD/examples/dataplane/images:/mnt:ro" koalaman/shellcheck:v0.11.0 /mnt/dto-launch.sh
```

The images have not been built, because building them is a CPU-heavy job
and DTO must be compiled on the target CPU. Neither has run.

## Sources

Versions are as of 2026-10-16, read from GitHub release/tag APIs, Docker Hub,
Launchpad and Debian's archive APIs.

- DTO: https://github.com/intel/DTO (README, `dto.c`, `CMakeLists.txt`, `accelConfig.sh` @ `fbf41a6`)
- DSA characterization (DTO on CacheBench): https://arxiv.org/pdf/2305.02480
- LightDSA (Redis RDB with DSA): https://dl.acm.org/doi/pdf/10.1145/3767295.3769356
- idxd `CAP_SYS_RAWIO` gate and device table: `drivers/dma/idxd/cdev.c`, `drivers/dma/idxd/init.c`; `pasid_enabled`: `drivers/dma/idxd/sysfs.c` (torvalds/linux master)
- vfio memlock bypass: `drivers/vfio/vfio_iommu_type1.c` (`lock_cap`)
- IAA crypto: `Documentation/driver-api/crypto/iaa/iaa-crypto.rst`; `mm/zswap.c`; zram backends `drivers/block/zram/` (commit 917a59e81c34 "zram: introduce custom comp backends API" drops `crypto_alloc_comp`, in v6.12); QAT acomp `drivers/crypto/intel/qat/qat_common/qat_comp_algs.c` (zstd: commit 879a4f78ea3f, in v7.1)
- Intel device plugins v0.37.1: https://github.com/intel/intel-device-plugins-for-kubernetes (`cmd/{dsa,iaa,qat}_plugin/README.md`, `pkg/idxd/plugin.go`, `cmd/qat_plugin/dpdkdrv/dpdkdrv.go`, `demo/*`, `deployments/qat_dpdk_app/`)
- qatlib 26.08.0 `INSTALL` (memlock, standalone mode, `QAT_POLICY`): https://github.com/intel/qatlib
- QAT_Engine v2.3.0 (`dockerfiles/README.md`): https://github.com/intel/QAT_Engine
- QATzip v2.0.0: https://github.com/intel/QATzip; QAT-ZSTD-Plugin v1.0.0: https://github.com/intel/QAT-ZSTD-Plugin
- QPL v1.9.0 (`doc/.../installation.rst`, `hw_configuration_driver.c`): https://github.com/intel/qpl
- zlib-accel v1.1.0: https://github.com/intel/zlib-accel
- accel-config v4.1.9 (`accel-config-config-wq.txt`): https://github.com/intel/idxd-config
- Envoy v1.40.0: `contrib/extensions_metadata.yaml`, `bazel/deps.yaml`, `api/contrib/envoy/extensions/private_key_providers/qat/v3alpha/qat.proto`, `api/envoy/extensions/transport_sockets/tls/v3/common.proto`, `docs/root/configuration/other_features/qatzip.rst`
- Istio 1.31.2: `istio/api` `mesh/v1alpha1/proxy.proto`; `istio/proxy` `BUILD`, `bazel/extension_config/extensions_build_config.bzl`; `istio/istio` `manifests/charts/gateway`
- PostgreSQL: `src/include/access/toast_compression.h` (master), `src/bin/pg_dump/compress_gzip.c` (REL_18_STABLE); docker-library/postgres 18 `Dockerfile` (PGDATA layout); zstd TOAST thread: https://www.postgresql.org/message-id/CDC6B8F7-696B-45E0-94E2-2BEF8F4B8CF8%40enterprisedb.com
- pgvector v0.8.7 `sql/vector.sql`: https://github.com/pgvector/pgvector
- OpenZFS 2.4.4 `config/kernel.m4`, `module/os/linux/zfs/qat_compress.c`: https://github.com/openzfs/zfs
- ClickHouse codec removal: `docs/changelogs/v26.1.1.912-stable.md`, `src/Compression/CompressionInfo.h`
- IAA in DBMS (CMU, ADMS 2025): https://db.cs.cmu.edu/papers/2025/laspias-adms2025.pdf
- LiteLLM v1.105.1: `litellm/caching/redis_cache.py`, `litellm/caching/caching.py`, `litellm/_redis.py`, `proxy_server_config.yaml`
- Kubernetes NodeSwap GA: `pkg/features/kube_features.go` (v1.34.1); k3s v1.34.1+k3s1 `pkg/agent/config/config.go` (kubelet.conf.d), `pkg/daemons/agent/agent_linux.go` (`FailSwapOn`), `pkg/cli/cmds/agent.go` (`--nonroot-devices`); k3d v5.9.0 `--runtime-ulimit`

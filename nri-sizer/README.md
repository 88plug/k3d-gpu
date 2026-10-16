# nri-sizer (experimental)

An NRI plugin that sizes CPU inference runtimes to the cpuset the balloons
policy actually gave a pod. It runs at NRI index 95, after
`nri-resource-policy-balloons` (index 90). The manifest is
`share/nri-runtime-sizer.yml`. It is not deployed by `k3d-gpu up`, and its
image is not published.

For a pod labelled `k3d-gpu.io/cores=performance|efficient` whose cpuset is a
single core type, it injects the following env. Env the pod already sets is
never overridden.

| env | value | read by |
|---|---|---|
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` | physical cores in the cpuset | libgomp, Intel OMP, MKL, OpenBLAS, torch intra-op |
| `LLAMA_ARG_THREADS` | physical cores | llama.cpp `-t` (`common/arg.cpp` `set_env`) |
| `PYTHON_CPU_COUNT` | logical CPUs | `os.cpu_count()`, Python >= 3.13 only |
| `K3D_GPU_OV_PLUGIN_CONFIG` | `{"INFERENCE_NUM_THREADS":"N","SCHEDULING_CORE_TYPE":"PCORE_ONLY\|ECORE_ONLY"}` | pass to `ovms --plugin_config` |
| `K3D_GPU_CPUSET`, `K3D_GPU_CPUS`, `K3D_GPU_CORE_TYPE` | raw facts | entrypoints |

Every sized container also gets `/run/k3d-gpu/cpus.json` (read-only). The
plugin bind-mounts a per-container directory and replaces the file there by
rename, so the file follows cpuset changes. Env cannot follow them. A mixed P+E
cpuset (the shared balloon) gets the file only: it is resized whenever other
balloons change, so a frozen number would go stale.

## How each runtime picks threads in a cpuset (static audit)

| runtime | default in a 4-CPU balloon on an i9-13900T | source |
|---|---|---|
| llama.cpp | **24** (host physical cores) | `common/common.cpp` `cpu_get_num_math`: on a hybrid CPU it pins to every CPU `0..sysconf(ONLN)`. The first CPU outside the cpuset fails, and it falls back to host-wide `thread_siblings`. On a non-hybrid host it uses host physical cores directly. |
| OpenVINO / OVMS | 4 (E), 2 (P with 2 cores x SMT) | `lin_system_conf.cpp` `check_valid_cpu` filters the CPU map by the affinity mask, which is already correct |
| torch intra-op | min(host physical, cpuset logical) | `c10/core/thread_pool.cpp` `defaultNumThreads` clamps to the cpuset |
| vLLM grammar pool | **16** = `(os.cpu_count()+1)//2` | `vllm/v1/structured_output/__init__.py`. `os.cpu_count()` is host-wide. The stock image is Python 3.12 (`docker/Dockerfile`), so `PYTHON_CPU_COUNT` does nothing there. |

## Run

```sh
go test ./...            # plan tables, real-NRI ordering test, sim invariants
go run ./cmd/sizer-sim   # simulated verdict per host, balloon and runtime arm
```

`plugin_test.go` drives the real NRI v0.8.0 runtime adaptation (the version
k3s v1.34.1 vendors). It registers a stand-in balloons plugin at index 90 and
this plugin at 95, both as external plugins over a unix socket. The test checks
that the sizer sees the balloons cpuset in the same CreateContainer pass. An
ablation at index 85 sees no cpuset and injects nothing.

`PREREGISTRATION.md` holds the acceptance criteria and predictions. It was
committed before any code.

# nri-sizer: pre-registered acceptance criteria

Written and committed BEFORE any implementation code (see git history).

## Claim under test (from the idea, unchanged)

OVMS CPU inference, llama.cpp CPU decode and vLLM guided-JSON on NVIDIA, each in
4-CPU E and P balloons. Injected env gives >=1.5x throughput or >=40% lower p99
than default env in at least 2 of 3 runtimes, and lands within 5% of a
hand-tuned sweep over threads 1-32. Falsified if default env is within 10% of
hand-tuned for all three.

## Static prototype acceptance criteria

- AC1 Plan. For a pod labelled `k3d-gpu.io/cores` whose cpuset is a single core
  type: OMP_NUM_THREADS, MKL_NUM_THREADS, OPENBLAS_NUM_THREADS and
  LLAMA_ARG_THREADS = distinct physical cores in the cpuset; PYTHON_CPU_COUNT
  and K3D_GPU_CPUS = logical CPUs in the cpuset; OpenVINO SCHEDULING_CORE_TYPE
  = PCORE_ONLY / ECORE_ONLY. Mixed-type cpuset: no thread env, ANY_CORE in the
  file only. Env the pod already sets is never overridden. Unlabelled pods get
  nothing. Verified on 4 host topologies (13900T 8P+16E, 12900K 8P+8E,
  155H 6P+8E+2LPE, non-hybrid 16C/32T) by table tests.
- AC2 Ordering. Plugin index > 90 (balloons). The plugin reads the cpuset from
  req.Container.Linux.Resources.Cpu.Cpus at CreateContainer. A unit test feeds a
  request whose cpuset was rewritten by an earlier plugin and checks the plan
  follows the rewritten cpuset, not the pod spec.
- AC3 Faithful runtime defaults. The simulation's default-thread functions are
  ports of the audited source. Tests: llama.cpp cpu_get_num_math() returns 24
  for a 4-E-core balloon and 8 for the full machine on a 13900T; vLLM grammar
  pool = (os.cpu_count()+1)//2 = 16; OpenVINO = physical cores of the mask.
- AC4 Simulation verdict. Pre-registered PREDICTIONS (written before running):
  - P1 OVMS arm: injected/default gain < 1.10x at every grid point (OpenVINO
    already filters by the affinity mask). The original "2 of 3" claim then
    needs BOTH the llama.cpp and the vLLM arms.
  - P2 llama.cpp arm: gain >= 1.5x across most of the grid where the
    oversubscribed barrier wake cost is >= 50 us; < 1.5x when it is ~0.
  - P3 vLLM guided-JSON arm: p99 reduction >= 40% only where host-side CPU per
    engine step is a large share of step time. The sim reports the boundary;
    it does not assert a win.
  - P4 Injected lands within 5% of the sweep optimum wherever SMT gives no
    gain on math threads; it misses where the SMT gain h > 5%.
- AC5 Ablation. Injecting the HOST CPU count (cpuset reading removed) must give
  <= 1.05x over default at every grid point. If it gives more, the sim grants
  the win and is broken.
- AC6 No god's-eye state. The injector package must not import the simulation
  package or see any performance parameter. A test over `go list -deps`
  enforces this.
- AC7 Hygiene. gofmt clean, go vet clean, go test passes, tests/detect_test.sh
  still passes.

## Out of scope for this prototype (deliberately)

- The LD_PRELOAD sysconf shim. It is plain prior art (libsysconfcpus 2016,
  container_cpu_detection 2018) and carries breakage risk. Measure env-only
  first.
- Wiring into `k3d-gpu up`. The image is not published, so the manifest ships
  as an opt-in file only.

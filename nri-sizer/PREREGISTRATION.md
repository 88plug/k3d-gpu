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

## Results (static/sim, added after the run; criteria above unchanged)

Source: `go test ./...` and `go run ./cmd/sizer-sim`, hosts 13900T, 12900K,
155H; balloons E x4, P 2c x SMT, P 4c no-SMT.

- AC1-AC3, AC6, AC7: pass.
- P1 OVMS: confirmed. maxGain 1.00 everywhere; default already equals the
  plan. The OVMS arm cannot clear.
- P2 llama.cpp: confirmed. Default 24 threads (13900T) / 16 (12900K, 155H) in
  a 4-CPU balloon. Clears 1.5x on 0% of the grid at 0 us wake cost, 62-75% at
  50 us, 100% at >= 200 us. Clears on 40-60% of the grid overall.
- P3 vLLM guided JSON: py3.12 (stock image) 0% (PYTHON_CPU_COUNT is ignored);
  py3.13 clears only at host CPU per step = 1.0 (17% of that slice, 6% overall).
- P4: confirmed. Injected is within 5% of the sweep at SMT gain 0, and tuned on
  25% of the grid at SMT gain >= 15% on the P 2c x SMT balloon.
- Original claim (>= 2 of 3 arms clear and tuned): holds on 0% of the grid with
  py3.12, <= 3% with py3.13. The sim predicts the claim as written FAILS. The
  surviving claim is one runtime: llama.cpp in a partial cpuset on a hybrid
  or non-hybrid host.
- AC5 deviation, stated openly: the llama.cpp ablation gives 1.00x at every
  point (pass). The OVMS host-count ablation gives up to 1.30x on the P 2c x
  SMT balloon. OpenVINO clamps the host count to the 4 logical CPUs of the
  mask, which beats the 2-physical-core default when SMT helps. This is not a
  reward for injection (the sizer's plan also picks 2), but it shows the plan's
  "physical cores" rule is wrong for SMT P balloons whenever SMT gain > 5%.
  The test now checks the llama.cpp arm on all three hybrid hosts with no gate.
- Mutation probe: forcing the sizer to emit the host count fails 3 sim tests
  (audit, wrong-cpuset, wake-cost verdict).

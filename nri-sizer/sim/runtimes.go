// Package sim models how CPU inference runtimes pick their thread counts
// inside a cpuset, and what that costs, so the sizer's claim can be falsified
// before any live benchmark.
//
// The default-thread functions are ports of the runtimes' source (cited per
// function). The performance models are parametric assumptions; the grid in
// verdict.go perturbs every parameter instead of fixing a favourable one.
package sim

import (
	"strconv"

	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// Proc is what a process inside the container can observe: the host's sysfs
// (not namespaced), its affinity mask (the cpuset), its env and Python version.
type Proc struct {
	Host     *sizer.Topology
	Affinity map[int]bool
	Env      map[string]string
	PyMinor  int // Python 3.x minor version of the runtime image
}

// NewProc builds a Proc for cpus with env.
func NewProc(host *sizer.Topology, cpus []int, env map[string]string, pyMinor int) Proc {
	aff := map[int]bool{}
	for _, c := range cpus {
		aff[c] = true
	}
	if env == nil {
		env = map[string]string{}
	}
	return Proc{Host: host, Affinity: aff, Env: env, PyMinor: pyMinor}
}

func envInt(env map[string]string, k string) (int, bool) {
	v, ok := env[k]
	if !ok {
		return 0, false
	}
	n, err := strconv.Atoi(v)
	return n, err == nil && n > 0
}

// hostPhysicalCores ports llama.cpp cpu_get_num_physical_cores() (common/common.cpp):
// distinct /sys/devices/system/cpu/cpuN/topology/thread_siblings over the HOST,
// since sysfs is not namespaced by the cpuset.
func hostPhysicalCores(t *sizer.Topology) int {
	sib := map[string]bool{}
	for _, c := range t.CPUs {
		sib[c.Core] = true
	}
	return len(sib)
}

// LlamaCppThreads ports llama.cpp's default -t: LLAMA_ARG_THREADS if set, else
// cpu_get_num_math() (common/common.cpp). On a hybrid x86 CPU that pins the
// calling thread to every CPU 0..sysconf(_SC_NPROCESSORS_ONLN)-1 in turn; the
// first CPU outside the cpuset makes pthread_setaffinity_np fail, the function
// returns -1, and the caller falls back to the host-wide physical core count.
func LlamaCppThreads(p Proc) int {
	if n, ok := envInt(p.Env, "LLAMA_ARG_THREADS"); ok {
		return n
	}
	nCPU := len(p.Host.CPUs) // sysconf(_SC_NPROCESSORS_ONLN) reads host /sys
	if p.Host.Hybrid() {
		result := 0
		ok := true
		for cpu := 0; cpu < nCPU; cpu++ {
			if !p.Affinity[cpu] { // pin_cpu(cpu) != 0
				ok = false
				break
			}
			if p.Host.CPUs[cpu].Type == sizer.Efficient {
				continue // efficiency cores harm lockstep threading
			}
			cpu++ // hyperthreading isn't useful for linear algebra
			result++
		}
		if ok && result > 0 {
			return result
		}
	}
	return hostPhysicalCores(p.Host)
}

// OpenVINOThreads ports OpenVINO's CPU plugin default: the CPU mapping table is
// filtered by the process affinity mask (src/inference/src/os/lin/
// lin_system_conf.cpp check_valid_cpu -> get_process_mask) and one stream uses
// the physical cores of the preferred core type left in it. An explicit
// INFERENCE_NUM_THREADS (via K3D_GPU_OV_PLUGIN_CONFIG -> --plugin_config) wins.
func OpenVINOThreads(p Proc, injected int) int {
	if injected > 0 {
		return injected
	}
	pc, ec := map[string]bool{}, map[string]bool{}
	for cpu := range p.Affinity {
		c := p.Host.CPUs[cpu]
		if c.Type == sizer.Efficient {
			ec[c.Core] = true
		} else {
			pc[c.Core] = true
		}
	}
	if len(pc) > 0 {
		return len(pc)
	}
	return len(ec)
}

// PythonCPUCount ports os.cpu_count(): the host's online CPUs, not the
// affinity mask; PYTHON_CPU_COUNT overrides it only on Python >= 3.13
// (Doc/using/cmdline.rst). The stock vllm/vllm-openai image is 3.12
// (docker/Dockerfile ARG PYTHON_VERSION=3.12).
func PythonCPUCount(p Proc) int {
	if p.PyMinor >= 13 {
		if n, ok := envInt(p.Env, "PYTHON_CPU_COUNT"); ok {
			return n
		}
	}
	return len(p.Host.CPUs)
}

// VLLMGrammarWorkers ports vllm/v1/structured_output/__init__.py:
// max(1, (multiprocessing.cpu_count() + 1) // 2) grammar-compile workers.
func VLLMGrammarWorkers(p Proc) int {
	n := (PythonCPUCount(p) + 1) / 2
	if n < 1 {
		n = 1
	}
	return n
}

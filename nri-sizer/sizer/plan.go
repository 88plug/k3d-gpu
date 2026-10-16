package sizer

import (
	"encoding/json"
	"fmt"
	"strconv"
)

// CoresLabel is the pod label the k3d-gpu balloons policy matches on.
const CoresLabel = "k3d-gpu.io/cores"

// OpenVINO property values for ov::hint::scheduling_core_type
// (src/inference/include/openvino/runtime/properties.hpp).
const (
	ovAnyCore   = "ANY_CORE"
	ovPCoreOnly = "PCORE_ONLY"
	ovECoreOnly = "ECORE_ONLY"
)

// Input is everything the plugin knows about one container at CreateContainer.
type Input struct {
	Cpuset string            // cpuset after every earlier NRI plugin (balloons at index 90) adjusted it
	Labels map[string]string // pod labels
	Env    map[string]string // env the container already has
}

// OpenVINO is the plugin_config fragment for OVMS --plugin_config / ov::Core.
type OpenVINO struct {
	InferenceNumThreads string `json:"INFERENCE_NUM_THREADS,omitempty"`
	SchedulingCoreType  string `json:"SCHEDULING_CORE_TYPE"`
}

// File is the content of /run/k3d-gpu/cpus.json inside the container.
type File struct {
	Cpuset      string   `json:"cpuset"`
	CoreType    string   `json:"coreType"` // performance | efficient | uniform | mixed
	Logical     int      `json:"logical"`
	Physical    int      `json:"physical"`
	Performance int      `json:"performance"`
	Efficient   int      `json:"efficient"`
	Threads     int      `json:"threads"` // 0 when the cpuset mixes core types
	OpenVINO    OpenVINO `json:"openvino"`
}

// KV is one env var.
type KV struct{ Key, Value string }

// Plan is what the plugin injects.
type Plan struct {
	Env  []KV // in a fixed order; keys the container already sets are left out
	File File
}

// EnvKeys are the variables a plan may set, in injection order.
//
//	OMP/MKL/OPENBLAS_NUM_THREADS  libgomp, Intel OMP, MKL, OpenBLAS, and torch
//	                              intra-op (ATen intraop_default_num_threads)
//	LLAMA_ARG_THREADS             llama.cpp -t (common/arg.cpp set_env)
//	PYTHON_CPU_COUNT              os.cpu_count() on Python >= 3.13; sizes
//	                              pools such as vLLM's grammar compiler
//	K3D_GPU_*                     raw facts, for entrypoints and OVMS
var EnvKeys = []string{
	"OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
	"LLAMA_ARG_THREADS", "PYTHON_CPU_COUNT",
	"K3D_GPU_CPUSET", "K3D_GPU_CPUS", "K3D_GPU_CORE_TYPE", "K3D_GPU_OV_PLUGIN_CONFIG",
}

// Compute returns the plan for one container, or nil when the container is not
// pinned by the balloons policy (no cores label, or no cpuset yet).
//
// Thread env is only set when every CPU in the cpuset has the same core type:
// that is the sized performance/efficient balloon. A mixed cpuset (the shared
// balloon) changes whenever other balloons resize, so a number frozen into env
// at create time would go stale; it gets the file only.
func Compute(t *Topology, in Input) (*Plan, error) {
	want := in.Labels[CoresLabel]
	if want != "performance" && want != "efficient" {
		return nil, nil
	}
	cpus, err := ParseCPUList(in.Cpuset)
	if err != nil {
		return nil, err
	}
	if len(cpus) == 0 {
		return nil, nil
	}
	n, err := t.Count(cpus)
	if err != nil {
		return nil, err
	}

	f := File{
		Cpuset:      FormatCPUList(cpus),
		Logical:     n.Logical,
		Physical:    n.Physical,
		Performance: n.Performance,
		Efficient:   n.Efficient,
	}
	switch {
	case n.Performance == n.Logical:
		f.CoreType, f.OpenVINO.SchedulingCoreType = Performance.String(), ovPCoreOnly
	case n.Efficient == n.Logical:
		f.CoreType, f.OpenVINO.SchedulingCoreType = Efficient.String(), ovECoreOnly
	case n.Performance == 0 && n.Efficient == 0:
		f.CoreType, f.OpenVINO.SchedulingCoreType = Uniform.String(), ovAnyCore
	default:
		f.CoreType, f.OpenVINO.SchedulingCoreType = "mixed", ovAnyCore
	}

	p := &Plan{}
	if f.CoreType != "mixed" {
		// One math thread per physical core: SMT siblings share the core's
		// FMA units, and llama.cpp and OpenVINO skip siblings by default too.
		f.Threads = n.Physical
		f.OpenVINO.InferenceNumThreads = strconv.Itoa(n.Physical)
		ov, err := json.Marshal(f.OpenVINO)
		if err != nil {
			return nil, err
		}
		vals := map[string]string{
			"OMP_NUM_THREADS":          strconv.Itoa(n.Physical),
			"MKL_NUM_THREADS":          strconv.Itoa(n.Physical),
			"OPENBLAS_NUM_THREADS":     strconv.Itoa(n.Physical),
			"LLAMA_ARG_THREADS":        strconv.Itoa(n.Physical),
			"PYTHON_CPU_COUNT":         strconv.Itoa(n.Logical),
			"K3D_GPU_CPUSET":           f.Cpuset,
			"K3D_GPU_CPUS":             strconv.Itoa(n.Logical),
			"K3D_GPU_CORE_TYPE":        f.CoreType,
			"K3D_GPU_OV_PLUGIN_CONFIG": string(ov),
		}
		for _, k := range EnvKeys {
			if _, set := in.Env[k]; set {
				continue
			}
			p.Env = append(p.Env, KV{k, vals[k]})
		}
	}
	p.File = f
	return p, nil
}

// JSON renders the file content.
func (f File) JSON() []byte {
	b, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		panic(fmt.Sprintf("marshal cpus file: %v", err)) // only plain fields: cannot fail
	}
	return append(b, '\n')
}

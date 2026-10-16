package sim

import (
	"encoding/json"
	"strconv"

	"github.com/88plug/k3d-gpu/nri-sizer/hosts"
	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// Balloon is a 4-CPU allocation the balloons policy could hand out.
type Balloon struct {
	Name  string
	Label string
	CPUs  []int
}

// Balloons4 returns the 4-CPU balloons of the claim on a hybrid host: E, P as
// 2 cores x SMT, and P as 4 cores with one thread each (balloons placement is
// not pinned down by its config, so both P layouts are tested).
func Balloons4(t *sizer.Topology) []Balloon {
	p := hosts.IDs(t, sizer.Performance)
	e := hosts.IDs(t, sizer.Efficient)
	return []Balloon{
		{"E x4", "efficient", e[len(e)-4:]},
		{"P 2c x SMT", "performance", p[len(p)-4:]},
		{"P 4c no-SMT", "performance", []int{p[1], p[3], p[5], p[7]}},
	}
}

// InjectMode selects what the plugin injects (ablations).
type InjectMode int

const (
	Default   InjectMode = iota // no plugin
	Sizer                       // sizer.Compute from the real cpuset
	HostCount                   // ablation: cpuset reading removed, inject the host CPU count
)

// EnvFor returns the container env under a mode. Only Sizer calls the sizer,
// with nothing but the cpuset, labels and topology.
func EnvFor(mode InjectMode, host *sizer.Topology, b Balloon) map[string]string {
	switch mode {
	case Sizer:
		plan, err := sizer.Compute(host, sizer.Input{Cpuset: sizer.FormatCPUList(b.CPUs), Labels: map[string]string{sizer.CoresLabel: b.Label}})
		if err != nil || plan == nil {
			panic("sizer gave no plan for a single-type balloon")
		}
		env := map[string]string{}
		for _, kv := range plan.Env {
			env[kv.Key] = kv.Value
		}
		return env
	case HostCount:
		n := strconv.Itoa(len(host.CPUs))
		ov, _ := json.Marshal(sizer.OpenVINO{InferenceNumThreads: n, SchedulingCoreType: "ANY_CORE"})
		return map[string]string{"LLAMA_ARG_THREADS": n, "PYTHON_CPU_COUNT": n, "K3D_GPU_OV_PLUGIN_CONFIG": string(ov)}
	}
	return map[string]string{}
}

// ovInjected is the INFERENCE_NUM_THREADS OVMS receives: the entrypoint
// passes K3D_GPU_OV_PLUGIN_CONFIG to --plugin_config.
func ovInjected(env map[string]string) int {
	var cfg sizer.OpenVINO
	if err := json.Unmarshal([]byte(env["K3D_GPU_OV_PLUGIN_CONFIG"]), &cfg); err != nil {
		return 0
	}
	n, _ := strconv.Atoi(cfg.InferenceNumThreads)
	return n
}

// DecodeGrid is every combination of the decode-model parameters.
func DecodeGrid() []DecodeParams {
	var out []DecodeParams
	for _, wake := range []float64{0, 10e-6, 50e-6, 200e-6, 1e-3} {
		for _, smt := range []float64{0, 0.15, 0.3} {
			for _, es := range []float64{0.4, 0.6} {
				for _, work := range []float64{0.2, 0.8} {
					for _, bw := range []float64{0.02, 0.1} {
						for _, bar := range []float64{300, 1000} {
							for _, sc := range []float64{0.5e-6, 2e-6} {
								out = append(out, DecodeParams{Work: work, BWFloor: bw, ESpeed: es, SMTGain: smt, Barriers: bar, SyncCost: sc, WakeCost: wake})
							}
						}
					}
				}
			}
		}
	}
	return out
}

// GuidedGrid is every combination of the guided-decoding parameters.
func GuidedGrid() []GuidedParams {
	var out []GuidedParams
	for _, hostFrac := range []float64{0.05, 0.25, 1.0} {
		for _, comp := range []float64{0.05, 0.5} {
			for _, rate := range []float64{1, 5} {
				for seed := int64(1); seed <= 3; seed++ {
					out = append(out, GuidedParams{StepGPU: 0.02, HostCPU: 0.02 * hostFrac, Compile: comp, Rate: rate, Horizon: 20, Seed: seed, TickSecs: 0.0005})
				}
			}
		}
	}
	return out
}

// ArmResult summarises one runtime arm over its grid.
type ArmResult struct {
	Arm           string
	DefaultN      int     // default thread/worker count
	InjectedN     int     // with the sizer
	HostN         int     // with the host-count ablation
	Points        int     // grid points
	Clear         int     // points where injected clears the bar (>=1.5x or >=40% lower p99)
	ClearTuned    int     // ... and lands within 5% of the hand-tuned sweep
	DefaultNear   int     // points where default is within 10% of hand-tuned
	MaxHostGain   float64 // best gain of the HostCount ablation over default (must stay <= 1.05)
	MinGain       float64
	MaxGain       float64
	InjectedTuned int // points where injected is within 5% of hand-tuned (regardless of gain)

	// Breakdowns keyed by the parameter the prediction names:
	// [hits, points]. Decode arms: Clear by WakeCost, InjectedTuned by
	// SMTGain. vLLM arms: Clear by HostCPU/StepGPU.
	ClearBy map[float64][2]int
	TunedBy map[float64][2]int
}

func bump(m map[float64][2]int, k float64, hit bool) {
	v := m[k]
	if hit {
		v[0]++
	}
	v[1]++
	m[k] = v
}

// Frac is a fraction helper.
func Frac(n, d int) float64 {
	if d == 0 {
		return 0
	}
	return float64(n) / float64(d)
}

func decodeArm(name string, host *sizer.Topology, b Balloon, threads func(Proc) int) ArmResult {
	nd := threads(NewProc(host, b.CPUs, EnvFor(Default, host, b), 12))
	ni := threads(NewProc(host, b.CPUs, EnvFor(Sizer, host, b), 12))
	nh := threads(NewProc(host, b.CPUs, EnvFor(HostCount, host, b), 12))
	r := ArmResult{Arm: name, DefaultN: nd, InjectedN: ni, HostN: nh, MinGain: 1e9,
		ClearBy: map[float64][2]int{}, TunedBy: map[float64][2]int{}}
	for _, d := range DecodeGrid() {
		td := TokenTime(host, b.CPUs, nd, d)
		ti := TokenTime(host, b.CPUs, ni, d)
		th := TokenTime(host, b.CPUs, nh, d)
		_, tb := BestThreads(host, b.CPUs, 32, d)
		gain := td / ti
		r.Points++
		r.MinGain = min(r.MinGain, gain)
		r.MaxGain = max(r.MaxGain, gain)
		r.MaxHostGain = max(r.MaxHostGain, td/th)
		tuned := ti <= 1.05*tb
		bump(r.ClearBy, d.WakeCost, gain >= 1.5)
		bump(r.TunedBy, d.SMTGain, tuned)
		if tuned {
			r.InjectedTuned++
		}
		if gain >= 1.5 {
			r.Clear++
			if tuned {
				r.ClearTuned++
			}
		}
		if td <= 1.10*tb {
			r.DefaultNear++
		}
	}
	return r
}

// LlamaArm evaluates llama.cpp CPU decode in balloon b.
func LlamaArm(host *sizer.Topology, b Balloon) ArmResult {
	return decodeArm("llama.cpp decode", host, b, LlamaCppThreads)
}

// OVMSArm evaluates OVMS CPU inference in balloon b.
func OVMSArm(host *sizer.Topology, b Balloon) ArmResult {
	return decodeArm("OVMS CPU", host, b, func(p Proc) int { return OpenVINOThreads(p, ovInjected(p.Env)) })
}

// VLLMArm evaluates vLLM guided JSON with the given Python minor version.
func VLLMArm(host *sizer.Topology, b Balloon, pyMinor int) ArmResult {
	L := len(b.CPUs)
	wd := VLLMGrammarWorkers(NewProc(host, b.CPUs, EnvFor(Default, host, b), pyMinor))
	wi := VLLMGrammarWorkers(NewProc(host, b.CPUs, EnvFor(Sizer, host, b), pyMinor))
	wh := VLLMGrammarWorkers(NewProc(host, b.CPUs, EnvFor(HostCount, host, b), pyMinor))
	r := ArmResult{Arm: "vLLM guided JSON py3." + strconv.Itoa(pyMinor), DefaultN: wd, InjectedN: wi, HostN: wh, MinGain: 1e9,
		ClearBy: map[float64][2]int{}, TunedBy: map[float64][2]int{}}
	for _, g := range GuidedGrid() {
		rd, ri, rh := RunGuided(L, wd, g), RunGuided(L, wi, g), RunGuided(L, wh, g)
		bestITL, bestTTFT := rd.ITLp99, rd.TTFTp99
		for w := 1; w <= 16; w++ {
			x := RunGuided(L, w, g)
			bestITL, bestTTFT = min(bestITL, x.ITLp99), min(bestTTFT, x.TTFTp99)
		}
		itlRed := 1 - ri.ITLp99/rd.ITLp99
		ttftRed := 1 - ri.TTFTp99/rd.TTFTp99
		gain := rd.ITLp99 / ri.ITLp99 // reported as the ITL p99 ratio
		r.Points++
		r.MinGain = min(r.MinGain, gain)
		r.MaxGain = max(r.MaxGain, gain)
		r.MaxHostGain = max(r.MaxHostGain, rd.ITLp99/rh.ITLp99, rd.TTFTp99/rh.TTFTp99)
		itlTuned := ri.ITLp99 <= 1.05*bestITL
		ttftTuned := ri.TTFTp99 <= 1.05*bestTTFT
		if itlTuned && ttftTuned {
			r.InjectedTuned++
		}
		bump(r.ClearBy, g.HostCPU/g.StepGPU, itlRed >= 0.40 || ttftRed >= 0.40)
		bump(r.TunedBy, g.HostCPU/g.StepGPU, itlTuned && ttftTuned)
		if itlRed >= 0.40 || ttftRed >= 0.40 {
			r.Clear++
			if (itlRed >= 0.40 && itlTuned) || (ttftRed >= 0.40 && ttftTuned) {
				r.ClearTuned++
			}
		}
		if rd.ITLp99 <= 1.10*bestITL && rd.TTFTp99 <= 1.10*bestTTFT {
			r.DefaultNear++
		}
	}
	return r
}

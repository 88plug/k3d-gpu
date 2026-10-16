package sim

import (
	"os/exec"
	"strings"
	"testing"

	"github.com/88plug/k3d-gpu/nri-sizer/hosts"
	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

func cpusOf(t *sizer.Topology) []int {
	out := make([]int, 0, len(t.CPUs))
	for id := range t.CPUs {
		out = append(out, id)
	}
	return out
}

// AC3: the ported defaults reproduce the static source audit.
func TestRuntimeDefaultsMatchSourceAudit(t *testing.T) {
	h := hosts.Build(hosts.I9_13900T)
	b := Balloons4(h)
	eBal, pSMT := b[0], b[1]

	if n := LlamaCppThreads(NewProc(h, eBal.CPUs, nil, 12)); n != 24 {
		t.Errorf("llama.cpp in 4-E balloon: %d threads, want 24 (host physical cores)", n)
	}
	if n := LlamaCppThreads(NewProc(h, cpusOf(h), nil, 12)); n != 8 {
		t.Errorf("llama.cpp with the whole 13900T: %d, want 8 (P-cores, siblings skipped)", n)
	}
	// A cpuset that starts at CPU 0 still fails at the first CPU outside it.
	if n := LlamaCppThreads(NewProc(h, []int{0, 1, 2, 3}, nil, 12)); n != 24 {
		t.Errorf("llama.cpp in cpus 0-3: %d, want 24", n)
	}
	x := hosts.Build(hosts.Xeon16)
	if n := LlamaCppThreads(NewProc(x, []int{0, 1, 2, 3}, nil, 12)); n != 16 {
		t.Errorf("llama.cpp non-hybrid 4-cpu cpuset: %d, want 16", n)
	}
	if n := LlamaCppThreads(NewProc(h, eBal.CPUs, map[string]string{"LLAMA_ARG_THREADS": "4"}, 12)); n != 4 {
		t.Errorf("LLAMA_ARG_THREADS ignored: %d", n)
	}

	if n := OpenVINOThreads(NewProc(h, pSMT.CPUs, nil, 12), 0); n != 2 {
		t.Errorf("OpenVINO in P 2c x SMT: %d, want 2", n)
	}
	if n := OpenVINOThreads(NewProc(h, eBal.CPUs, nil, 12), 0); n != 4 {
		t.Errorf("OpenVINO in E x4: %d, want 4", n)
	}

	env := EnvFor(Sizer, h, eBal)
	if n := VLLMGrammarWorkers(NewProc(h, eBal.CPUs, nil, 12)); n != 16 {
		t.Errorf("vLLM default grammar workers: %d, want 16", n)
	}
	if n := VLLMGrammarWorkers(NewProc(h, eBal.CPUs, env, 12)); n != 16 {
		t.Errorf("vLLM py3.12 honoured PYTHON_CPU_COUNT: %d, want 16", n)
	}
	if n := VLLMGrammarWorkers(NewProc(h, eBal.CPUs, env, 13)); n != 2 {
		t.Errorf("vLLM py3.13 with PYTHON_CPU_COUNT=4: %d, want 2", n)
	}
}

// AC5 (as interpreted, see README): wherever the sizer wins, the win must
// vanish when the cpuset reading is removed (inject the host CPU count).
func TestAblationHostCountRemovesTheWin(t *testing.T) {
	h := hosts.Build(hosts.I9_13900T)
	for _, b := range Balloons4(h) {
		for _, r := range []ArmResult{LlamaArm(h, b), OVMSArm(h, b)} {
			if r.Clear > 0 && r.MaxHostGain > 1.05 {
				t.Errorf("%s %s: host-count ablation gains %.2fx: the sim rewards injection itself", b.Name, r.Arm, r.MaxHostGain)
			}
		}
	}
}

// The win must come from matching THIS cpuset, not from injecting a small
// number: sizing the E balloon with the plan of a different 4-CPU balloon
// (P 2c x SMT -> 2 threads) must be tuned less often than the true plan.
func TestWrongCpusetPlanLoses(t *testing.T) {
	h := hosts.Build(hosts.I9_13900T)
	b := Balloons4(h)
	eBal, pSMT := b[0], b[1]
	right := LlamaCppThreads(NewProc(h, eBal.CPUs, EnvFor(Sizer, h, eBal), 12))
	wrong := LlamaCppThreads(NewProc(h, eBal.CPUs, EnvFor(Sizer, h, pSMT), 12))
	if right == wrong {
		t.Fatalf("plans do not differ (%d)", right)
	}
	var rightTuned, wrongTuned int
	for _, d := range DecodeGrid() {
		_, best := BestThreads(h, eBal.CPUs, 32, d)
		if TokenTime(h, eBal.CPUs, right, d) <= 1.05*best {
			rightTuned++
		}
		if TokenTime(h, eBal.CPUs, wrong, d) <= 1.05*best {
			wrongTuned++
		}
	}
	if wrongTuned >= rightTuned {
		t.Fatalf("wrong-cpuset plan tuned %d times, true plan %d", wrongTuned, rightTuned)
	}
}

// The sim must be able to say no: with free wakeups the llama.cpp arm never
// clears 1.5x, and with 1 ms wakeups it always does. The verdict then rests on
// a measurable quantity, not on the model.
func TestLlamaVerdictHingesOnWakeCost(t *testing.T) {
	h := hosts.Build(hosts.I9_13900T)
	r := LlamaArm(h, Balloons4(h)[0])
	if v := r.ClearBy[0]; v[0] != 0 {
		t.Errorf("clears with zero wake cost at %d/%d points", v[0], v[1])
	}
	if v := r.ClearBy[1e-3]; v[0] != v[1] {
		t.Errorf("1 ms wake cost clears only %d/%d", v[0], v[1])
	}
}

// Guided-decoding model sanity: equal workers give equal results, and the
// model is deterministic per seed.
func TestGuidedDeterministic(t *testing.T) {
	g := GuidedGrid()[0]
	a, b := RunGuided(4, 16, g), RunGuided(4, 16, g)
	if a != b || a.Steps == 0 {
		t.Fatalf("non-deterministic or empty: %+v %+v", a, b)
	}
}

// AC6: the injector cannot see the simulation or its parameters.
func TestSizerHasNoGodsEyeView(t *testing.T) {
	out, err := exec.Command("go", "list", "-deps", "github.com/88plug/k3d-gpu/nri-sizer/sizer").CombinedOutput()
	if err != nil {
		t.Fatalf("go list: %v\n%s", err, out)
	}
	for _, dep := range strings.Fields(string(out)) {
		if strings.HasPrefix(dep, "github.com/88plug/k3d-gpu/nri-sizer/") && dep != "github.com/88plug/k3d-gpu/nri-sizer/sizer" {
			t.Errorf("sizer depends on %s", dep)
		}
	}
}

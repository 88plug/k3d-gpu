// Command sizer-sim prints the simulated verdict of the balloon-truthful
// runtime sizing claim for each host, balloon and runtime arm.
package main

import (
	"fmt"
	"sort"
	"strings"

	"github.com/88plug/k3d-gpu/nri-sizer/hosts"
	"github.com/88plug/k3d-gpu/nri-sizer/sim"
)

func main() {
	fmt.Println("clear = injected >=1.5x throughput or >=40% lower p99 vs default; tuned = within 5% of the 1..32 sweep")
	fmt.Println("near  = DEFAULT already within 10% of the sweep (the claim's falsifier)")
	fmt.Println()
	for _, h := range []hosts.Spec{hosts.I9_13900T, hosts.I9_12900K, hosts.U7_155H} {
		host := hosts.Build(h)
		for _, b := range sim.Balloons4(host) {
			fmt.Printf("== %s, balloon %s (cpus %v)\n", h.Name, b.Name, b.CPUs)
			fmt.Printf("  %-26s %5s %5s %5s %7s %7s %7s %7s %8s %8s %8s\n",
				"arm", "def", "inj", "host", "clear", "c+tuned", "tuned", "near", "minGain", "maxGain", "hostGain")
			arms := []sim.ArmResult{
				sim.OVMSArm(host, b),
				sim.LlamaArm(host, b),
				sim.VLLMArm(host, b, 12),
				sim.VLLMArm(host, b, 13),
			}
			for _, r := range arms {
				fmt.Printf("  %-26s %5d %5d %5d %7.2f %7.2f %7.2f %7.2f %8.2f %8.2f %8.2f\n",
					r.Arm, r.DefaultN, r.InjectedN, r.HostN,
					sim.Frac(r.Clear, r.Points), sim.Frac(r.ClearTuned, r.Points),
					sim.Frac(r.InjectedTuned, r.Points), sim.Frac(r.DefaultNear, r.Points),
					r.MinGain, r.MaxGain, r.MaxHostGain)
				key := "wake"
				if strings.HasPrefix(r.Arm, "vLLM") {
					key = "hostCPU/step"
				}
				fmt.Printf("      clear by %s: %s\n", key, breakdown(r.ClearBy))
				if !strings.HasPrefix(r.Arm, "vLLM") {
					fmt.Printf("      tuned by SMT gain: %s\n", breakdown(r.TunedBy))
				}
			}
			// The claim needs 2 of 3 arms to clear AND be tuned. The decode and
			// guided grids are independent, so if OVMS never clears the joint
			// fraction is P(llama.cpp) * P(vLLM).
			if arms[0].ClearTuned > 0 {
				fmt.Println("  OVMS clears somewhere: joint fraction needs a per-point join")
				continue
			}
			l := sim.Frac(arms[1].ClearTuned, arms[1].Points)
			for _, v := range arms[2:] {
				fmt.Printf("  claim holds (>=2 of 3, clear+tuned) with %s: %.2f of the grid\n", v.Arm, l*sim.Frac(v.ClearTuned, v.Points))
			}
		}
	}
}

func breakdown(m map[float64][2]int) string {
	keys := make([]float64, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Float64s(keys)
	var parts []string
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%g=%.2f", k, sim.Frac(m[k][0], m[k][1])))
	}
	return strings.Join(parts, " ")
}

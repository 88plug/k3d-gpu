package sim

import (
	"math"

	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// DecodeParams is the parametric CPU-decode cost model (llama.cpp style:
// lockstep threads, one barrier per graph op). Every field is perturbed by
// the verdict grid.
type DecodeParams struct {
	Work     float64 // compute per token, P-core-seconds
	BWFloor  float64 // memory-bandwidth floor per token, seconds
	ESpeed   float64 // E-core speed relative to a P-core
	SMTGain  float64 // extra throughput of a second SMT thread on a P-core (0..1)
	Barriers float64 // barriers per token (graph ops)
	SyncCost float64 // seconds per barrier when every thread has a CPU
	WakeCost float64 // seconds a barrier waits per extra round of descheduled threads
}

// TokenTime returns seconds per token for t threads in cpus on host.
//
// Up to one thread per physical core runs at full core speed; threads on SMT
// siblings add SMTGain each. More threads than CPUs cannot add compute; they
// time-share, and each barrier then waits for descheduled threads: ceil(t/L)-1
// extra wake rounds. Nothing here knows which thread count the sizer chose.
func TokenTime(host *sizer.Topology, cpus []int, t int, d DecodeParams) float64 {
	cores := map[string]float64{} // core -> speed
	logical := len(cpus)
	for _, id := range cpus {
		c := host.CPUs[id]
		s := 1.0
		if c.Type == sizer.Efficient {
			s = d.ESpeed
		}
		cores[c.Core] = s
	}
	speeds := make([]float64, 0, len(cores))
	for _, s := range cores {
		speeds = append(speeds, s)
	}
	phys := len(speeds)
	// Lockstep threads: every thread gets an equal slice, so the slowest
	// placement sets the pace. Threads fill physical cores first, then siblings.
	run := t
	if run > logical {
		run = logical
	}
	rate := 0.0
	minSpeed := math.Inf(1)
	for _, s := range speeds {
		minSpeed = math.Min(minSpeed, s)
	}
	if run <= phys {
		rate = float64(run) * minSpeed
	} else {
		rate = float64(phys)*minSpeed + float64(run-phys)*d.SMTGain*minSpeed
	}
	compute := d.Work / rate
	tok := math.Max(compute, d.BWFloor)
	rounds := math.Ceil(float64(t)/float64(logical)) - 1
	tok += d.Barriers * (d.SyncCost*math.Log2(float64(t)+1) + d.WakeCost*rounds)
	return tok
}

// BestThreads sweeps t = 1..maxT and returns the fastest (hand-tuned baseline).
func BestThreads(host *sizer.Topology, cpus []int, maxT int, d DecodeParams) (int, float64) {
	best, bt := 1, math.Inf(1)
	for t := 1; t <= maxT; t++ {
		if v := TokenTime(host, cpus, t, d); v < bt {
			best, bt = t, v
		}
	}
	return best, bt
}

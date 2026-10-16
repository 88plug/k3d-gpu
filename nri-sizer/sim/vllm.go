package sim

import (
	"math"
	"math/rand"
	"sort"
)

// GuidedParams is the vLLM guided-decoding host-CPU model: the engine loop
// thread needs HostCPU seconds of CPU per step, then waits StepGPU on the GPU;
// grammar compiles of Compile CPU-seconds each run on a pool of W threads.
// All runnable threads share the cpuset's L CPUs equally (processor sharing).
type GuidedParams struct {
	StepGPU  float64 // GPU time per decode step, s
	HostCPU  float64 // engine-thread CPU per step, s
	Compile  float64 // CPU-seconds per grammar compile
	Rate     float64 // new-schema requests per second (Poisson)
	Horizon  float64 // simulated seconds
	Seed     int64
	TickSecs float64
}

// GuidedResult holds the p99s of one run.
type GuidedResult struct {
	ITLp99  float64 // decode step (inter-token) latency p99, s
	TTFTp99 float64 // guided request: arrival -> grammar ready -> first step, s
	Steps   int
}

func p99(v []float64) float64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	return s[int(math.Ceil(0.99*float64(len(s))))-1]
}

// RunGuided simulates W grammar workers on L CPUs.
func RunGuided(L, W int, g GuidedParams) GuidedResult {
	rng := rand.New(rand.NewSource(g.Seed))
	var arrivals []float64
	for t := rng.ExpFloat64() / g.Rate; t < g.Horizon; t += rng.ExpFloat64() / g.Rate {
		arrivals = append(arrivals, t)
	}
	type job struct{ arrive, left float64 }
	var queue, running []*job
	var itl, ttft []float64
	var waitingFirstStep []float64 // arrival times of compiled requests awaiting a step

	next := 0
	stepStart, hostLeft, gpuLeft := 0.0, g.HostCPU, 0.0
	dt := g.TickSecs
	for now := 0.0; now < g.Horizon; now += dt {
		for next < len(arrivals) && arrivals[next] <= now {
			queue = append(queue, &job{arrive: arrivals[next], left: g.Compile})
			next++
		}
		for len(running) < W && len(queue) > 0 {
			running, queue = append(running, queue[0]), queue[1:]
		}
		runnable := len(running)
		if hostLeft > 0 {
			runnable++
		}
		share := 1.0
		if runnable > L {
			share = float64(L) / float64(runnable)
		}
		// Compile workers progress.
		var still []*job
		for _, j := range running {
			j.left -= share * dt
			if j.left <= 0 {
				waitingFirstStep = append(waitingFirstStep, j.arrive)
			} else {
				still = append(still, j)
			}
		}
		running = still
		// Engine thread: host phase then GPU phase.
		if hostLeft > 0 {
			hostLeft -= share * dt
			if hostLeft <= 0 {
				gpuLeft = g.StepGPU
			}
		} else {
			gpuLeft -= dt
			if gpuLeft <= 0 {
				end := now + dt
				itl = append(itl, end-stepStart)
				for _, a := range waitingFirstStep {
					ttft = append(ttft, end-a)
				}
				waitingFirstStep = waitingFirstStep[:0]
				stepStart, hostLeft = end, g.HostCPU
			}
		}
	}
	// Requests still compiling or waiting at the horizon count with their age
	// so far: a lower bound, never dropped (dropping would favour the
	// configuration with the bigger backlog).
	for _, j := range append(queue, running...) {
		ttft = append(ttft, g.Horizon-j.arrive)
	}
	for _, a := range waitingFirstStep {
		ttft = append(ttft, g.Horizon-a)
	}
	return GuidedResult{ITLp99: p99(itl), TTFTp99: p99(ttft), Steps: len(itl)}
}

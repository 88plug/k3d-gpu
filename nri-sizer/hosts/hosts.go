// Package hosts builds CPU topologies of named machines and writes them out as
// a fake sysfs tree, for tests and the simulation.
package hosts

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"

	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// Spec describes a host. P-cores are numbered first with SMT siblings
// adjacent (2k, 2k+1), as on Intel client parts under Linux; E-cores follow,
// one CPU each. A non-hybrid host (E == 0, Hybrid false) numbers siblings
// (i, i+Cores) as on most servers.
type Spec struct {
	Name   string
	P      int // P-cores (each 2 logical CPUs when SMT)
	E      int // E-cores incl. LP E-cores (1 logical CPU each)
	SMT    bool
	Hybrid bool
}

// Named hosts used by tests and the simulation.
var (
	I9_13900T = Spec{Name: "i9-13900T", P: 8, E: 16, SMT: true, Hybrid: true} // probe host
	I9_12900K = Spec{Name: "i9-12900K", P: 8, E: 8, SMT: true, Hybrid: true}
	U7_155H   = Spec{Name: "Core Ultra 7 155H", P: 6, E: 10, SMT: true, Hybrid: true} // 8 E + 2 LP E
	Xeon16    = Spec{Name: "16C/32T non-hybrid", P: 16, SMT: true}
	All       = []Spec{I9_13900T, I9_12900K, U7_155H, Xeon16}
)

// Build returns the topology for a spec.
func Build(s Spec) *sizer.Topology {
	t := &sizer.Topology{CPUs: map[int]sizer.CPU{}}
	ptype := sizer.Uniform
	if s.Hybrid {
		ptype = sizer.Performance
	}
	if s.Hybrid {
		id := 0
		for core := 0; core < s.P; core++ {
			if s.SMT {
				sib := fmt.Sprintf("%d-%d", id, id+1)
				t.CPUs[id] = sizer.CPU{ID: id, Core: sib, Type: ptype}
				t.CPUs[id+1] = sizer.CPU{ID: id + 1, Core: sib, Type: ptype}
				id += 2
			} else {
				t.CPUs[id] = sizer.CPU{ID: id, Core: strconv.Itoa(id), Type: ptype}
				id++
			}
		}
		for core := 0; core < s.E; core++ {
			t.CPUs[id] = sizer.CPU{ID: id, Core: strconv.Itoa(id), Type: sizer.Efficient}
			id++
		}
		return t
	}
	for core := 0; core < s.P; core++ {
		if s.SMT {
			sib := fmt.Sprintf("%d,%d", core, core+s.P)
			t.CPUs[core] = sizer.CPU{ID: core, Core: sib, Type: ptype}
			t.CPUs[core+s.P] = sizer.CPU{ID: core + s.P, Core: sib, Type: ptype}
		} else {
			t.CPUs[core] = sizer.CPU{ID: core, Core: strconv.Itoa(core), Type: ptype}
		}
	}
	return t
}

// IDs returns the CPU IDs of type ty in ascending order.
func IDs(t *sizer.Topology, ty sizer.CoreType) []int {
	var out []int
	for id := 0; id < len(t.CPUs); id++ {
		if c, ok := t.CPUs[id]; ok && c.Type == ty {
			out = append(out, id)
		}
	}
	return out
}

// WriteSysfs writes t as <root>/sys/... in the layout sizer.LoadSysfs reads.
func WriteSysfs(root string, t *sizer.Topology) error {
	all := make([]int, 0, len(t.CPUs))
	for id := range t.CPUs {
		all = append(all, id)
	}
	write := func(rel, content string) error {
		p := filepath.Join(root, "sys", rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		return os.WriteFile(p, []byte(content+"\n"), 0o644)
	}
	if err := write("devices/system/cpu/online", sizer.FormatCPUList(all)); err != nil {
		return err
	}
	if p := IDs(t, sizer.Performance); len(p) > 0 {
		if err := write("devices/cpu_core/cpus", sizer.FormatCPUList(p)); err != nil {
			return err
		}
	}
	if e := IDs(t, sizer.Efficient); len(e) > 0 {
		if err := write("devices/cpu_atom/cpus", sizer.FormatCPUList(e)); err != nil {
			return err
		}
	}
	for id, c := range t.CPUs {
		if err := write(fmt.Sprintf("devices/system/cpu/cpu%d/topology/thread_siblings_list", id), c.Core); err != nil {
			return err
		}
	}
	return nil
}

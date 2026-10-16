// Package sizer turns the cpuset a container actually received into the
// thread-count and core-type settings that CPU inference runtimes read.
//
// It only sees what an NRI plugin sees at CreateContainer: the container's
// cpuset string, its pod labels, its existing env and the node's sysfs CPU
// topology. It has no knowledge of runtime performance.
package sizer

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// CoreType is the Intel hybrid core kind of a logical CPU.
type CoreType int

const (
	// Uniform means the host is not hybrid (no cpu_core/cpu_atom PMUs).
	Uniform CoreType = iota
	// Performance is a P-core (listed in /sys/devices/cpu_core/cpus).
	Performance
	// Efficient is an E-core or LP E-core (listed in /sys/devices/cpu_atom/cpus).
	Efficient
)

func (t CoreType) String() string {
	switch t {
	case Performance:
		return "performance"
	case Efficient:
		return "efficient"
	default:
		return "uniform"
	}
}

// CPU is one logical CPU of the node.
type CPU struct {
	ID   int
	Core string // thread_siblings_list: identical for SMT siblings of one physical core
	Type CoreType
}

// Topology is the node's logical CPUs keyed by ID.
type Topology struct {
	CPUs map[int]CPU
}

// ParseCPUList parses a Linux cpulist such as "0-3,8,10-11".
func ParseCPUList(s string) ([]int, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil, nil
	}
	seen := map[int]bool{}
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		lo, hi, isRange := strings.Cut(part, "-")
		a, err := strconv.Atoi(lo)
		if err != nil || a < 0 {
			return nil, fmt.Errorf("bad cpulist %q", s)
		}
		b := a
		if isRange {
			if b, err = strconv.Atoi(hi); err != nil || b < a {
				return nil, fmt.Errorf("bad cpulist %q", s)
			}
		}
		for c := a; c <= b; c++ {
			seen[c] = true
		}
	}
	out := make([]int, 0, len(seen))
	for c := range seen {
		out = append(out, c)
	}
	sort.Ints(out)
	return out, nil
}

// FormatCPUList renders sorted CPU IDs as a compact cpulist.
func FormatCPUList(cpus []int) string {
	c := append([]int(nil), cpus...)
	sort.Ints(c)
	var parts []string
	for i := 0; i < len(c); {
		j := i
		for j+1 < len(c) && c[j+1] == c[j]+1 {
			j++
		}
		if j == i {
			parts = append(parts, strconv.Itoa(c[i]))
		} else {
			parts = append(parts, fmt.Sprintf("%d-%d", c[i], c[j]))
		}
		i = j + 1
	}
	return strings.Join(parts, ",")
}

func readTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// LoadSysfs reads the topology from <root>/sys. root is "/" on a node, or the
// host-root mount inside the plugin pod.
func LoadSysfs(root string) (*Topology, error) {
	sys := filepath.Join(root, "sys")
	online, err := ParseCPUList(readTrim(filepath.Join(sys, "devices/system/cpu/online")))
	if err != nil || len(online) == 0 {
		return nil, fmt.Errorf("read online cpus under %s: %v", sys, err)
	}
	pcores, err := ParseCPUList(readTrim(filepath.Join(sys, "devices/cpu_core/cpus")))
	if err != nil {
		return nil, err
	}
	ecores, err := ParseCPUList(readTrim(filepath.Join(sys, "devices/cpu_atom/cpus")))
	if err != nil {
		return nil, err
	}
	kind := map[int]CoreType{}
	for _, c := range pcores {
		kind[c] = Performance
	}
	for _, c := range ecores {
		kind[c] = Efficient
	}
	t := &Topology{CPUs: map[int]CPU{}}
	for _, c := range online {
		sib := readTrim(filepath.Join(sys, fmt.Sprintf("devices/system/cpu/cpu%d/topology/thread_siblings_list", c)))
		if sib == "" {
			sib = strconv.Itoa(c)
		}
		t.CPUs[c] = CPU{ID: c, Core: sib, Type: kind[c]}
	}
	return t, nil
}

// Hybrid reports whether the topology has both P- and E-cores.
func (t *Topology) Hybrid() bool {
	var p, e bool
	for _, c := range t.CPUs {
		p = p || c.Type == Performance
		e = e || c.Type == Efficient
	}
	return p && e
}

// Count summarises a set of CPUs.
type Count struct {
	Logical     int
	Physical    int // distinct physical cores (SMT siblings counted once)
	Performance int // logical P-cores
	Efficient   int // logical E-cores
}

// Count summarises cpus against the topology. Unknown CPU IDs are an error.
func (t *Topology) Count(cpus []int) (Count, error) {
	var n Count
	cores := map[string]bool{}
	for _, id := range cpus {
		c, ok := t.CPUs[id]
		if !ok {
			return Count{}, fmt.Errorf("cpu %d is not an online cpu of this node", id)
		}
		n.Logical++
		cores[c.Core] = true
		switch c.Type {
		case Performance:
			n.Performance++
		case Efficient:
			n.Efficient++
		}
	}
	n.Physical = len(cores)
	return n, nil
}

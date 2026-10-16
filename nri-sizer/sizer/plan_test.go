package sizer_test

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/88plug/k3d-gpu/nri-sizer/hosts"
	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

func envMap(p *sizer.Plan) map[string]string {
	m := map[string]string{}
	for _, kv := range p.Env {
		m[kv.Key] = kv.Value
	}
	return m
}

func TestParseFormatCPUList(t *testing.T) {
	got, err := sizer.ParseCPUList("16-19, 2,0-1,19")
	if err != nil {
		t.Fatal(err)
	}
	if want := []int{0, 1, 2, 16, 17, 18, 19}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
	if s := sizer.FormatCPUList(got); s != "0-2,16-19" {
		t.Fatalf("format = %q", s)
	}
	for _, bad := range []string{"a", "3-1", "-1", "1-", "1,,2"} {
		if _, err := sizer.ParseCPUList(bad); err == nil {
			t.Errorf("ParseCPUList(%q) accepted", bad)
		}
	}
}

// LoadSysfs must read back exactly what the fake sysfs encodes, so the plugin
// and the tests see one topology.
func TestLoadSysfsRoundTrip(t *testing.T) {
	for _, h := range hosts.All {
		want := hosts.Build(h)
		dir := t.TempDir()
		if err := hosts.WriteSysfs(dir, want); err != nil {
			t.Fatal(err)
		}
		got, err := sizer.LoadSysfs(dir)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("%s: sysfs round trip differs", h.Name)
		}
		if got.Hybrid() != h.Hybrid {
			t.Fatalf("%s: Hybrid() = %v", h.Name, got.Hybrid())
		}
	}
}

// AC1: the plan for single-type balloons on every host.
func TestComputeSingleTypeBalloons(t *testing.T) {
	type tc struct {
		host              hosts.Spec
		label             string
		cpus              []int
		physical, logical int
		coreType, ovCore  string
	}
	var cases []tc
	for _, h := range hosts.All {
		topo := hosts.Build(h)
		if !h.Hybrid {
			cases = append(cases, tc{h, "performance", []int{0, 1, 2, 3}, 4, 4, "uniform", "ANY_CORE"})
			cases = append(cases, tc{h, "efficient", []int{0, 16}, 1, 2, "uniform", "ANY_CORE"}) // SMT pair 0,16
			continue
		}
		p := hosts.IDs(topo, sizer.Performance)
		e := hosts.IDs(topo, sizer.Efficient)
		cases = append(cases,
			tc{h, "performance", p[:4], 2, 4, "performance", "PCORE_ONLY"},                         // 2 cores x SMT
			tc{h, "performance", []int{p[0], p[2], p[4], p[6]}, 4, 4, "performance", "PCORE_ONLY"}, // 4 cores, no siblings
			tc{h, "efficient", e[:4], 4, 4, "efficient", "ECORE_ONLY"},
			tc{h, "efficient", e[len(e)-4:], 4, 4, "efficient", "ECORE_ONLY"},
		)
	}
	for _, c := range cases {
		topo := hosts.Build(c.host)
		cpuset := sizer.FormatCPUList(c.cpus)
		p, err := sizer.Compute(topo, sizer.Input{Cpuset: cpuset, Labels: map[string]string{sizer.CoresLabel: c.label}})
		if err != nil || p == nil {
			t.Fatalf("%s %s %s: plan=%v err=%v", c.host.Name, c.label, cpuset, p, err)
		}
		env := envMap(p)
		for _, k := range []string{"OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "LLAMA_ARG_THREADS"} {
			if env[k] != itoa(c.physical) {
				t.Errorf("%s %s: %s=%q want %d", c.host.Name, cpuset, k, env[k], c.physical)
			}
		}
		if env["PYTHON_CPU_COUNT"] != itoa(c.logical) || env["K3D_GPU_CPUS"] != itoa(c.logical) {
			t.Errorf("%s %s: logical env %q/%q want %d", c.host.Name, cpuset, env["PYTHON_CPU_COUNT"], env["K3D_GPU_CPUS"], c.logical)
		}
		if env["K3D_GPU_CPUSET"] != cpuset || env["K3D_GPU_CORE_TYPE"] != c.coreType {
			t.Errorf("%s %s: cpuset/type env %q/%q", c.host.Name, cpuset, env["K3D_GPU_CPUSET"], env["K3D_GPU_CORE_TYPE"])
		}
		var ov sizer.OpenVINO
		if err := json.Unmarshal([]byte(env["K3D_GPU_OV_PLUGIN_CONFIG"]), &ov); err != nil {
			t.Fatal(err)
		}
		if ov.SchedulingCoreType != c.ovCore || ov.InferenceNumThreads != itoa(c.physical) {
			t.Errorf("%s %s: ov config %+v", c.host.Name, cpuset, ov)
		}
		if p.File.Threads != c.physical || p.File.CoreType != c.coreType || p.File.OpenVINO != ov {
			t.Errorf("%s %s: file %+v", c.host.Name, cpuset, p.File)
		}
		if len(p.Env) != len(sizer.EnvKeys) {
			t.Errorf("%s %s: %d env vars, want %d", c.host.Name, cpuset, len(p.Env), len(sizer.EnvKeys))
		}
	}
}

func TestComputeMixedCpusetGetsFileOnly(t *testing.T) {
	topo := hosts.Build(hosts.I9_13900T)
	p, err := sizer.Compute(topo, sizer.Input{Cpuset: "0-1,16-17", Labels: map[string]string{sizer.CoresLabel: "performance"}})
	if err != nil || p == nil {
		t.Fatalf("plan=%v err=%v", p, err)
	}
	if len(p.Env) != 0 {
		t.Fatalf("mixed cpuset got env %v", p.Env)
	}
	f := p.File
	if f.CoreType != "mixed" || f.Threads != 0 || f.OpenVINO.SchedulingCoreType != "ANY_CORE" ||
		f.Performance != 2 || f.Efficient != 2 || f.Physical != 3 || f.Logical != 4 {
		t.Fatalf("file %+v", f)
	}
}

func TestComputeKeepsUserEnv(t *testing.T) {
	topo := hosts.Build(hosts.I9_13900T)
	p, err := sizer.Compute(topo, sizer.Input{
		Cpuset: "16-19",
		Labels: map[string]string{sizer.CoresLabel: "efficient"},
		Env:    map[string]string{"OMP_NUM_THREADS": "7", "LLAMA_ARG_THREADS": "3"},
	})
	if err != nil {
		t.Fatal(err)
	}
	env := envMap(p)
	if _, ok := env["OMP_NUM_THREADS"]; ok {
		t.Error("overrode OMP_NUM_THREADS")
	}
	if _, ok := env["LLAMA_ARG_THREADS"]; ok {
		t.Error("overrode LLAMA_ARG_THREADS")
	}
	if env["MKL_NUM_THREADS"] != "4" {
		t.Errorf("MKL_NUM_THREADS=%q", env["MKL_NUM_THREADS"])
	}
}

func TestComputeSkips(t *testing.T) {
	topo := hosts.Build(hosts.I9_13900T)
	for _, in := range []sizer.Input{
		{Cpuset: "16-19"}, // unlabelled
		{Cpuset: "16-19", Labels: map[string]string{sizer.CoresLabel: "shared"}},
		{Cpuset: "", Labels: map[string]string{sizer.CoresLabel: "efficient"}}, // balloons did not pin
	} {
		p, err := sizer.Compute(topo, in)
		if err != nil || p != nil {
			t.Errorf("%+v: plan=%v err=%v, want nil,nil", in, p, err)
		}
	}
	if _, err := sizer.Compute(topo, sizer.Input{Cpuset: "40-43", Labels: map[string]string{sizer.CoresLabel: "efficient"}}); err == nil {
		t.Error("accepted cpus outside the node")
	}
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

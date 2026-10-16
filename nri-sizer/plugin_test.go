package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/containerd/nri/pkg/adaptation"
	"github.com/containerd/nri/pkg/api"
	"github.com/containerd/nri/pkg/stub"

	"github.com/88plug/k3d-gpu/nri-sizer/hosts"
	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// fakeBalloons stands in for nri-resource-policy-balloons: at CreateContainer
// it assigns a cpuset, exactly as the real policy does through an adjustment.
type fakeBalloons struct {
	cpus string
	live
}

func (b *fakeBalloons) CreateContainer(_ context.Context, _ *api.PodSandbox, _ *api.Container) (*api.ContainerAdjustment, []*api.ContainerUpdate, error) {
	adj := &api.ContainerAdjustment{}
	adj.SetLinuxCPUSetCPUs(b.cpus)
	return adj, nil, nil
}

// live marks a plugin as being in the runtime's invocation list: it has
// received a RunPodSandbox event. Synchronize returning is not enough, the
// runtime only appends the plugin after it.
type live struct{ seen atomic.Bool }

func (l *live) RunPodSandbox(context.Context, *api.PodSandbox) error {
	l.seen.Store(true)
	return nil
}

// livePlugin is the real plugin plus the readiness marker.
type livePlugin struct {
	*plugin
	live
}

// waitLive sends probe pod events until every plugin has received one.
func waitLive(t *testing.T, rt *adaptation.Adaptation, ls ...*live) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for {
		ready := true
		for _, l := range ls {
			ready = ready && l.seen.Load()
		}
		if ready {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("plugins never became live")
		}
		probe := &api.PodSandbox{Id: "probe", Name: "probe", Namespace: "default"}
		if err := rt.RunPodSandbox(context.Background(), &api.StateChangeEvent{Event: api.Event_RUN_POD_SANDBOX, Pod: probe}); err != nil {
			t.Fatal(err)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// runNRI drives the real NRI v0.8.0 runtime adaptation (the version k3s
// v1.34.1 vendors) with the fake balloons plugin and the sizer as external
// plugins over a unix socket, and returns the merged CreateContainer reply.
func runNRI(t *testing.T, sizerIdx string, labels map[string]string, env []string) (*api.CreateContainerResponse, string) {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "nrisz") // short path: unix socket limit
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	sock := filepath.Join(dir, "nri.sock")

	noop := func(context.Context, []*api.ContainerUpdate) ([]*api.ContainerUpdate, error) { return nil, nil }
	syncFn := func(ctx context.Context, cb adaptation.SyncCB) error { _, err := cb(ctx, nil, nil); return err }
	rt, err := adaptation.New("test-runtime", "0.0", syncFn, noop,
		adaptation.WithPluginPath(filepath.Join(dir, "plugins")),
		adaptation.WithPluginConfigPath(filepath.Join(dir, "conf.d")),
		adaptation.WithSocketPath(sock))
	if err != nil {
		t.Fatal(err)
	}
	if err := rt.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(rt.Stop)

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	bal := &fakeBalloons{cpus: "16-19"}
	bs, err := stub.New(bal, stub.WithPluginName("balloons"), stub.WithPluginIdx("90"), stub.WithSocketPath(sock), stub.WithOnClose(func() {}))
	if err != nil {
		t.Fatal(err)
	}
	if err := bs.Start(ctx); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(bs.Stop)

	cpusDir := filepath.Join(dir, "cpus")
	sp := &livePlugin{plugin: &plugin{topo: hosts.Build(hosts.I9_13900T), dir: cpusDir}}
	ss, err := stub.New(sp, stub.WithPluginName("k3d-gpu-sizer"), stub.WithPluginIdx(sizerIdx), stub.WithSocketPath(sock), stub.WithOnClose(func() {}))
	if err != nil {
		t.Fatal(err)
	}
	if err := ss.Start(ctx); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(ss.Stop)
	waitLive(t, rt, &bal.live, &sp.live)

	pod := &api.PodSandbox{Id: "pod0", Name: "llm", Namespace: "default", Uid: "uid0", Labels: labels}
	if err := rt.RunPodSandbox(ctx, &api.StateChangeEvent{Event: api.Event_RUN_POD_SANDBOX, Pod: pod}); err != nil {
		t.Fatal(err)
	}
	// The pod spec itself asks for no cpuset: only balloons sets one.
	ctr := &api.Container{Id: "ctr0", PodSandboxId: "pod0", Name: "server", Env: env,
		Linux: &api.LinuxContainer{Resources: &api.LinuxResources{Cpu: &api.LinuxCPU{}}}}
	rpl, err := rt.CreateContainer(ctx, &api.CreateContainerRequest{Pod: pod, Container: ctr})
	if err != nil {
		t.Fatal(err)
	}
	return rpl, cpusDir
}

func replyEnv(rpl *api.CreateContainerResponse) map[string]string {
	m := map[string]string{}
	for _, kv := range rpl.GetAdjust().GetEnv() {
		m[kv.Key] = kv.Value
	}
	return m
}

// AC2: after balloons (index 90) the sizer (95) sees the balloons cpuset in the
// same CreateContainer pass and sizes the container to it.
func TestNRIOrderingSizerSeesBalloonsCpuset(t *testing.T) {
	rpl, cpusDir := runNRI(t, "95", map[string]string{sizer.CoresLabel: "efficient"}, []string{"PATH=/usr/bin"})
	if got := rpl.GetAdjust().GetLinux().GetResources().GetCpu().GetCpus(); got != "16-19" {
		t.Fatalf("balloons cpuset lost: %q", got)
	}
	env := replyEnv(rpl)
	for k, want := range map[string]string{
		"OMP_NUM_THREADS": "4", "LLAMA_ARG_THREADS": "4", "PYTHON_CPU_COUNT": "4",
		"K3D_GPU_CPUSET": "16-19", "K3D_GPU_CORE_TYPE": "efficient",
	} {
		if env[k] != want {
			t.Errorf("%s=%q want %q (env %v)", k, env[k], want, env)
		}
	}
	var mounted bool
	for _, m := range rpl.GetAdjust().GetMounts() {
		if m.Destination == MountPoint && m.Source == filepath.Join(cpusDir, "ctr0") {
			mounted = true
		}
	}
	if !mounted {
		t.Fatalf("no %s mount in %v", MountPoint, rpl.GetAdjust().GetMounts())
	}
	b, err := os.ReadFile(filepath.Join(cpusDir, "ctr0", "cpus.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f sizer.File
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	if f.Cpuset != "16-19" || f.Threads != 4 || f.OpenVINO.SchedulingCoreType != "ECORE_ONLY" {
		t.Fatalf("cpus.json %+v", f)
	}
}

// Ablation of the ordering: registered BEFORE balloons the sizer sees no
// cpuset and must inject nothing. If it injected anyway it would be reading
// something other than the balloons decision.
func TestNRIOrderingAblationBeforeBalloons(t *testing.T) {
	rpl, _ := runNRI(t, "85", map[string]string{sizer.CoresLabel: "efficient"}, nil)
	for k := range replyEnv(rpl) {
		if strings.HasPrefix(k, "K3D_GPU_") || strings.HasSuffix(k, "_THREADS") {
			t.Fatalf("sizer before balloons injected %s", k)
		}
	}
}

func TestNRIUnlabelledPodUntouched(t *testing.T) {
	rpl, _ := runNRI(t, "95", nil, nil)
	if len(replyEnv(rpl)) != 0 || len(rpl.GetAdjust().GetMounts()) != 0 {
		t.Fatalf("unlabelled pod adjusted: %v", rpl.GetAdjust())
	}
}

// A balloon resize reaches cpus.json; the env stays as created (documented).
func TestRefreshRewritesFileOnResize(t *testing.T) {
	p := &plugin{topo: hosts.Build(hosts.I9_13900T), dir: t.TempDir()}
	pod := &api.PodSandbox{Labels: map[string]string{sizer.CoresLabel: "efficient"}}
	ctr := &api.Container{Id: "c1", Linux: &api.LinuxContainer{Resources: &api.LinuxResources{Cpu: &api.LinuxCPU{Cpus: "16-19"}}}}
	if _, err := p.adjust(pod, ctr); err != nil {
		t.Fatal(err)
	}
	if _, err := p.UpdateContainer(context.Background(), pod, ctr, &api.LinuxResources{Cpu: &api.LinuxCPU{Cpus: "16-23"}}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(p.dir, "c1", "cpus.json"))
	var f sizer.File
	if err := json.Unmarshal(b, &f); err != nil || f.Threads != 8 || f.Cpuset != "16-23" {
		t.Fatalf("after resize: %+v err=%v", f, err)
	}
	// A container the plugin never sized is left alone.
	other := &api.Container{Id: "c2"}
	if _, err := p.UpdateContainer(context.Background(), pod, other, &api.LinuxResources{Cpu: &api.LinuxCPU{Cpus: "16-23"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p.dir, "c2")); err == nil {
		t.Fatal("created state for a container it never sized")
	}
	if err := p.RemoveContainer(context.Background(), pod, ctr); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p.dir, "c1")); err == nil {
		t.Fatal("state not removed")
	}
}

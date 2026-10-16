// Command nri-sizer is an NRI plugin that runs after the balloons resource
// policy (index 90). At CreateContainer it reads the cpuset balloons assigned,
// and for pods labelled k3d-gpu.io/cores=performance|efficient it injects
// thread-count env (OMP/MKL/OPENBLAS/LLAMA_ARG_THREADS, PYTHON_CPU_COUNT, an
// OpenVINO plugin_config) sized to that cpuset. Every pinned container also
// gets /run/k3d-gpu/cpus.json, rewritten when the container's cpuset changes.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/containerd/nri/pkg/api"
	"github.com/containerd/nri/pkg/stub"

	"github.com/88plug/k3d-gpu/nri-sizer/sizer"
)

// MountPoint is where the per-container directory appears inside containers.
const MountPoint = "/run/k3d-gpu"

type plugin struct {
	topo *sizer.Topology
	dir  string // node directory holding one subdirectory per container
}

func envOf(ctr *api.Container) map[string]string {
	m := map[string]string{}
	for _, kv := range ctr.GetEnv() {
		k, v, _ := strings.Cut(kv, "=")
		m[k] = v
	}
	return m
}

func (p *plugin) ctrDir(id string) string { return filepath.Join(p.dir, id) }

// writeFile replaces cpus.json by rename inside the per-container directory.
// The directory, not the file, is bind-mounted, so the container sees the new
// file after a rename.
func (p *plugin) writeFile(id string, f sizer.File) error {
	d := p.ctrDir(id)
	if err := os.MkdirAll(d, 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(d, ".cpus.json.tmp")
	if err := os.WriteFile(tmp, f.JSON(), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(d, "cpus.json"))
}

// adjust computes the adjustment for one container from the cpuset that the
// earlier plugins left in ctr. NRI applies each plugin's cpuset adjustment to
// the request before it calls the next plugin (nri v0.8.0
// pkg/adaptation/result.go adjustResources), so ctr already carries the
// balloons cpuset here.
func (p *plugin) adjust(pod *api.PodSandbox, ctr *api.Container) (*api.ContainerAdjustment, error) {
	plan, err := sizer.Compute(p.topo, sizer.Input{
		Cpuset: ctr.GetLinux().GetResources().GetCpu().GetCpus(),
		Labels: pod.GetLabels(),
		Env:    envOf(ctr),
	})
	if err != nil || plan == nil {
		return nil, err
	}
	if err := p.writeFile(ctr.GetId(), plan.File); err != nil {
		return nil, err
	}
	adj := &api.ContainerAdjustment{}
	for _, kv := range plan.Env {
		adj.AddEnv(kv.Key, kv.Value)
	}
	adj.AddMount(&api.Mount{
		Destination: MountPoint,
		Source:      p.ctrDir(ctr.GetId()),
		Type:        "bind",
		Options:     []string{"rbind", "ro"},
	})
	return adj, nil
}

// refresh rewrites cpus.json for a container this plugin already sized.
func (p *plugin) refresh(pod *api.PodSandbox, ctr *api.Container, cpus string) error {
	if _, err := os.Stat(p.ctrDir(ctr.GetId())); err != nil {
		return nil // not ours
	}
	plan, err := sizer.Compute(p.topo, sizer.Input{Cpuset: cpus, Labels: pod.GetLabels(), Env: envOf(ctr)})
	if err != nil || plan == nil {
		return err
	}
	return p.writeFile(ctr.GetId(), plan.File)
}

func (p *plugin) CreateContainer(_ context.Context, pod *api.PodSandbox, ctr *api.Container) (*api.ContainerAdjustment, []*api.ContainerUpdate, error) {
	adj, err := p.adjust(pod, ctr)
	if err != nil {
		// Never block a container on sizing: log and let it start unsized.
		log.Printf("size %s/%s: %v", pod.GetName(), ctr.GetName(), err)
		return nil, nil, nil
	}
	return adj, nil, nil
}

func (p *plugin) UpdateContainer(_ context.Context, pod *api.PodSandbox, ctr *api.Container, r *api.LinuxResources) ([]*api.ContainerUpdate, error) {
	if cpus := r.GetCpu().GetCpus(); cpus != "" {
		if err := p.refresh(pod, ctr, cpus); err != nil {
			log.Printf("refresh %s/%s: %v", pod.GetName(), ctr.GetName(), err)
		}
	}
	return nil, nil
}

func (p *plugin) PostUpdateContainer(_ context.Context, pod *api.PodSandbox, ctr *api.Container) error {
	if err := p.refresh(pod, ctr, ctr.GetLinux().GetResources().GetCpu().GetCpus()); err != nil {
		log.Printf("refresh %s/%s: %v", pod.GetName(), ctr.GetName(), err)
	}
	return nil
}

func (p *plugin) RemoveContainer(_ context.Context, _ *api.PodSandbox, ctr *api.Container) error {
	if id := ctr.GetId(); id != "" {
		_ = os.RemoveAll(p.ctrDir(id))
	}
	return nil
}

func main() {
	hostRoot := flag.String("host-root", "/", "root under which the node's /sys is mounted")
	dir := flag.String("dir", "/run/k3d-gpu/cpus", "node directory for per-container cpus.json (hostPath at the same path)")
	idx := flag.String("idx", "95", "NRI plugin index; must sort after balloons (90)")
	flag.Parse()

	if n, err := strconv.Atoi(*idx); err != nil || len(*idx) != 2 || n <= 90 {
		log.Fatalf("plugin index %q must be two digits and sort after the balloons policy (90)", *idx)
	}
	topo, err := sizer.LoadSysfs(*hostRoot)
	if err != nil {
		log.Fatal(err)
	}
	p := &plugin{topo: topo, dir: *dir}
	s, err := stub.New(p, stub.WithPluginName("k3d-gpu-sizer"), stub.WithPluginIdx(*idx))
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("nri-sizer: %d cpus, hybrid=%v, index %s\n", len(topo.CPUs), topo.Hybrid(), *idx)
	if err := s.Run(context.Background()); err != nil && !errors.Is(err, context.Canceled) {
		log.Fatal(err)
	}
}

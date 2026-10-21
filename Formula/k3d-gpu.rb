# Homebrew (Linuxbrew) formula. This repo doubles as the tap:
#   brew tap 88plug/k3d-gpu https://github.com/88plug/k3d-gpu
#   brew install 88plug/k3d-gpu/k3d-gpu
# url and sha256 are rewritten by .github/workflows/packages.yml on every release.
class K3dGpu < Formula
  desc "Bootstrap GPU-ready k3d clusters on Docker"
  homepage "https://github.com/88plug/k3d-gpu"
  url "https://github.com/88plug/k3d-gpu/archive/refs/tags/aur-v0.1.74.tar.gz"
  sha256 "f68b4a105bdc8271d38bbcc8a4cebb707d39cfe33a6eff1b133891d720a93b02"
  license "FSL-1.1-ALv2"

  depends_on :linux

  def install
    libexec.install "scripts/k3d-gpu", "scripts/k3d-gpu-mcp"
    pkgshare.install Dir["share/*.yml", "share/telemetry"]
    pkgshare.install "images.lock"
    (pkgshare/"console").install Dir["console/*.py", "console/*.json"]
    (pkgshare/"bench").install "bench/vs.py", "bench/llm_load.py", "bench/competitors.lock", "bench/energy.sh",
                               "bench/agent_trace.py", "bench/agent_trace_gen.py"
    (pkgshare/"bench/apb").install "bench/apb/apb.py", "bench/apb/capacity.py"
    (pkgshare/"oracle").install "oracle/bundle.py", "oracle/project.py", "oracle/validate.py", "oracle/share.py"
    (pkgshare/"oracle/schema").install "oracle/schema/bundle-v1.json"
    # The launcher reads its manifests from K3D_GPU_SHARE (default /usr/share/k3d-gpu);
    # k3d-gpu-mcp runs the launcher next to it in libexec and passes its environment on.
    (bin/"k3d-gpu").write_env_script libexec/"k3d-gpu", K3D_GPU_SHARE: "${K3D_GPU_SHARE:-#{pkgshare}}"
    (bin/"k3d-gpu-mcp").write_env_script libexec/"k3d-gpu-mcp", K3D_GPU_SHARE: "${K3D_GPU_SHARE:-#{pkgshare}}"
  end

  test do
    assert_match "k3d-gpu #{version}", shell_output("#{bin}/k3d-gpu version")
    assert_match "#{pkgshare}/nvidia-device-plugin.yml", shell_output("#{bin}/k3d-gpu help")
    assert_match "/x/nvidia-device-plugin.yml", shell_output("K3D_GPU_SHARE=/x #{bin}/k3d-gpu help")
    assert_path_exists pkgshare/"nvidia-device-plugin.yml"
    assert_path_exists pkgshare/"console/plan.py"
    assert_path_exists bin/"k3d-gpu-mcp"
  end
end

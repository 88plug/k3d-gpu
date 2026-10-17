# Homebrew (Linuxbrew) formula. This repo doubles as the tap:
#   brew tap 88plug/k3d-gpu https://github.com/88plug/k3d-gpu
#   brew install 88plug/k3d-gpu/k3d-gpu
# url and sha256 are rewritten by .github/workflows/packages.yml on every release.
class K3dGpu < Formula
  desc "Bootstrap GPU-ready k3d clusters on Docker"
  homepage "https://github.com/88plug/k3d-gpu"
  url "https://github.com/88plug/k3d-gpu/archive/refs/tags/aur-v0.1.39.tar.gz"
  sha256 "01499e29a1c6006a8d99771be57e048f891859caebf78a845f7e243f669039c1"
  license "FSL-1.1-ALv2"

  depends_on :linux

  def install
    libexec.install "scripts/k3d-gpu"
    pkgshare.install Dir["share/*.yml", "share/telemetry"]
    # Dir[] so a release tarball cut before bench/ shipped still installs.
    (pkgshare/"bench").install Dir["bench/vs.py", "bench/llm_load.py", "bench/competitors.lock"]
    # The launcher reads its manifests from K3D_GPU_SHARE (default /usr/share/k3d-gpu).
    (bin/"k3d-gpu").write_env_script libexec/"k3d-gpu", K3D_GPU_SHARE: "${K3D_GPU_SHARE:-#{pkgshare}}"
  end

  test do
    assert_match "k3d-gpu #{version}", shell_output("#{bin}/k3d-gpu version")
    assert_match "#{pkgshare}/nvidia-device-plugin.yml", shell_output("#{bin}/k3d-gpu help")
    assert_match "/x/nvidia-device-plugin.yml", shell_output("K3D_GPU_SHARE=/x #{bin}/k3d-gpu help")
    assert_path_exists pkgshare/"nvidia-device-plugin.yml"
  end
end

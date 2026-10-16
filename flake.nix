{
  description = "k3d-gpu: accelerator-ready k3d clusters (NVIDIA, Intel GPU/iGPU/NPU/Gaudi, Xeon DSA/IAA/QAT/AMX)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      # k3d-gpu is FSL-1.1-ALv2 (unfree to nixpkgs); allow exactly this package.
      allowK3dGpu = pkg: lib.getName pkg == "k3d-gpu";
      forAllSystems =
        f:
        lib.genAttrs systems (
          system:
          f (
            import nixpkgs {
              inherit system;
              config.allowUnfreePredicate = allowK3dGpu;
            }
          )
        );
    in
    {
      overlays.default = final: _prev: { k3d-gpu = final.callPackage ./packaging/nix/package.nix { }; };

      packages = forAllSystems (pkgs: rec {
        k3d-gpu = pkgs.callPackage ./packaging/nix/package.nix { };
        default = k3d-gpu;
      });

      checks = forAllSystems (pkgs: {
        k3d-gpu = self.packages.${pkgs.stdenv.hostPlatform.system}.k3d-gpu;
        smoke =
          let
            pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.k3d-gpu;
          in
          pkgs.runCommand "k3d-gpu-smoke" { } ''
            ${pkg}/bin/k3d-gpu version | grep -Fx "k3d-gpu ${pkg.version}"
            ${pkg}/bin/k3d-gpu help | grep -F "${pkg}/share/k3d-gpu/nvidia-device-plugin.yml"
            for f in ${./share}/*.yml; do
              cmp "$f" "${pkg}/share/k3d-gpu/$(basename "$f")"
            done
            K3D_GPU_SHARE=/elsewhere ${pkg}/bin/k3d-gpu help | grep -F /elsewhere/nvidia-device-plugin.yml
            touch $out
          '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}

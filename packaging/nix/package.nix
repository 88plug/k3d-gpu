# k3d-gpu for Nix: the launcher plus share/*.yml, laid out like packaging/aur/PKGBUILD.
# Built by ../../flake.nix; also usable with callPackage from any nixpkgs.
{
  lib,
  stdenvNoCC,
  makeWrapper,
  bash,
  k3d,
  kubectl,
}:

let
  root = ../..;
  launcher = root + "/scripts/k3d-gpu";
  # Version comes from the launcher's VERSION="x.y.z" line, which auto-release.yml keeps in
  # lockstep with the AUR pkgver.
  versionLine = lib.findFirst (lib.hasPrefix "VERSION=") (throw "no VERSION= in scripts/k3d-gpu") (
    lib.splitString "\n" (builtins.readFile launcher)
  );
in
stdenvNoCC.mkDerivation {
  pname = "k3d-gpu";
  version = builtins.head (builtins.match ''VERSION="([^"]+)".*'' versionLine);

  src = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      launcher
      (root + "/scripts/k3d-gpu-mcp")
      (lib.fileset.fileFilter (f: f.hasExt "yml") (root + "/share"))
      (lib.fileset.fileFilter (f: f.hasExt "py" || f.hasExt "json") (root + "/console"))
      (root + "/bench/llm_load.py")
      (root + "/bench/energy.sh")
      (root + "/bench/agent_trace.py")
      (root + "/bench/agent_trace_gen.py")
      (root + "/bench/apb/apb.py")
      (root + "/bench/apb/capacity.py")
      (root + "/oracle/bundle.py")
      (root + "/oracle/project.py")
      (root + "/oracle/validate.py")
      (root + "/oracle/share.py")
      (root + "/oracle/schema/bundle-v1.json")
      (root + "/images.lock")
      (root + "/bench/vs.py")
      (root + "/bench/competitors.lock")
      (root + "/integrations/eas")
      (root + "/integrations/eat/upstream/patches")
      (root + "/integrations/eat/eat-check.sh")
      (root + "/README.md")
      (root + "/LICENSE.md")
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  buildInputs = [ bash ]; # patchShebangs rewrites #!/usr/bin/env bash to this bash

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 scripts/k3d-gpu "$out/bin/k3d-gpu"
    install -Dm755 scripts/k3d-gpu-mcp "$out/bin/k3d-gpu-mcp"
    install -Dm644 -t "$out/share/k3d-gpu" share/*.yml
    install -Dm644 -t "$out/share/k3d-gpu/telemetry" share/telemetry/*.yml
    install -Dm644 -t "$out/share/k3d-gpu/telemetry/optional" share/telemetry/optional/*.yml
    install -Dm644 -t "$out/share/k3d-gpu/console" console/*.py console/*.json
    install -Dm644 images.lock "$out/share/k3d-gpu/images.lock"
    install -Dm755 -t "$out/share/k3d-gpu/bench" bench/vs.py bench/llm_load.py bench/energy.sh
    install -Dm644 bench/competitors.lock "$out/share/k3d-gpu/bench/competitors.lock"
    install -Dm644 -t "$out/share/k3d-gpu/bench" bench/agent_trace.py bench/agent_trace_gen.py
    install -Dm644 -t "$out/share/k3d-gpu/bench/apb" bench/apb/apb.py bench/apb/capacity.py
    install -Dm644 -t "$out/share/k3d-gpu/oracle" oracle/bundle.py oracle/project.py oracle/validate.py oracle/share.py
    install -Dm644 -t "$out/share/k3d-gpu/oracle/schema" oracle/schema/bundle-v1.json
    # k3d-gpu eas, its overlay's patch drafts and eat-check.sh, in the repo's integrations/ layout.
    mkdir -p "$out/share/k3d-gpu/integrations"
    cp -r integrations/eas "$out/share/k3d-gpu/integrations/eas"
    install -Dm644 -t "$out/share/k3d-gpu/integrations/eat/upstream/patches" integrations/eat/upstream/patches/*.patch
    install -Dm755 integrations/eat/eat-check.sh "$out/share/k3d-gpu/integrations/eat/eat-check.sh"
    install -Dm644 README.md "$out/share/doc/k3d-gpu/README.md"
    install -Dm644 LICENSE.md "$out/share/licenses/k3d-gpu/LICENSE.md"
    runHook postInstall
  '';

  # The launcher defaults to /usr/share/k3d-gpu; point it at the store copy unless the user
  # overrides K3D_GPU_SHARE. k3d and kubectl are appended to PATH so a host install wins.
  postFixup = ''
    wrapProgram "$out/bin/k3d-gpu" \
      --set-default K3D_GPU_SHARE "$out/share/k3d-gpu" \
      --suffix PATH : ${
        lib.makeBinPath [
          k3d
          kubectl
        ]
      }
  '';

  meta = {
    description = "Bootstrap GPU-ready k3d clusters on Docker";
    homepage = "https://github.com/88plug/k3d-gpu";
    license = {
      spdxId = "FSL-1.1-ALv2";
      fullName = "Functional Source License, Version 1.1, ALv2 Future License";
      url = "https://fsl.software/FSL-1.1-ALv2.template.md";
      # Source-available, not free until the Apache-2.0 conversion date, so nixpkgs
      # treats it as unfree; flake.nix allows exactly this package.
      free = false;
      redistributable = true;
    };
    mainProgram = "k3d-gpu";
    platforms = lib.platforms.linux;
  };
}

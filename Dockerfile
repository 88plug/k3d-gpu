ARG K3S_TAG="v1.34.1-k3s1-amd64"
#https://hub.docker.com/r/rancher/k3s/tags
ARG UBUNTU_TAG="26.04"
#https://hub.docker.com/_/ubuntu/tags  (26.04 = default/latest; 24.04 also built)

FROM rancher/k3s:$K3S_TAG AS k3s

# A k3s NODE image does not need the CUDA toolkit. The node only runs containerd
# + the NVIDIA container runtime; GPU pods get the driver libraries injected from
# the host by nvidia-container-runtime and bring their own CUDA from their
# workload image. So we build on plain ubuntu (not nvidia/cuda) — this tracks the
# newest Ubuntu LTS without waiting for NVIDIA to publish a CUDA base for it, and
# sheds the unused CUDA layer.
FROM ubuntu:$UBUNTU_TAG

# Install the NVIDIA container toolkit (distro-agnostic stable/deb repo — the
# package set is identical regardless of the Ubuntu version underneath).
RUN apt-get update && apt-get install -y curl gnupg ca-certificates \
    && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
    && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      tee /etc/apt/sources.list.d/nvidia-container-toolkit.list \
    && apt-get update && apt-get install -y nvidia-container-toolkit-base nvidia-container-toolkit nvidia-container-runtime util-linux \
    && nvidia-ctk runtime configure --runtime=containerd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=k3s / /
COPY --from=k3s /bin /bin

# Bake the NVIDIA device plugin into k3s's auto-deploy manifests dir. k3s applies
# everything here on startup, so the cluster comes up with GPUs already exposed —
# no `kubectl apply` step needed. Placed before the VOLUME lines so the volume's
# initial contents (copied from the image) include it.
COPY share/nvidia-device-plugin.yml /var/lib/rancher/k3s/server/manifests/nvidia-device-plugin.yaml

VOLUME /var/lib/kubelet
VOLUME /var/lib/rancher/k3s
VOLUME /var/lib/cni
VOLUME /var/log

ENV PATH="$PATH:/bin/aux"

ENTRYPOINT ["/bin/k3s"]
CMD ["agent"]

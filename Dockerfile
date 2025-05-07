ARG K3S_TAG="v1.32.4-k3s1-amd64"
#https://hub.docker.com/r/rancher/k3s/tags
ARG CUDA_TAG="12.9.0-base-ubuntu24.04"
#https://hub.docker.com/r/nvidia/cuda/tags

FROM rancher/k3s:$K3S_TAG AS k3s
FROM nvidia/cuda:$CUDA_TAG

# Install the NVIDIA container toolkit
RUN apt-get update && apt-get install -y curl \
    && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
    && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      tee /etc/apt/sources.list.d/nvidia-container-toolkit.list \
    && apt-get update && apt-get install -y nvidia-container-toolkit-base nvidia-container-toolkit nvidia-container-runtime util-linux \
    && nvidia-ctk runtime configure --runtime=containerd

COPY --from=k3s / / --exclude=/bin/
COPY --from=k3s /bin /bin

VOLUME /var/lib/kubelet
VOLUME /var/lib/rancher/k3s
VOLUME /var/lib/cni
VOLUME /var/log

RUN sysctl -w fs.inotify.max_user_watches=100000
RUN sysctl -w fs.inotify.max_user_instances=100000

ENV PATH="$PATH:/bin/aux"

ENTRYPOINT ["/bin/k3s"]
CMD ["agent"]

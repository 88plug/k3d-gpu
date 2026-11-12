# Bastion for Intel's AI for Enterprise Solutions installer (EAS es_auto_installer.sh),
# which installs Intel's agent toolkit as its `agent-toolkit` layer.
#
# es_auto_installer.sh supports Ubuntu and RHEL only; it creates a venv, runs
# ansible-playbook as a non-root user with sudo, and writes env/<name>/ inside its
# own checkout. Running it here keeps all of that off the host. The container shares
# the host network, so the k3d API (https://0.0.0.0:<port>) and the node IPs are
# reachable exactly as they are from the host.
#
# Runs as the image's own uid-1000 `ubuntu` user with passwordless sudo, like the EAT
# bastion: the installer probes `sudo -n true` before every run (ensure_sudo).
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

# What `es_auto_installer.sh configure` installs on Ubuntu 24.04 (python3, python3-venv),
# plus git (init clones the layer repos), jq (the llm_services role pipes the model
# catalog through it), and what the roles call on the bastion.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sudo git curl ca-certificates openssl \
       python3 python3-venv jq rsync \
    && rm -rf /var/lib/apt/lists/* \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

# yq, kubectl and helm at the versions es_auto_installer.sh v1.2.0-rc.1 pins
# (YQ_VERSION, KUBECTL_VERSION, HELM_VERSION, lines 51-53), from their release
# artifacts by sha256. The installer downloads them with no checksum, and only when
# the command is missing (_install_yq, _install_kubectl, _install_helm), so with
# them here it downloads nothing.
ARG YQ_VERSION=v4.53.2
ARG YQ_SHA256=d56bf5c6819e8e696340c312bd70f849dc1678a7cda9c2ad63eebd906371d56b
ARG KUBECTL_VERSION=v1.34.3
ARG KUBECTL_SHA256=ab60ca5f0fd60c1eb81b52909e67060e3ba0bd27e55a8ac147cbc2172ff14212
ARG HELM_VERSION=v3.20.2
ARG HELM_SHA256=258e830a9e613c8a7a302d6059b4bb3b9758f2f3e1bb8ea0d707ce10a9a72fea
RUN curl -fsSL -o /tmp/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
    && echo "${YQ_SHA256}  /tmp/yq" | sha256sum -c - \
    && install -m 0755 /tmp/yq /usr/local/bin/yq \
    && curl -fsSL -o /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  /tmp/kubectl" | sha256sum -c - \
    && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl \
    && curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && echo "${HELM_SHA256}  /tmp/helm.tgz" | sha256sum -c - \
    && tar -xzf /tmp/helm.tgz -C /usr/local/bin --strip-components=1 linux-amd64/helm \
    && rm -f /tmp/yq /tmp/kubectl /tmp/helm.tgz

USER ubuntu
WORKDIR /home/ubuntu

# Bastion for Intel's Enterprise Agent Toolkit (EAT) brownfield deployment.
#
# EAT's deployer is Ubuntu-only: it apt-installs its tools, writes
# /usr/local/bin, /etc/hosts and ~/.kube/config, and asserts an Ubuntu 22.04+
# deployment machine (core/playbooks/setup-bastion.yml). Running it in this
# container keeps all of that off the host. The container shares the host
# network so the k3d API (https://0.0.0.0:<port>) and the node IP serving the
# ingress are reachable exactly as they are from the host.
#
# Runs as the image's own uid-1000 `ubuntu` user: EAT refuses to run as root and
# its bastion playbook reads /home/<ansible_user>/.kube/config.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

# What EAT's own one-click installer (deploy-agentic-stack.sh install_prereqs)
# installs, plus sudo (EAT calls it internally) and rsync (ansible.posix.synchronize).
RUN apt-get update \
    && apt-get install -y --no-install-recommends sudo git curl wget openssl ca-certificates \
       python3 python3-pip python3-venv jq unzip rsync dnsutils \
    && rm -rf /var/lib/apt/lists/* \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu \
    && printf '[global]\nbreak-system-packages = true\n' > /etc/pip.conf

# setup-bastion.yml runs `pip3 install kubernetes ...` as root against the system
# Python. Ubuntu 24.04 marks it externally managed (PEP 668), so pip refuses
# ("error: externally-managed-environment"). EAT exports PIP_BREAK_SYSTEM_PACKAGES
# but sudo drops it; /etc/pip.conf applies to every user. Throwaway container only.

# Helm v3.14.0, the version setup-bastion.yml asks for, from its release tarball
# by sha256. setup-bastion.yml installs helm by running get-helm-3 from helm's
# `main` branch, but only when /usr/local/bin/helm is missing (`creates:`), so
# with helm already here that floating script is downloaded and never run.
ARG HELM_VERSION=v3.14.0
ARG HELM_SHA256=f43e1c3387de24547506ab05d24e5309c0ce0b228c23bd8aa64e9ec4b8206651
RUN curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && echo "${HELM_SHA256}  /tmp/helm.tgz" | sha256sum -c - \
    && tar -xzf /tmp/helm.tgz -C /usr/local/bin --strip-components=1 linux-amd64/helm \
    && rm /tmp/helm.tgz

USER ubuntu
WORKDIR /home/ubuntu

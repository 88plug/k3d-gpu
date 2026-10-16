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
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

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

USER ubuntu
WORKDIR /home/ubuntu

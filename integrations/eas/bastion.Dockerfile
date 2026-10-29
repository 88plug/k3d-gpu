# Bastion for Intel's Enterprise AI Solutions installer (intel/enterprise-ai-solutions,
# es_auto_installer.sh) on a k3d-gpu cluster in its bring-your-own mode.
#
# Everything the installer's `configure` would install with sudo and apt is baked here
# at a pinned version, so `k3d-gpu eas up` never runs `configure`:
#   - kubectl, helm and yq from their release downloads, each checked by sha256. The
#     sha256 values are the ones the upstream checksum files publish for these releases
#     (dl.k8s.io <file>.sha256, get.helm.sh <file>.sha256sum, yq's `checksums` file).
#   - the installer's Python requirements (requirements.txt at the EAS commit named in
#     requirements.lock) as a hash-locked venv at /opt/eas-venv; the installer reuses a
#     venv that has ansible-playbook (es_auto_installer.sh _ensure_venv).
#   - kubernetes.core 6.3.0 (EAS collections/requirements.yml) by sha256, into that venv's
#     purelib over the 6.2.0 copy the ansible 12.3.0 package bundles, which is where the
#     installer's _ensure_collections looks; with 6.3.0 there it makes no network call.
#
# kubectl is v1.37.2, the minor of the node image's K3S_TAG (Dockerfile): EAS's own
# v1.34.3 is outside kubectl's +/-1 minor skew against a v1.37 API server
# (https://kubernetes.io/releases/version-skew-policy/#kubectl). helm is the newest v3
# (EAS asks for >= v3.20.2); yq the newest v4 (EAS asks for >= v4.53.2).
#
# Runs as root: es_auto_installer.sh ensure_sudo runs `sudo -k` under `set -e` unless
# EUID is 0, and this image has no sudo. eas.sh hands the state dir back to the
# invoking user's uid:gid when it exits.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive

# The state dir (the EAS checkout and its ext/ clones) belongs to the invoking user, and
# git refuses a repository another uid owns ("detected dubious ownership") unless told.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

ARG KUBECTL_VERSION=v1.37.2
ARG KUBECTL_SHA256=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
ARG HELM_VERSION=v3.22.1
ARG HELM_SHA256=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
ARG YQ_VERSION=v4.54.1
ARG YQ_SHA256=8e34fc298390875de416e6a4afcb8cabeceb25d9aa8506c1a2f9353cf702ea5f
RUN curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
    && curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && echo "${HELM_SHA256}  /tmp/helm.tgz" | sha256sum -c - \
    && tar -xzf /tmp/helm.tgz -C /usr/local/bin --strip-components=1 linux-amd64/helm \
    && curl -fsSL -o /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
    && echo "${YQ_SHA256}  /usr/local/bin/yq" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/kubectl /usr/local/bin/helm /usr/local/bin/yq \
    && rm /tmp/helm.tgz

COPY requirements.lock /opt/eas-requirements.lock
RUN python3 -m venv /opt/eas-venv \
    && /opt/eas-venv/bin/pip install --no-cache-dir --disable-pip-version-check --require-hashes --no-deps \
       -r /opt/eas-requirements.lock

ARG KUBERNETES_CORE_VERSION=6.3.0
ARG KUBERNETES_CORE_SHA256=86a2a89892b75120a32e58135ab96982e3c02ad9f6b59eb2412671952622e748
RUN curl -fsSL -o /tmp/kubernetes-core.tar.gz \
       "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/artifacts/kubernetes-core-${KUBERNETES_CORE_VERSION}.tar.gz" \
    && echo "${KUBERNETES_CORE_SHA256}  /tmp/kubernetes-core.tar.gz" | sha256sum -c - \
    && purelib=$(/opt/eas-venv/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') \
    && /opt/eas-venv/bin/ansible-galaxy collection install --force --offline -p "${purelib}" /tmp/kubernetes-core.tar.gz \
    && rm /tmp/kubernetes-core.tar.gz

WORKDIR /eas-state

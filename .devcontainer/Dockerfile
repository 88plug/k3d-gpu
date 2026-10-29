# Toolchain for working on k3d-gpu: every static validator `make check` runs.
# Each tool is pinned to an exact release and verified against its sha256.
FROM mcr.microsoft.com/devcontainers/base:2.2.5-trixie@sha256:71b6fe7c4e07ec747a94a72b2a17f2f8c286eaa7b1c59862888df764efb8b6c6

ARG SHELLCHECK_VERSION=0.11.0
ARG HADOLINT_VERSION=2.15.1
ARG KUBECONFORM_VERSION=0.8.0
ARG ACTIONLINT_VERSION=1.7.12
ARG HELM_VERSION=4.3.1
ARG TARGETARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# python3-yaml runs kubeconform's openapi2jsonschema (CRD -> JSON schema).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-yaml=6.0.2-1+b2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/tools
RUN case "${TARGETARCH:-amd64}" in \
      amd64) sc_arch=x86_64;  hl_arch=x86_64; \
             sc_sum=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198; \
             hl_sum=c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507; \
             kc_sum=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883; \
             al_sum=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8; \
             hm_sum=0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b ;; \
      arm64) sc_arch=aarch64; hl_arch=arm64; \
             sc_sum=12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588; \
             hl_sum=f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d; \
             kc_sum=1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87; \
             al_sum=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6; \
             hm_sum=8cf8f66ecbc252ac8f7f06cca68aba88508ddfde3a6a34feb1678ad1694b4f88 ;; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && arch="${TARGETARCH:-amd64}" \
    && rel=https://github.com \
    && curl -fsSLo sc.tar.xz "${rel}/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.${sc_arch}.tar.xz" \
    && curl -fsSLo hadolint "${rel}/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-${hl_arch}" \
    && curl -fsSLo kc.tar.gz "${rel}/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-${arch}.tar.gz" \
    && curl -fsSLo al.tar.gz "${rel}/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_${arch}.tar.gz" \
    && curl -fsSLo helm.tar.gz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${arch}.tar.gz" \
    && curl -fsSLo openapi2jsonschema "https://raw.githubusercontent.com/yannh/kubeconform/v${KUBECONFORM_VERSION}/scripts/openapi2jsonschema.py" \
    && printf '%s  %s\n' \
         "${sc_sum}" sc.tar.xz \
         "${hl_sum}" hadolint \
         "${kc_sum}" kc.tar.gz \
         "${al_sum}" al.tar.gz \
         "${hm_sum}" helm.tar.gz \
         d145babfbb765004030764e1b4e518bfb7a4bd7f111691a08fa57983b81881f3 openapi2jsonschema \
       | sha256sum -c - \
    && tar -xJf sc.tar.xz --strip-components=1 "shellcheck-v${SHELLCHECK_VERSION}/shellcheck" \
    && tar -xzf kc.tar.gz kubeconform \
    && tar -xzf al.tar.gz actionlint \
    && tar -xzf helm.tar.gz --strip-components=1 "linux-${arch}/helm" \
    && install -m 0755 shellcheck hadolint kubeconform actionlint helm openapi2jsonschema /usr/local/bin/ \
    && rm -rf /tmp/tools

WORKDIR /workspaces/k3d-gpu
# vscode (uid 1000 in the base image); numeric so the host can resolve it.
USER 1000

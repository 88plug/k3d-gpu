# The QEMU that runs H62's two KVM guests on crescent (EXPERIMENTS.md "H62 pre-registration",
# deviation D3), in a container so the host gets no package: Ubuntu 26.04's qemu-system-x86 (QEMU
# 10.2, which passes AMX through to a guest from QEMU 7.0 on), qemu-img and genisoimage for the
# cloud-init seed. Every version is a pin of bench/h62/h62.py (PINS); bench/h62/vm.sh builds it.
FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        qemu-system-x86=1:10.2.1+ds-1ubuntu3.3 \
        qemu-utils=1:10.2.1+ds-1ubuntu3.3 \
        genisoimage=9:1.1.11-5 \
    && dpkg-query -W > /h62-packages.txt \
    && rm -rf /var/lib/apt/lists/*

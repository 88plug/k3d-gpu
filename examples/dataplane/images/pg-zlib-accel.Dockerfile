# PostgreSQL 18 client/server tools (pgvector image) plus Intel zlib-accel, a zlib
# shim that offloads whole-buffer deflate/inflate to QAT (via QATzip) and IAA (via
# QPL). Used by the pg_dump --compress=gzip CronJobs in pgvector-backup.yaml;
# zlib-accel's README lists "pg_dump / pg_restore with gzip" as a tested use case.
#
#   docker build -f examples/dataplane/images/pg-zlib-accel.Dockerfile \
#       -t k3d-gpu/pg-zlib-accel:18-zla1.1.0 examples/dataplane/images
#   k3d image import -c <cluster> k3d-gpu/pg-zlib-accel:18-zla1.1.0
#
# Debian trixie packages qatlib 24.02.0, QATzip 1.3.1 (zlib-accel needs >= 1.3.0),
# oneTBB 2022.1 and accel-config 4.1.8; QPL is not packaged, so it is built here.

ARG PGVECTOR_IMAGE=pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0
ARG QPL_TAG=v1.9.0
ARG ZLIB_ACCEL_TAG=v1.1.0

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS build
ARG QPL_TAG
ARG ZLIB_ACCEL_TAG
# The accelerator libraries are pinned to trixie's versions; the toolchain is not.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates cmake g++ git make nasm zlib1g-dev \
        libqatzip-dev=1.3.1-2 libqat-dev=24.02.0-1+b1 libtbb-dev=2022.1.0-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# QPL: shared library, no tests/examples (their submodules are not needed).
# libaccel-config is dlopen()ed at run time (QPL default), so it is a runtime dep only.
RUN git clone --depth 1 -b "${QPL_TAG}" https://github.com/intel/qpl \
    && cmake -S qpl -B qpl/build -DCMAKE_BUILD_TYPE=Release \
        -DQPL_LIBRARY_TYPE=SHARED -DQPL_BUILD_TESTS=OFF -DQPL_BUILD_EXAMPLES=OFF \
        -DCMAKE_INSTALL_PREFIX=/opt/qpl \
    && cmake --build qpl/build -j"$(nproc)" --target install
RUN git clone --depth 1 -b "${ZLIB_ACCEL_TAG}" https://github.com/intel/zlib-accel \
    && cmake -S zlib-accel -B zlib-accel/build -DCMAKE_BUILD_TYPE=Release \
        -DUSE_QAT=ON -DUSE_IAA=ON -DQPL_PATH=/opt/qpl -DDEBUG_LOG=ON -DCOVERAGE=OFF \
    && cmake --build zlib-accel/build -j"$(nproc)"

FROM ${PGVECTOR_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libaccel-config1=4.1.8-1 libqat4=24.02.0-1+b1 libqatzip3=1.3.1-2 \
        libtbb12=2022.1.0-1+deb13u1 libusdm0=24.02.0-1+b1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/qpl/lib/libqpl.so* /usr/local/lib/
COPY --from=build /src/zlib-accel/build/libzlib-accel.so* /usr/local/lib/
RUN ldconfig

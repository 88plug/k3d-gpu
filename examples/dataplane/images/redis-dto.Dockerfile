# Redis with Intel DTO (DSA Transparent Offload) preloaded through dto-launch.
#
# Build ON the Sapphire Rapids (or newer) target host: DTO's CMake adds
# -march=native when the compiler supports -mwaitpkg, so a binary built on a
# different CPU can use instructions the target lacks.
#
#   docker build -f examples/dataplane/images/redis-dto.Dockerfile \
#       -t k3d-gpu/redis-dto:8.10.2-dto-fbf41a6 examples/dataplane/images
#   k3d image import -c <cluster> k3d-gpu/redis-dto:8.10.2-dto-fbf41a6
#
# redis:8.10.2 is Debian trixie (docker-library/official-images library/redis),
# so libdto is built on trixie too and links against the same glibc.

ARG REDIS_IMAGE=redis:8.12.0@sha256:56a1737794e36c3ebe8a4544a8ab421af3ab1a6488fb2238aec228c404933fed
# intel/DTO has no release after v1.1 (2024-07); this main commit carries the
# CMake build and the DTO_ACCEL_CONFIG_SUPPORT / DTO_NUMA_SUPPORT switches.
ARG DTO_COMMIT=fbf41a67e3719a1c9a8692b6952afe0d88043c76

FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0 AS build
ARG DTO_COMMIT
# Toolchain only; the source is pinned to DTO_COMMIT.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates cmake gcc git libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init dto && git -C dto fetch --depth 1 https://github.com/intel/DTO "${DTO_COMMIT}" \
    && git -C dto checkout FETCH_HEAD
# No accel-config / libnuma: the queue list comes from dto-launch (DTO_WQ_LIST).
RUN cmake -S dto -B dto/build -DCMAKE_BUILD_TYPE=Release \
        -DDTO_ACCEL_CONFIG_SUPPORT=OFF -DDTO_NUMA_SUPPORT=OFF -DDTO_BUILD_TESTS=OFF \
    && cmake --build dto/build -j"$(nproc)" \
    && cmake --install dto/build --prefix /opt/dto

FROM ${REDIS_IMAGE}
COPY --from=build /opt/dto/lib/libdto.so* /usr/local/lib/
COPY dto-launch.sh /usr/local/bin/dto-launch
RUN chmod 0755 /usr/local/bin/dto-launch && ldconfig
ENTRYPOINT ["/usr/local/bin/dto-launch"]
CMD ["redis-server"]

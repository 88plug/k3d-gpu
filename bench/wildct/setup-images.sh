#!/usr/bin/env bash
# wildct setup image pulls, each pinned by digest (the refs the rows' runners use).
set -u
for i in openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 \
  ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d7f4449e2ae0d02b40e0431f7c212dcf231c9e3a7777a6afc2cc8f312 \
  ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375acf22d45551b0650af7e96361ad1a3abc65ad2bde215f23488da2711e \
  ghcr.io/colinianking/stress-ng:72a9d0cae0f420a887991d4024d3101c4af7a4fc@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe \
  python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb; do
  nice -n 10 docker pull -q "$i" || echo "PULL FAILED $i"
done
echo PULL-DONE; df -BG / | tail -1

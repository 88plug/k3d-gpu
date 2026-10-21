#!/usr/bin/env bash
# wildct setup downloads (EXPERIMENTS.md "Wildct pre-registration", W6), at the original rows' commits.
set -euo pipefail
W=~/k3dgpu-bench-wildct; P=$W/hfvenv/bin/python; R=$W/repo
export HF_HUB_DISABLE_TELEMETRY=1
nice -n 10 $P $R/bench/xhost/fetch_models.py $W/models OpenVINO/Qwen3-4B-int4-ov@b467368d16b75df14055562fe927ae8e1f15f7ef OpenVINO/Qwen3-0.6B-int4-ov@f864c6106efb6c7f7b4ef274a78a98e37210dddd --draft OpenVINO/Qwen3-4B-int4-ov=OpenVINO/Qwen3-0.6B-int4-ov
M=$W/h57/models
nice -n 10 $W/hfvenv/bin/hf download google/gemma-4-26B-A4B-it-qat-q4_0-gguf gemma-4-26B_q4_0-it.gguf --revision d1c082be9cf3c8a514acf63b8761f4b41935842e --local-dir $M/google/gemma-4-26B-A4B-it-qat-q4_0-gguf
nice -n 10 $W/hfvenv/bin/hf download OpenVINO/gemma-4-26b-a4b-it-int4-ov --revision 4e5885c3f03844f5887982def09708bdc9f53f7a --local-dir $M/OpenVINO/gemma-4-26b-a4b-it-int4-ov
nice -n 10 $W/hfvenv/bin/hf download google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant --revision 9537141506fe8875b3ed45b264af13580cb29166 --local-dir $M/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant
echo d1c082be9cf3c8a514acf63b8761f4b41935842e > $M/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/.pinned-revision
echo 4e5885c3f03844f5887982def09708bdc9f53f7a > $M/OpenVINO/gemma-4-26b-a4b-it-int4-ov/.pinned-revision
echo 9537141506fe8875b3ed45b264af13580cb29166 > $M/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant/.pinned-revision
cd $M; ln -sfn google/gemma-4-26B-A4B-it-qat-q4_0-gguf google_gemma-4-26B-A4B-it-qat-q4_0-gguf
ln -sfn google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant google_gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant
$P $R/bench/wildct/verify_models.py $W/models OpenVINO/Qwen3-4B-int4-ov@b467368d16b75df14055562fe927ae8e1f15f7ef OpenVINO/Qwen3-0.6B-int4-ov@f864c6106efb6c7f7b4ef274a78a98e37210dddd
$P $R/bench/wildct/verify_models.py $M --skip gemma-4-26B-it-mmproj.gguf google/gemma-4-26B-A4B-it-qat-q4_0-gguf@d1c082be9cf3c8a514acf63b8761f4b41935842e OpenVINO/gemma-4-26b-a4b-it-int4-ov@4e5885c3f03844f5887982def09708bdc9f53f7a google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant@9537141506fe8875b3ed45b264af13580cb29166
echo DL-DONE; df -BG / | tail -1

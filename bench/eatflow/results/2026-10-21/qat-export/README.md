# A1's model for H50/H50o: the Gemma 4 26B-A4B QAT IR with the grid snap (amendment M1)

Reproduce: on crescent, k3d-gpu commit `35b419ce` (`commit.txt`), under `~/k3dgpu-bench.lock`:

    OV_VENV=~/k3dgpu-gemma/ovenv QAT_KEEP_UNSNAPPED=~/k3dgpu-gemma/models/k3dgpu/unsnapped-lm \
    EXPORT_NOTE="..." bash bench/models/export_qat_ov.sh \
        ~/k3dgpu-gemma/models/google_gemma-4-26B-A4B-it-qat-q4_0-unquantized \
        google/gemma-4-26B-A4B-it-qat-q4_0-unquantized f1e06dc520982d9b9edd76859fdb7ab209449949 \
        ~/k3dgpu-gemma/models/k3dgpu_gemma-4-26B-A4B-it-qat-int4-sym-g32-ov image-text-to-text

Pins (`manifest.json` `pins`): openvino 2026.4.1, nncf 3.4.0, optimum-intel 2.2.0, optimum 2.3.0,
torch 2.14.1+cpu, transformers 5.5.4. transformers is below the latest (5.20.2): optimum-intel
2.2.0 caps it. Task `image-text-to-text`: optimum-intel registers `gemma4` for that task only.
Source weights verified against the Hub's sha256 by `bench/eatflow/crescent/models.sh gemma4`.

Export: 339 s; NNCF put 325 of 358 language-model weights at int4_sym group 32 (the rest float).
The IR is 17 GB. `manifest.json` holds the sha256 of all 26 files.

QAT grid (`bench/models/qat_grid.py`, rel-RMS error of each INT4 weight against the checkpoint):

| kind | weights | as exported (median / max) | after the snap (median / max) |
|---|---|---|---|
| attention | 115 | 0.0514 / 0.0541 | 0.0019 / 0.0019 |
| experts | 90 | 0.0797 / 0.0798 | 0.0019 / 0.0019 |
| other | 120 | 0.0784 / 0.0796 | 0.0019 / 0.0019 |

325 of 325 matched and snapped, none skipped (`snap-openvino_language_model.json` has each
tensor's pre-snap error and its snapped error against the export and the checkpoint). The first
export attempt matched 30 of 325 and the gate stopped it before the rename (NNCF's
`._openvino_orig_weight` and fused-expert names; fixed in #179).

The embeddings and vision models carry no INT4 weight (`grid-*-export.json`: 0 matched, 0
skipped), so they are not snapped.

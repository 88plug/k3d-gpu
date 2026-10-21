# Ledger stub for the launcher tests (tests/model_opt_test.sh, detect_test.sh, autoquant_test.sh)

Verdicts and hardware classes as in EXPERIMENTS.md: H34 SUPPORTED on the
i9-13900T, its replication H34x REFUTED on the Xeon 6741P; H27 REFUTED on the
i9-13900T, H27x REFUTED on the Xeon 6741P; H54q pre-registered, PENDING.

## Hypotheses

| H27 | A <=60 s on-box probe picks the weight precision for agents better than the static int4 default, on an i9-13900T. | predictions | method | REFUTED |
| H27x | H27's hypothesis on the Xeon 6741P (Granite Rapids, AMX). | predictions | method | REFUTED |
| H34 | Same-engine CPU Fast Draft beats the target alone. | predictions | Result ([H34 result](#h34-result-i9-13900t-2026-10-17)) | SUPPORTED |
| H34x | H34's hypothesis on the Xeon 6741P. | predictions | method | REFUTED |
| H54q | QAT matters at int4 on this model: the QAT-matched OpenVINO IR OVQ is non-inferior to the bf16 QAT reference and the published base-model IR scores below it. | predictions | method | PENDING |

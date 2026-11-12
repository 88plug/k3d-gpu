# H59r preparation records (crescent, 2026-11-12)

- `drafter/`: prep.sh's L render with no cluster (`run.py prep-render`): `agent.yaml`, `agent.err`
  and the conversion Job it ran once with docker (`job.json`, Job `gguf-convert-9fc347f23770`,
  `full-b12110`, the pip set `transformers==5.22.2 huggingface-hub==2.4.0 tokenizers==0.23.3
  safetensors==0.8.0`). On crescent the Job's script was `convert.sh`. It is kept here as
  `convert.sh.txt` so the repository's shellcheck does not read a function dump with no shebang
  as a script. It is byte-identical to the `convert.sh` key of ConfigMap `gguf-convert` in
  `agent.yaml` (sha256 `3ec0f1ab...`, the script H59's prep ran).
- `drafter-built/`: what the Job left beside the drafter GGUF: `manifest.json` (source, convert
  arguments, quantize type, sha256, bytes), `pip-freeze.txt` and `prep-job.json` (the Job spec
  every hold's render was compared with). The drafter is H59's file: sha256 `f6f80c36...`,
  321,145,312 bytes.
- `../prep.log`: the one prep run, at main `3eda0c9c`, from 01:19:51Z to 01:26:59Z.
- `../weights.sha256`: the sha256 of every file the arms read. Every line equals H59's
  (`bench/h59/results/2026-11-01/weights.sha256`).

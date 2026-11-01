# H59 preparation records (crescent, 2026-11-01)

- `drafter/`: prep.sh's L render with no cluster (`run.py prep-render`): `agent.yaml`, `agent.err`
  and the conversion Job it ran once with docker (`job.json`). On crescent the Job's script was
  `convert.sh`. It is kept here as `convert.sh.txt` so the repository's shellcheck does not read
  a function dump with no shebang as a script. It is byte-identical to the `convert.sh` key of
  ConfigMap `gguf-convert` in `agent.yaml` (sha256 `3ec0f1ab...`).
- `drafter-built/`: what the Job left beside the drafter GGUF: `manifest.json` (source, convert
  arguments, quantize type, sha256, bytes), `pip-freeze.txt` and `prep-job.json` (the Job spec
  every hold's render was compared with).
- `../prep-1.log`: the first run at main `2da9d56a`, which stopped at the model check that
  amendment R3 fixes. `../prep-2.log`: the second run at `3ab406a8`.
- `../weights.sha256`: the sha256 of every file the arms read.

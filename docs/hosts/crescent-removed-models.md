# Crescent: removed models

Model weights removed from crescent's root filesystem (`/`, ext4 on the 3.8 TB
Micron 7450 at `/dev/nvme0n1p1`) to free space. Each entry names the exact
Hugging Face revision, lists every file with its size and hash, and gives the
command that puts it back. The record is pushed before the files are deleted.

## Qwen/Qwen3.8-2.4T-A95B-FP8

| Field | Value |
|---|---|
| Path on crescent | `/home/devcloud/models/Qwen3.8-2.4T-A95B-FP8` |
| Repository | [Qwen/Qwen3.8-2.4T-A95B-FP8](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B-FP8) |
| Revision | `d2dc35658bcf77e66643428cb52e774cc3b5bd29`, the `main` sha on 2026-10-29 (upstream last modified 2026-08-12) |
| Access | Public, not gated. An anonymous download of a shard at this revision works. |
| Files | 224: 213 safetensors shards, `model.safetensors.index.json`, tokenizer, config, license and readme files. The directory also held `.cache/huggingface/` download metadata (451 small files). |
| Size | 2,496,154,395,007 bytes (2.27 TiB). `du` showed 2.3T. |
| Layout | `huggingface_hub` local-dir layout: plain files, no `blobs/` or `snapshots/`, no hard links, no symlinks in or out. |
| Downloaded | 2026-08-13 22:10Z to 2026-08-14 03:36Z by `devcloud` with `snapshot_download` from `~/venvs/hf`, unauthenticated, for the openrouter-battlemage-b70 campaign (its SCORECARD-2026-08-13 lists it as a background download). Log: `~/logs/downloads/qwen3.8-max-fp8.log`. |
| Removed | 2026-10-29, while holding `~/k3dgpu-bench.lock`, between ledger holds. |

Why it was removed:

- `/` was 95.04 percent full (`df` rounds this to 96), above the 95 start gate
  of the cross-host holds. This directory was the largest single consumer.
- Nothing used it. No open file, process mount namespace, container mount or
  command, systemd unit, cron job, symlink, launcher config, or k3d-gpu branch
  named it. Every shard's atime predated its mtime, so no weight byte had been
  read since the download.
- Crescent cannot serve it: 2.5 TB of FP8 weights against 503 GB of RAM and
  4 x 32 GB of Arc Pro B70 memory.
- The 2026-10-20 cleanup kept it only because its name matched the `Qwen3.8-*`
  keep rule, not because of a reference. That decision is in
  `~/models-pruned-manifest-2026-10-20.json` on crescent; the removal is in
  `~/models-pruned-manifest-2026-10-29.json` next to it.

### Restore

```sh
hf download Qwen/Qwen3.8-2.4T-A95B-FP8 \
  --revision d2dc35658bcf77e66643428cb52e774cc3b5bd29 \
  --local-dir /home/devcloud/models/Qwen3.8-2.4T-A95B-FP8
```

`hf` is huggingface_hub 1.27.0 in `~/venvs/hf` on crescent. The first download
took 5 h 26 min. It needs 2.5 TB free on the target filesystem.

### Files

Each row was checked on 2026-10-29 against the files on crescent (name and
size) and against the Hugging Face tree API at this revision
(`/api/models/Qwen/Qwen3.8-2.4T-A95B-FP8/tree/<revision>?recursive=true`: size,
`lfs.oid` and `oid`). For LFS files the hash is the SHA-256 of the file, so
`sha256sum <file>` must print it. For the other files it is the git blob id,
so `git hash-object <file>` must print it.

| File | Bytes | Hash | Value |
|---|---:|---|---|
| `.gitattributes` | 1,635 | git blob | `aa7aacd0134a92c3c1943fdecc75cd8b7420cce6` |
| `LICENSE` | 3,390 | git blob | `41147e54a8d297203446bff7c600114d45d43676` |
| `README.md` | 35,980 | git blob | `192cc42f1bf3e92def9ca3cc81aa40c0fd70b965` |
| `chat_template.jinja` | 7,495 | git blob | `7015404dca3780915f21386e82d4782db219c653` |
| `config.json` | 55,622 | git blob | `f5dd0be634b7dcccbc3c87aa90870c24740a9694` |
| `generation_config.json` | 202 | git blob | `023756cfadf88e5bf69eefeee3e172f38c448d64` |
| `merges.txt` | 3,353,259 | git blob | `a494e019ca1502219fd0128658b979e5f05ae8e8` |
| `model-00001-of-00213.safetensors` | 17,182,224,624 | sha256 | `7cf60543433eeb13659f604d9a0abe6f34e5b3c164cb9ccda5a209a6eb45ca36` |
| `model-00002-of-00213.safetensors` | 8,591,112,304 | sha256 | `503b8fcf18178c3c7e730e39f1b83ea068691fdb656952ed097d6945382a931c` |
| `model-00003-of-00213.safetensors` | 17,182,224,624 | sha256 | `b47a21679b062297fdc9da5d5c7fa6183cd8e99cc0c402910c3401194006ffe3` |
| `model-00004-of-00213.safetensors` | 8,591,112,304 | sha256 | `895b8eafede58aecc60064c015537d1b4d5e3b479309a61823d9665edf549201` |
| `model-00005-of-00213.safetensors` | 17,182,226,672 | sha256 | `06c4fbf720f30bac89014f6a006497981cf6cf1d84063486a9250447908ab4a4` |
| `model-00006-of-00213.safetensors` | 8,591,113,328 | sha256 | `a8a4c03ce66f40bdbfff28cff14b6f4e274e7fbec999602fc0b0f39ff7c53ad9` |
| `model-00007-of-00213.safetensors` | 17,182,224,624 | sha256 | `d3ab97f43a3f4949c918ad61ac3648d83049bc65af0241cf3621c836d1b15325` |
| `model-00008-of-00213.safetensors` | 8,591,112,304 | sha256 | `e1cdafbc802668dd87fa5d38136d0e43aae561d901d5a2da0fb9950d35fbb849` |
| `model-00009-of-00213.safetensors` | 17,182,224,624 | sha256 | `141c3264ca34417d8771cf91bca710757a9bff0160b88fcd638b2125ba5188f7` |
| `model-00010-of-00213.safetensors` | 8,591,112,304 | sha256 | `7d513199875763618983aeeca3bf1ab1374c4928f4aa3a0c9061765706489a51` |
| `model-00011-of-00213.safetensors` | 3,985,468,096 | sha256 | `e4f2593e0df42d7e6ade46f83ef35add352e6ffd1db3643fdd675e345760caeb` |
| `model-00012-of-00213.safetensors` | 17,182,224,624 | sha256 | `b1745c7443303625ecb9b9c4ad6f6b7b95bc18c1a23f2a5423d5c1eccaa784e4` |
| `model-00013-of-00213.safetensors` | 8,591,112,304 | sha256 | `08908c79000ff78da9c54516702c4d4b756f2d433f96cc3d9613f553e02c8465` |
| `model-00014-of-00213.safetensors` | 17,182,224,624 | sha256 | `eef877137b8b5f08abef9f6eb5d699e2c0c35d9adea1171f2315b4d9c6f2169c` |
| `model-00015-of-00213.safetensors` | 8,591,112,304 | sha256 | `95ef37f9d2722fce2d320878d0e1b6526e32417805f8918aac03e1cd0338bf15` |
| `model-00016-of-00213.safetensors` | 17,182,224,624 | sha256 | `4b10f2c4945a389c6cdb526122b73f32cdf65c32dab833675635509fffbadc2e` |
| `model-00017-of-00213.safetensors` | 8,591,112,304 | sha256 | `b680033fc018a2a4a9d4870cdad1673a8990545c7c4bb9d38aee343eda002ab4` |
| `model-00018-of-00213.safetensors` | 17,182,224,624 | sha256 | `a72b7b27ad85accc6a2140674b205e408a714e463d97976c4248b80cb96dd476` |
| `model-00019-of-00213.safetensors` | 8,591,112,304 | sha256 | `a04764369ed9bfdc26760f16b9f51300e03d496e1f0304401276e411e6d91b9f` |
| `model-00020-of-00213.safetensors` | 3,972,721,896 | sha256 | `b3ce2f6b1770e6fab35732a7a4a394bebec37d2cc41ff4c8fb166ce5c8d46ee3` |
| `model-00021-of-00213.safetensors` | 17,182,224,624 | sha256 | `c83ce1fc6f7992a7e6c77afd35fc0cee4c99bd018bf71d0761d83a5d394f195d` |
| `model-00022-of-00213.safetensors` | 8,591,112,304 | sha256 | `f24d056123ad0ee7c710c0f717e6d39218037ae332278594f42d81e9a4131981` |
| `model-00023-of-00213.safetensors` | 17,182,224,624 | sha256 | `431c6f6c8d2af79cce890c0dad2dfe40683c8d3bc0213d45b0d0b43c96a1e3cf` |
| `model-00024-of-00213.safetensors` | 8,591,112,304 | sha256 | `88b64603b057b1ba87c7eac589fc89ea31ed237ea0053dcd0c619b7a567ab557` |
| `model-00025-of-00213.safetensors` | 4,068,474,992 | sha256 | `476b889b4fec9e023e53a679dcf44148837a78d4dc230f08b3dcdc380b1e9757` |
| `model-00026-of-00213.safetensors` | 2,810,632,544 | sha256 | `8d7c651101361889a569ce2ff8e45f1efe8fe5e098fa480029ad65afda2adff9` |
| `model-00027-of-00213.safetensors` | 17,182,226,672 | sha256 | `3ab45832069adb0d1bc0003fb2a9c34054c246536b15c0ea3d77e21994511b36` |
| `model-00028-of-00213.safetensors` | 8,591,113,328 | sha256 | `ae1e3847397c7d8fc20a144816d197c66ec5e44246d046bdea55d11a558af3e6` |
| `model-00029-of-00213.safetensors` | 17,182,226,672 | sha256 | `095e85e036df350f42ae3e086864201482e595ab48b1b4e932e67b559289e743` |
| `model-00030-of-00213.safetensors` | 8,591,113,328 | sha256 | `65d2895e2e2e15df4ddf55ab457f4387d71e63415fd2e674768f6ff0f84e85b2` |
| `model-00031-of-00213.safetensors` | 17,182,226,672 | sha256 | `727334f7c83d32be949de6914b292fee6667e984932cad6008b481cb096ccbdd` |
| `model-00032-of-00213.safetensors` | 8,591,113,328 | sha256 | `d0e3ae45eadc070136fc56f558deb6d8824d104501e894e2f274896591d5a7ab` |
| `model-00033-of-00213.safetensors` | 17,182,226,672 | sha256 | `c12235432960df9e06303d1fe30ef2c25b3603cc1ff17051158f2c664bf00cae` |
| `model-00034-of-00213.safetensors` | 8,591,113,328 | sha256 | `14060da398d2e1719d94759d411de048a10150d07e3bb56232dff5986bd898a4` |
| `model-00035-of-00213.safetensors` | 17,182,226,672 | sha256 | `5f8d3271053e4fb21246c3fa7d4a23db23cbe6fb645f4f7ef5f6e1b0ae86c95c` |
| `model-00036-of-00213.safetensors` | 8,591,113,328 | sha256 | `8c46fe1fdb4443b29e1e76cbeebde47174968b18a6cc64a1a354139e5da3fba3` |
| `model-00037-of-00213.safetensors` | 3,981,110,048 | sha256 | `a41cbf39d3e8589aff549b921b0fa938f6a86ff549a522c90386d17d028a9f05` |
| `model-00038-of-00213.safetensors` | 17,182,226,672 | sha256 | `0103bd69079c91cb6f0f76d64e2c2d4de2cc19778e83b1acce2b3ea8d2997695` |
| `model-00039-of-00213.safetensors` | 8,591,113,328 | sha256 | `8fcdadd8cba6090973cbcd0ff5220433fde3577c21aec0b2f517e2315fd269e9` |
| `model-00040-of-00213.safetensors` | 17,182,226,672 | sha256 | `153b813fc2b0a6b81444e7e6a983e0641a84e89c3e365a7deaf86357e913b032` |
| `model-00041-of-00213.safetensors` | 8,591,113,328 | sha256 | `b5051c46394f4dd7e93f4df06ca9f2f53e5256d4b2f2dfd6ee5056ec82d8b5e9` |
| `model-00042-of-00213.safetensors` | 17,182,226,672 | sha256 | `aed37f5a293794216280a20041591e9ea0317b391e67bc19fdeeec6eeef626de` |
| `model-00043-of-00213.safetensors` | 8,591,113,328 | sha256 | `e5dde034878c5ea48210601693a0d11c88a128a66fc7074e2362d4dd1699d187` |
| `model-00044-of-00213.safetensors` | 17,182,226,672 | sha256 | `d16dc32850d0076ba764b57bf8f00e9b9103661b6dbb8402f700390595a0f118` |
| `model-00045-of-00213.safetensors` | 8,591,113,328 | sha256 | `8130ce23fc3d798a3b302809ff4b595a42f082e5995f949c5af05ecedf7af37b` |
| `model-00046-of-00213.safetensors` | 3,939,167,408 | sha256 | `a2d67fc22dd1d3b50da40e116fb235254272af02514f41b83f3e8270b9a6d085` |
| `model-00047-of-00213.safetensors` | 17,182,226,672 | sha256 | `32092ce96d7575dfa5050b19496eb0cf5dfb9d1acf250319b8d7a0014194b04a` |
| `model-00048-of-00213.safetensors` | 8,591,113,328 | sha256 | `184b6257ed3c71c8fc4af7251783f2c7f704ddd3e15464de9dd4e47036fba470` |
| `model-00049-of-00213.safetensors` | 17,182,226,672 | sha256 | `f2c8c0b9fd92e0ea8c1b7e07acccd24b9cf3550e03bfa89fc3455b36d1cf2709` |
| `model-00050-of-00213.safetensors` | 8,591,113,328 | sha256 | `d3e0c7ac758ba65fab5e929805c576aeacc23b4607993abe6462bc4828dd0228` |
| `model-00051-of-00213.safetensors` | 2,810,632,584 | sha256 | `e5f379e2f054b7d081630879d9044e991477a97f41392bc9ef7a0979755813d9` |
| `model-00052-of-00213.safetensors` | 17,182,226,672 | sha256 | `88fb71474c3862b18f09744c3a7c77b177edce74ca3edbf236c9e502ccb90f5c` |
| `model-00053-of-00213.safetensors` | 8,591,113,328 | sha256 | `4b3f6ef5966a6a0d901d3720e9006a5dbe5445d249443b33c4b136d9f4d9cee1` |
| `model-00054-of-00213.safetensors` | 17,182,226,672 | sha256 | `ce9ce4350b5c41ce0608e3d11ae36b270110214d2ba52fe09da86e385a00de29` |
| `model-00055-of-00213.safetensors` | 8,591,113,328 | sha256 | `5c63c2539251dd93341a8d4e56d04700481b34bf568e7b8c47e993d55a16e623` |
| `model-00056-of-00213.safetensors` | 17,182,226,672 | sha256 | `53149582cd30e50d61be57987b0a8fc43629bf54633d794be87bea1147452569` |
| `model-00057-of-00213.safetensors` | 8,591,113,328 | sha256 | `9d83ae16f725d8ca7fc3ad1c3aa1acf6809b6a0d952137a096d6bb7358d63f89` |
| `model-00058-of-00213.safetensors` | 17,182,226,672 | sha256 | `63b4a27cc0aacfa9a32654ce8af14f1e6321b560fbb79383fec54654e595dd09` |
| `model-00059-of-00213.safetensors` | 8,591,113,328 | sha256 | `5addd26035b5ec05cd6da55c7d01384557bc6b70fa1f8da657eff1b4d0657a3c` |
| `model-00060-of-00213.safetensors` | 3,909,971,424 | sha256 | `c1b00bd4429f5026c40d5ecc47564d317dec4a350c5808e6de30657ee4d30dca` |
| `model-00061-of-00213.safetensors` | 17,182,226,672 | sha256 | `d1496ee2cfce255e2b14bc869c51c4313e900a10f6263ecb3caac36ed609cb61` |
| `model-00062-of-00213.safetensors` | 8,591,113,328 | sha256 | `2be5ec7fc4d7323812b347af1a46ae16c3268e5f39a22440ff5fff20a58f8df8` |
| `model-00063-of-00213.safetensors` | 17,182,226,672 | sha256 | `0c0d284e344d61032a2880c2515ed37c6753b1ea3256c7f472302f5eac9caf6a` |
| `model-00064-of-00213.safetensors` | 8,591,113,328 | sha256 | `3c1c760f33b99e1c887f36f088028bf8727e7d341d6510299b8df345fa70653c` |
| `model-00065-of-00213.safetensors` | 17,182,226,672 | sha256 | `2fb990cb9e17e0c0d1e285a73c913580fd6d513de3d530dfe39a6fa44e0642f0` |
| `model-00066-of-00213.safetensors` | 8,591,113,328 | sha256 | `03d41f1042ad89777495a8d4a6119958bc92a76a1c669c1159ebe90bef057f21` |
| `model-00067-of-00213.safetensors` | 17,182,226,672 | sha256 | `f031d5a96db8e22714bf36335737065ac620328c9f4304901524446e808584d2` |
| `model-00068-of-00213.safetensors` | 8,591,113,328 | sha256 | `9adcd513200dd0963d017b56e6cd5b0fd61fa3ab60c92d524b724fe7c60ad8e7` |
| `model-00069-of-00213.safetensors` | 3,905,595,736 | sha256 | `5017d886a51a86723a7f75c88c1a8a0e861c6fedde09b80508f5f3ad9b41535e` |
| `model-00070-of-00213.safetensors` | 17,182,226,672 | sha256 | `d0886c84ad1d83ad9e6b0680cbb61b8216fddc715bf6e0aeccc4370e2c4ecee7` |
| `model-00071-of-00213.safetensors` | 8,591,113,328 | sha256 | `efce287e8ba20b099cd3e75ea4f76575ff91549291f2cbec5e0868543b16a1bd` |
| `model-00072-of-00213.safetensors` | 17,182,226,672 | sha256 | `e0211d2414473e3a02de7ceff39de4e21717ea856efe85355c44fc6479e13c7d` |
| `model-00073-of-00213.safetensors` | 8,591,113,328 | sha256 | `6fe56d0385ab7c02fc8cb5d34137e9711915316cd1b998e1686eb5f57856953c` |
| `model-00074-of-00213.safetensors` | 17,182,226,672 | sha256 | `089e1756ebb0421915cf25422ae6a04f440e75606f71c37f83d7ac179595ca26` |
| `model-00075-of-00213.safetensors` | 8,591,113,328 | sha256 | `ffaac37463988eeb40713ddce6085daac75464ccdecb48200fedd0d3ff02ea25` |
| `model-00076-of-00213.safetensors` | 17,182,226,672 | sha256 | `f1c5991be04100ff063b0b496f31909ef9e0b565e3a57bb79f8808532c79bb5b` |
| `model-00077-of-00213.safetensors` | 8,591,113,328 | sha256 | `6745710e079ee4c516247dc76b44498e0bc710279f26900c20ea5962d44ec533` |
| `model-00078-of-00213.safetensors` | 3,901,220,008 | sha256 | `a7689a74f22b9e6957c06d6a5d6b17008d72d791cbd6f6d5d5196bda11d00189` |
| `model-00079-of-00213.safetensors` | 17,182,226,672 | sha256 | `6a7d95efc3245f4c01a156f7f56a25240ef4b483ad6a81d3747ce9ca2491a5fc` |
| `model-00080-of-00213.safetensors` | 8,591,113,328 | sha256 | `0a416a63e12665be65c97e832cf3fda01656e911c3af63a4f37b694791fe35f2` |
| `model-00081-of-00213.safetensors` | 17,182,226,672 | sha256 | `d69ce68d3926e647fa8b2aeaecb506bc44a661a55d1f4848744e94599a4874ea` |
| `model-00082-of-00213.safetensors` | 8,591,113,328 | sha256 | `bd787f8ba6c12608f0a4193d760d48b97c9fd6b5b9a57d5718fe55a24aabb09a` |
| `model-00083-of-00213.safetensors` | 17,182,226,672 | sha256 | `960b11035d8558ee868184bbb1099414d046d5f751d324a7a7669eebff519a15` |
| `model-00084-of-00213.safetensors` | 8,591,113,328 | sha256 | `8d6923c4dfd65f15a720ef17948d44d328c0752ee6f224994c54f5eeb8f5531f` |
| `model-00085-of-00213.safetensors` | 17,182,226,672 | sha256 | `2a71117ceb3e016136760e4d9355bbaf532c2f2a74077353a5bc0a4b51a8e056` |
| `model-00086-of-00213.safetensors` | 8,591,113,328 | sha256 | `a183b997cf8a9b805ea2784ad3981a818fc463ba9ea3ae3e65fb229fa08da4ca` |
| `model-00087-of-00213.safetensors` | 3,909,971,424 | sha256 | `ef98219f9ae135e476e92473a5bf68aa841f8fa7c1a11149437fd4bf6c4a7f7f` |
| `model-00088-of-00213.safetensors` | 17,182,226,672 | sha256 | `3f1691eb7feb6b63a4fab921c1338a8884bbe52dc45a9e78c1d0840cf658b2b0` |
| `model-00089-of-00213.safetensors` | 8,591,113,328 | sha256 | `a17ac8646fb17fe2484e40610099742efdaddf25a4f3259c315a87dd6b132d3d` |
| `model-00090-of-00213.safetensors` | 17,182,226,672 | sha256 | `cac60c92a6384d6be0350e72065395c624ba2dd3150e6e3dbc36de0e91d373bc` |
| `model-00091-of-00213.safetensors` | 8,591,113,328 | sha256 | `1fce7df0259a3c24f93aa36dbe96e7f9756be0e0d8793d43d89727a4ac1550cb` |
| `model-00092-of-00213.safetensors` | 17,182,226,672 | sha256 | `edb210621fdbf5d038ced2cf4b6e4b54c03f1b04bac741eb1bdf6dc658e9a30c` |
| `model-00093-of-00213.safetensors` | 8,591,113,328 | sha256 | `3f008c3c02825bd974ad61d7029d52d9c063ff095e28f802dae798de67cd290c` |
| `model-00094-of-00213.safetensors` | 17,182,226,672 | sha256 | `4ed95fc6d10b1ee723873a631c46e5432aa9ba494f954d6ccd71e9a278ba6456` |
| `model-00095-of-00213.safetensors` | 8,591,113,328 | sha256 | `4b5069739123e456e57613a8fa4d07417d4085d9330244b03c8f29084c168bb5` |
| `model-00096-of-00213.safetensors` | 3,905,595,736 | sha256 | `7af652f54c5c80d0c3390570792031d3400c162759d008fc32a1d7e76be9b17d` |
| `model-00097-of-00213.safetensors` | 17,182,226,672 | sha256 | `d570867290a6d5ec4ed0f182b0c0387262fa5812f0a395595d407e1ceb7b6a6b` |
| `model-00098-of-00213.safetensors` | 8,591,113,328 | sha256 | `0d932d1c22f907f6117ce0a1f04c210075db25261f43c68f93460d0a2d029038` |
| `model-00099-of-00213.safetensors` | 17,182,226,672 | sha256 | `5b5107734428b628513f7fbf97ea60fcaf3cdb030e9dcfcdf5144c653adf761b` |
| `model-00100-of-00213.safetensors` | 8,591,113,328 | sha256 | `060eb24c9c348313f972c9199615bc24e0ec7e63e8e803f5363815c485b03ad4` |
| `model-00101-of-00213.safetensors` | 17,182,226,672 | sha256 | `1f85037b5f476ee0d2ce40b9727fb278740f2d4166346198d0a67b9da22b8c63` |
| `model-00102-of-00213.safetensors` | 8,591,113,328 | sha256 | `c2e2b623fb54cb14ed62060321a7545fe148c15e9122991d6c7bfec178778411` |
| `model-00103-of-00213.safetensors` | 17,182,226,672 | sha256 | `335e6ccbc3c51806654cc4801efb36f4818130b70978fed9a3f04f860fead717` |
| `model-00104-of-00213.safetensors` | 8,591,113,328 | sha256 | `7d72d7aa3e24e8ba96a6773f9c0711e0ce65a24090df8519a8ff2bc39f044e1c` |
| `model-00105-of-00213.safetensors` | 3,901,220,008 | sha256 | `a299b029523193881d953c9245f19f7c9230f02e672ae414ad6802c2b4563fd6` |
| `model-00106-of-00213.safetensors` | 17,182,226,672 | sha256 | `c1184cad2a318d6e57c44a4555fe8ff116cf2aafa2c498a9aaecf3cf8f1277cd` |
| `model-00107-of-00213.safetensors` | 8,591,113,328 | sha256 | `a8071c5157b881a055052c5d723449904ea192b673e4ca9a281d1c5b9ee70386` |
| `model-00108-of-00213.safetensors` | 17,182,226,672 | sha256 | `eb04e91abff5558e27a381e722b7131fab745331e6290400afa5ed5971425aac` |
| `model-00109-of-00213.safetensors` | 8,591,113,328 | sha256 | `943faca2ea09fc67c2c5608c2505f158143ca94bbbb116f14380435349849e19` |
| `model-00110-of-00213.safetensors` | 17,182,226,672 | sha256 | `09b714711b3adec864b6b91d87d009c1d16266b203cb75bf5bf19732ca0b98e5` |
| `model-00111-of-00213.safetensors` | 8,591,113,328 | sha256 | `435efadcb7ddcb28fcd4ca4fd9877617cc5202145e31ef5d3418a68c0eaff51a` |
| `model-00112-of-00213.safetensors` | 17,182,226,672 | sha256 | `9a76f30938ac8cd0948e1c3edf482284136e4a5cc01318d99ae6603f25e0d2bb` |
| `model-00113-of-00213.safetensors` | 8,591,113,328 | sha256 | `6f02c29ec3a6a3d06aa57bfb8ff5514dae0dbae9181988f399d877f1a6e2dd5b` |
| `model-00114-of-00213.safetensors` | 3,909,971,424 | sha256 | `ac6faebc048829c9a2df68f0f806bedc578a41698b8236543ccea374fa5b1359` |
| `model-00115-of-00213.safetensors` | 17,182,226,672 | sha256 | `c1e9775fb9ba08143284049964bbfebea2e41806be9c5b6f27608a5125eb6ffb` |
| `model-00116-of-00213.safetensors` | 8,591,113,328 | sha256 | `1217e811433aa44c6d9debed9c516d4766f6e8a2476ffa1d09b42084125fd80f` |
| `model-00117-of-00213.safetensors` | 17,182,226,672 | sha256 | `967ea202cd3e14fad746f71cddc8c2a2995c5183509720bd45e560caa07dd895` |
| `model-00118-of-00213.safetensors` | 8,591,113,328 | sha256 | `647884740ae5605e22535ea9a88f7e0497537d884cbd2b67c199ea4475ca785f` |
| `model-00119-of-00213.safetensors` | 17,182,226,672 | sha256 | `782203570702bf4b554d3dbabfefd2edec93927a40f8931edcfcb3d4fec2a162` |
| `model-00120-of-00213.safetensors` | 8,591,113,328 | sha256 | `dbd1dab720bf683bb6211d650b3b139e8a9c720db1fc04db282b224d6b7ede10` |
| `model-00121-of-00213.safetensors` | 17,182,226,672 | sha256 | `586181c5f1884246324046ad8ce97d40aa949fd058bd5481dea9b9ecad36b38f` |
| `model-00122-of-00213.safetensors` | 8,591,113,328 | sha256 | `59e4efc32bfd861dce3c3872784bd8753fbafed742dfe4c3cac7e447a92de93c` |
| `model-00123-of-00213.safetensors` | 3,905,595,736 | sha256 | `566bf1719b0204db975cd4823cbba9c65238aaa8e13502ac7220f7728bceccbe` |
| `model-00124-of-00213.safetensors` | 17,182,226,672 | sha256 | `a817a82fbf484c546dccf7d3f1b7cdbd9f1358917847145a0aca58f02b1bfebe` |
| `model-00125-of-00213.safetensors` | 8,591,113,328 | sha256 | `180ed73fa1f2a94d3f2b90e7898a2d4cdd97d3efc768141f4924a012fdf7dd2b` |
| `model-00126-of-00213.safetensors` | 17,182,226,672 | sha256 | `aef6483c47ae786c329a019b31a2617c63029ddc1a05f1b3fe20ae4aa1fa380d` |
| `model-00127-of-00213.safetensors` | 8,591,113,328 | sha256 | `e3f24fb946e0222c6a0dbbe7039c64c738d70a5f8db30e7690727664fc0ca480` |
| `model-00128-of-00213.safetensors` | 17,182,226,672 | sha256 | `ee526dd0089168b9e14b5016545931ed4fda4f2d56f47491063ca7750aad6c47` |
| `model-00129-of-00213.safetensors` | 8,591,113,328 | sha256 | `67f89fccc93c9aa36e3c39be7a6903374e15c4e8a55de239f808aba0983f0b57` |
| `model-00130-of-00213.safetensors` | 17,182,226,672 | sha256 | `a273faa4d502da79ab1e8863a2fd54b47cad9816406ff0704c13d2d2129b66b2` |
| `model-00131-of-00213.safetensors` | 8,591,113,328 | sha256 | `ec5a5b994873521a51cc3d6466c4ea9b6648ab3364446be062fe74ea74f733bc` |
| `model-00132-of-00213.safetensors` | 3,901,220,008 | sha256 | `40cdf55e533cd35f0c25cc1a8f451a473f1e1b676fd9c9482b77702eff89f507` |
| `model-00133-of-00213.safetensors` | 17,182,226,672 | sha256 | `d7111136a522e51e385034d72766dcf8e02ff865e6efa1bbee69acf3fff77520` |
| `model-00134-of-00213.safetensors` | 8,591,113,328 | sha256 | `1bd79e11e17cc85418fad958e848f604e03f8ed86260679bcff04428825d5083` |
| `model-00135-of-00213.safetensors` | 17,182,226,672 | sha256 | `292639452ed92bf174d76b6aa07663eed3fd85e5fc832af679ac790c23105a01` |
| `model-00136-of-00213.safetensors` | 8,591,113,328 | sha256 | `122a2d1f41b6cdd011b0eb921c90514746d44cd294ce29256c39ffa9300a5454` |
| `model-00137-of-00213.safetensors` | 17,182,226,672 | sha256 | `e35513af96a3ce02d5cb532a221fc330b94f95de405131e87dc3b3e0fb73e05e` |
| `model-00138-of-00213.safetensors` | 8,591,113,328 | sha256 | `d7a2f6fae7a3f0fab6d4b2fbb578a7856f8805257a797f5fd7c9fd485bb8816b` |
| `model-00139-of-00213.safetensors` | 17,182,226,672 | sha256 | `2de9e2bf9cde3818b11225939e6e36b60e52f9b7b92a8d0f2387130a158e5b81` |
| `model-00140-of-00213.safetensors` | 8,591,113,328 | sha256 | `5a6a12db14b788ce4e6b28bd32e6482a1e4e3ef172b77c789c744606a7264482` |
| `model-00141-of-00213.safetensors` | 3,909,971,424 | sha256 | `45e9d375a7e74ec8e5bde4d4a359da3264d0a59477b31a26f97e237732103924` |
| `model-00142-of-00213.safetensors` | 17,182,226,672 | sha256 | `059aca41bf7f3c3538090139c773a009a8e9d06aa586c0c73f080468b457a46e` |
| `model-00143-of-00213.safetensors` | 8,591,113,328 | sha256 | `cbac09bf8dd80773c7e8c285d9996a6766a550b0f669255580dd361a6b81db30` |
| `model-00144-of-00213.safetensors` | 17,182,226,672 | sha256 | `695bc4f9026de017d7efa7679ecc6bbce75c683f21f9bd2f4e20cafb3c1462cb` |
| `model-00145-of-00213.safetensors` | 8,591,113,328 | sha256 | `439cf2edd8ee1a747bb0a55e7d4d8b43173aa249a3910c1c453f73df4d001eb7` |
| `model-00146-of-00213.safetensors` | 17,182,226,672 | sha256 | `36d9e6a2f03e944606ca33b6f36603fd6dfd27fb68363970fd3c286ffa2f7051` |
| `model-00147-of-00213.safetensors` | 8,591,113,328 | sha256 | `67edaf73f45a02ab08941b483d5b1e2149f6e8d8a0f7d5eaa1a10b846822a3ef` |
| `model-00148-of-00213.safetensors` | 17,182,226,672 | sha256 | `1af45f5d35a19123c588f298a6ae236bd06d4c8e9281f711888663bef0124776` |
| `model-00149-of-00213.safetensors` | 8,591,113,328 | sha256 | `59b697e054d2e32e958ed8e3afe2287bbf68e6323d2d2cc556be76360c9a1117` |
| `model-00150-of-00213.safetensors` | 3,905,595,736 | sha256 | `a2a2a2741b4bc8f866b3b323cc3c962877c5ce23881e08e471c250ce48e1cda2` |
| `model-00151-of-00213.safetensors` | 17,182,226,672 | sha256 | `1d74543f2f32a2fb02b4ab467fab64709ae4a6d70d93f52e4f3823efea24a920` |
| `model-00152-of-00213.safetensors` | 8,591,113,328 | sha256 | `c9e1a8f176a5f7f6969b4f999c3063bf6088124cd2ed93acc06ddec62eea3b1a` |
| `model-00153-of-00213.safetensors` | 17,182,226,672 | sha256 | `2586ceaa0f009ab0e560be582495f3c93d9fc0728157ce424051cb156037207d` |
| `model-00154-of-00213.safetensors` | 8,591,113,328 | sha256 | `ee0c6f94bf0fe84e7ccf6f0619ce5a6cdb50cc0c03eefa2a97588a57a76962df` |
| `model-00155-of-00213.safetensors` | 17,182,226,672 | sha256 | `32b106a67e00342cfa66bc2cdd9ccdee99aa42a9017abd6362eaff239e068a6d` |
| `model-00156-of-00213.safetensors` | 8,591,113,328 | sha256 | `c63ed4499d58d4e9ee62c6606954057ead5dc2a8c2118a1b6e7bf2fa9ddb7eda` |
| `model-00157-of-00213.safetensors` | 17,182,226,672 | sha256 | `9e414ccea3f8eac837dad6b3ddcfc5f3a42235a0b38b91b42dc7c18a094f86ee` |
| `model-00158-of-00213.safetensors` | 8,591,113,328 | sha256 | `1afa80ba1498068b756b9f7ab7c2655eb99ba7360ae0a45b21d2cabb13a44eca` |
| `model-00159-of-00213.safetensors` | 3,901,220,008 | sha256 | `0504a60d9559ef17d3a110c7a537b6194898fce5b1d6bbb776128624da92e7c8` |
| `model-00160-of-00213.safetensors` | 17,182,226,672 | sha256 | `21ee7635a1d69c34804c78039654f0b4737cd284dacfeb4baac82fa65bc460e1` |
| `model-00161-of-00213.safetensors` | 8,591,113,328 | sha256 | `0f1b7b8d9861108c75df242e7531a54ab310e2f15b6e4a0ef67f414fabceb0be` |
| `model-00162-of-00213.safetensors` | 17,182,226,672 | sha256 | `2068ee77a4feda181502feceb8cba54a2e8985fcf9f4b57efacbc103e85b043d` |
| `model-00163-of-00213.safetensors` | 8,591,113,328 | sha256 | `c343c86b5f4b188fc5062e3f77898f62167a73f806d5640a6f3d867015ad0e91` |
| `model-00164-of-00213.safetensors` | 17,182,226,672 | sha256 | `96377c102ef9b1bc67b0c05eb9045fd00bb4d0206ec18b241fe53daf29807b4e` |
| `model-00165-of-00213.safetensors` | 8,591,113,328 | sha256 | `dc76a871b1299a3270d2935bd3856fd8e8aa1154e1984ee2a74a1a142f1cd42d` |
| `model-00166-of-00213.safetensors` | 17,182,226,672 | sha256 | `9cf095a2c69f24bd7ab22d1c80ec8033f8a117af5d1b52f49b259ac0de75ba8d` |
| `model-00167-of-00213.safetensors` | 8,591,113,328 | sha256 | `0919ce382f059c2affedc86849a827c626a24f247dae6e84109775c10dfa177d` |
| `model-00168-of-00213.safetensors` | 3,909,971,424 | sha256 | `be74f5a09fdc546231cb79eb8171800acb6fb0cbde2cf49916b18b4474a170d7` |
| `model-00169-of-00213.safetensors` | 17,182,226,672 | sha256 | `45f00c6b045b43da019b479cf6434f5aeb40b2f6cb74dad005a0b1c62ba7a558` |
| `model-00170-of-00213.safetensors` | 8,591,113,328 | sha256 | `79bfc66285a57214d07b4e7c638b29585bc776b37373b5fd1f49e064324173a3` |
| `model-00171-of-00213.safetensors` | 17,182,226,672 | sha256 | `6dc993bcaa3b1905798e11300281ce9ef89935a3afc4f4662f91fb8e41ee0ad2` |
| `model-00172-of-00213.safetensors` | 8,591,113,328 | sha256 | `132695864d978c8c838e1d6ef0ec0cc8cccccf61c5013c1d8d5745c88c988d98` |
| `model-00173-of-00213.safetensors` | 17,182,226,672 | sha256 | `beb64b8ca3777f88dcfeed8f1ef698551a0e5afc2f286bfcac3671e412e871fb` |
| `model-00174-of-00213.safetensors` | 8,591,113,328 | sha256 | `3698964e34ba0a006e6eee5153733a6c36d48490de49d2ed9b33d2d2486dd0ea` |
| `model-00175-of-00213.safetensors` | 17,182,226,672 | sha256 | `5d2ca9bd7b29e4f9c7fdd08aaf81412f1f96353d04ede7bcd83f97cb77a090ac` |
| `model-00176-of-00213.safetensors` | 8,591,113,328 | sha256 | `a73585702bb7a5e5ce0c9d2a8ecca76d54c655613e562a2053b9bab646f1627f` |
| `model-00177-of-00213.safetensors` | 3,905,595,736 | sha256 | `7c168cbae5bab1ee83d74242b6e4bd021307d89e5130c48f4a4ea00778b94d4b` |
| `model-00178-of-00213.safetensors` | 17,182,226,672 | sha256 | `315231f80dd01134904e552f37f84140a6e16c964fcdd9c9184954e73b090bda` |
| `model-00179-of-00213.safetensors` | 8,591,113,328 | sha256 | `e72d886e8aea95ca3e4d6f7ef7e5c3e4f9c12a894cae03aae5a1902e3c10a45b` |
| `model-00180-of-00213.safetensors` | 17,182,226,672 | sha256 | `4b5e1ffa56920cda0fa4d9e594498028a7405d7d37f0d5739566f79c69829384` |
| `model-00181-of-00213.safetensors` | 8,591,113,328 | sha256 | `7f3e4b6ac69bfa32bb22937ef698a0ddf13658bafd952e7e318abc53f39299a8` |
| `model-00182-of-00213.safetensors` | 17,182,226,672 | sha256 | `ac4657984212dd0c9a1c7f574105c4a9cb0dc8ba97bd35d17db341b7bb3ba4b9` |
| `model-00183-of-00213.safetensors` | 8,591,113,328 | sha256 | `97ed192cc4fd06f0889cb47cf2c0f7bda36c5e5033be5d0641f43c23f22ba130` |
| `model-00184-of-00213.safetensors` | 2,915,342,896 | sha256 | `2f2d800d2aa65e328e10bf42675ceca25066ef60858f77848a14bf21caeaaa1f` |
| `model-00185-of-00213.safetensors` | 17,182,220,528 | sha256 | `af93ede6c7ac8ee02aaf43f4904ff067764690c293194c43cce82c82cec3d4f6` |
| `model-00186-of-00213.safetensors` | 8,591,110,256 | sha256 | `2ec89a62a6a8cc6a018801b801154cc621a2597b7ca170350416dfe92724dfc9` |
| `model-00187-of-00213.safetensors` | 17,182,226,672 | sha256 | `0b2610fe0613a49f788958479ad0410655365602e5a379debc251c4c7089d744` |
| `model-00188-of-00213.safetensors` | 8,591,113,328 | sha256 | `9740b0636d5e31c04ca38ae002ec2233eb9f02b1707d4b5bcc5357e2226717d7` |
| `model-00189-of-00213.safetensors` | 17,182,226,672 | sha256 | `154efc8028e1d77aec1097892ec658b7a81bae5992b1d096219a4b1d78c83b15` |
| `model-00190-of-00213.safetensors` | 8,591,113,328 | sha256 | `c03d607cb928c15b8a8bf8e11451f9646e4299642ee62d3a0198cee496a7bdd8` |
| `model-00191-of-00213.safetensors` | 17,182,226,672 | sha256 | `1d1adf7b5d2c9a3beb5509254a17bd3a6e9c48b3eff9c9c2be9f0bc09cebd296` |
| `model-00192-of-00213.safetensors` | 8,591,113,328 | sha256 | `3dd10d4d6d7b3805015b8d150c6670d3bcb6ae4be95526a0259ba81d51b15e61` |
| `model-00193-of-00213.safetensors` | 3,565,758,112 | sha256 | `d73e3647b4e2f92b9ea0b30536f4356c4fa6b68e0846409b9fdd8ec237ddd2f0` |
| `model-00194-of-00213.safetensors` | 17,182,226,672 | sha256 | `469fe70cc084ca5608cdd206692982ef79a8ed60e56eaa1ab9eba870ed30e82e` |
| `model-00195-of-00213.safetensors` | 8,591,113,328 | sha256 | `56ded66cd06138f206c271073d21d57208808a6b73d50c7bce16de5cc4b8a5ce` |
| `model-00196-of-00213.safetensors` | 17,182,226,672 | sha256 | `a17c667e859a0fe7d0e791c31e0180f64b69e5ba7f83ed82fa8d56c599d8b815` |
| `model-00197-of-00213.safetensors` | 8,591,113,328 | sha256 | `5a764f85ff7f48ecd60d8ea22c3b6a16be4299a5fe44253025f2441a131b83a4` |
| `model-00198-of-00213.safetensors` | 17,182,226,672 | sha256 | `8994c60baa017741379d575569f285d4ca3bb8a7d7dffa84e296a5a98c9c0e7a` |
| `model-00199-of-00213.safetensors` | 8,591,113,328 | sha256 | `4199e5650719c714d90fbce9ecdc36b78c3f05bc256905bd013e163c0525f26c` |
| `model-00200-of-00213.safetensors` | 17,182,226,672 | sha256 | `3815d442be1ee1c3fd1d3f967fe3da003e4e0a6951a6fa2d1e26298fd3a0c643` |
| `model-00201-of-00213.safetensors` | 8,591,113,328 | sha256 | `c548b90cb4be0309569c886041629c770a66517fab1aadcc25ce96499c204886` |
| `model-00202-of-00213.safetensors` | 3,905,595,736 | sha256 | `c7baf4b2e6916b5e07f9a034a2075f4d02fa877dd557b4d7380305f0e9651b0b` |
| `model-00203-of-00213.safetensors` | 17,182,226,672 | sha256 | `bb38dbe0ce8c6285ab54a5bd29268b775f3e9f23491bcb2abed348d8399f6ec9` |
| `model-00204-of-00213.safetensors` | 8,591,113,328 | sha256 | `965ef55eb66726ff30501c471ccec4556b409f28cb0012af6b7a6a5579ef05b3` |
| `model-00205-of-00213.safetensors` | 17,182,226,672 | sha256 | `20cd1f01c7cbb0c414c23d5f21b67bb56f49859b77527bdd2679ac27e1d7bba0` |
| `model-00206-of-00213.safetensors` | 8,591,113,328 | sha256 | `e9263fdf30d98b91c4c49b67c8022b41b4c6256f2d31677923bdfc4d07f768c3` |
| `model-00207-of-00213.safetensors` | 17,182,226,672 | sha256 | `1179b3eee5246cca647115b4cb76cdf450093f1b31b67ad3ecc223d25245aeb6` |
| `model-00208-of-00213.safetensors` | 8,591,113,328 | sha256 | `aec8b416bb53a0bc4dd35695f97878d5d31011d5d587d528c2170caa1836d5e8` |
| `model-00209-of-00213.safetensors` | 17,182,226,672 | sha256 | `1580b98a85c0e4b5141b00cc133f0abddcced6566556e8a7067664a0c69e9f47` |
| `model-00210-of-00213.safetensors` | 8,591,113,328 | sha256 | `c3ef138ef5f7700f34b9d495f98831a6b3caf6e0a799d8040911acaa05bc4227` |
| `model-00211-of-00213.safetensors` | 3,905,595,736 | sha256 | `d788743012156750787e2f4e0ee3fb964db147472d62668a190fceec05e04a64` |
| `model-00212-of-00213.safetensors` | 4,068,474,976 | sha256 | `ba874192a7583031a3ee6f2d77d458e699768c34cb6eb6dcb7dd80dbd23c88dd` |
| `model-00213-of-00213.safetensors` | 570,426,312 | sha256 | `79721368d9e1c05bea5291ed8950c01efacbd33a9f718354f02814278b5c34a1` |
| `model.safetensors.index.json` | 28,691,675 | sha256 | `67f75ab10833869c951b5c8e02ddcf4fa11974a8dcb950c51193680c90a4f77c` |
| `tokenizer.json` | 12,809,320 | sha256 | `0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3` |
| `tokenizer_config.json` | 16,438 | git blob | `c43fe0e2704754be7ba12b5e89e1a11a49c88127` |
| `vocab.json` | 6,722,759 | git blob | `0aa0ce0658d60ac4a5d609f4eadb0e8e43514176` |

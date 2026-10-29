import json, struct, urllib.request, sys, numpy as np
sys.path.insert(0, "wt157/bench/models")
import qat_grid as Q
U = "https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized/resolve/f1e06dc520982d9b9edd76859fdb7ab209449949/"
def rng(f, a, b):
    r = urllib.request.Request(U + f, headers={"Range": f"bytes={a}-{b-1}"})
    with urllib.request.urlopen(r, timeout=60) as x: return x.read()
for f in ("model-00001-of-00002.safetensors",):
    n = struct.unpack("<Q", rng(f, 0, 8))[0]
    hdr = json.loads(rng(f, 8, 8 + n))
    names = [k for k in hdr if k != "__metadata__"]
    pick = [k for k in names if "layers.3." in k and "language" in k and hdr[k]["dtype"]=="BF16" and len(hdr[k]["shape"])>=2][:12]
    for k in pick:
        v = hdr[k]; a, b = v["data_offsets"]
        nbytes = min(b - a, 2 * 32 * 4096)  # first 4096 blocks
        raw = rng(f, 8 + n + a, 8 + n + a + nbytes)
        x = (np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16).view(np.float32).astype(np.float64)
        shp = v["shape"]
        bl = x[: (len(x)//32)*32].reshape(-1, 32)
        amax = np.abs(bl).max(1); 
        d = Q.grid_step(bl)
        r = bl / d[:, None]; off = np.abs(r - np.rint(r)).max(1)
        m = np.rint(amax / d)
        nn = bl / (np.where(amax>0, amax, 1)/8)[:, None]   # absmax/8 scale
        q = np.clip(np.rint(nn), -8, 7) * (amax/8)[:, None]
        q2, s2 = Q.snap_blocks(bl); snapped = q2 * s2.astype(np.float64)[:, None]
        print(k, shp, "nblk", len(bl), "on_grid(maxoff<0.02)", round(float((off < 0.02).mean()), 4),
              "m hist", {int(i): int((m == i).sum()) for i in np.unique(m)},
              "absmax8 relrms", round(Q.rel_rms(q.reshape(-1), bl.reshape(-1)), 4),
              "snap relrms", round(Q.rel_rms(snapped.reshape(-1), bl.reshape(-1)), 5))

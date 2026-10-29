import json,struct,subprocess,sys,numpy as np
from collections import Counter
B=sys.argv[1]; shard=sys.argv[2]; names=sys.argv[3:]
def rng(a,b): return subprocess.run(['curl','-sL','-m','120','-r',f'{a}-{b}',f'{B}/{shard}'],capture_output=True).stdout
n=struct.unpack('<Q',rng(0,7))[0]; hdr=json.loads(rng(8,8+n-1)); base=8+n
for name in names:
    t=hdr[name]; s,e=t['data_offsets']; shp=t['shape']; cols=shp[-1]
    raw=rng(base+s, base+s+cols*2*64-1)
    w=(np.frombuffer(raw,dtype=np.uint16).astype(np.uint32)<<16).view(np.float32).reshape(-1,cols)
    for blk in (32,):
        bl=w[:, :cols//blk*blk].reshape(-1,blk)
        uq=[]; ok=0; zero=0; ks=Counter()
        for b in bl:
            u=np.unique(b); uq.append(len(u))
            nz=np.sort(np.abs(u[u!=0]))
            if len(nz)==0: continue
            d=nz[0]
            k=u/d
            if np.all(np.abs(k-np.round(k))<0.08): ok+=1; ks[(int(np.round(k).min()),int(np.round(k).max()))]+=1
            zero+= (0 in u)
        print(name, shp, 'uniq/32-block max',max(uq),'mean',round(np.mean(uq),2),'| u/d_min integral frac',round(ok/len(bl),3),'| contains 0 frac',round(zero/len(bl),3), '| span(kmax-kmin) top', Counter({k:v for k,v in Counter((b-a) for (a,b),c in ks.items() for _ in range(c)).items()}).most_common(3))

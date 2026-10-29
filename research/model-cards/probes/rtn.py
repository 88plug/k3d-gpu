import json,struct,subprocess,sys,numpy as np
def load(B,shard,name,rows=64):
    rng=lambda a,b: subprocess.run(['curl','-sL','-m','60','-r',f'{a}-{b}',f'{B}/{shard}'],capture_output=True).stdout
    n=struct.unpack('<Q',rng(0,7))[0]; hdr=json.loads(rng(8,8+n-1)); base=8+n
    t=hdr[name]; so=t['data_offsets'][0]; cols=t['shape'][-1]
    return (np.frombuffer(rng(base+so,base+so+cols*2*rows-1),dtype=np.uint16).astype(np.uint32)<<16).view(np.float32).reshape(-1,cols)
def q40(w,g=32):   # llama.cpp Q4_0
    b=w.reshape(-1,g); m=b[np.arange(len(b)),np.abs(b).argmax(1)]; d=(m/-8).astype(np.float16).astype(np.float32); d[d==0]=1
    q=np.clip(np.round(b/d[:,None]),-8,7); return (q*d[:,None]).reshape(w.shape)
def sym_step(w,g=32):  # use exact grid step (QAT-aware)
    b=w.reshape(-1,g); out=[]
    for x in b:
        u=np.abs(x[x!=0]); d=np.min(u) if len(u) else 1
        out.append(np.clip(np.round(x/d),-8,7)*np.float32(np.float16(d)))
    return np.array(out).reshape(w.shape)
def asym(w,g):  # int4 asym (NNCF INT4_ASYM style min/max)
    b=w.reshape(-1,g); lo=b.min(1,keepdims=True); hi=b.max(1,keepdims=True); s=(hi-lo)/15; s[s==0]=1
    z=np.round(-lo/s); q=np.clip(np.round(b/s)+z,0,15); return ((q-z)*s).reshape(w.shape)
def symg(w,g):  # int4 sym absmax/8? NNCF-like: scale=absmax/8 clamp[-8,7]
    b=w.reshape(-1,g); s=np.abs(b).max(1,keepdims=True)/8; s[s==0]=1
    return (np.clip(np.round(b/s),-8,7)*s).reshape(w.shape)
rel=lambda a,b: float(np.sqrt(((a-b)**2).mean())/np.sqrt((b**2).mean()))
Q="https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-unquantized/resolve/f1e06dc52098"
Bf="https://huggingface.co/google/gemma-4-26B-A4B-it/resolve/4d7ae4984b7d"
for nm in ['model.language_model.layers.3.self_attn.q_proj.weight','model.language_model.layers.3.experts.gate_up_proj']:
    wq=load(Q,'model-00001-of-00002.safetensors',nm); wb=load(Bf,'model-00001-of-00002.safetensors',nm)
    print(nm)
    print('  QAT ckpt -> Q4_0 llama.cpp RTN  rel-RMS vs QAT ckpt', round(rel(q40(wq),wq),4))
    print('  QAT ckpt -> grid-step sym32     rel-RMS vs QAT ckpt', round(rel(sym_step(wq),wq),4))
    for g in (32,64,128):
        print(f'  QAT ckpt -> INT4_ASYM g{g}  ', round(rel(asym(wq,g),wq),4), f'| INT4_SYM(absmax/8) g{g}', round(rel(symg(wq,g),wq),4))
    print('  bf16 ckpt -> Q4_0 RTN rel-RMS vs bf16', round(rel(q40(wb),wb),4), '| bf16 -> ASYM g128', round(rel(asym(wb,128),wb),4))
    print('  QAT ckpt vs bf16 ckpt rel diff', round(rel(wq,wb),4))

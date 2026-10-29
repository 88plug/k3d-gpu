import struct,sys,subprocess,json,numpy as np
G=sys.argv[1]; url=sys.argv[2]; tname=sys.argv[3]; st_url=sys.argv[4]; st_shard=sys.argv[5]; st_name=sys.argv[6]
f=open(G,'rb'); r=lambda n:f.read(n)
u32=lambda:struct.unpack('<I',r(4))[0]; u64=lambda:struct.unpack('<Q',r(8))[0]
def s(): n=u64(); return r(n).decode('utf8','replace')
def val(t):
    if t==8: return s()
    if t==9:
        et=u32(); n=u64(); return [val(et) for _ in range(n)]
    fmt={0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}[t]
    return struct.unpack(fmt,r(struct.calcsize(fmt)))[0]
r(4);u32();nt=u64();nkv=u64(); align=32
for _ in range(nkv):
    k=s();t=u32();v=val(t)
    if k=='general.alignment': align=v
info={}
for _ in range(nt):
    name=s(); nd=u32(); dims=[u64() for _ in range(nd)]; tt=u32(); off=u64(); info[name]=(dims,tt,off)
pos=f.tell(); data=(pos+align-1)//align*align
dims,tt,off=info[tname]; assert tt==2, tt
ne0=dims[0]; nblk=ne0//32*64
raw=subprocess.run(['curl','-sL','-m','60','-r',f'{data+off}-{data+off+nblk*18-1}',url],capture_output=True).stdout
blks=np.frombuffer(raw,dtype=np.uint8).reshape(-1,18)
d=blks[:,:2].copy().view(np.float16).astype(np.float32).ravel()
q=blks[:,2:]; lo=(q&0xF).astype(np.int8)-8; hi=(q>>4).astype(np.int8)-8
vals=np.concatenate([lo,hi],axis=1)*d[:,None]   # q4_0 layout: first 16 low nibbles, then 16 high
# safetensors first 4 rows
def rng(a,b): return subprocess.run(['curl','-sL','-m','60','-r',f'{a}-{b}',f'{st_url}/{st_shard}'],capture_output=True).stdout
n=struct.unpack('<Q',rng(0,7))[0]; hdr=json.loads(rng(8,8+n-1)); base=8+n
t=hdr[st_name]; so=t['data_offsets'][0]; cols=t['shape'][-1]
w=(np.frombuffer(rng(base+so,base+so+cols*2*64-1),dtype=np.uint16).astype(np.uint32)<<16).view(np.float32).reshape(-1,32)
print('gguf dims',dims,'st shape',t['shape'])
diff=np.abs(vals-w); rel=diff.max()/np.abs(w).max()
print('max abs diff',diff.max(),'max |w|',np.abs(w).max(),'rel',rel,'exact-match frac',np.mean(diff<1e-6), 'frac within bf16 eps',np.mean(diff<=np.abs(w)*2**-8+1e-9))
print('gguf d sample',d[:4],'grid step sample',[np.sort(np.abs(np.unique(b)[np.unique(b)!=0]))[0] for b in w[:4]])
step=np.array([np.sort(np.abs(np.unique(b)[np.unique(b)!=0]))[0] if np.any(b!=0) else 0 for b in w])
am=np.abs(w).max(1); pos_ext=np.array([b[np.argmax(np.abs(b))]>0 for b in w])
match=np.isclose(np.abs(d),step,rtol=2e-3)
lc=np.isclose(np.abs(d),am/8,rtol=2e-3)
print('blocks',len(w),'gguf d == QAT grid step:',match.mean(),'| gguf d == absmax/8 (llama.cpp rule):',lc.mean())
print('positive-extreme blocks',pos_ext.mean(),'match among pos-ext',match[pos_ext].mean(),'match among neg-ext',match[~pos_ext].mean())
print('block-level exact reproduction frac', np.mean(np.all(np.abs(vals-w)<1e-6,axis=1)))
err=np.sqrt(((vals-w)**2).mean())/np.sqrt((w**2).mean()); print('relative RMS error gguf vs QAT bf16',err)

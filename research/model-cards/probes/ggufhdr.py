import struct,sys,re
from collections import Counter,defaultdict
T={0:'F32',1:'F16',2:'Q4_0',3:'Q4_1',6:'Q5_0',7:'Q5_1',8:'Q8_0',9:'Q8_1',10:'Q2_K',11:'Q3_K',12:'Q4_K',13:'Q5_K',14:'Q6_K',15:'Q8_K',16:'IQ2_XXS',17:'IQ2_XS',18:'IQ3_XXS',19:'IQ1_S',20:'IQ4_NL',21:'IQ3_S',22:'IQ2_S',23:'IQ4_XS',24:'I8',25:'I16',26:'I32',27:'I64',28:'F64',29:'IQ1_M',30:'BF16',34:'TQ1_0',35:'TQ2_0',39:'MXFP4'}
f=open(sys.argv[1],'rb'); r=lambda n:f.read(n)
u32=lambda:struct.unpack('<I',r(4))[0]; u64=lambda:struct.unpack('<Q',r(8))[0]
def s(): n=u64(); return r(n).decode('utf8','replace')
def val(t):
    if t==8: return s()
    if t==9:
        et=u32(); n=u64(); return [val(et) for _ in range(n)]
    fmt={0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}[t]
    return struct.unpack(fmt,r(struct.calcsize(fmt)))[0]
assert r(4)==b'GGUF'; ver=u32(); nt=u64(); nkv=u64()
print('version',ver,'tensors',nt,'kv',nkv)
for _ in range(nkv):
    k=s(); t=u32(); v=val(t)
    if isinstance(v,list): v=f'[list len {len(v)}]' if len(v)>8 else v
    if isinstance(v,str) and len(v)>150: v=v[:150]+'...'
    if 'chat_template' in k: v='<template>'
    print(' ',k,'=',v)
c=Counter(); g=defaultdict(Counter)
for _ in range(nt):
    name=s(); nd=u32(); dims=[u64() for _ in range(nd)]; tt=u32(); off=u64()
    c[T.get(tt,tt)]+=1; g[re.sub(r'\.\d+\.','.N.',name)][T.get(tt,tt)]+=1
print(c)
for k,v in sorted(g.items()): print('   ',k,dict(v))

# Hybrid: P-cores fused prefill (chunk C, LIFO-free FCFS) + side engine (E-cores) prefilling the queue tail concurrently, KV handed to P zero-copy.
import random
T0=0.062; CSEQ=0.0019; TPP=1/172.0
def run(N, R_side=0.0, slow=1.0, chunk=256, slo=20, heavy_frac=0.0, think=5.0, seed=1, horizon=1800):
    rng=random.Random(seed); t=0.0; ag=[]
    for i in range(N):
        h=i<round(heavy_frac*N)
        ag.append(dict(h=h,state='think',wake=rng.expovariate(1/think)*rng.random(),pf=0,dec=0,start=0,out=200 if h else 96,new=1700 if h else 396))
    lat={False:[],True:[]}
    while t<horizon:
        for a in ag:
            if a['state']=='think' and a['wake']<=t:
                a['state']='pf';a['start']=t;a['pf']=a['new'];a['dec']=a['out']
        decs=[a for a in ag if a['state']=='dec']; pfs=sorted([a for a in ag if a['state']=='pf'],key=lambda a:a['start'])
        # side engine takes the newest prefill (tail), P takes oldest (head)
        head=pfs[:1] if R_side>0 else pfs; tail=pfs[1:] if R_side>0 else []
        used=0
        for a in (head if R_side>0 else pfs):
            take=min(a['pf'],chunk-used);a['pf']-=take;used+=take
            if a['pf']==0:a['state']='dec'
            if used>=chunk:break
        if not decs and used==0 and not tail:
            t=max(t+0.001,min([a['wake'] for a in ag if a['state']=='think'],default=t+1));continue
        step=((T0+CSEQ*len(decs)) if decs else 0)*slow+TPP*used
        if step==0: step=0.05
        cap=R_side*step
        for a in tail:
            take=min(a['pf'],cap);a['pf']-=take;cap-=take
            if a['pf']<=1e-9:a['pf']=0;a['state']='dec'
            if cap<=0:break
        t+=step
        for a in decs:
            a['dec']-=1
            if a['dec']==0:
                lat[a['h']].append(t-a['start']);a['state']='think';a['wake']=t+rng.expovariate(1/think)
    p=lambda x:(sorted(x)[int(0.95*(len(x)-1))] if x else 0)
    return p(lat[False]),p(lat[True])
def cap(slo,**kw):
    best=0
    for N in range(1,40):
        ok=all((lambda r:r[0]<=slo and r[1]<=3*slo)(run(N,slo=slo,seed=s,**kw)) for s in (1,2,3))
        if ok:best=N
        elif N>best+3:break
    return best
cfg={'base':dict(),'E50 slow1.10':dict(R_side=50,slow=1.10),'E80 slow1.10':dict(R_side=80,slow=1.10),'E80 slow1.20':dict(R_side=80,slow=1.20),'E+iGPU130 slow1.25':dict(R_side=130,slow=1.25)}
for hf in (0.0,0.3):
  for slo in (20,30):
    print('heavy',hf,'slo',slo,{k:cap(slo,heavy_frac=hf,**v) for k,v in cfg.items()},flush=True)

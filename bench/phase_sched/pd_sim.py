# P/D split sim: decode on P-cores (no fused prefill), prefill on side engine(s) of rate R tok/s, optional P-core prefill when idle.
import random
T0=0.062; CSEQ=0.0019; TPP=1/172.0
def run(N, mode, R_side=66.0, slow=1.0, slo=20.0, heavy_frac=0.0, think=5.0, seed=1, horizon=1800):
    rng=random.Random(seed); t=0.0; ag=[]
    for i in range(N):
        h=i<round(heavy_frac*N)
        ag.append(dict(h=h,state='think',wake=rng.expovariate(1/think)*rng.random(),pf=0,dec=0,start=0,out=200 if h else 96,new=(1500+200) if h else (300+96)))
    lat={False:[],True:[]}; side_t=0.0
    dt=0.0
    while t<horizon:
        for a in ag:
            if a['state']=='think' and a['wake']<=t:
                a['state']='pf';a['start']=t;a['pf']=a['new'];a['dec']=a['out']
        decs=[a for a in ag if a['state']=='dec']; pfs=sorted([a for a in ag if a['state']=='pf'],key=lambda a:a['start'])
        if mode=='fused':
            used=0
            for a in pfs:
                take=min(a['pf'],256-used);a['pf']-=take;used+=take
                if a['pf']==0:a['state']='dec'
                if used>=256:break
            if not decs and used==0:
                t=max(t+0.001,min([a['wake'] for a in ag if a['state']=='think'],default=t+1));continue
            step=(T0+CSEQ*len(decs) if decs else 0)+TPP*used
        else:
            # decode step on P; side engine prefills concurrently during the step
            if decs: step=(T0+CSEQ*len(decs))*slow
            elif pfs: step=0.05
            else:
                t=max(t+0.001,min([a['wake'] for a in ag if a['state']=='think'],default=t+1));continue
            cap=R_side*step
            # 'split+P': P-cores also prefill when no decoders (time-sliced)
            if mode=='split+P' and not decs: cap+=step/TPP
            for a in pfs:  # side engine FCFS
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
    for N in range(1,48):
        ok=all((lambda r:r[0]<=slo and r[1]<=3*slo)(run(N,slo=slo,seed=s,**kw)) for s in (1,2,3))
        if ok:best=N
        elif N>best+3:break
    return best
cfg={'fused':dict(mode='fused'),
 'split iGPU66 slow1.15':dict(mode='split',R_side=66,slow=1.15),
 'split iGPU+E 130 slow1.25':dict(mode='split',R_side=130,slow=1.25),
 'split iGPU+E 130 slow1.25 +P':dict(mode='split+P',R_side=130,slow=1.25),
 'split ideal172 slow1.0':dict(mode='split',R_side=172,slow=1.0)}
for hf in (0.0,0.3):
  for slo in (10,20,30):
    print('heavy',hf,'slo',slo,{k:cap(slo,heavy_frac=hf,**v) for k,v in cfg.items()},flush=True)

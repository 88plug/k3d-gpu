import random
T0=0.062; CSEQ=0.0019; TPP=1/172.0
def run(N, chunk=256, policy='fcfs', adaptive=False, slo=20.0, heavy_frac=0.0, warm=False, think=5.0, seed=1, horizon=1800, decode_first_pf_cap=None):
    rng=random.Random(seed); t=0.0; ag=[]
    for i in range(N):
        h = i < round(heavy_frac*N)
        ag.append(dict(h=h,state='think',wake=rng.expovariate(1/think)*rng.random(),pf=0,dec=0,start=0,prev=0,wd=0,out=(200 if h else 96),new=(1500 if h else 300)))
    lat={False:[],True:[]}
    while t<horizon:
        for a in ag:
            if a['state']=='think' and a['wake']<=t:
                a['state']='pf';a['start']=t;a['pf']=a['new']+max(0,a['prev']-a['wd']);a['dec']=a['out'];a['wd']=0
        decs=[a for a in ag if a['state']=='dec']; pfs=[a for a in ag if a['state']=='pf']
        if policy=='fcfs': pfs.sort(key=lambda a:a['start'])
        elif policy=='srpt': pfs.sort(key=lambda a:a['pf'])
        elif policy=='edf': pfs.sort(key=lambda a:a['start']+slo*(3 if a['h'] else 1))
        budget=chunk
        if adaptive and decs:
            # slack-aware: worst decoder slack -> allowed prefill time per step so remaining steps fit
            worst=min((a['start']+slo*(3 if a['h'] else 1)-t)/max(1,a['dec']) for a in decs)
            allow=worst-(T0+CSEQ*len(decs))
            budget=max(16,min(1024,int(allow/TPP)))
            # but if an urgent prefill waiting (its own slack low) give it full chunk
            if pfs:
                p=pfs[0]; ps=(p['start']+slo*(3 if p['h'] else 1)-t)-(p['pf']*TPP+p['out']*(T0+CSEQ*len(decs)))
                if ps<worst*p['out']: budget=max(budget,256)
        used=0
        for a in pfs:
            take=min(a['pf'],budget-used);a['pf']-=take;used+=take
            if a['pf']==0:a['state']='dec'
            if used>=budget:break
        if warm and used<budget:
            for a in ag:
                if a['state']=='think' and a['prev']>a['wd']:
                    take=min(a['prev']-a['wd'],budget-used);a['wd']+=take;used+=take
                    if used>=budget:break
        if not decs and used==0:
            t=max(t+0.001,min([a['wake'] for a in ag if a['state']=='think'],default=t+1));continue
        t+=(T0+CSEQ*len(decs) if decs else 0)+TPP*used
        for a in decs:
            a['dec']-=1
            if a['dec']==0:
                lat[a['h']].append(t-a['start']);a['state']='think';a['prev']=a['out'];a['wake']=t+rng.expovariate(1/think)
    def p95(x):
        x=sorted(x); return x[int(0.95*(len(x)-1))] if x else 0
    return p95(lat[False]),p95(lat[True])
def cap(slo,**kw):
    best=0
    for N in range(1,41):
        ok=True
        for s in (1,2,3):
            l,h=run(N,slo=slo,seed=s,**kw)
            if l>slo or h>3*slo: ok=False;break
        if ok: best=N
        elif N>best+3: break
    return best
cfgs={'fcfs256':dict(),'fcfs64':dict(chunk=64),'fcfs1024':dict(chunk=1024),'adapt':dict(adaptive=True,policy='edf'),'adapt+warm':dict(adaptive=True,policy='edf',warm=True)}
for hf in (0.0,0.3):
  for slo in (10,20,30):
    print('heavy',hf,'slo',slo,{k:cap(slo,heavy_frac=hf,**v) for k,v in cfgs.items()},flush=True)

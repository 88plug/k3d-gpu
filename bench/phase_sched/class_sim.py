import random
T0=0.062; CSEQ=0.0019; TPP=1/172.0
def run(NI, NB, prio, seed=1, horizon=2400, think_i=5.0, think_b=2.0, bg_chunk=64):
    rng=random.Random(seed); t=0.0; ag=[]
    for i in range(NI+NB):
        b=i>=NI
        ag.append(dict(b=b,state='think',wake=rng.expovariate(1/(think_b if b else think_i))*rng.random(),pf=0,dec=0,start=0,out=96,new=396))
    lat={False:[],True:[]}
    while t<horizon:
        for a in ag:
            if a['state']=='think' and a['wake']<=t:
                a['state']='pf';a['start']=t;a['pf']=a['new'];a['dec']=a['out']
        decs=[a for a in ag if a['state']=='dec']
        # in prio mode, background decoders only run when no interactive prefill is pending is unnecessary (CSEQ small); keep them.
        pfs=[a for a in ag if a['state']=='pf']
        if prio:
            ip=sorted([a for a in pfs if not a['b']],key=lambda a:a['start']); bp=sorted([a for a in pfs if a['b']],key=lambda a:a['start'])
            order=ip+bp
        else: order=sorted(pfs,key=lambda a:a['start'])
        used=0; budget=256
        for a in order:
            lim=budget if (not prio or not a['b']) else (bg_chunk if any(not d['b'] for d in decs) else budget)
            take=min(a['pf'],lim-used) if lim>used else 0
            a['pf']-=take;used+=take
            if a['pf']==0:a['state']='dec'
            if used>=budget:break
        if not decs and used==0:
            t=max(t+0.001,min([a['wake'] for a in ag if a['state']=='think'],default=t+1));continue
        t+=(T0+CSEQ*len(decs) if decs else 0)+TPP*used
        for a in decs:
            a['dec']-=1
            if a['dec']==0:
                lat[a['b']].append(t-a['start']);a['state']='think';a['wake']=t+rng.expovariate(1/(think_b if a['b'] else think_i))
    p=lambda x:(sorted(x)[int(0.95*(len(x)-1))] if x else 0)
    return p(lat[False]),p(lat[True]),len(lat[True])

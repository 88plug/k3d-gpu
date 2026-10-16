from class_sim import run
import sys
for NI,slo in ((2,20),(3,20),(4,20),(5,30),(7,30)):
    out=[]
    for prio in (False,True):
        best=0
        for NB in range(0,30):
            rs=[run(NI,NB,prio,seed=s,horizon=1800) for s in (1,2,3)]
            if all(r[0]<=slo and r[1]<=120 for r in rs): best=NB
            elif NB>best+2:break
        out.append(best)
    print('NI',NI,'slo',slo,'bg fcfs',out[0],'bg prio',out[1],'total',NI+out[0],'->',NI+out[1],flush=True)

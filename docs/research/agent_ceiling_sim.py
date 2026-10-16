# Closed-loop agent ceiling sim, stdlib only. Inputs: agent_workload_params.json.
# Engine numbers are H2 measurements (Qwen3-4B int4, OVMS, i9-13900T P-cores).
# Decode beyond c=4 is NOT measured: cap=56 (flat) and cap=100 (still scaling) bracket it.
# Time-stepped (dt) single CPU engine:
# prefill FCFS at P tok/s preempts decode (compute share); decode batch b at agg rate R(b).
import random, math
def lognorm(mean, sd, rng):
    s2=math.log(1+(sd/mean)**2); mu=math.log(mean)-s2/2
    return rng.lognormvariate(mu, math.sqrt(s2))
def R(b, cap):  # aggregate decode tok/s; measured 1->15.7, 4->56; beyond 4: linear-to-cap
    if b<=0: return 0
    if b<=4: return 15.7+(56-15.7)*(b-1)/3
    return min(cap, 56+(cap-56)*(b-4)/4) if cap>56 else 56
def sim(N, delta, out, tool_mean, tool_sd, cap, P=172.0, T=1800, seed=1):
    rng=random.Random(seed); dt=0.02; t=0
    pq=[]; lat=[]
    agents={i:{'ph':'tool','rem':rng.uniform(0,3),'t0':0.0} for i in range(N)}
    while t<T:
        for i,a in agents.items():
            if a['ph']=='tool':
                a['rem']-=dt
                if a['rem']<=0: a['ph']='pre'; a['rem']=delta; a['t0']=t; pq.append(i)
        if pq:  # prefill owns the engine this tick
            i=pq[0]; a=agents[i]; a['rem']-=P*dt
            if a['rem']<=0: pq.pop(0); a['ph']='dec'; a['rem']=out
        else:
            dec=[i for i,a in agents.items() if a['ph']=='dec']
            if dec:
                per=R(len(dec),cap)/len(dec)*dt
                for i in dec:
                    a=agents[i]; a['rem']-=per
                    if a['rem']<=0:
                        if t>120: lat.append(t-a['t0'])
                        a['ph']='tool'; a['rem']=lognorm(tool_mean,tool_sd,rng)
        t+=dt
    lat.sort()
    return (lat[int(.95*len(lat))] if lat else float('inf')), len(lat)/(T-120)
if __name__ == '__main__':
  scen={'toolbench-like (d=300,o=43)':(300,43),'react-small (d=600,o=100)':(600,100),'swe-like (d=1500,o=200)':(1500,200)}
  for name,(d,o) in scen.items():
    for tm,ts,tl in [(0.925,3.55,'SWE tool'),(1.923,2.133,'BFCL tool')]:
      for cap in (56,100):
        row=[]
        for N in (1,2,4,6,8,12,16):
          p95,thr=sim(N,d,o,tm,ts,cap)
          row.append(f"N{N}:{p95:5.1f}s")
        print(f"{name:28s} {tl:9s} cap{cap:3d} "+' '.join(row))

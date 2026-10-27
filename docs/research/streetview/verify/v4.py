import json, numpy as np, laspy, glob, collections
from scipy.spatial import cKDTree
m=json.load(open("../coverage_z17_meta.json"))
P=[];C=[]
for f in glob.glob("../lidar/*.laz"):
    l=laspy.read(f); P.append(np.c_[l.x,l.y,l.z]); C.append(np.array(l.classification))
    print(f, len(l.x), "x",l.x.min(),l.x.max(),"y",l.y.min(),l.y.max())
P=np.vstack(P); C=np.concatenate(C)
print("classes",collections.Counter(C.tolist()).most_common(8))
g=P[C==2]; t=cKDTree(g[:,:2])
out=[]
for p in m:
    if p.get("elev") is None: continue
    ii=t.query_ball_point([p["x"],p["y"]],1.5)
    if len(ii)<5: continue
    zg=np.median(g[ii,2]); out.append((p["id"],p["date"],p["elev"],zg,p["elev"]-zg))
h=np.array([o[4] for o in out])
print("n panos with lidar ground",len(out),"elev-ground median",np.median(h).round(3),"p10",np.percentile(h,10).round(3),"p90",np.percentile(h,90).round(3),"min",h.min().round(2),"max",h.max().round(2))
by=collections.defaultdict(list)
for o in out: by[o[1][:4]].append(o[4])
for k in sorted(by): print(k,len(by[k]),np.round(np.median(by[k]),3), np.round(np.std(by[k]),3))

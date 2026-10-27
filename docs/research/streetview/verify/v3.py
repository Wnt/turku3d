import numpy as np, sys
from streetlevel import streetview
import streetlevel.streetview.depth as D
cap={}; o=D.parse_planes
def pp(h,r):
    x=o(h,r); cap["p"]=x; cap["h"]=h; return x
D.parse_planes=pp
for pid in sys.argv[1:]:
    p=streetview.find_panorama_by_id(pid, download_depth=True)
    H,W=cap["h"]["height"],cap["h"]["width"]
    idx=np.array(cap["p"]["indices"]).reshape(H,W)
    pls=cap["p"]["planes"]
    rows=[]
    for i,pl in enumerate(pls):
        c=(idx==i).sum()
        if abs(pl["n"][2])>0.9 and c>20 and (np.where(idx==i)[0].mean()>H/2):
            rows.append((round(abs(pl["d"]),3), c, [round(v,3) for v in pl["n"]]))
    rows.sort(key=lambda r:-r[1])
    print(pid, "horizontal ground-ish planes (|d| = height below camera, px count):")
    for r in rows[:15]: print("  ",r)
    ds=np.array([r[0] for r in rows]); w=np.array([r[1] for r in rows])
    print("  dominant d", ds[0], "spread of d among planes with >200px:", sorted(set(ds[w>200])))

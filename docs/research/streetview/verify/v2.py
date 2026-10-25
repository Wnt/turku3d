import json, numpy as np
from streetlevel import streetview
import streetlevel.streetview.depth as D
cap={}
o=D.parse_planes
def pp(h,r):
    x=o(h,r); cap["p"]=x; cap["h"]=h; return x
D.parse_planes=pp
m=json.load(open("../coverage_z17_meta.json"))
ids=["9oQUgUbnSDpde6Vhe1T8xA"]+[p["id"] for p in m[::30] if p.get("depth")]
res=[]
for pid in ids[:11]:
    try: p=streetview.find_panorama_by_id(pid, download_depth=True)
    except Exception as e: print(pid,"ERR",e); continue
    idx=np.array(cap["p"]["indices"]).reshape(cap["h"]["height"],cap["h"]["width"])
    used=np.unique(idx); n=len(cap["p"]["planes"])
    lower=np.unique(idx[cap["h"]["height"]//2+5:,:])
    # normals of planes with large lower-hemisphere usage: ground planes have n ~ (0,0,+-1)
    gp=[pl for i,pl in enumerate(cap["p"]["planes"]) if i in lower and abs(pl["n"][2])>0.9] if isinstance(cap["p"]["planes"][0],dict) else None
    print(pid, p.date, "planes", n, "used", len(used), "lower-hemi planes", len(lower), "near-horizontal planes in lower hemi", None if gp is None else len(gp), "elev", round(p.elevation,2))

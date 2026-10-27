import time, numpy as np, json
from streetlevel import streetview
import streetlevel.streetview.depth as D
# patch parse_planes to count planes
orig = D.parse_planes
info = {}
def pp(*a, **k):
    r = orig(*a, **k); info["planes"] = r; return r
D.parse_planes = pp
for pid in ["Nb9Xt8amYTusRITAXv3umQ", "staCISwu-xHr70y_JtFYdA"]:
    t=time.time()
    p = streetview.find_panorama_by_id(pid, download_depth=True)
    print(pid, p.date, "elev", p.elevation, "sizes", [ (s.x,s.y) if hasattr(s,"x") else str(s) for s in p.image_sizes][-2:], "hist", [(h.id,str(h.date)) for h in p.historical][:3])
    if p.depth is not None:
        dd = p.depth.data; print(" depth shape", dd.shape, "min", dd[dd>0].min(), "max", dd.max(), "sky", (dd<0).mean())
        pl = info.get("planes"); print(" n planes raw:", len(pl) if pl is not None else None)
        # count unique plane index map if exposed
        print(" depth attrs", [a for a in dir(p.depth) if not a.startswith("_")])
    print(" t", round(time.time()-t,2))

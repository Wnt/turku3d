import json, collections, numpy as np
from concurrent.futures import ThreadPoolExecutor
from streetlevel import streetview
ps = json.load(open("coverage_z17.json"))
def f(p):
    try:
        q = streetview.find_panorama_by_id(p["id"], download_depth=True)
        return dict(p, date=str(q.date), hist=[str(h.date) for h in q.historical], depth=q.depth is not None,
                    maxw=q.image_sizes[-1].x if q.image_sizes else None, elev=q.elevation, src=q.source)
    except Exception as e:
        return dict(p, err=str(e))
with ThreadPoolExecutor(6) as ex: out = list(ex.map(f, ps))
json.dump(out, open("coverage_z17_meta.json","w"))
ok=[o for o in out if "err" not in o]
print("ok", len(ok), "err", len(out)-len(ok))
print("current date:", sorted(collections.Counter(o["date"] for o in ok).items()))
allyears = collections.Counter(d[:4] for o in ok for d in [o["date"]]+o["hist"])
print("all captures by year (incl historical):", sorted(allyears.items()))
print("depth available:", collections.Counter(o["depth"] for o in ok))
print("max width:", collections.Counter(o["maxw"] for o in ok))
print("source:", collections.Counter(o["src"] for o in ok))
print("n historical per pano:", collections.Counter(len(o["hist"]) for o in ok))

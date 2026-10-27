import sys, json, time, numpy as np
from PIL import Image
from streetlevel import streetview
pid = sys.argv[1]; zoom = int(sys.argv[2]) if len(sys.argv)>2 else 5
pano = streetview.find_panorama_by_id(pid, download_depth=True)
t=time.time()
img = streetview.get_panorama(pano, zoom=zoom)
print("pano", img.size, "secs", round(time.time()-t,1))
img.save(f"pano_{pid}_z{zoom}.jpg", quality=92)
img.resize((2048,1024), Image.LANCZOS).save(f"pano_{pid}_preview.jpg", quality=85)
meta = dict(id=pano.id, lat=pano.lat, lon=pano.lon, date=str(pano.date), elevation=pano.elevation,
  heading=pano.heading, pitch=pano.pitch, roll=pano.roll, source=pano.source,
  historical=[(h.id,str(h.date)) for h in pano.historical],
  links=[dict(id=l.pano.id, dir=l.direction) for l in pano.links],
  neighbors=[dict(id=n.id, lat=n.lat, lon=n.lon, date=str(n.date)) for n in pano.neighbors])
if pano.depth is not None:
    d = pano.depth.data.astype(np.float32)
    np.save(f"depth_{pid}.npy", d)
    v = d.copy(); v[v<0]=np.nan
    vis = np.clip(1 - np.nan_to_num(v, nan=100)/60, 0, 1)
    Image.fromarray((vis*255).astype(np.uint8)).resize((1024,512), Image.NEAREST).save(f"depth_{pid}_preview.png")
    meta["depth"] = dict(w=pano.depth.width, h=pano.depth.height, min=float(np.nanmin(v)), max=float(np.nanmax(v)), sky_frac=float((d<0).mean()))
json.dump(meta, open(f"meta_{pid}.json","w"), indent=1)
print(json.dumps({k:meta[k] for k in meta if k!='neighbors'}, indent=1))

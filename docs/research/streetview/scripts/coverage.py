import json, collections, numpy as np
from streetlevel import streetview
from streetlevel.geo import wgs84_to_tile_coord
from pyproj import Transformer
t = Transformer.from_crs(4326, 3877, always_xy=True)
lat, lon = 60.4506, 22.2642
tx, ty = wgs84_to_tile_coord(lat, lon, 17)
allp = {}
for dx in (-1,0,1):
  for dy in (-1,0,1):
    for p in streetview.get_coverage_tile(tx+dx, ty+dy):
      allp[p.id] = p
print("tiles z17 around", tx, ty, "panos:", len(allp))
ps = list(allp.values())
xy = np.array([t.transform(p.lon, p.lat) for p in ps])
print("bbox m", xy.min(0).round(), xy.max(0).round(), "extent", (xy.max(0)-xy.min(0)).round())
dates = collections.Counter(str(p.date)[:4] for p in ps)
print("current-capture years:", sorted(dates.items()))
print("months:", collections.Counter(str(p.date) for p in ps).most_common(8))
print("sources:", collections.Counter(p.source for p in ps))
# nearest-neighbour spacing
from scipy.spatial import cKDTree
kd = cKDTree(xy); d,_ = kd.query(xy, k=2)
print("NN spacing m: median %.1f p10 %.1f p90 %.1f" % tuple(np.percentile(d[:,1],[50,10,90])))
# 500m x 500m box around centre
cx, cy = 23459500, 6704500
m = (abs(xy[:,0]-cx)<250)&(abs(xy[:,1]-cy)<250)
print("panos in 500x500 m box:", m.sum())
json.dump([dict(id=p.id, lat=p.lat, lon=p.lon, date=str(p.date), x=float(a), y=float(b)) for p,(a,b) in zip(ps,xy)], open("coverage_z17.json","w"))

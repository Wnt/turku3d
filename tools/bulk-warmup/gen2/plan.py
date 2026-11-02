#!/usr/bin/env python3
"""Municipality L0 footprint, 500 m dependency halo, and a 15 km far-scene ring."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pyproj import Transformer
from shapely.geometry import shape, box
from shapely.ops import transform
from turku3d import config as C

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('state', type=Path)
a=p.parse_args()
source=a.state/'turku-boundary.geojson'
feature=json.loads(source.read_text())['features'][0]
assert feature['properties']['name']=='Turku'
municipality=transform(Transformer.from_crs(4326,3877,always_xy=True).transform,shape(feature['geometry']))
# Coordinates are engine X/Z; the northern half of Turku has negative tile j.
local=transform(lambda x,y:(x-C.ORIGIN_E,C.ORIGIN_N-y),municipality)
far=local.buffer(15000)
def cells(geom,step):
 x0,z0,x1,z1=geom.bounds
 return [(i,j) for i in range(math.floor(x0/step),math.ceil(x1/step)) for j in range(math.floor(z0/step),math.ceil(z1/step)) if geom.intersects(box(i*step,j*step,(i+1)*step,(j+1)*step))]
old=json.loads((a.state/'previous-walk.json').read_text())
city={f't0_{i}_{j}' for i,j in cells(local,250)}
full=city|set(old)
# Far scene is source-direct coarse terrain, never 250 m collision tiles outside the city.
far_cells=cells(far,1000)
ordered=list(dict.fromkeys([*old,*sorted(city,key=lambda t:sum(int(v)**2 for v in t.split('_')[1:]))]))
(a.state/'walk.json').write_text(json.dumps(ordered)+'\n')
halo={f't0_{i+dx}_{j+dz}' for t in full for _,i,j in [tuple(map(int,t.replace('t','').split('_')))] for dx in range(-2,3) for dz in range(-2,3)}
(a.state/'input-tiles.json').write_text(json.dumps(sorted(halo))+'\n')
(a.state/'far-cells.json').write_text(json.dumps(far_cells)+'\n')
result=dict(boundary_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),boundary_properties=feature['properties'],city_tiles=len(city),previous_tiles=len(old),build_tiles=len(full),input_halo_tiles=len(halo),far_1km_cells=len(far_cells),far_buffer_m=15000,city_bounds_local=local.bounds,regional_bounds_local=far.bounds,far_policy='source-direct scenery, no full collision build outside city/walk')
(a.state/'plan.json').write_text(json.dumps(result,indent=2)+'\n')
fig,ax=plt.subplots(figsize=(9,11))
xz=[(i+.5,j+.5) for i,j in far_cells]
ax.scatter([x for x,z in xz],[-z for x,z in xz],s=5,c='#cddbe2',marker='s',label='15 km far terrain (1 km cells)')
for g in getattr(local,'geoms',[local]):
 x,z=g.exterior.xy; ax.plot([v/1000 for v in x],[-v/1000 for v in z],color='#273f56',linewidth=1)
for tiles,color,label in [(city,'#1b998b',f'Municipality L0: {len(city):,}'),(set(old),'#ee964b',f'Previous walk: {len(old):,}')]:
 xy=[tuple(map(int,t.split('_')[1:])) for t in tiles]
 ax.scatter([(x+.5)/4 for x,z in xy],[-(z+.5)/4 for x,z in xy],s=2,color=color,marker='s',label=label)
ax.set_aspect('equal'); ax.set_xlabel('East from origin (km)'); ax.set_ylabel('North from origin (km)'); ax.legend(loc='upper left'); ax.set_title(f'Turku Gen 2 staging — {len(full):,} full tiles\nMunicipal boundary: OpenStreetMap, ODbL 1.0'); fig.tight_layout(); fig.savefig(a.state/'tile-map.png',dpi=160)
print(json.dumps(result))

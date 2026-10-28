"""Render only licensed city/OSM data and procedural maps; never open street photographs."""
import json
import os
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image
import geopandas as gpd
import rasterio
import laspy
from shapely.geometry import box, Point

root = Path(os.environ['TURKU3D_WORK']) / 'video/assets'
root.mkdir(parents=True, exist_ok=True)
raw = Path(os.environ['TURKU3D_DATA']) / 'raw'
bounds = (23459500, 6704100, 23459830, 6704430)
plt.rcParams.update({'figure.facecolor':'#0c1725','axes.facecolor':'#0c1725','text.color':'#edf4fa','axes.labelcolor':'#ccd9e7','xtick.color':'#9caec2','ytick.color':'#9caec2','font.size':17})

# Same place as the overhead camera; the video labels the source and rendered views.
with rasterio.open(raw/'ortho/ortho2022to/work-3x3.vrt') as ds:
    win = rasterio.windows.from_bounds(*bounds, transform=ds.transform)
    rgb = ds.read([1,2,3],window=win,out_shape=(3,1400,1400))
Image.fromarray(np.moveaxis(rgb,0,2)).save(root/'ortho.png')
print('ortho',flush=True)

parts=[]
with laspy.open(raw/'laz-3x3/23459500_6704500.laz') as ds:
    for c in ds.chunk_iterator(1_000_000):
        x,y=np.asarray(c.x),np.asarray(c.y)
        keep=(x>bounds[0])&(x<bounds[2])&(y>bounds[1])&(y<bounds[3])
        if keep.any(): parts.append(np.column_stack((x[keep]-23459500,y[keep]-6704500,np.asarray(c.z)[keep],np.asarray(c.classification)[keep])))
p=np.concatenate(parts)
np.save(root/'office-points.npy',p[::max(1,len(p)//300000)])
p=p[::max(1,len(p)//140000)]
fig=plt.figure(figsize=(14,8),dpi=140); ax=fig.add_subplot(111,projection='3d')
ax.scatter(p[:,0],p[:,1],p[:,2],c=p[:,2],cmap='turbo',s=.17,alpha=.85,rasterized=True)
ax.view_init(32,-70);ax.set_box_aspect((1,1,.34));ax.set_axis_off();fig.subplots_adjust(0,0,1,1)
fig.savefig(root/'lidar.png',facecolor=fig.get_facecolor());plt.close(fig)
ground=p[np.isin(p[:,3],[2,12])]
# A real narrow lidar section across the riverfront, not invented curb heights.
near=ground[np.abs(ground[:,0]-168)<1.5]
fig,ax=plt.subplots(figsize=(10,6),dpi=140)
ax.scatter(near[:,1],near[:,2],s=14,c='#75e7cf');ax.set_ylim(-1,15);ax.set_xlim(-350,-100)
ax.set_xlabel('North from origin / m',fontsize=24);ax.set_ylabel('Height / m',fontsize=24)
ax.tick_params(labelsize=24);ax.grid(alpha=.15)
fig.tight_layout();fig.savefig(root/'relief.png');plt.close(fig)
print('lidar',flush=True)

lod=gpd.read_parquet(raw/'wfs/work-3x3/bldg_Building_LOD2.parquet')
sub=lod[lod.geometry.intersects(box(*bounds))]
polys=[]; colors=[]
for _, row in sub.iterrows():
    geom=row.geometry
    if geom.geom_type!='Polygon' or not geom.has_z: continue
    coords=np.asarray(geom.exterior.coords); coords[:,0]-=23459500;coords[:,1]-=6704500
    polys.append(coords);colors.append('#e7b773' if row.surface_type=='RoofSurface' else '#518d9c')
fig=plt.figure(figsize=(14,8),dpi=140);ax=fig.add_subplot(111,projection='3d')
ax.add_collection3d(Poly3DCollection(polys,facecolors=colors,edgecolor='#122a36',linewidths=.2))
ax.set_xlim(0,330);ax.set_ylim(-400,-70);ax.set_zlim(0,70);ax.set_box_aspect((1,1,.32));ax.view_init(38,-65);ax.set_axis_off();fig.subplots_adjust(0,0,1,1);fig.savefig(root/'lod2.png');plt.close(fig)
reg=gpd.read_parquet(raw/'wfs/work-3x3/GIS_Rakennukset.parquet')
row=reg.iloc[reg.geometry.distance(Point(23459668,6704254)).argmin()]
fields={k:str(row[k]) for k in ['PysyvaRakennusTunnus','Osoitenimi_fi','Osoitenumero','Valmistunut','Kerrosluku']}
(root/'register.json').write_text(json.dumps(fields,indent=2,ensure_ascii=False))
print('building',fields,flush=True)

osm=gpd.read_parquet(raw/'osm/work-3x3/work-3x3-261024.parquet')
local=osm[osm.geometry.intersects(box(23459000,6703900,23460500,6705000))]
fig,ax=plt.subplots(figsize=(14,8),dpi=140)
for field,color,width,label in [('highway','#d8e9ed',1.2,'Streets & paths'),('barrier','#e9b572',2,'Barriers'),('natural','#63d6a9',2,'Trees & nature')]:
    group=local[local[field].notna()]
    if len(group): group.plot(ax=ax,color=color,linewidth=width,markersize=7,label=label)
ax.set_xlim(23459000,23460500);ax.set_ylim(6703900,6705000);ax.set_axis_off();fig.subplots_adjust(0,0,1,1);fig.savefig(root/'osm.png');plt.close(fig)
print('osm',flush=True)
(root/'source-provenance.json').write_text(json.dumps({'bbox_epsg3877':bounds,'city':'© City of Turku · CC BY 4.0','osm':'© OpenStreetMap contributors · ODbL','lidar':'raw/laz-3x3/23459500_6704500.laz','ortho':'raw/ortho/ortho2022to/work-3x3.vrt','lod2':'raw/wfs/work-3x3/bldg_Building_LOD2.parquet','register':fields},indent=2,ensure_ascii=False))

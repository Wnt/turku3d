"""Low-cost terrain beyond the city survey, from attributed public elevation/land cover."""
from functools import lru_cache
from pathlib import Path
import numpy as np
from scipy.ndimage import map_coordinates
from turku3d import config as C
from .far_ground import survey_colours

@lru_cache(maxsize=2)
def grid(path):
    with np.load(path) as data:
        return {key: data[key] for key in data.files}

def covers(tile, path):
    data = grid(str(path)); step = float(data['step'])
    x,z = float(data['xmin']),float(data['zmin'])
    rows,cols = data['height'].shape
    return (tile.bbox.xmin >= x and tile.bbox.zmin >= z
            and tile.bbox.xmax <= x+(cols-1)*step and tile.bbox.zmax <= z+(rows-1)*step)

def mesh(tile, path):
    from .horizon import layer_mesh
    data = grid(str(path))
    if not covers(tile,path): raise ValueError(f'{tile.id}: outside regional source grid')
    step = 62.5 if tile.level >= 4 else 31.25 if tile.level >= 2 else 15.625
    count = int(round(tile.size/step))
    xs=np.linspace(tile.bbox.xmin,tile.bbox.xmax,count+1)
    zs=np.linspace(tile.bbox.zmin,tile.bbox.zmax,count+1)
    gx,gz=np.meshgrid(xs,zs)
    sample=np.array([(gz.ravel()-data['zmin'])/data['step'],(gx.ravel()-data['xmin'])/data['step']])
    h=map_coordinates(data['height'],sample,order=1,mode='nearest')
    cover=map_coordinates(data['cover'],sample,order=0,mode='nearest')
    h[cover==80]=0
    p=np.c_[gx.ravel(),h,gz.ravel()]
    palette={10:(.12,.19,.08),20:(.20,.25,.12),30:(.24,.29,.16),40:(.32,.34,.16),
             50:survey_colours()['built'],60:(.38,.35,.28),70:(.75,.78,.80),80:(.10,.19,.24),90:(.18,.26,.16),95:(.12,.19,.08),100:(.24,.29,.16)}
    if not np.isin(cover,list(palette)).all(): raise ValueError('Unknown regional land-cover category')
    colours=np.array([palette[int(kind)] for kind in cover],dtype=np.float32)
    a=(np.arange(count)[:,None]*(count+1)+np.arange(count)[None,:]).ravel()
    ix=np.concatenate([np.c_[a,a+count+1,a+1],np.c_[a+1,a+count+1,a+count+2]])
    return layer_mesh('regional' if tile.level >= 4 else 'ground',p,ix,colours,np.asarray(tile.origin))

def build(tile, path):
    terrain=mesh(tile,path)
    return [terrain],dict(triangles=len(terrain.indices),draws=1,regional=True)

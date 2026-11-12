"""Retained explicit mapped paint, draped onto canonical road receivers."""
import hashlib
import pickle
from pathlib import Path
import shapely


def load(cache,bounds):
    polygons=[];sources=[];window=shapely.box(*bounds)
    for path in sorted(Path(cache).glob('*/markings.*.pkl')):
        raw=path.read_bytes();value=pickle.loads(raw)['value']
        selected=[g.intersection(window) for g in value if g.intersects(window)]
        polygons.extend(g for g in selected if g.area>1e-6)
        sources.append(dict(id=str(path.relative_to(cache)),sha256=hashlib.sha256(raw).hexdigest(),
                            kind='mapped-road-paint',uncertaintyM=.3))
    return polygons,sources


def append(scene,polygons):
    if not polygons:return
    from .fixtures import support_query
    from .surface_inlays import drape
    query=support_query(scene);material='mapped-road-paint'
    scene['materials'].append(dict(id=material,color=[.72,.71,.66],roughness=.9,metalness=0.))
    for i,polygon in enumerate(polygons):
        centre=polygon.representative_point();hit=query(centre.x,centre.y)
        if hit is None or hit[0]['kind'] not in ('road','crossing','cycleway'):continue
        meshes,_=drape(scene,f'mapped-road-paint:{i}',polygon,material,
                       support_kinds=('road','crossing','cycleway'),reference_height=hit[1],height_tolerance=.5)
        scene['meshes'].extend(meshes)

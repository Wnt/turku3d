"""Read immutable city inputs; keep source IDs and complete (unclipped) footprints."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Polygon, box
from shapely.ops import transform

from turku3d import config as C


@dataclass
class Building:
    id: str
    footprint: Polygon  # engine x/z, source start vertex retained
    properties: dict = field(default_factory=dict)


def envelope():
    tiles = C.OFFICE_BLOCK.visual_tiles
    return C.BBoxLocal(min(t.bbox.xmin for t in tiles), min(t.bbox.zmin for t in tiles),
                       max(t.bbox.xmax for t in tiles), max(t.bbox.zmax for t in tiles)).buffered(C.MESH_MARGIN)


def local_polygon(poly):
    return transform(lambda e, n, h=None: (np.asarray(e)-C.ORIGIN_E, C.ORIGIN_N-np.asarray(n)), poly)


def read_footprints(path: Path) -> list[Building]:
    df = gpd.read_parquet(path)
    df = df[df.geom_type.isin(['Polygon', 'MultiPolygon']) & df.kplkoodi.isin(['66000', '66001'])]
    bounds = box(*envelope().to_en().as_tuple())
    buildings, seen = [], set()
    for row in df.itertuples():
        if not row.geometry.intersects(bounds):
            continue
        poly = shapely.force_2d(row.geometry)
        if not poly.is_valid:
            raise ValueError(f'invalid kantakartta footprint: {row.feature_key}')
        bid = str(row.rakennustunnus) if row.rakennustunnus is not None else ''
        if bid in ('', '<NA>', 'nan', 'None'):
            bid = 'kanta-' + row.feature_key
        parts = list(poly.geoms) if poly.geom_type == 'MultiPolygon' else [poly]
        for k, part in enumerate(parts):
            key = shapely.normalize(part).wkb
            if key in seen:
                continue
            seen.add(key)
            ident = bid + (f'-part{k}' if len(parts) > 1 else '')
            if any(b.id == ident for b in buildings):
                ident += '-' + hashlib.sha256(key).hexdigest()[:12]
            buildings.append(Building(ident, local_polygon(part), {
                'source_feature': row.feature_key, 'registry_id': bid,
                'facade_material': str(row.julkisivumateriaali), 'levels': str(row.kerrosluku)}))
    return sorted(buildings, key=lambda b: b.id)


def write_roofer_footprints(buildings, path):
    polys = [transform(lambda x, z: (np.asarray(x)+C.ORIGIN_E, C.ORIGIN_N-np.asarray(z)), b.footprint)
             for b in buildings]
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({'building_id': [b.id for b in buildings]}, geometry=polys,
                     crs=C.CRS_HORIZONTAL).to_file(path, layer='footprints', driver='GPKG')


def read_points(paths, bounds):
    """Chunked class-6 read; no downsampling and no class-12 contamination in residuals."""
    import laspy
    chunks = []
    for path in paths:
        with laspy.open(path) as reader:
            for p in reader.chunk_iterator(1_000_000):
                select = (p.classification == 6)
                e, n, h = np.asarray(p.x)[select], np.asarray(p.y)[select], np.asarray(p.z)[select]
                x, y, z = C.en_to_local(e, n, h)
                keep = ((x >= bounds.xmin) & (x <= bounds.xmax) & (z >= bounds.zmin) & (z <= bounds.zmax))
                if keep.any():
                    chunks.append(np.column_stack((x[keep], y[keep], z[keep])))
    return np.concatenate(chunks) if chunks else np.empty((0, 3), dtype=np.float64)


def attach_osm(buildings, path):
    df = gpd.read_parquet(path)
    for b in buildings:
        en = transform(lambda x, z: (np.asarray(x)+C.ORIGIN_E, C.ORIGIN_N-np.asarray(z)), b.footprint)
        entries, best, score = [], {}, 0.
        for i in df.sindex.query(en.buffer(1)):
            row = df.iloc[i]
            tags = json.loads(row.tags)
            if tags.get('entrance') and en.distance(row.geometry) <= 1:
                entries.append({'osm_id': str(row.osm_id), 'tags': tags,
                                'geometry': shapely.geometry.mapping(row.geometry)})
            if tags.get('building') and row.geometry.area:
                overlap = en.intersection(row.geometry).area / en.union(row.geometry).area
                if overlap > score:
                    best, score = tags, overlap
        b.properties['osm'] = {k: best[k] for k in ('building:levels', 'roof:shape') if k in best}
        b.properties['entrances'] = entries

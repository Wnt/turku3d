"""CityGML surfaces from the lossless WFS GeoParquet cache (including holes)."""

import geopandas as gpd
import numpy as np
import shapely
from shapely.ops import unary_union

from turku3d import config as C

from .inputs import local_polygon
from .shells import Surface
from .source_surfaces import classify_surfaces


def read(path, buildings):
    df = classify_surfaces(gpd.read_parquet(path))
    candidates = []
    for key, group in df.groupby("feature_key", sort=True):
        grounds = group[group.surface_type == "GroundSurface"]
        fp = unary_union([shapely.force_2d(g) for g in grounds.geometry])
        if fp.is_empty or not fp.is_valid:
            continue
        candidates.append((key, group, local_polygon(fp)))
    out, matches = {}, {}
    tree = shapely.STRtree([fp for _, _, fp in candidates])
    for b in buildings:
        scores = [
            (
                candidates[k][2].intersection(b.footprint).area
                / candidates[k][2].union(b.footprint).area,
                k,
            )
            for k in tree.query(b.footprint)
        ]
        score, k = max(scores, default=(0, 0))
        if score < 0.5:
            continue
        key, group, _ = candidates[k]
        surfaces = []
        seen = set()
        for row in group.itertuples():
            if row.surface_id and row.surface_id in seen:
                continue
            seen.add(row.surface_id)
            rings = []
            for ring in [row.geometry.exterior, *row.geometry.interiors]:
                v = np.asarray(ring.coords)
                if v.shape[1] != 3:
                    raise ValueError(f"{key}: surface without N2000 heights")
                rings.append(np.column_stack(C.en_to_local(v[:, 0], v[:, 1], v[:, 2])))
            surfaces.append(Surface(rings, row.surface_type))
        out[b.id] = surfaces
        matches[b.id] = {"city_id": key, "footprint_iou": score}
    return out, matches

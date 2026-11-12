"""The shell and register facts of one LoD2 building record, read from the lossless WFS cache.

This is the building kind's own reader for the workbench and for `describe`: it turns the rows of one
`bldg:Building_LOD2` feature into the `shell` and `register` sections of a description
(docs/specs/building-description.md). It reads `$TURKU3D_DATA/raw/wfs/<area>/bldg_Building_LOD2.parquet`.
"""

import json
import os
from pathlib import Path

import numpy as np

LAYER = "bldg_Building_LOD2"
KINDS = {
    "WallSurface": "wall",
    "RoofSurface": "roof",
    "GroundSurface": "ground",
    "ClosureSurface": "closure",
}
TOUCH_M = 0.6


def wfs_root() -> Path:
    return Path(os.environ.get("TURKU3D_DATA", Path.home() / "turku3d/data")) / "raw/wfs"


def areas_at(e: float, n: float) -> list[Path]:
    """The cached WFS areas whose bounding box holds the point, smallest file first."""
    hits = []
    for summary in wfs_root().glob(f"*/{LAYER}.summary.json"):
        box = json.loads(summary.read_text()).get("bbox_en")
        parquet = summary.with_name(f"{LAYER}.parquet")
        if box and box[0] <= e <= box[2] and box[1] <= n <= box[3] and parquet.exists():
            hits.append(parquet)
    return sorted(hits, key=lambda p: p.stat().st_size)


def read_area(parquet: Path):
    import geopandas as gpd

    return gpd.read_parquet(parquet)


def key_of(row) -> str:
    """The entity key: the permanent building id, or the GML id when the record has none."""
    permanent = row.pysyvarakennustunnus
    return str(permanent) if isinstance(permanent, str) and permanent else f"gml-{row.gml_id}"


def _rings(polygon) -> list[list[list[float]]]:
    out = []
    for ring in [polygon.exterior, *polygon.interiors]:
        points = np.asarray(ring.coords)[:-1]
        out.append(
            [[round(float(e), 3), round(float(n), 3), round(float(h), 3)] for e, n, h in points]
        )
    return out


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def register(row) -> dict:
    out = {}
    if isinstance(row.taydellinen_osoite_fi, str):
        out["address"] = row.taydellinen_osoite_fi.split(",")[0]
    year = _number(row.yearOfConstruction) or _number(str(row.valmistunut or "")[-4:])
    if year:
        out["year"] = int(year)
    storeys = _number(row.kerrosluku) or _number(row.storeysAboveGround)
    if storeys and storeys >= 1:
        out["storeys"] = int(storeys)
    if isinstance(row.function, str) and row.function:
        out["use"] = row.function
    height = _number(row.measuredHeight)
    if height:
        out["height_m"] = height
    return out


def shell(rows, neighbours=()) -> dict:
    """The `shell` section from the rows of one building feature; `neighbours` are row groups of other features."""
    surfaces = []
    terrain = []
    seen = set()
    for row in rows.itertuples():
        geometry = row.geometry
        if geometry.geom_type == "LineString" and row.geom_role == "lod2TerrainIntersection":
            terrain.append([[round(float(v), 3) for v in p] for p in np.asarray(geometry.coords)])
        elif geometry.geom_type == "Polygon" and row.surface_type in KINDS:
            if row.surface_id and row.surface_id in seen:
                continue
            seen.add(row.surface_id)
            surfaces.append({"kind": KINDS[row.surface_type], "rings": _rings(geometry)})
    out = {"source": f"turku-lod2:{rows.iloc[0].feature_key}", "surfaces": surfaces}
    if terrain:
        out["terrain"] = terrain
    touching = []
    footprint = _footprint(rows)
    for other in neighbours:
        other_footprint = _footprint(other)
        if (
            footprint is None
            or other_footprint is None
            or footprint.distance(other_footprint) > TOUCH_M
        ):
            continue
        heights = _wall_heights(other)
        if heights is None:
            continue
        touching.append(
            {
                "id": f"building/{key_of(other.iloc[0])}",
                "footprint": [
                    [round(x, 3), round(y, 3)]
                    for x, y in list(other_footprint.exterior.coords)[:-1]
                ],
                "base_h": heights[0],
                "top_h": heights[1],
            }
        )
    if touching:
        out["neighbours"] = touching
    return out


def _footprint(rows):
    import shapely
    from shapely.ops import unary_union

    grounds = [shapely.force_2d(g) for g in rows[rows.surface_type == "GroundSurface"].geometry]
    if not grounds:
        return None
    union = unary_union(grounds).buffer(0)
    if union.geom_type == "MultiPolygon":
        union = max(union.geoms, key=lambda g: g.area)
    return union if union.geom_type == "Polygon" and not union.is_empty else None


def _wall_heights(rows):
    """Lowest wall point and the median wall top of a building: the height a party wall hides."""
    tops, lows = [], []
    for geometry in rows[rows.surface_type == "WallSurface"].geometry:
        z = np.asarray(geometry.exterior.coords)[:, 2]
        tops.append(z.max())
        lows.append(z.min())
    if not tops:
        return None
    return round(float(min(lows)), 3), round(float(np.median(tops)), 3)


def building_at(e: float, n: float, key: str | None = None, reach_m: float = 40.0):
    """(rows of the building, rows of each touching candidate) for a key near a point, or the nearest building."""
    import shapely

    for parquet in areas_at(e, n):
        frame = read_area(parquet)
        grounds = frame[frame.surface_type == "GroundSurface"]
        distance = grounds.geometry.apply(
            lambda g: shapely.force_2d(g).distance(shapely.Point(e, n))
        )
        near = grounds[distance < reach_m + 60]
        if key is not None:
            hit = frame[(frame.pysyvarakennustunnus == key) | ("gml-" + frame.gml_id == key)]
            if hit.empty:
                continue
            feature = hit.iloc[0].feature_key
        else:
            if near.empty:
                continue
            feature = grounds.loc[distance.idxmin()].feature_key
        rows = frame[frame.feature_key == feature]
        others = [frame[frame.feature_key == k] for k in near.feature_key.unique() if k != feature]
        return rows, others
    raise LookupError(f"no LoD2 building {key or ''} near {e:.0f}, {n:.0f} in {wfs_root()}")

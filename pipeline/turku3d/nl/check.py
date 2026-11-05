"""Counts of built NL tiles against their sources (run through tools/cities/run.sh <city>).

    python -m turku3d.nl.check OUT_ROOT t0_1_0 t0_0_0 ...   (OUT_ROOT/<tile>/manifest.json)

Per tile: 3DBAG buildings owned by the tile (largest LoD2.2 ground part's centroid inside it, as the
generator assigns them) vs the manifest's ``buildings``; BGT street-area and water polygons over the
tile with their area; ground key points; OSM roads in the manifest; trees. Prints one JSON line per
tile and a total; exit 1 when a tile with source buildings built none or fewer than 90 % of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import shapely

from turku3d import config as C
from turku3d.spatial import frame


def source_counts(tile: C.VisualTile) -> dict:
    from turku3d.coarse.sources import Sources

    sources = Sources()
    box = shapely.box(*tile.bbox.as_tuple())
    out = {}
    found, path = sources.wfs_source("bldg:Building_LOD2", tile)
    owned = 0
    if path:
        f = frame(path, tile.bbox.buffered(100), whole_features=True)
        f = f[f.surface_type == "GroundSurface"]
        for _, group in f.groupby("feature_key"):
            union = shapely.make_valid(shapely.union_all(group.geometry_local.apply(shapely.force_2d).values))
            polys = sorted(
                (p for p in getattr(union, "geoms", [union]) if p.geom_type == "Polygon"),
                key=lambda p: -p.area,
            )
            if polys and C.VisualTile.at(polys[0].centroid.x, polys[0].centroid.y) == tile:
                owned += 1
    out["3dbag_buildings"] = owned
    for layer, key in (("GIS:Katualueet", "bgt_streets"), ("kanta:Vesikohde", "bgt_water")):
        _, path = sources.wfs_source(layer, tile)
        if path is None:
            out[key], out[f"{key}_m2"] = 0, 0
            continue
        geoms = [shapely.force_2d(g) for g in frame(path, tile.bbox).geometry_local]
        geoms = [g for g in geoms if g.intersects(box)]
        out[key] = len(geoms)
        out[f"{key}_m2"] = round(shapely.union_all(geoms).intersection(box).area) if geoms else 0
    laz, _ = sources.lidar_paths(tile)
    out["ground_blocks"] = len(laz)
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root, tiles = Path(argv[0]), argv[1:]
    failed, total = False, {}
    for tid in tiles:
        tile = C.VisualTile.parse(tid)
        m = json.loads((root / tid / "manifest.json").read_text())
        row = {"tile": tid, "built_buildings": m.get("buildings"), "trees": m.get("trees"),
               "bridges": m.get("bridges"), "water_level": m.get("water_level"),
               "road_edges": (m.get("roads") or {}).get("edges") if isinstance(m.get("roads"), dict) else None,
               **source_counts(tile)}
        src, built = row["3dbag_buildings"], row["built_buildings"] or 0
        row["ok"] = not src or built >= 0.9 * src
        failed |= not row["ok"]
        print(json.dumps(row, ensure_ascii=False))
        for k, v in row.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "water_level":
                total[k] = total.get(k, 0) + v
    print(json.dumps({"total": total, "tiles": len(tiles), "ok": not failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

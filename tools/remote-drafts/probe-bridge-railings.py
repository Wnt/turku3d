"""Count cached source crossings affected by outer-edge railing selection; build the reported overpass."""

import importlib.util
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely
from turku3d import config as C
from turku3d.coarse.bridge_details import deck_outline, railing_lines
from turku3d.coarse.bridges import (
    RAILS,
    footprint,
    merge_crossings,
    number,
    way_width,
)


def audit():
    started = time.monotonic()
    ways, outlines = {}, {}
    paths = sorted(C.raw_dir("osm").glob("*/*.parquet"))
    for path in paths:
        rows = pq.read_table(path, columns=["osm_id", "tags", "geometry_local"]).to_pylist()
        for row in rows:
            tags = json.loads(row["tags"] or "{}")
            bridge = tags.get("bridge", "no") not in ("no", "false", "0", "")
            if not bridge and tags.get("man_made") != "bridge":
                continue
            geom = shapely.force_2d(shapely.from_wkb(row["geometry_local"]))
            key = str(row["osm_id"])
            poly = footprint(geom)
            if tags.get("man_made") == "bridge" and not poly.is_empty:
                outlines[key] = (key, poly, tags)
            if (
                bridge
                and (tags.get("highway") or tags.get("railway") in RAILS)
                and geom.geom_type == "LineString"
                and not geom.is_ring
                and geom.length >= 0.5
            ):
                ways[key] = dict(
                    id=key,
                    line=geom,
                    tags=tags,
                    width=way_width(tags),
                    layer=number(tags.get("layer"), 1),
                    bridge=True,
                )
    decks = merge_crossings(list(ways.values()), list(outlines.values()))
    rails = railing_lines(decks)
    changed = []
    for deck, rail in zip(decks, rails, strict=True):
        members = deck.get("member_ways", [deck])
        openings = shapely.union_all(
            [
                shapely.Point(p).buffer(w["width"] / 2 + 0.5)
                for w in members
                for p in (w["line"].coords[0], w["line"].coords[-1])
            ]
        )
        old = deck_outline(deck).boundary.difference(openings)
        old = shapely.union_all([p for p in shapely.get_parts(old) if p.length >= 0.5])
        rail = shapely.union_all([p for p in shapely.get_parts(rail) if p.length >= 1.8])
        removed = old.difference(rail.buffer(1e-7)).length
        if removed > 0.01:
            changed.append(
                dict(id=deck["id"], name=deck["tags"].get("name"), removed_metres=round(removed, 3))
            )
        if deck["id"] == "576863642":
            parts = [p for p in shapely.get_parts(rail) if p.length >= 1.8]
            assert len(parts) == 2, [(p.length, p.wkt) for p in parts]
            assert all(p.length > 60 for p in parts)
    result = dict(
        clips=len(paths),
        crossings=len(decks),
        changed=len(changed),
        bridges=changed,
        audit_seconds=round(time.monotonic() - started, 3),
    )
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    import sys

    audit()
    if "--no-build" not in sys.argv:
        spec = importlib.util.spec_from_file_location(
            "build_probe", Path(__file__).with_name("probe-builds.py")
        )
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        result = probe.run(
            Path("/srv/turku3d/work/coarse"), tile_id="t0_10_10", first_budget=90, full_budget=180
        )
        from turku3d.stage_cache import decompress_payload

        metrics = json.loads((Path(result["output"]) / "stage-times.json").read_text())
        cache = Path("/srv/turku3d/work/coarse/.stages/t0_10_10/bridges") / (
            metrics["bridges"]["key"] + ".pkl"
        )
        _, decks, _, _ = pickle.loads(decompress_payload(cache.read_bytes()[32:]))
        target = next(d for d in decks if d.way["id"] == "576863642")
        expected = railing_lines([target.way])[0]
        metal = [m for m in target.meshes if m.metadata.get("surface") == "steel"]
        assert metal, "overpass lost outer railings"
        points = np.concatenate([m.positions[:, [0, 2]] for m in metal])
        distance = shapely.distance(shapely.points(points), expected)
        assert float(distance.max()) < 0.1, "built railing mesh strays from outer edges"
        result["railing_vertices"] = len(points)
        result["max_outer_edge_distance"] = float(distance.max())
        print(json.dumps(result), flush=True)

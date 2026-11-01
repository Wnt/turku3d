#!/usr/bin/env python3
"""G1 street-furniture check of built coarse tiles against their sources (pipeline env).

Per tile it reports:

* ``source``: register rows inside the tile by layer and kind (rows ``classify`` skips counted
  apart), the street-light register before and after its duplicate collapse, and what
  ``turku3d.props.read_sources`` keeps by kind and origin (register, OSM fallback, lit rule);
* ``placed``: props of the visual GLB by kind, the light list by category, and the manifest's
  ``furniture`` stage statistics (moves and drops with their reasons);
* ``anomalies``: placed props or lamp posts inside a building footprint, non-sign props or posts
  on a carriageway, props whose base is more than ``--tolerance`` m off the visual ground under
  them (floating or sunk), and same-kind furniture closer than 0.5 m (duplicates; separate signs
  on one pole are separate register rows by design).

It reads the tile directory the builder wrote (``--root DIR``: ``DIR/<tile>/manifest.json``,
the visual GLB, ``DIR/.inputs``) and the raw cache of ``TURKU3D_DATA``; it writes nothing else.

    bash pipeline/env/run.sh python scripts/dev/g1_tile_check.py --root DIR t0_0_0 t0_0_1 \\
        --json /tmp/g1-check.json --markdown /tmp/g1-check.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import shapely
from shapely.strtree import STRtree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(Path(__file__).parent))

import richness_scorecard as rs  # noqa: E402
from turku3d import config as C  # noqa: E402
from turku3d.coarse.inputs import gather  # noqa: E402
from turku3d.coarse.sources import FURNITURE_WFS_LAYERS, WFS_LAYERS, Sources  # noqa: E402
from turku3d.props import LAMP_LAYERS, SOURCES, classify, read_sources  # noqa: E402
from turku3d.props.lights import decode  # noqa: E402
from turku3d.spatial import frame as spatial_frame  # noqa: E402

DUPLICATE = 0.5


def ground_height(tris: np.ndarray, x: float, z: float) -> float | None:
    """Highest visual ground surface at (x, z), or None where no ground triangle covers it."""
    if not len(tris):
        return None
    xs, zs = tris[:, :, 0], tris[:, :, 2]
    near = (xs.min(1) <= x) & (xs.max(1) >= x) & (zs.min(1) <= z) & (zs.max(1) >= z)
    best = None
    for a, b, c in tris[near]:
        det = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
        if abs(det) < 1e-9:
            continue
        u = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / det
        v = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / det
        w = 1 - u - v
        if min(u, v, w) < -1e-6:
            continue
        y = u * a[1] + v * b[1] + w * c[1]
        best = y if best is None else max(best, y)
    return best


def source_counts(tile, sources):
    """Register rows inside the tile by layer/kind, and read_sources' kept props by kind/origin."""
    rows, paths = Counter(), {}
    for name in (*WFS_LAYERS[3:6], *FURNITURE_WFS_LAYERS, "GIS:Katualueet"):
        found, path = sources.wfs_source(name, tile)
        paths[name.replace(":", "_")] = path
        slug = name.replace(":", "_")
        if not path or slug not in SOURCES:
            continue
        frame = spatial_frame(path, tile.bbox)
        for row in frame.to_dict("records"):
            p = row["geometry_local"]
            if p is None or p.geom_type != "Point" or not tile.bbox.contains(p.x, p.y):
                continue
            if slug == "GIS_Varusteet":
                kind = classify(row) or "skipped"
            elif slug == "frn_CityFurniture_LOD2":
                kind = f"lamp:{LAMP_LAYERS.get(str(row.get('Tasonimi')), 'other')}"
            else:
                kind = SOURCES[slug]
            rows[f"{slug}:{kind}"] += 1
    osm = sources.osm_source(tile)
    paths["GIS_Keskilinjat"] = sources.wfs_source("GIS:Keskilinjat", tile)[1]
    kept, meta = read_sources(tile.bbox, source_paths=paths, osm_path=osm)
    origin = Counter(
        f"{p.kind}:{'register' if not p.source.startswith('osm') else p.source}" for p in kept
    )
    return dict(sorted(rows.items())), dict(sorted(origin.items())), meta["skipped"], paths


def check_tile(root: Path, tile_id: str, tolerance: float):
    tile = C.VisualTile.parse(tile_id)
    sources = Sources()
    source_rows, kept, skipped, paths = source_counts(tile, sources)
    manifest = json.loads((root / tile_id / "manifest.json").read_text())
    data = rs.Source(tiles=root).load(tile_id, ground=True)
    tris = np.concatenate(data.ground) if data.ground else np.zeros((0, 3, 3))
    # G1 props and the sign/stop poles they share the props layer with; the barrier layer's
    # instanced bollards, chains and hedges are not part of this check.
    props = [
        i
        for i in data.items
        if (i.category == "furniture" and i.kind not in ("bollard", "post"))
        or i.category in ("sign_pole", "lamp")
    ]
    props = [i for i in props if not i.ident.startswith("light:")]
    lights, posts = decode(manifest.get("lights"))
    inputs = gather(tile, root)
    footprints = [b.footprint for b in (*inputs.buildings, *inputs.wall_context)]
    buildings = STRtree(footprints) if footprints else None
    street_path = paths.get("GIS_Katualueet")
    carriageway = shapely.Polygon()
    if street_path:
        frame = spatial_frame(street_path, tile.bbox.buffered(5))
        carriageway = shapely.union_all(
            [
                shapely.make_valid(shapely.force_2d(g))
                for g, kind in zip(frame.geometry_local, frame["Katuosalaji"], strict=True)
                if kind == "Ajorata" and g is not None
            ]
        )

    def inside(x, z):
        if buildings is None:
            return False
        point = shapely.Point(x, z)
        return any(footprints[i].covers(point) for i in buildings.query(point))

    def on_road(x, z):
        return not carriageway.is_empty and carriageway.covers(shapely.Point(x, z))

    anomalies = []
    for p in props:
        if inside(p.x, p.z):
            anomalies.append(
                {"kind": p.kind, "id": p.ident, "issue": "in_building", "x": p.x, "z": p.z}
            )
        if p.category != "sign_pole" and on_road(p.x, p.z):
            anomalies.append(
                {"kind": p.kind, "id": p.ident, "issue": "on_carriageway", "x": p.x, "z": p.z}
            )
        g = ground_height(tris, p.x, p.z)
        if g is not None and abs(p.y - g) > tolerance:
            issue = "floating" if p.y > g else "sunk"
            anomalies.append(
                {
                    "kind": p.kind,
                    "id": p.ident,
                    "issue": issue,
                    "dy": round(p.y - g, 3),
                    "x": p.x,
                    "z": p.z,
                }
            )
    for q in posts:
        if inside(q["x"], q["z"]):
            anomalies.append(
                {"kind": "lamp_post", "issue": "in_building", "x": q["x"], "z": q["z"]}
            )
        if on_road(q["x"], q["z"]):
            anomalies.append(
                {"kind": "lamp_post", "issue": "on_carriageway", "x": q["x"], "z": q["z"]}
            )
        g = ground_height(tris, q["x"], q["z"])
        if g is not None and abs(q["y"] - g) > tolerance:
            anomalies.append(
                {
                    "kind": "lamp_post",
                    "issue": "floating" if q["y"] > g else "sunk",
                    "dy": round(q["y"] - g, 3),
                    "x": q["x"],
                    "z": q["z"],
                }
            )
    for light in lights:
        if light["category"] == "wall" and inside(light["x"], light["z"]):
            anomalies.append(
                {"kind": "wall_light", "issue": "in_building", "x": light["x"], "z": light["z"]}
            )
    by_kind = {}
    for p in props:
        # Separate signs on one pole are separate register rows by design; not duplicates.
        if p.category != "sign_pole":
            by_kind.setdefault(p.kind, []).append(p)
    for kind, items in by_kind.items():
        for a_index, a in enumerate(items):
            for b in items[a_index + 1 :]:
                if math.hypot(a.x - b.x, a.z - b.z) < DUPLICATE:
                    anomalies.append({"kind": kind, "issue": "duplicate", "x": a.x, "z": a.z})
    placed = Counter(p.kind for p in props)
    return {
        "tile": tile_id,
        "code": manifest.get("code"),
        "source_rows": source_rows,
        "kept_by_origin": kept,
        "read_sources_skipped": skipped,
        "placed": dict(sorted(placed.items())),
        "lights": dict(Counter(light["category"] for light in lights)),
        "posts": len(posts),
        "trees": manifest.get("trees"),
        "furniture_stage": manifest.get("furniture", {}),
        "anomalies": anomalies,
        "anomaly_counts": dict(Counter(f"{a['kind']}:{a['issue']}" for a in anomalies)),
    }


def markdown(results):
    lines = [
        "| Tile | Register rows (drawn kinds) | Kept: register / OSM / rule | Placed props | Posts | Hung | Wall | Anomalies |",
        "|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        rows = sum(v for k, v in r["source_rows"].items() if not k.endswith(":skipped"))
        kept = Counter()
        for k, v in r["kept_by_origin"].items():
            kept[k.split(":", 1)[1]] += v
        lights = r["lights"]
        lines.append(
            f"| {r['tile']} | {rows} | {kept['register']} / {kept['osm']} / {kept['osm_lit_street']} "
            f"| {sum(r['placed'].values())} | {r['posts']} | {lights.get('hung', 0)} "
            f"| {lights.get('wall', 0)} | {r['anomaly_counts'] or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("tiles", nargs="+")
    ap.add_argument("--root", type=Path, required=True, help="tile directory the builder wrote")
    ap.add_argument("--tolerance", type=float, default=0.12)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    a = ap.parse_args(argv)
    results = [check_tile(a.root, t, a.tolerance) for t in a.tiles]
    if a.json:
        a.json.write_text(json.dumps(results, indent=1))
    text = markdown(results)
    if a.markdown:
        a.markdown.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

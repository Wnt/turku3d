"""Audit delivered L0 building coverage and draw a top-down evidence map."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import geopandas as gpd
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import box, shape

from turku3d import config as C
from turku3d.package.gltf import read_glb

from .inputs import local_polygon


def _value(value):
    return "" if value is None or str(value) in ("<NA>", "nan", "None") else str(value)


def audit(layer, tiles, source):
    regions = [box(t.bbox.xmin, t.bbox.zmin, t.bbox.xmax, t.bbox.zmax) for t in tiles]
    source_rows = gpd.read_parquet(source)
    source_rows = source_rows[source_rows.geom_type.isin(["Polygon", "Point"])]
    footprints = json.loads((layer / "footprints.geojson").read_text())["features"]
    layer_shapes = [(f["properties"]["building_id"], shape(f["geometry"])) for f in footprints]
    tree = shapely.STRtree([p for _, p in layer_shapes])
    metrics = json.loads((layer / "metrics.json").read_text())
    records = {r["building_id"]: r for r in metrics["buildings"]}
    published = set()
    for tile in tiles:
        path = layer / f"{tile.id}.vis.glb"
        if not path.exists():
            continue
        gltf, _ = read_glb(path.read_bytes())
        published.update(
            node.get("extras", {}).get("building_id") for node in gltf["nodes"] if "mesh" in node
        )
    rows = []
    for source_row in source_rows.itertuples():
        source_geometry = source_row.geometry
        if not any(local_polygon(source_geometry).intersects(r) for r in regions):
            continue
        point_only = source_geometry.geom_type == "Point"
        if point_only:
            source_geometry = source_geometry.buffer(3, cap_style="square")
        footprint = local_polygon(source_geometry)
        candidates = [
            layer_shapes[i][0]
            for i in tree.query(footprint)
            if footprint.intersection(layer_shapes[i][1]).area / footprint.area > 0.95
        ]
        building_id = next((bid for bid in candidates if bid in published), None)
        record = records.get(building_id) if building_id else None
        choice = record.get("choice") if record else None
        if building_id:
            state = (
                "placeholder"
                if choice == "placeholder"
                else "lidar_shell"
                if choice in ("lidar_extrusion", "lidar_flat")
                else "model"
            )
            reason = None
        else:
            state = "missing"
            reason = (
                "no_layer_record"
                if not candidates
                else "no_valid_shell"
                if not any(records.get(bid, {}).get("shell_valid") for bid in candidates)
                else "owned_outside_requested_tiles_or_visual_missing"
            )
        rows.append(
            {
                "building_id": building_id,
                "source_id": source_row.feature_key,
                "source_geometry": "point" if point_only else "polygon",
                "state": state,
                "reason": reason,
                "choice": choice,
                "address": " ".join(
                    filter(
                        None, [_value(source_row.Osoitenimi_fi), _value(source_row.Osoitenumero)]
                    )
                ),
                "footprint": footprint,
            }
        )
    return rows


def draw_map(rows, tiles, path):
    bounds = C.BBoxLocal(
        min(t.bbox.xmin for t in tiles),
        min(t.bbox.zmin for t in tiles),
        max(t.bbox.xmax for t in tiles),
        max(t.bbox.zmax for t in tiles),
    )
    width, height = 1200, 1200
    image = Image.new("RGB", (width, height), "#131b25")
    pen = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    scale = min(1060 / (bounds.xmax - bounds.xmin), 1060 / (bounds.zmax - bounds.zmin))

    def pixel(x, z):
        return (70 + (x - bounds.xmin) * scale, 70 + (z - bounds.zmin) * scale)

    colors = {
        "model": "#44bb77",
        "lidar_shell": "#3e9add",
        "placeholder": "#efad35",
        "missing": "#ed4b55",
    }
    for row in rows:
        poly = row["footprint"]
        pen.polygon(
            [pixel(x, z) for x, z in poly.exterior.coords],
            fill=colors[row["state"]],
            outline="#10131b",
            width=2,
        )
        for hole in poly.interiors:
            pen.polygon([pixel(x, z) for x, z in hole.coords], fill="#131b25")
    for tile in tiles:
        b = tile.bbox
        pen.rectangle((*pixel(b.xmin, b.zmin), *pixel(b.xmax, b.zmax)), outline="#d8e0ed", width=2)
        pen.text(pixel(b.xmin + 5, b.zmin + 5), tile.id, fill="white", font=font)
    x = 72
    for name, color in colors.items():
        pen.rectangle((x, 1150, x + 25, 1175), fill=color)
        pen.text((x + 32, 1153), name.replace("_", " "), fill="white", font=font)
        x += 270
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layer", type=Path, default=C.work_root() / "build/layers/buildings")
    ap.add_argument("--source-area", default="work-3x3")
    ap.add_argument("--tiles", nargs="+", default=[t.id for t in C.PLAYABLE_TILES.visual_tiles])
    ap.add_argument("--out", type=Path, default=C.work_root() / "reports/building-coverage")
    args = ap.parse_args(argv)
    tiles = [C.VisualTile.parse(tid) for tid in args.tiles]
    source = C.raw_dir("wfs") / args.source_area / "GIS_Rakennukset.parquet"
    rows = audit(args.layer, tiles, source)
    args.out.mkdir(parents=True, exist_ok=True)
    draw_map(rows, tiles, args.out / "coverage.png")
    counts = Counter(row["state"] for row in rows)
    result = {
        "tiles": args.tiles,
        "footprints": len(rows),
        "covered": len(rows) - counts.get("missing", 0),
        "counts": dict(sorted(counts.items())),
        "missing": [
            {k: v for k, v in row.items() if k != "footprint"} for row in rows if row["reason"]
        ],
    }
    (args.out / "coverage.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({**result, "map": str(args.out / "coverage.png")}, indent=2))
    return 0 if not result["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

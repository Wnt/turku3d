"""Audit delivered L0 building coverage and draw a top-down evidence map."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import box

from turku3d import config as C
from turku3d.package.gltf import read_glb

from .inputs import attach_osm, read_footprints


def audit(layer, tiles, source):
    regions = [box(t.bbox.xmin, t.bbox.zmin, t.bbox.xmax, t.bbox.zmax) for t in tiles]
    buildings = read_footprints(
        source,
        C.BBoxLocal(
            min(t.bbox.xmin for t in tiles),
            min(t.bbox.zmin for t in tiles),
            max(t.bbox.xmax for t in tiles),
            max(t.bbox.zmax for t in tiles),
        ),
    )
    buildings = [b for b in buildings if any(b.footprint.intersects(r) for r in regions)]
    osm = sorted((C.raw_dir("osm") / source.parent.name).glob("*.parquet"))
    if osm:
        attach_osm(buildings, osm[-1])
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
    for b in buildings:
        record = records.get(b.id)
        choice = record.get("choice") if record else None
        if b.id in published:
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
                if record is None
                else "no_valid_shell"
                if not record.get("shell_valid")
                else "owned_outside_requested_tiles_or_visual_missing"
            )
        rows.append(
            {
                "building_id": b.id,
                "state": state,
                "reason": reason,
                "choice": choice,
                "address": " ".join(
                    filter(
                        None,
                        [
                            b.properties.get("osm", {}).get("addr:street"),
                            b.properties.get("osm", {}).get("addr:housenumber"),
                        ],
                    )
                )
                or b.properties.get("address", ""),
                "footprint": b.footprint,
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
    ap.add_argument("--tiles", nargs="+", default=[t.id for t in C.OFFICE_BLOCK.visual_tiles])
    ap.add_argument("--out", type=Path, default=C.work_root() / "reports/building-coverage")
    args = ap.parse_args(argv)
    tiles = [C.VisualTile.parse(tid) for tid in args.tiles]
    source = C.raw_dir("wfs") / args.source_area / "kanta_Rakennus.parquet"
    rows = audit(args.layer, tiles, source)
    args.out.mkdir(parents=True, exist_ok=True)
    draw_map(rows, tiles, args.out / "coverage.png")
    counts = Counter(row["state"] for row in rows)
    result = {
        "tiles": args.tiles,
        "footprints": len(rows),
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

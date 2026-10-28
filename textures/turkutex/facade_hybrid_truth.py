"""Local, independently double-labelled facade truth; no images or labels enter git."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from . import facade_fast as ff
from . import facade_priors as priors
from .building_jobs import latest
from .facade_fast_render import MATERIALS
from .streetview.facades import Facade, load_facades, merge_edges, save_facades


def edge_heights(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    tangent = end - start
    length = np.linalg.norm(tangent)
    tangent = tangent / length
    delta = points[:, :2] - start
    along = delta @ tangent
    across = np.abs(delta[:, 0] * tangent[1] - delta[:, 1] * tangent[0])
    keep = (across <= 0.6) & (along >= -0.5) & (along <= length + 0.5)
    if keep.sum() < 2 or np.ptp(along[keep]) < length * 0.6:
        return np.empty(0)
    return points[keep, 2]


def inventory(args):
    """Whole street-facing footprint runs across the cached 3x3 WFS area, with LOD2 heights."""
    import geopandas as gpd
    import shapely
    from shapely.geometry import LineString
    from shapely.geometry.polygon import orient
    from turku3d import config

    wfs = config.raw_dir("wfs/work-3x3")
    frame = gpd.read_parquet(wfs / "bldg_Building_LOD2.parquet")
    frame = frame[
        (frame.geom_type == "Polygon") & frame.surface_type.isin(["GroundSurface", "RoofSurface"])
    ]
    buildings = []
    for key, group in frame.groupby("feature_key", sort=True):
        bid = str(group.pysyvarakennustunnus.iloc[0])
        if bid in ("", "None", "nan", "0", "<NA>"):
            continue
        ground = group[group.surface_type == "GroundSurface"]
        roofs = group[group.surface_type == "RoofSurface"]
        if ground.empty or roofs.empty:
            continue
        footprint = shapely.make_valid(
            shapely.union_all([shapely.force_2d(g) for g in ground.geometry])
        )
        polygons = [p for p in getattr(footprint, "geoms", [footprint]) if p.geom_type == "Polygon"]
        if not polygons:
            continue
        footprint = orient(max(polygons, key=lambda p: p.area), 1)
        ground_points = np.concatenate([np.asarray(g.exterior.coords) for g in ground.geometry])
        roof_points = np.concatenate([np.asarray(g.exterior.coords) for g in roofs.geometry])
        buildings.append((bid, key, footprint, ground_points, roof_points))
    shapes = [r[2] for r in buildings]
    tree = shapely.STRtree(shapes)
    roads = gpd.read_parquet(wfs / "GIS_Keskilinjat.parquet").geometry
    roads = shapely.union_all([shapely.force_2d(g) for g in roads])
    walls = []
    for bi, (bid, key, shape, ground_points, roof_points) in enumerate(buildings):
        ring = np.asarray(shape.exterior.coords)[:-1, :2]
        for index, (a, b) in enumerate(merge_edges(ring, max_turn_deg=5, max_dev=0.3)):
            bottom = edge_heights(ground_points, ring[a], ring[b])
            upper = edge_heights(roof_points, ring[a], ring[b])
            if not len(bottom) or not len(upper) or np.ptp(np.quantile(upper, [0.1, 0.9])) > 2:
                continue
            base, top = float(np.median(bottom)), float(np.median(upper))
            if top - base < 3:
                continue
            fid = bid + "_" + hashlib.sha256((key + ":" + str(index)).encode()).hexdigest()[:10]
            wall = Facade(
                fid,
                ring[a],
                ring[b],
                top - base,
                base,
                dict(
                    building_id=bid,
                    street_facing=True,
                    height_source="LOD2 edge ground/eaves vertices",
                ),
            )
            if not 8 <= wall.length <= 65:
                continue
            start = wall.centre + wall.normal * 0.5
            ray = LineString([start, start + wall.normal * 45])
            hit = ray.intersection(roads)
            if hit.is_empty:
                continue
            distance = shapely.Point(start).distance(hit)
            if any(
                i != bi and shapely.Point(start).distance(ray.intersection(shapes[i])) < distance
                for i in tree.query(ray, predicate="intersects")
            ):
                continue
            walls.append(wall)
    args.root.mkdir(parents=True, exist_ok=True)
    save_facades(walls, args.root / "whole-walls.geojson")
    print(json.dumps(dict(buildings=len(buildings), street_walls=len(walls))))


def agreement(a: dict, b: dict) -> tuple[dict | None, str]:
    """Predeclared acceptance: exact counts/material and colour within 8 CIE76 units."""
    for label in (a, b):
        if not label["usable"] or label["wrong_wall"] or label["multiple_surfaces"]:
            return None, "unusable evidence"
        if label["occlusion_share"] > 0.2:
            return None, "occlusion above 20%"
    for field in ("storeys", "window_columns", "material"):
        if a[field] != b[field]:
            return None, f"disagreement: {field}"
    colours = [priors.colour_lab(label["wall_colour"]) for label in (a, b)]
    if np.linalg.norm(colours[0] - colours[1]) > 8:
        return None, "disagreement: colour"
    return dict(
        storeys=a["storeys"],
        window_columns=a["window_columns"],
        material=a["material"],
        wall_colour=priors.lab_colour(np.mean(colours, axis=0)),
    ), "agreed"


def prepare(args):
    from turku3d import config

    excluded = {
        priors.building_group(fid)
        for path in args.labels.glob("*/descriptions.json")
        for fid in json.loads(path.read_text())
    }
    candidates = [
        f
        for f in load_facades(args.inventory)
        if priors.building_group(f.id) not in excluded
        and f.length >= 6
        and f.height_m >= 3
        and f.props.get("street_facing", False)
    ]
    # First visit every building's widest street-facing wall, then its next wall.
    groups = {}
    for f in candidates:
        groups.setdefault(priors.building_group(f.id), []).append(f)
    for walls in groups.values():
        walls.sort(key=lambda f: (not f.props.get("street_facing", False), -f.length))
    selected = []
    for index in range(max(map(len, groups.values()), default=0)):
        for key in sorted(
            groups,
            key=lambda k: np.linalg.norm(groups[k][0].centre - [config.ORIGIN_E, config.ORIGIN_N]),
        ):
            if index < len(groups[key]):
                selected.append(groups[key][index])
    selected = selected[: args.limit]
    args.root.mkdir(parents=True, exist_ok=True)
    save_facades(selected, args.root / "candidates.geojson")
    ff.write_json(
        args.root / "selection.json",
        dict(
            excluded_buildings=sorted(excluded),
            minimum_width_m=6,
            inventory_sha256=ff.sha(args.inventory),
            candidates=len(selected),
            independent_buildings=len({priors.building_group(f.id) for f in selected}),
        ),
    )
    errors = ff.make_refs(selected, args.root / "refs", ("lookaround", "google", "mapillary"), 3, 1)
    screen(args, errors)


def screen(args, errors=None):
    errors = dict(errors or {})
    rows = []
    for f in load_facades(args.root / "candidates.geojson"):
        view, reason = ff.best_view(f, args.root / "refs")
        if view is None:
            errors.setdefault(f.id, reason)
            continue
        crop = ff.crop_for_call(f, view, args.root / "refs", args.root / "inputs")
        context = args.root / "inputs" / (f.id + "_context.jpg")
        shutil.copyfile(args.root / "refs" / f.id / view["files"]["outline"], context)
        rows.append(
            dict(
                facade_id=f.id,
                building_id=f.props["building_id"],
                width_m=f.length,
                height_m=f.height_m,
                crop=str(crop),
                context=str(context),
                crop_sha256=ff.sha(crop),
                view_id=view["view_id"],
                source=view["source"],
            )
        )
    ff.write_json(args.root / "screened.json", rows)
    ff.write_json(args.root / "rejected-refs.json", errors)
    print(json.dumps(dict(screened=len(rows), rejected=len(errors))))


def label_schema():
    properties = {
        "facade_id": {"type": "string"},
        "usable": {"type": "boolean"},
        "wrong_wall": {"type": "boolean"},
        "multiple_surfaces": {"type": "boolean"},
        "occlusion_share": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "storeys": {"type": "integer", "minimum": 0, "maximum": 30},
        "window_columns": {"type": "integer", "minimum": 0, "maximum": 60},
        "material": {"type": "string", "enum": list(MATERIALS)},
        "wall_colour": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    }
    item = dict(
        type="object", properties=properties, required=list(properties), additionalProperties=False
    )
    return dict(
        type="object",
        properties={"walls": dict(type="array", items=item)},
        required=["walls"],
        additionalProperties=False,
    )


def prescreen(args):
    from PIL import Image

    from .facade_hybrid import Evidence

    path = args.root / "screened.json"
    rows = json.loads(path.read_text())
    model = Evidence()
    kept, audit = [], {}
    for row in rows:
        with Image.open(row["crop"]) as image:
            evidence = model.measure(np.asarray(image.convert("RGB")))
        accepted = (
            evidence["facade_share"] >= 0.45
            and evidence["occlusion_share"] <= 0.35
            and not evidence["multiple_surfaces"]
        )
        audit[row["facade_id"]] = dict(**evidence, accepted=accepted)
        if accepted:
            kept.append(row)
    ff.write_json(args.root / "geometric-screened.json", rows)
    ff.write_json(args.root / "semantic-screen.json", audit)
    ff.write_json(path, kept)
    print(json.dumps(dict(semantic_candidates=len(kept), rejected=len(rows) - len(kept))))


def label(args):
    from .codex.runner import Runner, load_jobs

    rows = json.loads((args.root / "screened.json").read_text())
    completed = read_labels(args.root, args.reviewer)
    rows = [r for r in rows if r["facade_id"] not in completed][: args.label_limit]
    schema = args.root / f"{args.reviewer}.schema.json"
    structure = label_schema()
    if args.reviewer == "low":
        structure["properties"]["walls"]["items"] = ff.fr.schema()
        kept = json.loads((args.root / "agreed.json").read_text())
        rows = [r for r in rows if r["facade_id"] in kept]
    ff.write_json(schema, structure)
    jobs = []
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start : start + args.batch_size]
        if args.reviewer == "low":
            from string import Template

            from .paths import PROMPTS_DIR

            instructions = Template(
                (PROMPTS_DIR / "facade_fast_describe.md").read_text()
            ).substitute(
                facade_id="the image filename ID",
                width_m="listed below",
                height_m="listed below",
                aspect="listed below",
            )
            prompt = (
                "Return one independent description per attached wall in the walls array. "
                + instructions
            )
        else:
            prompt = (
                "Independently label these Turku facade photographs. All strings must be English. "
                "Do not run commands or generate images. "
                "For each ID, its JPG is a rectified wall; _context.jpg shows its red outline. "
                "Read ONLY the target wall. Reject wrong-wall/pavement images, multiple facade surfaces, "
                "heavy occlusion, incomplete width/height, or counts you cannot confidently read. "
                "usable means one clearly visible complete wall with confidently countable floors/bays. "
                "storeys includes ground floor, excludes attic roof windows and architectural bands. "
                "window_columns counts openings across one typical FULL upper floor, not panes/mullions, "
                "and is zero for a single-storey or blank wall. Colour is the dominant opaque cladding's "
                "unshadowed daylight sRGB hex; ignore trim, glass and trees. Report physical visible "
                "material, not structural material. occlusion_share is the fraction of target hidden "
                "by foreground objects. Return every ID exactly once; do not infer labels from dimensions. "
                + (
                    "Check counts from top down, then recheck left to right. "
                    if args.reviewer == "b"
                    else ""
                )
            )
        prompt += json.dumps(
            [{k: r[k] for k in ("facade_id", "width_m", "height_m")} for r in batch]
        )
        jobs.append(
            dict(
                id=args.reviewer
                + "-"
                + hashlib.sha256(json.dumps([r["facade_id"] for r in batch]).encode()).hexdigest()[
                    :16
                ],
                prompt=prompt,
                images=[
                    r[k]
                    for r in batch
                    for k in (("crop",) if args.reviewer == "low" else ("crop", "context"))
                ],
                schema=str(schema),
                timeout=360,
                model="gpt-6-astra",
                effort="low" if args.reviewer == "low" else "high",
                prompt_version="hybrid-truth-v2-" + args.reviewer,
                meta=dict(
                    kind="fast_describe" if args.reviewer == "low" else "facade_truth",
                    ids=[r["facade_id"] for r in batch],
                    crop_hashes={r["facade_id"]: r["crop_sha256"] for r in batch},
                ),
            )
        )
    path = args.root / f"jobs-{args.reviewer}.jsonl"
    path.write_text("".join(json.dumps(job) + "\n" for job in jobs))
    code = Runner(
        args.root / args.reviewer,
        parallel=3,
        max_pct=min(80, args.max_pct),
        retries=0,
        cache_dir=args.root / "call-cache",
    ).run(load_jobs(path))
    raise SystemExit(code)


def read_labels(root: Path, reviewer: str) -> dict:
    rows = {r["facade_id"]: r for r in json.loads((root / "screened.json").read_text())}
    result = {}
    for record in latest(root / reviewer / "out.jsonl").values():
        if not record.get("ok"):
            continue
        walls = record["answer"]["walls"]
        ids = [w["facade_id"] for w in walls]
        if len(set(ids)) != len(ids) or set(ids) != set(record["meta"]["ids"]):
            continue
        for wall in walls:
            fid = wall["facade_id"]
            if (
                fid in rows
                and record["meta"].get("crop_hashes", {}).get(fid) == rows[fid]["crop_sha256"]
            ):
                result[fid] = wall
    return result


def collect(args):
    readings = [read_labels(args.root, r) for r in ("a", "b")]
    kept, rejected = {}, {}
    for row in json.loads((args.root / "screened.json").read_text()):
        fid = row["facade_id"]
        if not all(fid in reading for reading in readings):
            rejected[fid] = "missing independent reading"
            continue
        label, reason = agreement(*(r[fid] for r in readings))
        if label is None:
            rejected[fid] = reason
        else:
            kept[fid] = dict(**row, label=label)
    ff.write_json(args.root / "agreed.json", kept)
    ff.write_json(args.root / "rejected-labels.json", rejected)
    from collections import Counter

    print(json.dumps(dict(agreed=len(kept), rejected=dict(Counter(rejected.values())))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["inventory", "prepare", "screen", "prescreen", "label", "collect"]
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--reviewer", choices=["a", "b", "low"])
    parser.add_argument("--max-pct", type=float, default=80)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--label-limit", type=int, default=1000)
    args = parser.parse_args()
    globals()[args.command](args)


if __name__ == "__main__":
    main()

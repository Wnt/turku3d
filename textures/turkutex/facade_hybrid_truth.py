"""Local, independently double-labelled facade truth; no images or labels enter git."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np

from . import facade_fast as ff
from . import facade_priors as priors
from .building_jobs import latest
from .facade_fast_render import MATERIALS
from .streetview.facades import Facade, load_facades, save_facades


def inventory(args):
    """Whole street-facing LOD2 wall surfaces across the cached 3x3 WFS area."""
    import geopandas as gpd
    import shapely
    from shapely.geometry import LineString
    from turku3d import config

    from .streetview.height import ground_height
    from .streetview.walls import wall_rectangles

    wfs = config.raw_dir("wfs/work-3x3")
    if args.radius:
        import pandas as pd

        from .streetview.visibility import file_bounds, overlaps, source_files

        bounds = (
            config.ORIGIN_E - args.radius,
            config.ORIGIN_N - args.radius,
            config.ORIGIN_E + args.radius,
            config.ORIGIN_N + args.radius,
        )
        files = [r[0] for r in source_files() if (b := file_bounds(r)) and overlaps(b, bounds)]
        frames = [
            gpd.read_parquet(
                path,
                columns=[
                    "feature_key",
                    "geom_index",
                    "geometry",
                    "geom_type",
                    "surface_type",
                    "pysyvarakennustunnus",
                ],
            )
            for path in files
        ]
        frame = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
        frame = frame.drop_duplicates(["feature_key", "geom_index"])
    else:
        frame = gpd.read_parquet(wfs / "bldg_Building_LOD2.parquet")
    frame = frame[
        (frame.geom_type == "Polygon") & frame.surface_type.isin(["GroundSurface", "WallSurface"])
    ]
    buildings = []
    excluded = (
        {f.props["building_id"] for f in load_facades(args.exclude_inventory)}
        if args.exclude_inventory
        else set()
    )
    for key, group in frame.groupby("feature_key", sort=True):
        bid = str(group.pysyvarakennustunnus.iloc[0])
        if bid in ("", "None", "nan", "0", "<NA>") or bid in excluded:
            continue
        ground = group[group.surface_type == "GroundSurface"]
        surfaces = group[group.surface_type == "WallSurface"]
        if ground.empty or surfaces.empty:
            continue
        footprint = shapely.make_valid(
            shapely.union_all([shapely.force_2d(g) for g in ground.geometry])
        )
        polygons = [p for p in getattr(footprint, "geoms", [footprint]) if p.geom_type == "Polygon"]
        if not polygons:
            continue
        buildings.append((bid, key, footprint, list(surfaces.geometry)))
    shapes = [r[2] for r in buildings]
    tree = shapely.STRtree(shapes)
    roads = None
    if not args.radius:
        roads = gpd.read_parquet(wfs / "GIS_Keskilinjat.parquet").geometry
        roads = shapely.union_all([shapely.force_2d(g) for g in roads])
    walls = []
    for bi, (bid, key, shape, surfaces) in enumerate(buildings):
        rectangles = wall_rectangles(surfaces)
        for p0, p1, base, top, normal in rectangles:
            centre = (p0 + p1) / 2
            if (
                args.radius
                and np.max(np.abs(centre - [config.ORIGIN_E, config.ORIGIN_N])) > args.radius
            ):
                continue
            if shape.boundary.distance(shapely.Point(centre)) > 0.5:
                continue  # A recessed upper wall does not include the ground storey.
            tangent = (p1 - p0) / np.linalg.norm(p1 - p0)
            if any(
                other_top > top + 3
                and abs(other_normal @ normal) > 0.99
                and abs(((a + b) / 2 - centre) @ normal) < 5
                and min(max((a - p0) @ tangent, (b - p0) @ tangent), np.linalg.norm(p1 - p0))
                - max(min((a - p0) @ tangent, (b - p0) @ tangent), 0)
                > np.linalg.norm(p1 - p0) * 0.8
                for a, b, _, other_top, other_normal in rectangles
            ):
                continue  # A low podium in front of the taller facade is not a whole facade.
            # Select the side facing an unobstructed street. Recessed upper walls
            # may lie inside the ground footprint on both sides; a centroid test
            # cannot determine their outward side reliably.
            choices = []
            for sign in (1, -1):
                start = centre + sign * normal * 0.5
                if shape.contains(shapely.Point(start)):
                    continue
                if roads is None:
                    choices.append((0, sign))
                    continue
                ray = LineString([start, start + sign * normal * 60])
                hit = ray.intersection(roads)
                if hit.is_empty:
                    continue
                distance = shapely.Point(start).distance(hit)
                if any(
                    i != bi
                    and shapely.Point(start).distance(ray.intersection(shapes[i])) < distance
                    for i in tree.query(ray, predicate="intersects")
                ):
                    continue
                choices.append((distance, sign))
            if not choices:
                continue
            _, sign = min(choices)
            terrain = ground_height(*(centre + sign * normal * 0.75))
            if terrain is not None:
                if base - terrain > 2:  # Upper-only surfaces cannot label the building's storeys.
                    continue
                base = max(base, terrain)
            if top - base < 3:
                continue
            signature = np.round(np.r_[p0, p1, base, top], 3).tolist()
            fid = bid + "_" + hashlib.sha256((key + repr(signature)).encode()).hexdigest()[:10]
            wall = Facade(
                fid,
                p0,
                p1,
                top - base,
                base,
                dict(
                    building_id=bid,
                    street_facing=True,
                    height_source="connected coplanar LOD2 WallSurface extent",
                ),
                "right" if sign == 1 else "left",
            )
            if not 8 <= wall.length <= 65:
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
    errors = {}
    start = time.monotonic()
    for index in range(0, len(selected), 10):
        errors.update(
            ff.make_refs(
                selected[index : index + 10],
                args.root / "refs",
                ("lookaround", "google", "mapillary"),
                3,
                1,
            )
        )
        print(
            json.dumps(
                dict(
                    stage="refs",
                    processed=min(index + 10, len(selected)),
                    total=len(selected),
                    seconds=round(time.monotonic() - start, 1),
                )
            ),
            flush=True,
        )
    screen(args, errors)


def screen(args, errors=None):
    from .building_jobs import view_height

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
                height_m=view_height(view, f.height_m),
                crop=str(crop),
                context=str(context),
                crop_sha256=ff.sha(crop),
                context_sha256=ff.sha(context),
                view_id=view["view_id"],
                source=view["source"],
            )
        )
    ff.write_json(args.root / "screened.json", rows)
    ff.write_json(args.root / "rejected-refs.json", errors)
    print(json.dumps(dict(screened=len(rows), rejected=len(errors))))


def refresh(args):
    """Regenerate selected local references with the current geometry and screen versions."""
    facades = load_facades(args.root / "candidates.geojson")
    errors = {}
    start = time.monotonic()
    for index in range(0, len(facades), 10):
        errors.update(
            ff.make_refs(
                facades[index : index + 10],
                args.root / "refs",
                ("lookaround", "google", "mapillary"),
                workers=3,
                max_views=4,
            )
        )
        ff.write_json(args.root / "errors.json", errors)
        print(
            json.dumps(
                dict(
                    stage="refs",
                    processed=min(index + 10, len(facades)),
                    total=len(facades),
                    seconds=round(time.monotonic() - start, 1),
                    errors=len(errors),
                )
            ),
            flush=True,
        )
    screen(args, errors)


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
    rows = [r for r in rows if r["facade_id"] not in completed]
    schema = args.root / f"{args.reviewer}.schema.json"
    structure = label_schema()
    if args.reviewer == "low":
        structure["properties"]["walls"]["items"] = ff.fr.schema()
        kept = json.loads((args.root / "agreed.json").read_text())
        rows = [r for r in rows if r["facade_id"] in kept]
    rows = rows[: args.label_limit]
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
                prompt_version="hybrid-truth-v3-" + args.reviewer,
                meta=dict(
                    kind="fast_describe" if args.reviewer == "low" else "facade_truth",
                    ids=[r["facade_id"] for r in batch],
                    crop_hashes={r["facade_id"]: r["crop_sha256"] for r in batch},
                    context_hashes={r["facade_id"]: r.get("context_sha256") for r in batch},
                ),
            )
        )
    path = args.root / f"jobs-{args.reviewer}.jsonl"
    path.write_text("".join(json.dumps(job) + "\n" for job in jobs))
    code = Runner(
        args.root / args.reviewer,
        parallel=args.parallel,
        max_pct=args.max_pct,
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
                and record["meta"].get("context_hashes", {}).get(fid)
                == rows[fid].get("context_sha256")
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


def combine(args):
    """Select independently agreed, unique-building walls from local collections."""
    if not args.source_root:
        raise ValueError("combine requires --source-root")
    candidates = []
    for root in args.source_root:
        root = root.resolve()
        agreed = json.loads((root / "agreed.json").read_text())
        readings = [read_labels(root, reviewer) for reviewer in ("a", "b")]
        for fid, row in agreed.items():
            if not all(fid in reading for reading in readings):
                continue
            label, _ = agreement(*(reading[fid] for reading in readings))
            if label == row["label"] and row["width_m"] >= args.min_width:
                candidates.append(row)
    candidates.sort(key=lambda row: (-row["width_m"], row["facade_id"]))
    selected, buildings = [], set()
    for row in candidates:
        if row["building_id"] in buildings:
            continue
        selected.append(row)
        buildings.add(row["building_id"])
        if len(selected) == args.limit:
            break
    if len(selected) < args.limit:
        raise ValueError(f"only {len(selected)} agreed unique-building walls; need {args.limit}")
    args.root.mkdir(parents=True, exist_ok=True)
    selected_ids = {row["facade_id"] for row in selected}
    facades = {
        facade.id: facade
        for root in args.source_root
        for facade in load_facades(root / "candidates.geojson")
        if facade.id in selected_ids
    }
    if set(facades) != selected_ids:
        raise ValueError("selected wall geometry missing from source inventories")
    save_facades([facades[row["facade_id"]] for row in selected], args.root / "candidates.geojson")
    for reviewer in ("a", "b"):
        records = args.root / reviewer / "out.jsonl"
        records.parent.mkdir(parents=True, exist_ok=True)
        with records.open("w") as output:
            for root in args.source_root:
                source = root / reviewer / "out.jsonl"
                if source.exists():
                    with source.open() as stream:
                        shutil.copyfileobj(stream, output)
    ff.write_json(
        args.root / "screened.json",
        [{k: v for k, v in r.items() if k != "label"} for r in selected],
    )
    ff.write_json(args.root / "agreed.json", {r["facade_id"]: r for r in selected})
    ff.write_json(
        args.root / "selection.json",
        dict(
            source_roots=[str(root.resolve()) for root in args.source_root],
            agreed_candidates=len(candidates),
            minimum_width_m=args.min_width,
            selected=len(selected),
            independent_buildings=len(buildings),
        ),
    )
    print(json.dumps(dict(selected=len(selected), independent_buildings=len(buildings))))


def reserve(args):
    """Select unreviewed wide walls from other buildings for a fresh reference pass."""
    if not args.source_root or not args.exclude_inventory:
        raise ValueError("reserve requires --source-root and --exclude-inventory")
    excluded = {f.props["building_id"] for f in load_facades(args.exclude_inventory)}
    candidates = []
    for root in args.source_root:
        rows = json.loads((root / "screened.json").read_text())
        rejected = json.loads((root / "rejected-labels.json").read_text())
        facades = {f.id: f for f in load_facades(root / "candidates.geojson")}
        candidates.extend(
            facades[row["facade_id"]]
            for row in rows
            if row["width_m"] >= args.min_width
            and row["building_id"] not in excluded
            and rejected.get(row["facade_id"]) == "missing independent reading"
        )
    candidates.sort(key=lambda f: (-f.length, f.id))
    selected = []
    for f in candidates:
        building = f.props["building_id"]
        if building in excluded:
            continue
        selected.append(f)
        excluded.add(building)
        if len(selected) == args.limit:
            break
    if len(selected) < args.limit:
        raise ValueError(f"only {len(selected)} unreviewed buildings; need {args.limit}")
    args.root.mkdir(parents=True, exist_ok=True)
    save_facades(selected, args.root / "candidates.geojson")
    ff.write_json(
        args.root / "selection.json",
        dict(
            source_roots=[str(root.resolve()) for root in args.source_root],
            exclude_inventory=str(args.exclude_inventory.resolve()),
            minimum_width_m=args.min_width,
            selected=len(selected),
            independent_buildings=len({f.props["building_id"] for f in selected}),
        ),
    )
    print(json.dumps(dict(selected=len(selected), independent_buildings=len(selected))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "inventory",
            "prepare",
            "screen",
            "refresh",
            "prescreen",
            "label",
            "collect",
            "combine",
            "reserve",
        ],
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument(
        "--radius", type=float, default=0, help="regional WFS radius around origin in metres"
    )
    parser.add_argument(
        "--exclude-inventory", type=Path, help="exclude every building in this local inventory"
    )
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--reviewer", choices=["a", "b", "low"])
    parser.add_argument("--max-pct", type=float, default=100)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--label-limit", type=int, default=1000)
    parser.add_argument("--source-root", type=Path, action="append")
    parser.add_argument("--min-width", type=float, default=8)
    args = parser.parse_args()
    args.root = args.root.resolve()
    globals()[args.command](args)


if __name__ == "__main__":
    main()

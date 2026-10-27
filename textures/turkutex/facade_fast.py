"""Fast facade pass: a rough procedural map for every street-facing wall without a map of its own.

plan      street-facing walls of the listed tiles whose approval has no generated or derived map
refs      reference crops for the planned walls (Look Around -> Google -> Mapillary, screened)
describe  one vision call per wall on its best screened crop -> a small description (cached)
render    procedural albedo/normal/ORM maps and sidecars into the staging tree
publish   bind the staged maps into the approval root under the export lock (source_mode fast)

A description is parameters, not pixels: the renderer (facade_fast_render) never sees a photo,
so the maps ship. A wall with no usable reference takes the description of its building's
best-read wall, else one from the building register (no call). A fast map never replaces a
generated or derived map; a later generated or derived map replaces a fast one.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import inspect
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image
from turku3d import config

from . import facade_fast_render as fr
from .facade_batch import asset_path, metric_size, uv_transform, write_json
from .post.pipeline import encode_ktx2, sha
from .streetview.facades import load_facades

PPM = 16  # the top of the packager's density ladder (pipeline/turku3d/package/facades.py)
MAX_SIDE = 2048
CROP_SIDE = 768  # long side of the crop the vision call sees
# Panorama resolution for the fast pass: Google zoom 3 and Look Around zoom 2 are about 4k px
# equirects, plenty for a 768 px crop, and decode about 16 times faster than the rollout's.
GOOGLE_ZOOM, LOOKAROUND_ZOOM = 3, 2
ENCODINGS = {"albedo": ("etc1s", False), "orm": ("etc1s", True), "normal": ("uastc-normal", True)}
MAP_KINDS = ("albedo", "normal", "orm")
PROMPT_VERSION = "facade-fast-describe-v1"


def tile_of(facade) -> str:
    """The L0 visual tile of a wall's centre (as the packager assigns facades to tiles)."""
    x, z = facade.centre[0] - config.ORIGIN_E, config.ORIGIN_N - facade.centre[1]
    size = config.VISUAL_TILE_SIZE
    return config.VisualTile(0, math.floor(x / size), math.floor(z / size)).id


def published_modes(assembly: Path) -> dict[str, str]:
    """source_mode of every bound wall of the approval root."""
    manifest = assembly / "MANIFEST.json"
    bound = json.loads(manifest.read_text())["bound"] if manifest.exists() else []
    modes = {}
    for facade_id in bound:
        building, index = facade_id.rsplit("_", 1)
        sidecar = assembly / "facades" / building / f"{index}.json"
        if sidecar.exists():
            modes[facade_id] = json.loads(sidecar.read_text()).get("source_mode") or "generate"
    return modes


# ------------------------------------------------------------------------------------------
# plan
# ------------------------------------------------------------------------------------------


def plan_walls(inventory: Path, assembly: Path, tiles: list[str], walls: list[str] | None = None):
    """Every street-facing wall of the tiles (or the named walls) whose approval has no map,
    or only a fast one; walls with a generated or derived map are listed as kept."""
    modes = published_modes(assembly)
    wanted = set(walls or [])
    rows, kept = [], {}
    for f in load_facades(inventory):
        tile = tile_of(f)
        if wanted:
            if f.id not in wanted:
                continue
        elif tile not in tiles or not f.props.get("street_facing"):
            continue
        mode = modes.get(f.id)
        if mode not in (None, "fast"):
            kept[f.id] = mode
            continue
        rows.append(
            {
                "facade_id": f.id,
                "building_id": f.props["building_id"],
                "tile": tile,
                "width_m": round(f.length, 3),
                "height_m": round(f.height_m, 3),
                "current": mode or "none",
            }
        )
    missing = wanted - {r["facade_id"] for r in rows} - set(kept)
    if missing:
        raise ValueError(f"walls not in the inventory: {sorted(missing)}")
    return {
        "inventory_sha256": sha(inventory),
        "tiles": sorted(tiles),
        "walls": rows,
        "kept": kept,
    }


def subset(inventory: Path, ids: list[str], output: Path):
    data = json.loads(inventory.read_text())
    wanted = set(ids)
    data["features"] = [
        f
        for f in data["features"]
        if f"{f['properties']['building_id']}_{f['properties']['facade_index']}" in wanted
    ]
    write_json(output, data)


def planned_facades(run: Path, inventory: Path):
    plan = json.loads((run / "plan.json").read_text())
    if plan["inventory_sha256"] != sha(inventory):
        raise ValueError("inventory changed since the plan was written")
    ids = {r["facade_id"] for r in plan["walls"]}
    return plan, [f for f in load_facades(inventory) if f.id in ids]


# ------------------------------------------------------------------------------------------
# refs and describe
# ------------------------------------------------------------------------------------------


def make_refs(facades, refs: Path, sources: tuple[str, ...], workers: int, max_views: int):
    """Reference crops per wall (cached per wall geometry); returns {facade_id: error}."""
    from .streetview.panos import PanoCache
    from .streetview.refs import SelectParams, facade_refs

    cache = PanoCache()
    params = SelectParams(
        max_views=max_views, zoom=GOOGLE_ZOOM, la_zoom=LOOKAROUND_ZOOM, long_side=CROP_SIDE
    )
    errors = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(facade_refs, cache, f, refs, params, sources): f for f in facades}
        for future in cf.as_completed(futures):
            f = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - one wall's failure is its reason
                errors[f.id] = f"{type(exc).__name__}: {exc}"
    return errors


def best_view(facade, refs: Path) -> tuple[dict | None, str | None]:
    from .building_jobs import screened_full_views

    views, reason = screened_full_views(facade, refs)
    return (views[0], None) if views else (None, reason)


def crop_for_call(facade, view: dict, refs: Path, inputs: Path) -> Path:
    """The best rectified crop, scaled to CROP_SIDE: the only image the vision call sees. It
    stays in the sandbox run directory (street imagery is never published)."""
    source = refs / facade.id / view["files"]["rectified"]
    out = inputs / f"{facade.id}.jpg"
    with Image.open(source) as image:
        image = image.convert("RGB")
        scale = CROP_SIDE / max(image.size)
        if scale < 1:
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out, quality=88)
    return out


def describe_jobs(facades, refs: Path, run: Path) -> tuple[list[dict], dict]:
    """One strict-schema job per wall with a screened full-wall view; the others' reasons."""
    from .paths import PROMPTS_DIR

    jobs, reasons = [], {}
    for f in facades:
        view, reason = best_view(f, refs)
        if view is None:
            reasons[f.id] = reason
            continue
        crop = crop_for_call(f, view, refs, run / "inputs")
        jobs.append(
            {
                "id": f"{f.id}.fast",
                "prompt_file": str(PROMPTS_DIR / "facade_fast_describe.md"),
                "vars": {
                    "facade_id": f.id,
                    "width_m": f"{f.length:.1f}",
                    "height_m": f"{f.height_m:.1f}",
                    "aspect": f"{f.length / f.height_m:.2f}:1",
                },
                "images": [str(crop)],
                "schema": str(fr.SCHEMA),
                "expect_images": 0,
                "timeout": 600,
                "prompt_version": PROMPT_VERSION,
                "meta": {
                    "kind": "fast_describe",
                    "facade_id": f.id,
                    "views": [view["view_id"]],
                    "reference": {
                        "source": view["source"],
                        "view_id": view["view_id"],
                        "crop_sha256": sha(crop),
                    },
                },
            }
        )
    return jobs, reasons


def call_cache() -> Path:
    """Descriptions are cached in the shared data root, keyed by prompt and crop hash, so a
    rerun in any sandbox (or a later content task) makes no new call for the same crop."""
    return config.data_root() / "textures/codex-cache"


def describe(
    run: Path,
    inventory: Path,
    refs: Path,
    parallel: int,
    max_pct: float,
    engine: str = "astra",
    segmentation: str = "classic",
) -> dict:
    if engine == "cv-priors":
        from .facade_priors import describe as describe_priors

        return describe_priors(run, inventory, refs)
    if engine == "cv-transforms":
        from .facade_cv_transforms import describe as describe_transforms

        return describe_transforms(run, inventory, refs, segmentation=segmentation)
    if engine in (
        "cv-models",
        "cmp",
        "cmp-grid",
        "grounding-dino",
        "grounding-dino-openings",
        "smolvlm",
        "qwen",
    ):
        from .facade_models import describe as describe_model

        return describe_model(run, inventory, refs, engine=engine)
    if engine == "cv":
        from .facade_cv import describe as describe_cv

        return describe_cv(run, inventory, refs, segmentation=segmentation)
    from .building_jobs import latest
    from .codex.runner import Runner, load_jobs
    from .codex.usage import used_percent
    from .jobs import _write

    _, facades = planned_facades(run, inventory)
    jobs, reasons = describe_jobs(facades, refs, run)
    job_path = run / "describe_jobs.jsonl"
    _write(jobs, job_path)
    start, before = time.time(), used_percent()
    code = (
        Runner(
            run / "run_describe", parallel=parallel, max_pct=max_pct, cache_dir=call_cache()
        ).run(load_jobs(job_path))
        if jobs
        else 0
    )
    after = used_percent()
    records = latest(run / "run_describe/out.jsonl")
    result = {
        "walls": len(facades),
        "jobs": len(jobs),
        "no_reference": reasons,
        "calls": sum(1 for r in records.values() if not r.get("cached")),
        "cached": sum(1 for r in records.values() if r.get("cached")),
        "failed": sorted(i for i, r in records.items() if not r.get("ok")),
        "seconds": round(time.time() - start, 1),
        "quota_before_percent": before,
        "quota_after_percent": after,
        "exit": code,
    }
    write_json(run / "describe.json", result)
    return result


# ------------------------------------------------------------------------------------------
# descriptions -> maps
# ------------------------------------------------------------------------------------------


def descriptions(run: Path, facades, attributes: dict) -> dict[str, dict]:
    """The description each wall is drawn from: its own reading of a visible wall, else the
    building's widest read wall (same spacing), else the register's. Recorded with source."""
    from .building_jobs import latest

    records = latest(run / "run_describe/out.jsonl")
    own, problems = {}, {}
    for f in facades:
        record = records.get(f"{f.id}.fast")
        if not record or not record.get("ok"):
            continue
        answer = record["answer"]
        issues = fr.validate(answer, f.id)
        if issues:
            problems[f.id] = "; ".join(issues)
        elif answer["wall_visible"]:
            own[f.id] = (answer, record)
        else:
            problems[f.id] = f"reference does not show the wall: {answer['problem']}"
    by_building: dict[str, list] = {}
    for f in facades:
        if f.id in own:
            by_building.setdefault(f.props["building_id"], []).append(f)
    result = {}
    for f in facades:
        building = f.props["building_id"]
        if f.id in own:
            answer, record = own[f.id]
            result[f.id] = {
                "source": "reference",
                "description": answer,
                "reference": record["meta"]["reference"],
                **({"cv": record["meta"]["cv"]} if "cv" in record["meta"] else {}),
                "call": {
                    "model": record["model"],
                    "effort": record["effort"],
                    "key": record["key"],
                    "cached": bool(record.get("cached")),
                },
            }
        elif by_building.get(building):
            donor = max(by_building[building], key=lambda d: d.length * d.height_m)
            result[f.id] = {
                "source": "building",
                "adopted_from": donor.id,
                "description": fr.adopt(own[donor.id][0], f.id, f.length, donor.length),
                "reason": problems.get(f.id, "no reference"),
            }
        else:
            result[f.id] = {
                "source": "register",
                "attributes": attributes.get(building, {}),
                "description": fr.register_description(
                    f.id, building, attributes.get(building, {}), f.length, f.height_m
                ),
                "reason": problems.get(f.id, "no reference"),
            }
    return result


def code_hash() -> str:
    return hashlib.sha256(
        (inspect.getsource(fr) + inspect.getsource(stage_wall)).encode()
    ).hexdigest()


def stage_wall(facade, entry: dict, staging: Path, ppm: float, code: str) -> dict:
    """Render and encode one wall's maps and sidecar (skipped when its key is unchanged)."""
    size = metric_size(facade.length, facade.height_m, ppm, MAX_SIDE)
    params = {
        "version": fr.DESCRIPTION_VERSION,
        "code": code,
        "facade": facade.to_feature(),
        "description": entry["description"],
        "size": size,
    }
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    target = asset_path(facade, staging)
    sidecar = target.with_suffix(".json")
    if sidecar.exists():
        old = json.loads(sidecar.read_text())
        if old.get("key") == key and all(
            (target.parent / n).is_file() and sha(target.parent / n) == h
            for n, h in old["hashes"].items()
        ):
            return {
                "facade_id": facade.id,
                "staged": True,
                "cached": True,
                "bytes": _bytes(old, target),
            }
    maps = fr.render(entry["description"], facade.length, facade.height_m, size, facade.id)
    post = staging / "post" / facade.id
    files, valid = {}, True
    for kind in MAP_KINDS:
        png = post / f"{kind}.png"
        fr.save_png(maps[kind], png)
        name = target.name + (".ktx2" if kind == "albedo" else f".{kind}.ktx2")
        target.parent.mkdir(parents=True, exist_ok=True)
        valid &= encode_ktx2(png, target.parent / name, *ENCODINGS[kind])["gltf_basisu_valid"]
        files[kind] = name
    layout = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in maps["layout"].items()}
    layout["window_m"] = [round(v, 3) for v in layout["window_m"]]
    record = {
        "key": key,
        "facade_id": facade.id,
        "building_id": facade.props["building_id"],
        "facade_index": facade.props["facade_index"],
        "width_m": facade.length,
        "height_m": facade.height_m,
        "size": list(size),
        "pixels_per_metre": [size[0] / facade.length, size[1] / facade.height_m],
        "uv": uv_transform(facade),
        "references": [],
        "source_mode": "fast",
        "fast_description": {
            "version": fr.DESCRIPTION_VERSION,
            "code": code,
            **{k: v for k, v in entry.items()},
            "layout": layout,
        },
        "geometry_source": facade.props.get("height_source", "buildings layer"),
        "min_pixels_per_metre": 0,
        "preview_sha256": sha(post / "albedo.png"),
        "files": files,
        "hashes": {n: sha(target.parent / n) for n in files.values()},
        "qa": {
            "seam": "not applicable: unique wall, clamped UVs",
            "scale_relative_error": abs(size[0] / facade.length / (size[1] / facade.height_m) - 1),
            "content_aspect_error": 0.0,
            "gltf_basisu_valid": bool(valid),
            "normal_method": "procedural relief of the description's layout",
        },
    }
    write_json(sidecar, record)
    return {
        "facade_id": facade.id,
        "staged": bool(valid),
        "cached": False,
        "bytes": _bytes(record, target),
    }


def _bytes(record: dict, target: Path) -> int:
    return sum((target.parent / n).stat().st_size for n in record["files"].values())


def render_all(
    run: Path, inventory: Path, staging: Path, wfs: Path | None, ppm: float, workers: int
):
    from .facade_derive import building_attributes, era

    plan, facades = planned_facades(run, inventory)
    buildings = sorted({f.props["building_id"] for f in facades})
    try:
        attributes = building_attributes(wfs, buildings) if wfs else {}
    except FileNotFoundError:
        attributes = {}
    for building in buildings:
        attributes.setdefault(
            building,
            {
                "material": "",
                "material_class": "render",
                "year": None,
                "era": era(None),
                "levels": None,
                "address": "",
            },
        )
    chosen = descriptions(run, facades, attributes)
    write_json(run / "descriptions.json", chosen)
    code = code_hash()
    start = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda f: stage_wall(f, chosen[f.id], staging, ppm, code), facades))
    tiles = {r["facade_id"]: r["tile"] for r in plan["walls"]}
    per_tile: dict[str, dict] = {}
    for r in results:
        t = per_tile.setdefault(tiles[r["facade_id"]], {"walls": 0, "bytes": 0})
        t["walls"] += 1
        t["bytes"] += r["bytes"]
    sources: dict[str, int] = {}
    for entry in chosen.values():
        sources[entry["source"]] = sources.get(entry["source"], 0) + 1
    summary = {
        "staged": sum(r["staged"] for r in results),
        "failed": sorted(r["facade_id"] for r in results if not r["staged"]),
        "cached": sum(r["cached"] for r in results),
        "sources": sources,
        "seconds": round(time.time() - start, 1),
        "per_tile": per_tile,
        "rows": results,
    }
    write_json(staging / "fast.json", summary)
    return summary


# ------------------------------------------------------------------------------------------
# publish
# ------------------------------------------------------------------------------------------


def publish(run: Path, staging: Path, inventory: Path, assembly: Path) -> dict:
    """Bind the staged fast maps under the export lock, against the approval root's own
    inventory: a staged wall binds only where that inventory has the same wall (same ID and
    geometry), and never where a generated or derived map is bound by then."""
    from types import SimpleNamespace

    from .export import export_assets, export_lock, matches

    planned_facades(run, inventory)
    staged = [
        r["facade_id"]
        for r in json.loads((staging / "fast.json").read_text())["rows"]
        if r["staged"]
    ]
    approved = assembly / "APPROVED_INVENTORY.geojson"
    with export_lock(assembly):
        target = {f.id: f for f in load_facades(approved)}
        source = {f.id: f for f in load_facades(inventory) if f.id in set(staged)}
        unmatched = sorted(
            f for f in staged if f not in target or not matches(source[f], target[f])
        )
        modes = published_modes(assembly)
        kept = sorted(f for f in staged if modes.get(f) not in (None, "fast"))
        ids = [f for f in staged if f not in kept and f not in unmatched]
        before = json.loads((assembly / "MANIFEST.json").read_text())
        result = {"bound": before["bound"]}
        if ids:
            subset(inventory, ids, staging / "fast.geojson")
            rows = [{"facade_id": f, "complete": True, "street_facing": True} for f in ids]
            write_json(
                staging / "qa.json",
                {"inventory_sha256": sha(staging / "fast.geojson"), "rows": rows},
            )
            result = export_assets(
                SimpleNamespace(
                    facades=approved,
                    source_inventory=staging / "fast.geojson",
                    source=staging,
                    qa=staging / "qa.json",
                    output=assembly,
                    merge=True,
                )
            )
    return {
        "published": len(ids),
        "kept_better": kept,
        "not_in_approved_inventory": unmatched,
        "bound_before": len(before["bound"]),
        "bound_after": len(result["bound"]),
    }


def contact_sheet(run: Path, inventory: Path, staging: Path, output: Path, assembly: Path | None):
    """Fast map per wall at 8 px/m, next to the wall's generated or derived map when the
    approval root has one (measurement only). No reference photo is drawn."""
    from PIL import ImageDraw

    from .facade_derive import published_maps

    _, facades = planned_facades(run, inventory)
    chosen = json.loads((run / "descriptions.json").read_text())
    scale, gap = 8.0, 12
    tiles = []
    for f in facades:
        fast = np.asarray(Image.open(staging / "post" / f.id / "albedo.png").convert("RGB"))
        other = None
        if assembly is not None:
            building, index = f.id.rsplit("_", 1)
            sidecar = assembly / "facades" / building / f"{index}.json"
            if sidecar.exists():
                meta = json.loads(sidecar.read_text())
                if meta.get("source_mode") != "fast":
                    other = published_maps(assembly, meta, ("albedo",))["albedo"]
        d = chosen[f.id]["description"]
        label = (
            f"{f.id} {f.length:.1f}x{f.height_m:.1f}m {chosen[f.id]['source']}: {d['material']} "
            f"{d['storeys']}st {d['window_columns']}col {d['ground_floor']}"
        )
        tiles.append((label, other, fast, f))
    width = 1800
    layout, x, y, row_h = [], gap, gap, 0
    for label, other, fast, f in tiles:
        w = round(f.length * scale)
        h = round(f.height_m * scale)
        block_w = max(2 * w + gap, 6 * len(label))
        if x + block_w > width:
            x, y, row_h = gap, y + row_h + gap, 0
        layout.append((label, other, fast, x, y, w, h))
        x += block_w + gap
        row_h = max(row_h, h + 16)
    sheet = Image.new("RGB", (width, y + row_h + gap), (245, 244, 240))
    draw = ImageDraw.Draw(sheet)
    for label, other, fast, x, y, w, h in layout:
        draw.text((x, y), label, fill=(0, 0, 0))
        for k, image in enumerate((other, fast)):
            if image is not None:
                sheet.paste(
                    Image.fromarray(image).resize((max(1, w), max(1, h))),
                    (x + k * (w + gap), y + 14),
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=85)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("plan", "refs", "describe", "render", "publish", "contact"):
        p = sub.add_parser(name)
        p.add_argument("--run", type=Path, required=True, help="run directory (plan.json, calls)")
        p.add_argument("--inventory", type=Path, required=True, help="approved facade inventory")
        if name in ("plan", "publish", "contact"):
            p.add_argument("--assembly", type=Path, required=True, help="approval root")
        if name == "plan":
            p.add_argument("--tiles", nargs="*", default=[], help="L0 tiles, e.g. t0_0_0")
            p.add_argument("--walls", help="comma-separated facade IDs instead of tiles")
        if name in ("refs", "describe"):
            p.add_argument("--refs", type=Path, required=True)
        if name == "refs":
            p.add_argument("--sources", default="lookaround,google,mapillary")
            p.add_argument("--ref-workers", type=int, default=8)
            p.add_argument("--max-views", type=int, default=2)
        if name == "describe":
            p.add_argument("-j", type=int, default=8)
            p.add_argument("--max-pct", type=float, default=90)
            p.add_argument(
                "--engine",
                choices=(
                    "cv-models",
                    "astra",
                    "cv",
                    "cv-priors",
                    "cv-transforms",
                    "cmp",
                    "cmp-grid",
                    "grounding-dino",
                    "grounding-dino-openings",
                    "smolvlm",
                    "qwen",
                ),
                default="astra",
            )
            p.add_argument("--segmentation", choices=("classic", "segformer"), default="classic")
        if name in ("render", "publish", "contact"):
            p.add_argument("--staging", type=Path, required=True)
        if name == "render":
            p.add_argument("--wfs", type=Path, help="raw WFS parquet directory (register fallback)")
            p.add_argument("--ppm", type=float, default=PPM)
            p.add_argument("-j", type=int, default=8)
        if name == "contact":
            p.add_argument("--output", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.command == "plan":
        if not a.tiles and not a.walls:
            ap.error("plan needs --tiles or --walls")
        plan = plan_walls(a.inventory, a.assembly, a.tiles, a.walls.split(",") if a.walls else None)
        write_json(a.run / "plan.json", plan)
        result = {"walls": len(plan["walls"]), "kept": len(plan["kept"])}
    elif a.command == "refs":
        _, facades = planned_facades(a.run, a.inventory)
        start = time.time()
        errors = make_refs(facades, a.refs, tuple(a.sources.split(",")), a.ref_workers, a.max_views)
        result = {"walls": len(facades), "errors": errors, "seconds": round(time.time() - start, 1)}
        write_json(a.run / "refs.json", result)
    elif a.command == "describe":
        result = describe(a.run, a.inventory, a.refs, a.j, a.max_pct, a.engine, a.segmentation)
        result = {k: v for k, v in result.items() if k != "no_reference"} | {
            "no_reference": len(result["no_reference"])
        }
    elif a.command == "render":
        summary = render_all(a.run, a.inventory, a.staging, a.wfs, a.ppm, a.j)
        result = {
            k: summary[k] for k in ("staged", "failed", "cached", "sources", "seconds", "per_tile")
        }
    elif a.command == "publish":
        result = publish(a.run, a.staging, a.inventory, a.assembly)
    else:
        contact_sheet(a.run, a.inventory, a.staging, a.output, a.assembly)
        result = {"contact": str(a.output)}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

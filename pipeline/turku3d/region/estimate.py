"""Offline estimates; unknown inventory and quota costs are explicitly labelled."""

import json
import math
import subprocess
from pathlib import Path

from turku3d import config as C

from .areas import geometry

# Planning placeholders, not measured throughput or a conversion of tokens to quota.
DEFAULTS = {
    "facade_label": {"tokens": 12000, "seconds": 60, "weekly_pct": None},
    "facade_improve": {"tokens": 20000, "seconds": 180, "weekly_pct": None},
    "ground_texture": {"tokens": 16000, "seconds": 120, "weekly_pct": None},
}


def ledger_rates():
    tool = C.REPO_ROOT / "scripts/dev/codex-cost"
    if not tool.exists():
        return {}
    rows = json.loads(
        subprocess.run(
            [str(tool), "--by", "job_type", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    )
    rates = {}
    aliases = {"label": "facade_label", "improve": "facade_improve"}
    for row in rows:
        kind = aliases.get(row["group"], row["group"])
        if (
            kind in DEFAULTS
            and row["calls"]
            and not row["unknown_usage"]
            and not row["unknown_wall"]
        ):
            rates[kind] = {
                "tokens": row["total_tokens"] / row["calls"],
                "seconds": row["wall_seconds"] / row["calls"],
                "weekly_pct": None,
            }
    return rates


def estimate(area, workers=4, averages: Path | None = None):
    if workers < 1:
        raise ValueError("workers must be positive")
    shape = geometry(area)
    tiles = area.visual_tiles
    # 136 buildings per 500 m source tile: architecture D2, planning density only.
    buildings = math.ceil(shape.area / 250000 * 136)
    facades = buildings * 2
    inventory_source = "placeholder: 136 buildings/0.25 km2; 2 street facades/building"
    inventory_area = "work-3x3" if geometry(C.WORK_3X3).covers(shape) else area.name
    source = C.raw_dir("wfs") / inventory_area / "kanta_Rakennus.parquet"
    if source.exists():
        import geopandas as gpd
        from shapely.affinity import affine_transform

        frame = gpd.read_parquet(source)
        region_en = affine_transform(shape, [1, 0, 0, -1, C.ORIGIN_E, C.ORIGIN_N])
        import shapely

        frame = frame[frame.geom_type.isin(["Polygon", "MultiPolygon"])]
        if "kplkoodi" in frame:
            frame = frame[frame.kplkoodi.isin(["66000", "66001"])]
        footprints = set()
        for geom in frame.geometry:
            if geom.intersects(region_en):
                parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
                footprints.update(shapely.normalize(shapely.force_2d(part)).wkb for part in parts)
        buildings = len(footprints)
        facades = buildings * 2
        inventory_source = "cached WFS footprint polygons 66000/66001, deduplicated; street facades = 2/building placeholder"
    measured = json.loads(averages.read_text())["job_types"] if averages else ledger_rates()
    rates = DEFAULTS | measured
    jobs = {}
    for kind, count in {
        "facade_label": facades,
        "facade_improve": facades,
        "ground_texture": len(tiles),
    }.items():
        rate = rates[kind]
        jobs[kind] = {
            "calls": count,
            "tokens": math.ceil(count * rate["tokens"]),
            "seconds": count * rate["seconds"],
            "weekly_pct": None if rate.get("weekly_pct") is None else count * rate["weekly_pct"],
            "source": (str(averages) if averages else "codex-cost ledger averages")
            if kind in measured
            else "placeholder",
        }
    laz = sorted(
        {
            laz_tile.name
            for t in tiles
            for laz_tile in C.laz_tiles_for_bbox(t.bbox.buffered(area.margin))
        }
    )
    missing = [n for n in laz if not (C.raw_dir("laz") / "pistepilvi" / f"{n}.laz").exists()]
    panos = math.ceil(facades / 3)
    return {
        "area": area.name,
        "visual_tiles": len(tiles),
        "collision_tiles": len(tiles) * 16,
        "tile_ids": [t.id for t in tiles],
        "laz_tiles": laz,
        "laz_downloads": missing,
        "buildings": buildings,
        "street_facades": facades,
        "panoramas_needed": panos,
        "inventory_source": inventory_source,
        "jobs": jobs,
        "wall_seconds": math.ceil(
            (len(tiles) * 450 + sum(j["seconds"] for j in jobs.values())) / workers
        ),
        "additional_disk_bytes": len(missing) * 150_000_000
        + panos * 25_000_000
        + len(tiles) * 100_000_000,
        "assumptions": [
            "150 MB/LAZ; 25 MB/panorama; 100 MB products/visual tile",
            "450 CPU seconds/visual tile; ideal worker scaling; network time excluded",
            "Codex quota percent is unknown without measured ledger averages; spending fails closed",
        ],
    }


def guard_quota(jobs, used, cap):
    if not 0 < cap <= 100:
        raise ValueError("quota cap must be in (0, 100]")
    if used is None or not math.isfinite(used) or not 0 <= used <= 100:
        raise RuntimeError("weekly quota unavailable; refusing Codex spending")
    values = [j.get("weekly_pct") for j in jobs]
    if any(v is None or not math.isfinite(v) or v < 0 for v in values):
        raise RuntimeError("measured weekly quota cost unavailable; refusing Codex spending")
    if used + sum(values) >= cap:
        raise RuntimeError(f"projected quota {used + sum(values):.3f}% reaches cap {cap}%")

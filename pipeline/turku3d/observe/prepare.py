"""Views of one building: its walls, the panoramas that see each, rectified crops and context views."""

from __future__ import annotations

import time
from pathlib import Path

from . import crops, views
from .lod2 import Building, Scene
from .panoramas import Panoramas

MAX_MISSING = 0.25  # a crop with more of the wall outside the panorama's picture is dropped


def street_presence(building: Building, crops: list) -> dict:
    """How much of a building the street sees: the salience its tier follows.

    Per wall the longest stretch a chosen panorama sees freely, times the wall's height: the facade area (m2) a
    passer-by can read, and the longest such stretch (m) as its frontage."""
    free = {}
    for crop in crops:
        free[crop["wall"]] = max(free.get(crop["wall"], 0.0), crop["x1_m"] - crop["x0_m"])
    height = {w.id: w.eaves - w.base for w in building.walls}
    return {"area_m2": round(sum(length * height.get(wall, 0.0) for wall, length in free.items()), 1),
            "frontage_m": round(max(free.values(), default=0.0), 1)}


def blocked_walls(record: dict, reading: dict, hidden: float = 0.7) -> dict:
    """{wall id: [{provider, year, distance_m}]} of the walls none of whose crops showed them, by the reader's
    report of each image: unusable, or hidden this much by what stands in front (things LoD2 does not hold)."""
    report = {v["file"]: v for v in reading.get("views", [])}
    out: dict = {}
    for crop in record["crops"]:
        seen = report.get(Path(crop["file"]).name, {})
        bad = not seen.get("usable", True) or seen.get("hidden_share", 0.0) >= hidden
        out.setdefault(crop["wall"], []).append(
            {"provider": crop["panorama"]["provider"], "year": str(crop["panorama"]["date"])[:4],
             "distance_m": crop["distance_m"], "file": Path(crop["file"]).name} if bad else None)
    return {wall: views for wall, views in out.items() if all(views)}


def other_views(scene: Scene, building: Building, panos: Panoramas, out: Path, record: dict, blocked: dict) -> list:
    """New crops, from panoramas on other paths, for walls whose crops did not show them (blocked_walls). The
    crops and context views are added to `record`; returns the new crops."""
    earlier = {wall: sum(c["wall"] == wall for c in record["crops"]) for wall in blocked}
    more = prepare(scene, building, panos, out, only=set(blocked), avoid=blocked, numbering=earlier,
                   context_from=len(record["contexts"]))
    record["crops"] += more["crops"]
    record["contexts"] += more["contexts"]
    record.setdefault("blocked", {}).update({wall: [v["file"] for v in views] for wall, views in blocked.items()})
    return more["crops"]


def prepare(scene: Scene, building: Building, panos: Panoramas, out: Path, *, per_wall: int = views.VIEWS_PER_WALL,
            only: set | None = None, avoid: dict | None = None, numbering: dict | None = None,
            context_from: int = 0, layers: bool = False, limit: int = views.CROPS_PER_BUILDING) -> dict:
    """Write the crops of one building under `out` and return the record the reader works from.

    `only`, `avoid`: a second preparation for some walls, away from the views that did not show them
    (views.building_views); its crops and contexts are numbered on from `numbering` and `context_from`."""
    started = time.time()
    before = dict(panos.timings)
    found = views.building_views(scene, building, panos, per_wall=per_wall, only=only, avoid=avoid, layers=layers,
                                 limit=limit)
    selected = time.time()
    records, unseen = [], list(found["unseen"])
    for wall_id, wall_views in found["views"].items():
        kept = 0
        for k, view in enumerate(wall_views, (numbering or {}).get(wall_id, 0)):
            name = f"{wall_id.split(':')[-1]}-{k + 1}-{view.pano.source[0]}-{view.pano.date[:7]}.jpg"
            try:
                record = crops.wall_crop(view, panos, out / name)
            except Exception as error:  # noqa: BLE001 - a panorama that cannot be fetched or decoded
                print(f"observe: {wall_id} from {view.pano.id}: {error}")
                continue
            if record["missing_fraction"] > MAX_MISSING:
                Path(record["file"]).unlink()
                continue
            records.append(record)
            kept += 1
        if not kept:
            unseen.append({"wall": wall_id, "reason": "the panoramas that see it do not show enough of it"})
    contexts = []
    for k, pano in enumerate(found["contexts"], context_from):
        try:
            contexts.append(crops.context_view(building, pano, panos, out / f"context-{k + 1}.jpg"))
        except Exception as error:  # noqa: BLE001
            print(f"observe: context from {pano.id}: {error}")
    done = time.time()
    seen = {r["wall"] for r in records}
    return {
        "building": building.id,
        "feature_key": building.feature_key,
        "register": building.register,
        "lod2": scene.fingerprint,
        "walls": [dict(w.to_dict(), exposed=[[round(a, 2), round(b, 2), round(z - w.base, 2)] for a, b, z in w.exposed()],
                       seen=w.id in seen) for w in building.walls],
        "crops": records,
        "street": street_presence(building, records),
        "contexts": contexts,
        "unseen": sorted(unseen, key=lambda u: u["wall"]),
        "candidates": found["candidates"],
        "timing": {"select_s": round(selected - started, 2), "crops_s": round(done - selected, 2),
                   **{k: round(panos.timings[k] - before[k], 2) for k in before}},
    }

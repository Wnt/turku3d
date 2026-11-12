"""Views of one building: its walls, the panoramas that see each, rectified crops and context views."""

from __future__ import annotations

import time
from pathlib import Path

from . import crops, views
from .lod2 import Building, Scene
from .panoramas import Panoramas

MAX_MISSING = 0.25  # a crop with more of the wall outside the panorama's picture is dropped


def prepare(scene: Scene, building: Building, panos: Panoramas, out: Path, *, per_wall: int = views.VIEWS_PER_WALL
            ) -> dict:
    """Write the crops of one building under `out` and return the record the reader works from."""
    started = time.time()
    before = dict(panos.timings)
    found = views.building_views(scene, building, panos, per_wall=per_wall)
    selected = time.time()
    records, unseen = [], list(found["unseen"])
    for wall_id, wall_views in found["views"].items():
        kept = 0
        for k, view in enumerate(wall_views):
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
    for k, pano in enumerate(found["contexts"]):
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
        "contexts": contexts,
        "unseen": sorted(unseen, key=lambda u: u["wall"]),
        "candidates": found["candidates"],
        "timing": {"select_s": round(selected - started, 2), "crops_s": round(done - selected, 2),
                   **{k: round(panos.timings[k] - before[k], 2) for k in before}},
    }

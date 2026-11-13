"""What the building generator cannot build from a description: the first, render-free check of a reading.

The generator (turku3d.world.kinds.building) lays every described wall out on its shell wall. Here the same layout
is tested part by part: an opening that reaches beyond the wall, openings that overlap, storeys that do not add up
to the shell, a wall whose frame lies on no shell wall. The findings are sorted by described wall with positions in
that wall's own coordinates, so the reader can be asked about exactly those places.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import box

INSET_M = 0.12  # the generator keeps openings this far inside the wall outline
BASE_LIFT_M = 0.15  # an opening that fits once lifted this much only stands on the wall base: the generator's limit


def build_report(doc: dict) -> dict:
    """{"walls": {wall id: [{what, why, u_m?, v_m?, reader}]}, "unmatched": [wall ids]}.

    `reader` is true for a finding a second look at the photographs can settle; false for the generator's own
    limit (an opening standing on the wall base)."""
    from turku3d.world.kinds.building import compile as compiler
    from turku3d.world.kinds.building import describe, priors, workbench
    from turku3d.world.kinds.building import facade as facades
    from turku3d.world.kinds.building import shell as shells

    key = doc["id"].split("/", 1)[1]
    near = next((w["frame"]["p0"] for w in doc.get("walls", []) if w.get("frame")), None)
    shell, register = workbench.shell_for(key, near)
    effective = describe.effective(key, shell, register, doc)
    local = compiler._localise(effective, compiler.building_origin(effective["shell"]))
    shell_walls = shells.read(local["shell"]).facades
    out, taken = {"walls": {}, "unmatched": []}, set()
    for wall in local.get("walls", []):
        hit = shells.match(shell_walls, wall["frame"]["p0"], wall["frame"]["p1"])
        if hit is None or hit.index in taken:
            out["unmatched"].append(wall["id"])
            continue
        taken.add(hit.index)
        start = float((np.asarray(wall["frame"]["p0"], float) - hit.origin[:2]) @ hit.u_dir[:2])
        length = float(np.linalg.norm(np.subtract(wall["frame"]["p1"], wall["frame"]["p0"])))
        notes, found = [], []
        plan = facades.layout(hit, priors.resolve_wall(local, wall), local, notes)
        found += [{"what": "storeys", "why": note.split(": ", 1)[-1], "reader": True} for note in notes]
        inside, kept = hit.outline.buffer(-INSET_M), []
        for p in plan.placed:
            shape = box(p.u0, p.v0, p.u0 + p.width, p.v0 + p.height)
            item = {"what": p.name, "u_m": round(p.u0 + p.width / 2 - start, 2), "v_m": round(p.v0 - plan.datum, 2)}
            if not inside.contains(shape):
                lifted = box(p.u0, p.v0 + BASE_LIFT_M, p.u0 + p.width, p.v0 + p.height)
                on_wall = item["u_m"] - p.width / 2 >= INSET_M and item["u_m"] + p.width / 2 <= length - INSET_M
                if inside.contains(lifted):
                    why, reader = "stands on the wall base, which the generator does not allow yet", False
                elif on_wall and (p.u0 < INSET_M or p.u0 + p.width > hit.width - INSET_M):
                    why, reader = "lies on another shell wall under the same frame", False
                else:
                    beyond = "the wall's left end" if p.u0 < INSET_M else "the wall's right end" \
                        if p.u0 + p.width > hit.width - INSET_M else "the wall's top (eaves or gable line)"
                    why, reader = f"{p.width:.1f} m wide and {p.height:.1f} m high, it reaches beyond {beyond}", True
                found.append({**item, "why": why, "reader": reader})
            elif any(shape.intersects(other) for other in kept):
                found.append({**item, "why": "overlaps another opening of the same wall", "reader": True})
            else:
                kept.append(shape)
        if found:
            out["walls"][wall["id"]] = found
    return out


def count(report: dict, reader_only: bool = False) -> int:
    return sum(1 for items in report["walls"].values() for item in items if item["reader"] or not reader_only)

"""Large post-war office, hospital and campus slabs: ribbon windows between spandrels.

Without a reading or description such a slab would get the sampled dense grid of
small punched windows. Registered 1960-1979 buildings in institutional or office
use with a large footprint and at least 20 m of shell instead get one ribbon per
storey between flush spandrel bands, at the register storey pitch. Proportions
(sill, head, mullion pitch) are bounded priors measured on the TYKS U-hospital
(vfix 041). Existing descriptions and reviewed readings keep priority.
"""

import copy

import numpy as np

from turku3d import config as C

USES = {"hospital", "clinic", "office", "commercial", "university", "college", "school",
        "public", "civic", "government", "research_institute", "dormitory"}
SILL, HEAD_GAP, PANE = 0.95, 0.55, 1.25


def is_slab(building, program):
    a = building.attributes or {}
    year = a.get("construction_year")
    return (
        not program.get("description_source")
        and isinstance(year, (int, float)) and 1960 <= year <= 1979
        and a.get("osm_use") in USES
        and building.footprint.area >= 1500
        and building.top - building.base >= 20
    )


def storey_pitch(building, walls):
    tops = np.asarray(walls).reshape(-1, 4, 3)[:, 2:, 1].min(axis=1)
    height = float(tops.max() - building.base)
    levels = (building.attributes or {}).get("levels")
    if not (isinstance(levels, (int, float)) and levels >= 1 and 2.8 <= height / levels <= 4.4):
        levels = max(1, round(height / 3.4))
    return float(np.clip(height / levels, 2.8, 4.4))


def slab_wall(wall, vertices, building, pitch):
    """Expand one shell wall into storey ribbons in its own surveyed frame."""
    from turku3d.facade_modernist import expand

    a, b = vertices[0], vertices[1]
    width = float(np.linalg.norm((b - a)[[0, 2]]))
    height = float(vertices[2:, 1].min() - building.base)
    count = int(height / pitch + 1e-6)
    if width < 4.0 or count < 1:
        return wall
    w = copy.deepcopy(wall)
    w.update(width_m=width, height_m=height)
    w["measured_geometry"] = dict(
        p0=[float(a[0] + C.ORIGIN_E), float(C.ORIGIN_N - a[2])],
        p1=[float(b[0] + C.ORIGIN_E), float(C.ORIGIN_N - b[2])],
        base_h=float(building.base),
    )
    w["measured_size"] = [width, height]
    w["measured_wall_id"] = f"{building.facade_key}:slab"
    w["observation"] = dict(needs_review=False, scope="full-wall", source="post-war slab class")
    w["modernist"] = dict(
        support=0.7,
        margins_m=[min(0.8, width / 8), min(0.8, width / 8)],
        bands=dict(first_m=0.0, pitch_m=pitch, count=count, sill_m=SILL,
                   head_m=pitch - HEAD_GAP, pane_m=PANE),
    )
    w = expand(w)
    w["bays"].update(count=0, support=0.7)
    w["paint_source"] = "post-war-slab-class"
    return w

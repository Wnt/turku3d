"""Multi-storey parking garages: open levels between parapet bands, never a window grid.

OSM parking use (building=parking, amenity=parking, parking=multi-storey) on a
shell at least two levels high selects the class. Each level above the street
has a continuous concrete parapet band on its deck edge, leaning outward as in
brutalist garages; the open gap above it reads as the dark deck interior. The
street level stays open for entrances; the roof deck gets its parapet. Band
heights and slant are measured on the TYKS garage (vfix 040) and used as
bounded priors; a reviewed reading of the wall always wins.
"""

import copy

import numpy as np

from turku3d import config as C

LEVEL = 2.9  # storey pitch prior for parking decks
PARAPET = 1.1
SLANT = 0.5  # outward lean of a parapet band's top edge
CONCRETE = "#a9a69f"


def is_garage(building):
    a = building.attributes or {}
    if a.get("parking") in ("underground", "surface", "street_side"):
        return False
    return (a.get("osm_use") == "parking" or a.get("parking") == "multi-storey") and (
        building.top - building.base >= 2 * LEVEL - 0.3
    )


def garage_wall(wall, vertices, building):
    """Expand one shell wall into the garage band profile in its own surveyed frame."""
    from turkutex.facade_program import value

    from turku3d.facade_modernist import expand

    height = float(building.top - building.base)
    levels = max(2, round(height / LEVEL))
    pitch = height / levels
    a, b = vertices[0], vertices[1]
    width = float(np.linalg.norm((b - a)[[0, 2]]))
    if width < 2.0:
        return wall
    w = copy.deepcopy(wall)
    w.update(width_m=width, height_m=height)
    w["measured_geometry"] = dict(
        p0=[float(a[0] + C.ORIGIN_E), float(C.ORIGIN_N - a[2])],
        p1=[float(b[0] + C.ORIGIN_E), float(C.ORIGIN_N - b[2])],
        base_h=float(building.base),
    )
    w["measured_size"] = [width, height]
    w["measured_wall_id"] = f"{building.facade_key}:garage"
    w["observation"] = dict(needs_review=False, scope="full-wall", source="parking garage class")
    w["modernist"] = dict(
        support=0.7,
        margins_m=[0.3, 0.3],
        glazed_floors_m=[[0.0, pitch - 0.35]],
        bands=dict(
            first_m=pitch,
            pitch_m=pitch,
            count=levels - 1,
            sill_m=PARAPET + 0.05,
            head_m=pitch - 0.35,
            slab=dict(depth_m=0.3, thickness_m=0.3, parapet_m=PARAPET, slant_m=SLANT, colour=CONCRETE),
        ),
    )
    w = expand(w)
    for section in ("windows", "base", "doors"):
        w.setdefault(section, {})
    w["bays"].update(count=0, support=0.7)
    w["wall"]["material"] = value("concrete", "parking garage class", 0.7)
    w["wall"]["colour"] = value(CONCRETE, "parking garage class", 0.7)
    w["base"]["colour"] = value(CONCRETE, "parking garage class", 0.7)
    w["windows"]["frame_colour"] = value("#3a3a38", "parking garage class", 0.7)
    w["paint_source"] = "parking-garage-class"
    return w

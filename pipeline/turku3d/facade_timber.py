"""Old wooden houses (timber dated before 1940): small framed windows on a stone or brick plinth.

Without a reading or description such a house got the sampled timber grid: the register's one
storey stretched over the whole eaves wall (windows about 3 m tall on a 5.5 m wall) and a plinth
band in the wall's own boards and paint, so Portsa read as "plinthless boxes with oversized tall
windows" (w2-compare street-wooden, passage-03). Here each outer wall stands on a projecting granite
or red-brick plinth, keeps the register's storeys at a believable height (one storey where the
register says one, the wall above the window heads a boarded knee wall), and gets windows of the
measured size in white casings with six panes or a T-frame. Proportions are the bounded priors of
``turkutex.facade_archetypes.TIMBER_OLD``.

Scope: every rule-sampled timber house dated before 1940, city-wide (the old wooden quarters and
the 1920s suburbs share the type). Existing descriptions and reviewed readings keep priority, and
walls standing on a roof or an inner LoD2 ring keep the sampled layout.
"""

import copy
import random

import numpy as np

from turku3d import config as C


def is_old_timber(building, program):
    from turkutex.facade_archetypes import old_timber

    return (
        not program.get("description_source")
        and program.get("archetype") == "timber_traditional"
        and old_timber(program.get("attributes") or building.attributes or {})
    )


def _rng(key, field):
    from turkutex.facade_program import stable_seed

    return random.Random(stable_seed(key, "timber-old:" + field))


def layout(height, levels, plinth, prior):
    """(plinth, [(bottom, storey height)], knee) of an eaves wall ``height`` m high.

    The register's storey count is kept where its storeys fit at 2.7-3.6 m; a count that does
    not fit (the register counts the rooms in the roof) falls to the storeys that do. What is
    left over lifts the plinth (at most ``plinth_lift_max_m``) and then becomes the knee wall.
    """
    low, high = prior["storey_m"]
    available = height - plinth
    knee = prior["knee_m"] if available >= high + prior["knee_m"] else prior["knee_low_m"]
    fit = max(1, int((available - prior["knee_low_m"]) // low))
    if isinstance(levels, (int, float)) and levels >= 1:
        count = int(levels)
    else:
        count = max(1, round((available - knee) / ((low + high) / 2)))
    count = min(count, fit)
    each = min(high, (available - knee) / count)
    if each < low:
        knee = max(0.0, available - low * count)
        each = (available - knee) / count
    spare = max(0.0, available - knee - each * count)
    lift = min(spare, prior["plinth_lift_max_m"])
    plinth += lift
    knee += spare - lift
    return plinth, [(plinth + i * each, each) for i in range(count)], knee


def timber_wall(wall, vertices, building, program):
    """Expand one shell wall into the measured old-wooden-house layout, surveyed over its outline edge."""
    from turkutex.facade_archetypes import TIMBER_OLD, TIMBER_OLD_SOURCE, parsed_year
    from turkutex.facade_program import value

    prior = TIMBER_OLD
    # A shell wall may be one fragment of a facade (a gable end is several quads under its sloped
    # top): the layout spans the whole outline edge the fragment lies on (``width_m``, the fragment
    # starting ``uv_offset`` m along it), as learned layouts do, so the fragments of one facade share
    # one window rhythm and one plinth, and the paint's wall metres (u + uv_offset) stay inside it.
    fragment = vertices[1] - vertices[0]
    fragment[1] = 0
    tangent = fragment / max(float(np.linalg.norm(fragment)), 1e-9)
    a = vertices[0] - tangent * float(wall.get("uv_offset", 0) or 0)
    width = max(float(wall.get("width_m") or 0), float(np.linalg.norm(fragment)))
    b = a + tangent * width
    base = float(building.base)
    if not wall.get("outline", True) or float(vertices[:2, 1].min()) > base + 1.0:
        return wall  # A setback or roof-step ring above a lower part keeps the sampled layout.
    attrs = program.get("attributes") or building.attributes or {}
    key = str(attrs.get("register_id") or building.facade_key)
    height = float(wall.get("layout_height_m", wall["height_m"]))
    top = float(vertices[:, 1].max()) - base
    plinth = _rng(key, "plinth").uniform(*prior["plinth_m"])
    plinth, storeys, knee = layout(height, program["storeys"]["value"], plinth, prior)
    if storeys[0][1] < prior["storey_min_m"]:
        return wall  # Too low for a lived-in storey: a shed keeps its sampled treatment.
    m = lambda x: value(x, TIMBER_OLD_SOURCE, 0.6)  # noqa: E731
    w = copy.deepcopy(wall)
    w.update(width_m=width, height_m=float(wall["height_m"]))  # uv_offset stays: the fragment's place on the edge
    w["measured_geometry"] = dict(
        p0=[float(a[0] + C.ORIGIN_E), float(C.ORIGIN_N - a[2])],
        p1=[float(b[0] + C.ORIGIN_E), float(C.ORIGIN_N - b[2])],
        base_h=base,
    )
    w["measured_size"] = [width, max(top, height)]
    w["measured_wall_id"] = f"{building.facade_key}:timber-old"
    w["observation"] = dict(needs_review=False, scope="full-wall", source="old wooden house class")
    weights = prior["plinth_weights"]
    material, colour = _rng(key, "plinth-material").choices(prior["plinths"], weights=weights)[0]
    w["material_bands"] = [dict(bottom_m=0.0, height_m=round(plinth, 3), material=material,
                                colour=colour, relief_m=prior["plinth_relief_m"], support=0.6)]
    w["base"]["plinth_height_m"] = m(0)  # the stone band above replaces the painted board plinth
    year = parsed_year(attrs.get("construction_year"))
    panes = prior["panes_early"] if year and year < prior["panes_until"] else prior["panes_late"]
    w["windows"]["divisions"] = m(list(panes))
    w["windows"]["frame_colour"] = m(field_value(w["trim"], "colour", "#e8e4da"))
    rectangles, roles = [], []
    ww = _rng(key, "window").uniform(*prior["window_m"])
    pitch = _rng(key, "pitch").uniform(*prior["pitch_m"])
    count = max(1, round(width / pitch)) if width >= prior["min_wall_m"] else 0
    bay = width / max(count, 1)
    ww = min(ww, bay * 0.6)
    # A mapped cafe, restaurant or shop opens this wall (coarse.premises): its street storey gets
    # the wooden town's shop windows, wide and low-silled, instead of the dwelling's sashes.
    shop = bool(wall.get("premise_front"))
    for n, (bottom, storey) in enumerate(storeys):
        if shop and n == 0:
            sill = prior["shop_sill_m"]
            wh = min(prior["shop_window_h_m"], storey - sill - prior["head_gap_m"])
            sw = min(_rng(key, "shop").uniform(*prior["shop_window_m"]), bay * prior["shop_bay_fraction"])
        else:
            sill = min(prior["sill_m"], storey * 0.3)
            wh = min(prior["window_h_m"], storey - sill - prior["head_gap_m"])
            sw = ww
        if wh < 0.8:
            continue
        for i in range(count):
            rectangles.append([round((i + 0.5) * bay - sw / 2, 3), round(bottom + sill, 3),
                               round(sw, 3), round(wh, 3)])
            roles.append("window")
    if knee >= prior["attic_knee_m"]:
        # A knee wall this tall has rooms behind it: one small attic light per bay.
        side = prior["attic_window_m"]
        attic = storeys[-1][0] + storeys[-1][1] + prior["attic_sill_m"]
        for i in range(count):
            rectangles.append([round((i + 0.5) * bay - side / 2, 3), round(attic, 3), side, side])
            roles.append("window")
    w["windows"].update(
        rectangles_m=rectangles,
        opening_roles=roles,
        head_shapes=["flat"] * len(rectangles),
        surround_shapes=[prior["casing"]] * len(rectangles),
        head_shape=m("flat"),
        size_m=m([ww, prior["window_h_m"]]),
    )
    doors = []
    entrances = getattr(building, "entrances", ()) or ()
    door_w, door_h = prior["door_m"]
    for point in entrances:
        t = _along(a, b, point)
        if t is not None and door_w / 2 <= t <= width - door_w / 2:
            doors.append(t)
    if not doors and count >= 3 and (shop or field_value(wall["base"], "variant", "windows") == "doors"):
        doors.append(width / 2)  # the sampled street door (a premise's own), centred between window bays
    w["doors"]["candidates"] = []
    for t in doors[:2]:
        left = t - door_w / 2
        # A window the door overlaps gives way to it.
        keep = [i for i, (x, y, rw, _h) in enumerate(rectangles)
                if not (y < storeys[0][0] + storeys[0][1] and x < left + door_w and left < x + rw)]
        w["windows"]["rectangles_m"] = [rectangles[i] for i in keep]
        for name in ("opening_roles", "head_shapes", "surround_shapes"):
            w["windows"][name] = [w["windows"][name][i] for i in keep]
        rectangles = w["windows"]["rectangles_m"]
        w["doors"]["candidates"].append(dict(rect_m=[round(left, 3), 0.0, door_w,
                                                     round(min(plinth + door_h, height - 0.3), 3)],
                                             surround=prior["casing"], support=0.6))
    belts = [round(bottom, 3) for bottom, _ in storeys[1:]]
    if knee >= 0.8:
        last_bottom, last = storeys[-1]
        belts.append(round(last_bottom + last, 3))
    w["trim"]["string_courses"] = m(belts)
    w["base"]["openings_m"] = []
    w["niches"] = []
    w["balconies"] = m([])
    w["bays"].update(count=0, support=0.6)
    w["timber_old"] = dict(plinth_m=round(plinth, 3), storeys=[round(s, 3) for _, s in storeys],
                           knee_m=round(knee, 3), plinth_material=material, windows=len(rectangles),
                           shopfront=shop)
    w["paint_source"] = "old-wooden-house-class"
    return w


def field_value(section, key, default=None):
    item = section.get(key, {})
    result = item.get("value") if isinstance(item, dict) else item
    return default if result is None else result


def _along(a, b, point, reach=1.5):
    """Metres along the wall a->b of a mapped door node within ``reach`` of it, else None."""
    p0, p1 = np.asarray(a, float)[[0, 2]], np.asarray(b, float)[[0, 2]]
    d = p1 - p0
    length = float(np.linalg.norm(d))
    if length < 1e-6:
        return None
    t = float((np.asarray(point, float) - p0) @ d) / length
    if not 0 <= t <= length:
        return None
    if float(np.linalg.norm(np.asarray(point, float) - (p0 + d * t / length))) > reach:
        return None
    return t

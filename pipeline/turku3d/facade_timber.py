"""Old wooden houses (timber dated before 1940): small framed windows on a stone or brick plinth.

Without a reading or description such a house got the sampled timber grid: the register's one
storey stretched over the whole eaves wall (windows about 3 m tall on a 5.5 m wall) and a plinth
band in the wall's own boards and paint, so Portsa read as "plinthless boxes with oversized tall
windows" (w2-compare street-wooden, passage-03). Here each outer wall stands on a projecting granite
or red-brick plinth, keeps the register's storeys at a believable height (one storey where the
register says one, the wall above the window heads a boarded knee wall), and gets windows of the
measured size in white casings with six panes or a T-frame. Proportions are the bounded priors of
``turkutex.facade_archetypes.TIMBER_OLD``.

The wall above the storeys is not left blank (pp-wood2, after the first stills): a register storey
with no room under the eaves is a storey in the roof (a storey and a half), so the knee wall gets a
row of small windows under the eaves and the gables the roof storey's windows and an attic light;
a gable over storeys that all fit gets one attic window. The plinth is the area's: tall granite or
red brick in the town of the centre milieu typing (Portsa), grey concrete under the 1920s-30s
houses outside it (Itäharju), the low stone or brick plinth elsewhere (``plinth_area``).

Scope: every rule-sampled timber house dated before 1940, city-wide (the old wooden quarters and
the 1920s suburbs share the type); the plinth by area as above. Existing descriptions and reviewed
readings keep priority, and walls standing on a roof or an inner LoD2 ring keep the sampled layout.
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


def plinth_area(attrs, prior):
    """Which plinth the house stands on: "town", "suburb" or "other" (``TIMBER_OLD["plinth_areas"]``).

    Scope (Jonni's area rule, 2026-11-13): the tall granite or brick plinth only in the town of the
    centre milieu typing (``coarse.milieus.in_town``), where the Portsa photos were taken; the grey
    concrete base only outside it for houses built from 1920 (the Itäharju photo; the 1920s-30s
    garden suburbs). Older houses outside the town keep the low stone or brick plinth. Without the
    milieu table (a source version that lacks it) no house is in the town.
    """
    from turku3d.coarse.milieus import in_town
    from turkutex.facade_archetypes import parsed_year

    if in_town(attrs):
        return "town"
    year = parsed_year(attrs.get("construction_year"))
    return "suburb" if year and year >= prior["plinth_suburb_from"] else "other"


def layout(height, levels, plinth, prior):
    """(plinth, [(bottom, storey height)], knee, roof) of an eaves wall ``height`` m high.

    The register's storey count is kept where its storeys fit at 2.7-3.6 m. A count that does not
    fit falls to the storeys that do; the rest (``roof``) are the register's rooms in the roof, a
    storey and a half: their floor is the top storey's ceiling, so the knee wall keeps room for
    their windows under the eaves and the gables carry theirs (``timber_wall``). What is left over
    lifts the plinth (at most ``plinth_lift_max_m``, never past ``plinth_total_max_m``) and then
    becomes the knee wall.
    """
    low, high = prior["storey_m"]
    available = height - plinth
    knee = prior["knee_m"] if available >= high + prior["knee_m"] else prior["knee_low_m"]
    fit = max(1, int((available - prior["knee_low_m"]) // low))
    if isinstance(levels, (int, float)) and levels >= 1:
        count = int(levels)
    else:
        count = max(1, round((available - knee) / ((low + high) / 2)))
    roof = max(0, count - fit)
    count = min(count, fit)
    if roof:
        knee = max(knee, min(prior["knee_m"], available - low * count))
    each = min(high, (available - knee) / count)
    if each < low:
        knee = max(0.0, available - low * count)
        each = (available - knee) / count
    spare = max(0.0, available - knee - each * count)
    lift = min(spare, prior["plinth_lift_max_m"], max(0.0, prior["plinth_total_max_m"] - plinth))
    plinth += lift
    knee += spare - lift
    return plinth, [(plinth + i * each, each) for i in range(count)], knee, roof


def edge_profiles(walls, program, base):
    """{wall index: [(u0, u1, top0, top1)]}: every shell fragment of the wall's outline edge, in edge
    metres along it and metres above ``base``. A gable end comes as several quads under its sloped
    top; each fragment is laid out alone, so this is how it knows where the gable has room."""
    edges, keys = {}, {}
    for index in range(len(walls) // 4):
        wall = program["walls"][index]
        if not wall.get("outline", True):
            continue
        v = walls[index * 4 : index * 4 + 4]
        run = v[1] - v[0]
        run[1] = 0
        length = float(np.linalg.norm(run))
        if length < 1e-6:
            continue
        tangent = run / length
        u0 = float(wall.get("uv_offset", 0) or 0)
        a = v[0] - tangent * u0
        key = (round(float(a[0]), 1), round(float(a[2]), 1), round(float(tangent[0]), 2),
               round(float(tangent[2]), 2), round(float(wall.get("width_m") or length), 1))
        keys[index] = key
        edges.setdefault(key, []).append((u0, u0 + length, float(v[3, 1]) - base, float(v[2, 1]) - base))
    return {index: sorted(edges[key]) for index, key in keys.items()}


def _top(profile, u):
    """The wall top (m above the base) at ``u`` m along the edge; None where no fragment of it stands
    (past its ends, or a gap the shell left: nothing is known there)."""
    for u0, u1, t0, t1 in profile:
        if u0 - 1e-6 <= u <= u1 + 1e-6:
            return t0 + (t1 - t0) * (u - u0) / max(u1 - u0, 1e-9)
    return None


def _under(profile, x0, x1, top):
    """True when the wall stands at least ``top`` m high wherever it stands over [x0, x1]."""
    points = [x0, x1] + [u for u0, u1, _, _ in profile for u in (u0, u1) if x0 < u < x1]
    return all(t is None or t >= top for t in (_top(profile, u) for u in points))


def gable_windows(profile, height, floor, roof, ww, prior):
    """Window rectangles (x, y, w, h; edge metres) in the gable over an eaves line ``height`` m up.

    The roof storey (``roof`` > 0, floor ``floor`` m up) gets its windows beside the ridge, two when
    they fit and one when not, and an attic light above them; a gable without a roof storey gets one
    attic window. Every window stays ``gable_margin_m`` under the verges, so none is cut by the roof.
    """
    if not profile:
        return []
    peak_u, peak = max(((u, t) for u0, u1, t0, t1 in profile for u, t in ((u0, t0), (u1, t1))),
                       key=lambda item: item[1])
    if peak - height < prior["gable_rise_m"]:
        return []
    margin = prior["gable_margin_m"]
    result, head = [], height
    if roof:
        low, high = prior["gable_window_h_m"]
        sill = floor + prior["gable_sill_m"]
        for h in (high, (low + high) / 2, low):
            for centres in ((peak_u - prior["gable_pair_m"] / 2, peak_u + prior["gable_pair_m"] / 2), (peak_u,)):
                rects = [(c - ww / 2, sill, ww, h) for c in centres]
                if all(_under(profile, x - 0.1, x + w + 0.1, y + h + margin) for x, y, w, h in rects):
                    result, head = rects, sill + h
                    break
            if result:
                break
    small, large = prior["attic_window_m"]
    for side in (large, small):
        sill = max(head, height) + prior["attic_gap_m"]
        rect = (peak_u - side / 2, sill, side, side)
        if _under(profile, rect[0] - 0.1, rect[0] + side + 0.1, sill + side + margin):
            result.append(rect)
            break
    return [[round(x, 3), round(y, 3), round(w, 3), round(h, 3)] for x, y, w, h in result]


def timber_wall(wall, vertices, building, program, profile=None):
    """Expand one shell wall into the measured old-wooden-house layout, surveyed over its outline edge.

    ``profile`` is the outline edge's fragments (``edge_profiles``); without it the fragment is
    taken as the whole edge."""
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
    if not profile:
        u0 = float(wall.get("uv_offset", 0) or 0)
        profile = [(u0, u0 + float(np.linalg.norm(fragment)), float(vertices[3, 1]) - base,
                    float(vertices[2, 1]) - base)]
    area = plinth_area(attrs, prior)
    plinths = prior["plinth_areas"].get(area) or dict(
        height_m=prior["plinth_m"], plinths=prior["plinths"], weights=prior["plinth_weights"],
        relief_m=prior["plinth_relief_m"])
    plinth = _rng(key, "plinth").uniform(*plinths["height_m"])
    plinth, storeys, knee, roof = layout(height, program["storeys"]["value"], plinth, prior)
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
    peak = max(max(t0, t1) for *_, t0, t1 in profile)
    w["measured_size"] = [width, max(top, height, peak)]
    w["measured_wall_id"] = f"{building.facade_key}:timber-old"
    w["observation"] = dict(needs_review=False, scope="full-wall", source="old wooden house class")
    material, colour = _rng(key, "plinth-material").choices(plinths["plinths"], weights=plinths["weights"])[0]
    w["material_bands"] = [dict(bottom_m=0.0, height_m=round(plinth, 3), material=material,
                                colour=colour, relief_m=plinths["relief_m"], support=0.6)]
    w["base"]["plinth_height_m"] = m(0)  # the stone band above replaces the painted board plinth
    year = parsed_year(attrs.get("construction_year"))
    panes = prior["panes_early"] if year and year < prior["panes_until"] else prior["panes_late"]
    w["windows"]["divisions"] = m(list(panes))
    w["windows"]["frame_colour"] = m(field_value(w["trim"], "colour", "#e8e4da"))
    rectangles, roles = [], []
    head = storeys[-1][0] + storeys[-1][1]  # the top storey's window heads, once laid out
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
            sw = min(ww, wh / prior["upright"])  # sashes stand upright
        if wh < 0.8:
            continue
        head = bottom + sill + wh
        for i in range(count):
            x = (i + 0.5) * bay - sw / 2
            if not _under(profile, x - 0.05, x + sw + 0.05, bottom + sill + wh + prior["eaves_gap_m"]):
                continue  # the wall is lower here than the building's layout (a lower wing): no cut window
            rectangles.append([round(x, 3), round(bottom + sill, 3), round(sw, 3), round(wh, 3)])
            roles.append("window")
    floor = storeys[-1][0] + storeys[-1][1]
    # This edge's own eaves line: the lowest top along it (a long wall's top, a gable end's corners).
    # The layout height is the building's and may stand above it (Portsa: 9.1 m over 7.4 m walls),
    # so the row under the eaves and the gable are measured from the wall itself.
    line = min([height] + [t for u0, u1, t0, t1 in profile if u1 - u0 >= 0.25 for t in (t0, t1)])
    is_gable = peak - line >= prior["gable_rise_m"]
    gable = gable_windows(profile, line, floor, roof, ww, prior) if is_gable else []
    cornice = field_value(w["trim"], "cornice", 0.0)
    cornice = float(cornice) if isinstance(cornice, (int, float)) else 0.0
    low, high = prior["eaves_window_m"]
    side = min(high, line - floor - cornice - 2 * prior["eaves_gap_m"] - 0.12)
    eaves = 0
    # A gable whose roof storey got its windows beside the ridge has them in this band already.
    roof_lit = any(y < line for _x, y, _w, _h in gable)
    if (not roof_lit and count and side >= low
            and (line - floor >= prior["eaves_room_knee_m"] if roof else height - head >= prior["eaves_band_m"])):
        # The rooms behind a tall blank band over the window heads (the building's, so all its walls
        # agree; or the register's storey in the roof) look out under the eaves: one small window per
        # bay over the windows below, clear of this wall's eaves board. On a gable end too low for
        # windows of its own the row runs at the same height, and a window stands only where the wall
        # is that high.
        sill = line - cornice - prior["eaves_gap_m"] - side
        ew = min(side * 1.15, ww)
        for i in range(count):
            x = (i + 0.5) * bay - ew / 2
            if not _under(profile, x - 0.05, x + ew + 0.05, sill + side + prior["eaves_gap_m"]):
                continue
            rectangles.append([round(x, 3), round(sill, 3), round(ew, 3), round(side, 3)])
            roles.append("window")
            eaves += 1
    for rect in gable:
        rectangles.append(rect)
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
                           shopfront=shop, plinth_area=area, roof_storeys=roof, eaves_windows=eaves,
                           gable_windows=len(gable), eaves_m=round(line, 3))
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

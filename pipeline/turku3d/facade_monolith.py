"""Modern public monoliths: blank volumes, curtain walls and panel grids by photographed wall.

Libraries, museums and halls are composed of blank volumes and a few fully glazed ones.
A building-wide description or the sampled archetype grid must not paint windows on
walls nobody photographed with windows. Once a reviewed reading or a wall's own
description shows a blank wall, every wall keeps only its own evidence:

- a wall with its own reference description keeps that description's layout, or becomes
  a full-height curtain wall when it describes near full-bay glazing;
- a reviewed wall keeps its reading (applied later by ``resolve``);
- every other wall is a blank volume in the class material, with a stone base course,
  and the photographed panel-joint grid when the readings report panel cladding.

The measured priors are in ``turkutex.facade_archetypes`` (``MONOLITH_*``).
"""

from __future__ import annotations

import copy
import re

import numpy as np

PANEL_TEXT = re.compile(r"\bpanel(s|led)?\b.*\b(grid|joints?|cladding)\b|\b(grid|joints?)\b.*\bpanels?\b")
BLANK_TEXT = re.compile(r"\b(blank|blind|windowless)\b")
LARGE_SECTION = re.compile(r"majority|whole|entire|main|upper")


def _reviewed(observed):
    return [w for w in (observed or {}).get("walls", [])
            if not w.get("observation", {}).get("needs_review", True)]


def _texts(wall):
    form = wall.get("structure", {})
    yield str(form.get("occluded_parts", ""))
    yield str(form.get("render_deferred", ""))
    for feature in form.get("features", []) or []:
        yield str(feature.get("kind", "")) if isinstance(feature, dict) else str(feature)
    for section in form.get("facade_observations", {}).get("sections", []):
        if section.get("belongs_to_facade") is False:
            continue
        yield " ".join([str(section.get("kind", "")), *map(str, section.get("features", []))])


def reviewed_blank(wall):
    """A reviewed reading whose wall is (mostly) blank, not a blank fragment."""
    form = wall.get("structure", {})
    floors = form.get("floors") or []
    if floors and all(set(f.get("bays") or []) == {"blank"} for f in floors):
        return True
    for section in form.get("facade_observations", {}).get("sections", []):
        if section.get("belongs_to_facade") is False:
            continue
        if BLANK_TEXT.search(str(section.get("kind", "")).lower()) and LARGE_SECTION.search(
                str(section.get("position", "")).lower()):
            return True
    return False


def own_descriptions(key):
    """Stored facades with a description observed on that facade (not adopted building-wide)."""
    from turku3d.facade_assets import building

    rows = []
    for facade in building(key).get("facades", []):
        fast = facade["metadata"].get("fast_description") or {}
        description = fast.get("description") or {}
        if (fast.get("source") in ("reference", "astra") and description.get("wall_visible", True)
                and not description.get("problem")):
            rows.append((facade, description))
    return rows


def evidence(key, observed):
    """Photographed blank-wall and panel-grid evidence for a building (no geometry)."""
    reviewed = _reviewed(observed)
    blank = any(reviewed_blank(w) for w in reviewed) or any(
        d.get("window_columns") == 0 and f["metadata"].get("width_m", 0) >= 4
        for f, d in own_descriptions(key)
    )
    panels = any(PANEL_TEXT.search(text.lower()) for w in reviewed for text in _texts(w))
    return dict(blank=blank, panels=panels)


def curtain(description):
    return (description.get("material") == "glass"
            and description.get("window_width", 0) >= 0.85
            and description.get("window_height", 0) >= 0.85)


def _own_wall_description(points, prepared):
    from turku3d.coarse.stored_facades import match_prepared

    matched = match_prepared(points, prepared, min_coverage=0.5)
    return matched[0] if matched else None


GLAZING = re.compile(r"\b(glazing|glazed|glass|curtain|shopfront|window(s|ed)?)\b")
GROUND = re.compile(r"\b(ground|lower|base|bottom|street)\b")
ENTRANCE = re.compile(r"\b(entrance|entry|doors?|lobby|foyer)\b")
LOCAL = re.compile(r"\blocali[sz]ed\b|\bsmall\b|\bsingle\b")
# Photographed walls lend their type to unphotographed walls facing within this angle.
SAME_SIDE = np.cos(np.radians(35))
CURTAIN_MIN_M = 7.0  # a windowed reading at least two storeys tall is a curtain wall
ENTRANCE_REACH_M = 2.0  # an OSM entrance node this close to a wall's baseline opens its ground storey


def _sections(reading):
    for section in reading.get("structure", {}).get("facade_observations", {}).get("sections", []):
        if section.get("belongs_to_facade") is not False:
            yield section, " ".join([str(section.get("kind", "")), str(section.get("material", "")),
                                     *map(str, section.get("features") or [])]).lower()


def reading_type(reading):
    """One reviewed wall's composition: {upper: blank|curtain|None, ground: glazed|plain|None}.

    ``upper`` is blank for a (mostly) blank reading and curtain for glazed floors or a large glazed
    section; a monolith's glazed walls are curtain walls (libraries, theatres, museums), not
    punched openings. ``ground`` is glazed for a non-localized glazing band or an entrance at the
    base, plain for a blank base, else unknown."""
    upper = ground = None
    floors = reading.get("structure", {}).get("floors") or []
    if reviewed_blank(reading):
        upper = "blank"
    elif floors and all("window" in (f.get("bays") or []) for f in floors):
        if float(reading.get("height_m") or 0) >= CURTAIN_MIN_M:
            upper = "curtain"
            ground = "glazed" if len(floors) >= 2 else None
        else:
            # One storey of windows (a low wing's row): its own reading paints its openings and
            # the volume lends the class's blank cladding, not a glass box, to its neighbours.
            upper = "blank"
    for section, text in _sections(reading):
        position = str(section.get("position", "")).lower()
        if upper is None and GLAZING.search(text) and LARGE_SECTION.search(position) \
                and not BLANK_TEXT.search(text):
            upper = "curtain"
        if GROUND.search(position):
            if (GLAZING.search(text) or ENTRANCE.search(text)) and not LOCAL.search(text):
                ground = "glazed"
            elif BLANK_TEXT.search(text) and ground is None:
                ground = "plain"
    return dict(upper=upper, ground=ground, reading=reading)


def description_type(description, width):
    """One wall's own reference description as the same composition."""
    if curtain(description) or (description.get("window_columns") and description.get("material") == "glass"):
        upper = "curtain"
    elif description.get("window_columns"):
        upper = "punched"
    else:
        upper = "blank"
    opened = description.get("ground_floor") in ("shops", "doors") or description.get("ground_openings")
    return dict(upper=upper, ground="glazed" if opened else "plain", description=description, width=width)


def _frame(points):
    """Midpoint and outward horizontal normal (engine x/z) of one extruded wall quad."""
    points = np.asarray(points, float)
    d = points[1, [0, 2]] - points[0, [0, 2]]
    n = np.array([d[1], -d[0]]) / max(float(np.linalg.norm(d)), 1e-9)
    return points[:2, [0, 2]].mean(axis=0), n


VOLUME_WEIGHT = 2.0  # metres of distance per metre of top-height difference (another volume)
SAME_TOP_M = 1.5     # a photograph reaching above a wall's top by more than this shows a taller volume
PART_REACH_M = 1.0   # a photograph of the wall's own part lies on that part's footprint outline


def donor(centre, normal, records, top=None, part=None, bottom=None):
    """The photographed wall whose type an unphotographed wall inherits.

    Donors on the wall's own building part come first: photographs on the outline of the wall's
    footprint part whose observed span lies within the wall's height (a reading covers only the
    storeys it saw, so its top may stay below the eave, never above it). A library's low stone
    wing inherits from the wing, never from the glass box beside it. Only
    a part without any typed photograph borrows from the rest of the building, where a different
    top height costs distance. Within the chosen set, the nearest wall facing the same side wins,
    else the nearest at all. ``records`` are (centre, outward normal, type, top) of every
    photographed wall of the building, whichever LoD2 part carries it."""
    known = [r for r in records if r[2] and r[2]["upper"]]
    if not known:
        return None
    own = []
    if part is not None and top is not None:
        import shapely

        low = top - 1e9 if bottom is None else bottom
        own = [r for r in known if len(r) > 3 and r[3] is not None and low < r[3] <= top + SAME_TOP_M
               and part.boundary.distance(shapely.Point(*np.asarray(r[0], float))) <= PART_REACH_M]
    pool = own or known
    same = [r for r in pool if float(np.asarray(r[1]) @ normal) >= SAME_SIDE]

    def cost(r):
        step = abs(r[3] - top) if not own and top is not None and len(r) > 3 and r[3] is not None else 0.0
        return float(np.linalg.norm(np.asarray(r[0]) - centre)) + VOLUME_WEIGHT * step

    return min(same or pool, key=cost)[2]


def evidence_records(observed, rows):
    """(centre, outward normal, type) in engine x/z for every reviewed reading and own description."""
    from turku3d import config as C

    def frame(p0, p1, normal):
        a = np.array([p0[0] - C.ORIGIN_E, C.ORIGIN_N - p0[1]])
        b = np.array([p1[0] - C.ORIGIN_E, C.ORIGIN_N - p1[1]])
        n = np.asarray(normal, float)
        if not np.isfinite(n).all() or np.linalg.norm(n) < 1e-6:
            d = b - a
            n = np.array([d[1], -d[0]])
        return (a + b) / 2, n / max(float(np.linalg.norm(n)), 1e-9)

    records = []
    for reading in (observed or {}).get("walls", []):
        g = reading.get("geometry", {})
        points = g.get("coordinates") or ([g["p0"], g["p1"]] if "p0" in g and "p1" in g else None)
        if reading.get("observation", {}).get("needs_review", True) or not points:
            continue
        n = g.get("normal")
        n = [n[0], -n[1]] if n is not None and len(n) == 2 else [np.nan, np.nan]  # EN -> engine x/z
        height = reading.get("height_m")
        top = g.get("base_h") + height if g.get("base_h") is not None and height else None
        records.append((*frame(points[0], points[-1], n), reading_type(reading), top))
    for facade, description in rows:
        points = facade.get("coordinates") or []
        if len(points) < 2:
            continue
        n = facade.get("normal")
        n = [n[0], n[2]] if n is not None and len(n) == 3 else [np.nan, np.nan]
        height = facade["metadata"].get("height_m")
        top = facade.get("base_height") + height if facade.get("base_height") is not None and height else None
        records.append((*frame(points[0], points[-1], n),
                        description_type(description, facade["metadata"].get("width_m", 0)), top))
    return records


def _near_entrance(points, entrances):
    if not entrances:
        return False
    a, b = np.asarray(points, float)[0, [0, 2]], np.asarray(points, float)[1, [0, 2]]
    d = b - a
    length = float(d @ d)
    for e in entrances:
        t = 0.0 if length < 1e-9 else min(1.0, max(0.0, float((np.asarray(e) - a) @ d) / length))
        if float(np.linalg.norm(np.asarray(e) - (a + t * d))) <= ENTRANCE_REACH_M:
            return True
    return False


def _drop_reading(wall):
    """A reading typed as a curtain wall is repainted by the class: its measured layout drew
    the glazed floors as blank cladding (the adapter leaves uncounted openings to the prior)."""
    for k in ("measured_wall_id", "measured_geometry", "measured_size", "render_sections",
              "geometry_components", "geometry_pending", "unsupported", "structure"):
        wall.pop(k, None)


def ground_band(wall, storeys, m):
    """A glazed street storey under a blank volume: curtain glazing one storey high."""
    height = wall.get("layout_height_m", wall["height_m"])
    storey = height / storeys if storeys else 4.0
    band = min(4.5, max(2.8, storey), 0.6 * height)
    glazed = copy.deepcopy(wall)
    glazed.update(height_m=band, layout_height_m=band)
    curtain_wall(glazed, None, m, glazed_ground=True)
    glazed.pop("ground_band", None)
    wall["ground_band"] = dict(height_m=band, wall=glazed)


def apply(program, key, vertices, observed, description, entrances=(), footprint=None):
    """Re-own every wall of a monolith to its own evidence or to its photographed neighbour's type.

    Photographed walls keep their reading or description. An unphotographed wall inherits the
    type of the nearest photographed wall facing the same side (else its nearest photographed
    neighbour): blank volume, curtain wall, or the described window layout. A glazed street storey
    under a blank volume follows the same evidence, or an OSM entrance on that wall."""
    from turkutex.facade_archetypes import MONOLITH_PANEL, MONOLITH_RENDER
    from turkutex.facade_program import value

    from turku3d.coarse.stored_facades import prepare_walls
    from turku3d.facade_programs import describe, field, matching_wall

    facts = evidence(key, observed)
    program["monolith"] = facts
    rows = own_descriptions(key)
    prepared = prepare_walls([f for f, _ in rows])
    by_id = {f["metadata"]["facade_id"]: d for f, d in rows}
    m = lambda x, source: value(x, source, 0.6)  # noqa: E731
    storeys = program["storeys"]["value"]
    # Opaque building descriptions keep their colour on blank volumes; a glass
    # description (the glazed volume) cannot colour the rendered ones.
    opaque = description if description and description.get("material") != "glass" else None
    blank_colour = (value(opaque["wall_colour"], "existing description", 1) if opaque
                    else m(MONOLITH_PANEL["colour"] if facts["panels"] else MONOLITH_RENDER,
                           "reference-measured monolith pigment"))
    blank_material = ("concrete" if facts["panels"] else "plaster") if not opaque else opaque["material"]
    # A joint grid belongs to panel cladding, never to brick or timber volumes.
    panels = facts["panels"] and blank_material not in ("brick", "wood")
    walls = program["walls"]
    quads = [np.asarray(vertices[i * 4: i * 4 + 4]) for i in range(len(walls))]
    frames = [_frame(q) for q in quads]
    records = evidence_records(observed, rows)
    types = []
    for points in quads:
        reading = matching_wall(points, observed)
        facade = None if reading or not prepared else _own_wall_description(points, prepared)
        own = by_id.get(facade["metadata"]["facade_id"]) if facade else None
        types.append(reading_type(reading) if reading
                     else description_type(own, facade["metadata"]["width_m"]) if own else None)

    def paint(wall, kind, *, own):
        upper = kind["upper"]
        if upper == "curtain":
            curtain_wall(wall, kind.get("description"), m,
                         glazed_ground=kind["ground"] == "glazed")
        elif upper == "punched":
            describe({**program, "walls": [wall]}, kind["description"], kind["width"])
        else:
            blank_wall(wall, blank_colour, blank_material, panels, m)
        role = {"curtain": "curtain", "punched": "own description", "blank": "blank"}[upper]
        wall["monolith_role"] = role if own else "inherited " + role

    for index, wall in enumerate(walls):
        kind = types[index]
        if kind and "reading" in kind:
            if kind["upper"] == "curtain":
                _drop_reading(wall)
                paint(wall, kind, own=True)
            else:
                # The reviewed reading owns this wall; add only what the class knows about it.
                reviewed_wall(wall, kind["reading"], storeys, panels, m)
                if kind["upper"] == "blank" and field(wall["wall"], "material", None) == "glass":
                    # A blank reading's cladding is the class volume, not the glass family prior.
                    _material(wall, blank_material, m)
                    wall["wall"]["colour"] = dict(blank_colour)
                wall["monolith_role"] = "reviewed"
            continue
        if kind:
            paint(wall, kind, own=True)
        else:
            kind = donor(*frames[index], records, top=float(quads[index][2:, 1].max()), part=footprint,
                         bottom=float(quads[index][:2, 1].min()))
            if kind is None:
                blank_wall(wall, blank_colour, blank_material, panels, m)
                continue
            if kind["upper"] == "punched" and "description" not in kind:
                kind = dict(kind, upper="curtain")
            paint(wall, kind, own=False)
        # The street storey of a blank volume: glazed where the evidence (own, or the donor's
        # non-localized base glazing) or an OSM entrance on this wall says so.
        street = wall.get("outline", True) and float(quads[index][:2, 1].min()) <= \
            float(np.asarray(vertices)[:, 1].min()) + 0.5
        if street and wall["monolith_role"].endswith("blank") and (
                kind["ground"] == "glazed" or _near_entrance(quads[index], entrances)):
            ground_band(wall, storeys, m)
    roles = ("blank", "curtain", "own description")
    program["monolith"]["walls"] = {
        **{role: sum(w.get("monolith_role") == role for w in walls) for role in roles},
        **{"inherited " + role: sum(w.get("monolith_role") == "inherited " + role for w in walls)
           for role in roles},
        "reviewed": sum(w.get("monolith_role") == "reviewed" for w in walls),
        "glazed street storey": sum("ground_band" in w for w in walls),
    }
    return program


CHANNEL_GLASS = re.compile(r"\bchannel glass\b|\bprofiled glass\b|\bu-(profile|glass)\b|\bglass planks?\b")
GLAZING_STRIP = re.compile(r"\b(glazing|glazed|glass)\b.*\b(strip|band|ribbon)\b|\bribbon\b")


PANEL_SIZE = re.compile(r"panel grid (\d+(?:\.\d+)?) x (\d+(?:\.\d+)?) m")


def measured_panel(reading):
    """A panel size measured on this wall (metres), when its reading records one."""
    for text in _texts(reading):
        match = PANEL_SIZE.search(text.lower())
        if match and all(0.3 <= float(x) <= 6 for x in match.groups()):
            return [float(match[1]), float(match[2])]
    return None


def reviewed_wall(wall, reading, floors, panels, m):
    """Uncounted glazing strips/bands of a reviewed monolith wall become one ribbon each.

    "Narrow" strips keep at most a third of the extent at its named edge: the museum's
    "narrow recessed" base glazing is measured at 0.4 of its wall under the cantilever.

    Blank volumes over recessed glazed bases are read as "lower/middle glazing strip";
    the adapter leaves uncounted openings to the prior, which is blank on a monolith.
    """
    from turkutex.facade_archetypes import MONOLITH_PANEL
    from turkutex.facade_sections import bounds

    if panels:
        size = measured_panel(reading)
        wall["panel_grid"] = m(
            [*size, MONOLITH_PANEL["joint_m"]] if size
            else [MONOLITH_PANEL["width_m"], MONOLITH_PANEL["height_m"], MONOLITH_PANEL["joint_m"]],
            "same-wall measured panel grid" if size else "reference-measured panel joint grid")
    if "render_sections" not in wall:
        return
    from turkutex.facade_archetypes import MONOLITH_CHANNEL_GLASS as CHANNEL

    sections = reading.get("structure", {}).get("facade_observations", {}).get("sections", [])
    for part in wall["render_sections"]:
        source = sections[part["index"]] if part["index"] < len(sections) else {}
        text = " ".join([str(source.get(k, "")) for k in ("kind", "material")]).lower()
        if CHANNEL_GLASS.search(text):
            # Translucent U-profile glass is a light, diffuse cladding plane, not a window: the
            # atlas's dark reflective glass cell would invert its measured milky pigment.
            part.update(kind="blank", material="render", count=1, rows=1,
                        colour=CHANNEL["colour"],
                        panel_grid=[CHANNEL["profile_m"], 1000.0, CHANNEL["joint_m"]])
    used = {s["index"] for s in wall["render_sections"]}
    for index, section in enumerate(reading.get("structure", {}).get("facade_observations", {})
                                    .get("sections", [])):
        text = " ".join([str(section.get("kind", "")), *map(str, section.get("features", []))]).lower()
        if index in used or section.get("belongs_to_facade") is False or not GLAZING_STRIP.search(text):
            continue
        extent = bounds(section.get("position"), max(3, floors or 3))
        if not extent:
            continue
        x, y, w, h = extent
        if "narrow" in text and h > 1 / 3:
            position = str(section.get("position", "")).lower()
            y = y + h - 1 / 3 if re.search(r"upper|top", position) else (
                y if re.search(r"lower|base|ground", position) else y + (h - 1 / 3) / 2)
            h = 1 / 3
        wall["render_sections"].append(dict(index=index, bounds=[x, y, w, h], material="glass",
                                            kind="window", count=None, rows=1, head="flat",
                                            surround="none", rhythm="ribbon"))
    wall["render_sections"].sort(key=lambda s: s["index"])


def _material(wall, name, m):
    for section in ("wall", "base", "trim"):
        wall[section]["material"] = m(name, "public monolith class material")


def blank_wall(wall, colour, material, panels, m):
    from turkutex.facade_archetypes import MONOLITH_BASE_COURSE_M, MONOLITH_GRANITE, MONOLITH_PANEL

    height = wall.get("layout_height_m", wall["height_m"])
    wall["bays"].update(count=0, centers_m=[], pitch_m=wall["width_m"], rhythm="regular")
    wall["windows"]["rectangles_m"] = []
    wall.pop("learned_layout", None)
    _material(wall, material, m)
    wall["wall"]["colour"] = dict(colour)
    # Rendered volumes stand on a granite base course; panel cladding runs to the ground.
    course = 0.1 if panels else min(MONOLITH_BASE_COURSE_M, 0.25 * height)
    wall["split"]["base_height_m"] = m(course, "reference-measured stone base course")
    wall["base"].update(
        colour=dict(colour) if panels else m(MONOLITH_GRANITE, "reference-measured stone base course"),
        material=m(material if panels else "stone", "reference-measured stone base course"),
        plinth_height_m=m(0, "base course is the ground band"),
        shopfront=m(False, "public monolith blank volume"),
        variant=m("plain", "public monolith blank volume"),
    )
    wall["trim"].update(cornice=m(0, "public monolith"), string_courses=m(0, "public monolith"),
                        window_surrounds=m(0, "public monolith"))
    wall["corners"]["quoins"] = m(0, "public monolith")
    wall["balconies"] = m([], "public monolith blank volume")
    if panels:
        wall["panel_grid"] = m([MONOLITH_PANEL["width_m"], MONOLITH_PANEL["height_m"],
                                MONOLITH_PANEL["joint_m"]], "reference-measured panel joint grid")
    wall["monolith_role"] = "blank"


def curtain_wall(wall, description, m, *, glazed_ground=False):
    """Full-height frameless glazing on measured panes above a stone plinth.

    The measured granite plinth stands under the glazing unless the street storey is itself
    glazed (``glazed_ground``) or the wall's own description shows no plinth."""
    from turkutex.facade_archetypes import MONOLITH_CURTAIN, MONOLITH_GRANITE

    width, height = wall["width_m"], wall.get("layout_height_m", wall["height_m"])
    count = max(1, round(width / MONOLITH_CURTAIN["pane_m"]))
    pitch = width / count
    plinth = (0.3 if glazed_ground
              else min(MONOLITH_CURTAIN["plinth_m"], 0.3 * height)
              if description is None or description.get("plinth") else 0.6)
    wall["bays"].update(count=count, pitch_m=pitch, centers_m=[(i + 0.5) * pitch for i in range(count)],
                        rhythm="regular")
    _material(wall, "glass", m)
    source = "reference-measured curtain wall"
    wall["wall"]["colour"] = m(MONOLITH_CURTAIN["mullion"], source)
    wall["split"]["base_height_m"] = m(plinth, source)
    wall["windows"].update(
        size_m=m([pitch * MONOLITH_CURTAIN["glass"], 10.0], source),
        sill_fraction=m(MONOLITH_CURTAIN["sill"], source),
        frame_colour=m(MONOLITH_CURTAIN["mullion"], source),
        divisions=m([1, 1], source),
        curtain_wall=m(True, source),
        rectangles_m=[],
    )
    wall["base"].update(
        colour=m(MONOLITH_GRANITE, source),
        material=m("stone", source),
        plinth_height_m=m(0, "plinth is the ground band"),
        shopfront=m(False, source),
        variant=m("plain", source),
    )
    wall["trim"].update(cornice=m(0, source), string_courses=m(0, source), window_surrounds=m(0, source),
                        colour=m(MONOLITH_CURTAIN["mullion"], source))
    wall["corners"]["quoins"] = m(0, source)
    wall["balconies"] = m([], source)
    wall.pop("learned_layout", None)

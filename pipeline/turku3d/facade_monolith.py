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


def apply(program, key, vertices, observed, description):
    """Re-own every unreviewed wall of a monolith to its own evidence or a blank volume."""
    from turkutex.facade_archetypes import MONOLITH_PANEL, MONOLITH_RENDER
    from turkutex.facade_program import value

    from turku3d.coarse.stored_facades import prepare_walls
    from turku3d.facade_programs import describe, matching_wall

    facts = evidence(key, observed)
    program["monolith"] = facts
    rows = own_descriptions(key)
    prepared = prepare_walls([f for f, _ in rows])
    by_id = {f["metadata"]["facade_id"]: d for f, d in rows}
    m = lambda x, source: value(x, source, 0.6)  # noqa: E731
    # Opaque building descriptions keep their colour on blank volumes; a glass
    # description (the glazed volume) cannot colour the rendered ones.
    opaque = description if description and description.get("material") != "glass" else None
    blank_colour = (value(opaque["wall_colour"], "existing description", 1) if opaque
                    else m(MONOLITH_PANEL["colour"] if facts["panels"] else MONOLITH_RENDER,
                           "reference-measured monolith pigment"))
    blank_material = ("concrete" if facts["panels"] else "plaster") if not opaque else opaque["material"]
    # A joint grid belongs to panel cladding, never to brick or timber volumes.
    panels = facts["panels"] and blank_material not in ("brick", "wood")
    for index, wall in enumerate(program["walls"]):
        points = np.asarray(vertices[index * 4: index * 4 + 4])
        reading = matching_wall(points, observed)
        if reading:
            # The reviewed reading owns this wall; add only what the class knows about it.
            reviewed_wall(wall, reading, program["storeys"]["value"], panels, m)
            continue
        facade = _own_wall_description(points, prepared) if prepared else None
        own = by_id.get(facade["metadata"]["facade_id"]) if facade else None
        if own and curtain(own):
            curtain_wall(wall, own, m)
        elif own and own.get("window_columns"):
            single = {**program, "walls": [wall]}
            describe(single, own, facade["metadata"]["width_m"])
        else:
            blank_wall(wall, blank_colour, blank_material, panels, m)
            continue
        wall["monolith_role"] = "curtain" if own and curtain(own) else "own description"
    program["monolith"]["walls"] = {
        role: sum(w.get("monolith_role") == role for w in program["walls"])
        for role in ("blank", "curtain", "own description")
    }
    return program


CHANNEL_GLASS = re.compile(r"\bchannel glass\b|\bprofiled glass\b|\bu-(profile|glass)\b|\bglass planks?\b")
GLAZING_STRIP = re.compile(r"\b(glazing|glazed|glass)\b.*\b(strip|band|ribbon)\b|\bribbon\b")


def reviewed_wall(wall, reading, floors, panels, m):
    """Uncounted glazing strips/bands of a reviewed monolith wall become one ribbon each.

    Blank volumes over recessed glazed bases are read as "lower/middle glazing strip";
    the adapter leaves uncounted openings to the prior, which is blank on a monolith.
    """
    from turkutex.facade_archetypes import MONOLITH_PANEL
    from turkutex.facade_sections import bounds

    if panels:
        wall["panel_grid"] = m([MONOLITH_PANEL["width_m"], MONOLITH_PANEL["height_m"],
                                MONOLITH_PANEL["joint_m"]], "reference-measured panel joint grid")
    if "render_sections" not in wall:
        return
    from turkutex.facade_archetypes import MONOLITH_CHANNEL_GLASS as CHANNEL

    sections = reading.get("structure", {}).get("facade_observations", {}).get("sections", [])
    for part in wall["render_sections"]:
        source = sections[part["index"]] if part["index"] < len(sections) else {}
        text = " ".join([str(source.get(k, "")) for k in ("kind", "material")]).lower()
        if CHANNEL_GLASS.search(text):
            # Translucent U-profile glass is an opaque-looking cladding plane, not a window.
            part.update(kind="blank", material="glass", count=1, rows=1,
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
        if "narrow" in text and h > 0.25:
            position = str(section.get("position", "")).lower()
            y = y + h - 0.25 if re.search(r"upper|top", position) else (
                y if re.search(r"lower|base|ground", position) else y + (h - 0.25) / 2)
            h = 0.25
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


def curtain_wall(wall, description, m):
    """Full-height frameless glazing on measured panes above a stone plinth."""
    from turkutex.facade_archetypes import MONOLITH_CURTAIN, MONOLITH_GRANITE

    width, height = wall["width_m"], wall.get("layout_height_m", wall["height_m"])
    count = max(1, round(width / MONOLITH_CURTAIN["pane_m"]))
    pitch = width / count
    plinth = min(MONOLITH_CURTAIN["plinth_m"], 0.3 * height) if description.get("plinth") else 0.6
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

"""Field authority and wall-local appearance priors for generated facades.

Support is an evidence score, not a probability. Scores below 0.6 are retained
in the source reading but cannot erase an existing field. Baked maps are not
inputs to these priors. Photo colours remain explicitly uncalibrated estimates.
"""

import copy
import json
from functools import lru_cache

from turku3d import config as C

MIN_SUPPORT = 0.6
MATERIAL_ALIASES = {
    "render": "plaster",
    "concrete_panel": "concrete",
    "granite": "stone",
    "rusticated-stone": "stone",
    "rusticated_stone": "stone",
}


def material(value):
    return MATERIAL_ALIASES.get(value, value)


def supported(value):
    return (
        isinstance(value, dict)
        and value.get("value") is not None
        and value.get("support", 0) >= MIN_SUPPORT
    )


def path():
    return C.REPO_ROOT / "textures/library/facade-appearance.json"


@lru_cache(maxsize=2)
def _read(filename, stamp):
    return json.loads(filename.read_text())


def evidence(key):
    from turku3d.facade_assets import aliases

    filename = path()
    rows = _read(filename, filename.stat().st_mtime_ns) if filename.is_file() else {}
    return next((rows[k] for k in aliases(key) if k in rows), {})


def overlay(target, source, support=0):
    """Merge supported leaves, including explicit false/zero/empty observations."""
    support = source.get("support", support)
    for key, val in source.items():
        if key in ("support", "source"):
            continue
        if isinstance(val, dict) and "value" in val:
            if supported(val):
                target[key] = copy.deepcopy(val)
        elif isinstance(val, dict):
            overlay(target.setdefault(key, {}), val, support)
        elif val is not None and support >= MIN_SUPPORT:
            target[key] = copy.deepcopy(val)


def reading_fields(own):
    """Attach the actual support to legacy arrays instead of trusting a whole wall."""
    result = {
        k: copy.deepcopy(own[k])
        for k in (
            "wall",
            "base",
            "windows",
            "doors",
            "trim",
            "corners",
            "bays",
            "balconies",
            "split",
            "style_era",
            "bay_windows",
        )
        if k in own
    }
    form = own.get("structure", {})
    rows = form.get("floors", [])
    if rows:
        layout = max(min(r["confidence"]["bay_count"], r["confidence"]["bay_kinds"]) for r in rows)
    else:
        layout = own.get("bays", {}).get("support", 0)
    for section in ("windows", "base", "doors"):
        result.setdefault(section, {})["support"] = layout
    if "candidates" in result["doors"]:
        result["doors"]["candidates"] = [
            d for d in result["doors"]["candidates"] if d.get("support", layout) >= MIN_SUPPORT
        ]
    result.setdefault("bays", {})["support"] = min(result.get("bays", {}).get("support", 0), layout)
    if rows:
        from turkutex.facade_program import value

        supported_rows = [
            r
            for r in rows
            if min(r["confidence"]["bay_count"], r["confidence"]["bay_kinds"]) >= MIN_SUPPORT
        ]
        if supported_rows:
            result["bays"]["count"] = max(r["bay_count"] or 0 for r in supported_rows)
        if any(r["role"] == "base" and "shop" in r["bays"] for r in supported_rows):
            result["base"].update(
                shopfront=value(True, "observed shop bays", layout),
                variant=value("shops", "observed shop bays", layout),
            )
    # Placement/section metadata is passed separately, never as appearance authority.
    return result


def apply_prior(wall, archetype, attrs, own, record):
    from turkutex.facade_program import value

    v = lambda x, source: value(x, source, 0.4)  # noqa: E731
    # Register/owner material outranks learned donor material. Never promote a
    # photographed wall material to building attributes.
    if attrs.get("material_class"):
        wall["wall"]["material"] = v(
            material(attrs["material_class"]), "building cladding evidence"
        )
    historic = archetype == "neo_renaissance"
    if historic and own and not supported(wall["trim"].get("cornice")):
        wall["trim"].update(
            ornament_level=v("high", "historicist style prior"),
            cornice=v(0.4, "historicist style prior"),
            window_surrounds=v(0.16, "historicist style prior"),
        )
        wall["windows"]["surround_shape"] = v("pediment", "historicist style prior")
    local = record.get("walls", {}).get(own["wall_id"], {}) if own else {}
    # Same-wall reviewed patches outrank building-wide descriptions and donor
    # palettes, whose inherited confidence does not make them measured pigment.
    # Explicit local appearance records remain the highest-priority prior;
    # calibrated observations are applied by the subsequent reading overlay.
    for section, key in (("wall", "colour"), ("trim", "colour")):
        item = local.get(section, {}).get(key)
        if not supported(item) and own:
            photographed = own.get(section, {}).get(key)
            if supported(photographed):
                item = {
                    **photographed,
                    "source": photographed.get("source", "photo")
                    + "; uncalibrated same-wall appearance prior",
                }
        if supported(item):
            wall[section][key] = copy.deepcopy(item)
            if section == "wall":
                wall["base"]["colour"] = copy.deepcopy(item)
    for key, item in local.get("details", {}).items():
        if supported(item):
            wall.setdefault("appearance_details", {})[key] = copy.deepcopy(item)
    if historic and own:
        wall.setdefault("appearance_details", {}).setdefault(
            "pilasters", v(True, "historicist style prior")
        )
        wall["appearance_details"].setdefault("balustrades", v(True, "historicist style prior"))
    return local


def section_material_support(own, section):
    """Support of a reading section's material: its own score, the reading's ``material``
    score, or a score the reading keyed by that material's name (``stone: 0.99``)."""
    from turkutex.facade_sections import MATERIAL_WORDS

    obs = (own or {}).get("structure", {}).get("facade_observations", {})
    sources = obs.get("sections", [])
    source = sources[section["index"]] if section["index"] < len(sources) else {}
    if "material_support" in source:
        return source["material_support"]
    family = section.get("material")
    names = {"material", family, material(family), *MATERIAL_WORDS.get(family, ())} - {None}
    scores = []
    for confidence in (source.get("confidence", {}), obs.get("confidence", {})):
        if isinstance(confidence, dict):
            scores += [v for k, v in confidence.items()
                       if k in names and isinstance(v, (int, float))]
        elif isinstance(confidence, (int, float)):
            scores.append(confidence)
    return max(scores, default=0)


def reading_materials(observed):
    """Supported cladding materials of reviewed walls: the wall field, else a material
    section covering most of the wall (a stone ``wall and top`` reading)."""
    from turkutex.facade_sections import sections

    result = []
    for w in (observed or {}).get("walls", []):
        if w.get("observation", {}).get("needs_review", True):
            continue
        item = w.get("wall", {}).get("material")
        if supported(item):
            result.append(item)
            continue
        form = w.get("structure", {})
        if not form.get("facade_observations"):
            continue
        for section in sections(form, (observed.get("storeys") or {}).get("value"))[0]:
            x, y, width, height = section["bounds"]
            support = section_material_support(w, section)
            if section["material"] and width * height >= 0.5 and support >= MIN_SUPPORT:
                result.append(dict(value=section["material"], support=support))
    return result


def building_material(attrs, record, observed, description=None):
    """Owner evidence, unopposed reviewed wall readings, register/OSM cladding, then the
    AI description's material.

    Register and OSM cladding are source data and outrank a description's material
    guess (the description itself is kept). An inferred family supplies unobserved
    zones only. Confident wall/zone readings still override it; colours and layouts
    are never copied between walls.
    """
    explicit = record.get("building", {}).get("material_class")
    if supported(explicit):
        return {**explicit, "value": material(explicit["value"])}
    readings = reading_materials(observed)
    families = {material(r["value"]) for r in readings}
    if len(families) == 1:
        return dict(
            value=families.pop(),
            support=min(r["support"] for r in readings),
            source="unopposed reviewed facade material; unobserved-zone prior",
        )
    if attrs.get("material_class"):
        return dict(
            value=material(attrs["material_class"]), support=0.4, source="register cladding"
        )
    if description:
        return dict(
            value=material(description["material"]),
            support=0.4,
            source="existing generated description",
        )
    return None


def finish_material(wall, family, own, record, description, key):
    """Keep cladding, base, trim and partial-section materials in one family by default."""
    import hashlib

    from turkutex.facade_archetypes import PALETTES
    from turkutex.facade_program import value

    local = record.get("walls", {}).get((own or {}).get("wall_id"), {})
    chosen = family or wall["wall"]["material"]
    own_material = local.get("wall", {}).get("material")
    if not supported(own_material):
        own_material = (own or {}).get("wall", {}).get("material")
    if supported(own_material):
        chosen = own_material
    chosen = {**chosen, "value": material(chosen["value"])}
    wall["wall"]["material"] = copy.deepcopy(chosen)
    for section in ("base", "trim"):
        item = local.get(section, {}).get("material")
        if not supported(item):
            item = (own or {}).get(section, {}).get("material")
        wall[section]["material"] = copy.deepcopy(item if supported(item) else chosen)
    # A material decision must also discard incompatible donor/era pigment. Stone
    # has a neutral mineral palette; lighter stone details derive from that wall.
    if chosen["value"] == "stone":
        palette = PALETTES["stone"]
        colour = palette[
            int.from_bytes(hashlib.sha256(str(key).encode()).digest()[:4]) % len(palette)
        ]
        for section in ("wall", "base", "trim"):
            observed_colour = (own or {}).get(section, {}).get("colour")
            same_wall = local.get(section, {}).get("colour")
            if supported(same_wall) or supported(observed_colour):
                continue
            if description and material(description["material"]) == "stone":
                continue
            if section == "wall":
                wall[section]["colour"] = value(colour, "stone-family pigment prior", 0.4)
            else:
                base = wall["wall"]["colour"]["value"]
                tint = (
                    base
                    if section == "base"
                    else "#"
                    + "".join(
                        f"{round(int(base[i : i + 2], 16) * 0.68 + 255 * 0.32):02x}"
                        for i in (1, 3, 5)
                    )
                )
                wall[section]["colour"] = value(tint, "stone-family pigment prior", 0.4)
    base_material = wall["base"]["material"]
    if supported(base_material) and material(base_material["value"]) != chosen["value"]:
        wall.setdefault("material_bands", []).append(
            dict(
                bottom_m=0,
                height_m=wall["split"]["base_height_m"]["value"],
                material=base_material["value"],
                support=base_material["support"],
            )
        )
    for section in wall.get("render_sections", []):
        support = section_material_support(own, section)
        section["material_support"] = support
        section["material"] = (
            material(section["material"])
            if support >= MIN_SUPPORT and section["material"]
            else chosen["value"]
        )

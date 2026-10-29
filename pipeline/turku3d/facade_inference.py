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
MATERIAL_ALIASES = {"render": "plaster", "concrete_panel": "concrete",
                    "granite": "stone", "rusticated-stone": "stone", "rusticated_stone": "stone"}


def material(value):
    return MATERIAL_ALIASES.get(value, value)


def supported(value):
    return isinstance(value, dict) and value.get("value") is not None and value.get("support", 0) >= MIN_SUPPORT


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
    result = {k: copy.deepcopy(own[k]) for k in ("wall", "base", "windows", "doors", "trim", "corners", "bays", "balconies", "split", "style_era", "bay_windows") if k in own}
    form = own.get("structure", {})
    rows = form.get("floors", [])
    if rows:
        layout = max(min(r["confidence"]["bay_count"], r["confidence"]["bay_kinds"]) for r in rows)
    else:
        layout = own.get("bays", {}).get("support", 0)
    for section in ("windows", "base", "doors"):
        result.setdefault(section, {})["support"] = layout
    if "candidates" in result["doors"]:
        result["doors"]["candidates"] = [d for d in result["doors"]["candidates"] if d.get("support", layout) >= MIN_SUPPORT]
    result.setdefault("bays", {})["support"] = min(result.get("bays", {}).get("support", 0), layout)
    if rows:
        from turkutex.facade_program import value
        supported_rows = [r for r in rows if min(r["confidence"]["bay_count"], r["confidence"]["bay_kinds"]) >= MIN_SUPPORT]
        if supported_rows:
            result["bays"]["count"] = max(r["bay_count"] or 0 for r in supported_rows)
        if any(r["role"] == "base" and "shop" in r["bays"] for r in supported_rows):
            result["base"].update(shopfront=value(True, "observed shop bays", layout),
                                  variant=value("shops", "observed shop bays", layout))
    # Placement/section metadata is passed separately, never as appearance authority.
    return result


def apply_prior(wall, archetype, attrs, own, record):
    from turkutex.facade_program import value

    v = lambda x, source: value(x, source, 0.4)  # noqa: E731
    # Register/owner material outranks learned donor material. Never promote a
    # photographed wall material to building attributes.
    if attrs.get("material_class") and (record.get("building", {}).get("material_class") or
                                       not supported(wall["wall"].get("material"))):
        wall["wall"]["material"] = v(material(attrs["material_class"]), "building cladding evidence")
    historic = archetype == "neo_renaissance"
    if historic and own and not supported(wall["trim"].get("cornice")):
        wall["trim"].update(
            ornament_level=v("high", "historicist style prior"),
            cornice=v(0.4, "historicist style prior"),
            window_surrounds=v(0.16, "historicist style prior"),
        )
        wall["windows"]["surround_shape"] = v("pediment", "historicist style prior")
    local = record.get("walls", {}).get(own["wall_id"], {}) if own else {}
    # These are low-resolution pigment estimates from this exact wall's reviewed
    # patches, used only when that field has no calibrated observation.
    for section, key in (("wall", "colour"), ("trim", "colour")):
        item = local.get(section, {}).get(key)
        if not supported(item) and own and not supported(wall[section].get(key)):
            photographed = own.get(section, {}).get(key)
            if supported(photographed):
                item = {**photographed, "source": photographed.get("source", "photo") +
                        "; uncalibrated same-wall appearance prior"}
        if supported(item):
            wall[section][key] = copy.deepcopy(item)
            if section == "wall":
                wall["base"]["colour"] = copy.deepcopy(item)
    for key, item in local.get("details", {}).items():
        if supported(item):
            wall.setdefault("appearance_details", {})[key] = copy.deepcopy(item)
    if historic and own:
        wall.setdefault("appearance_details", {}).setdefault("pilasters", v(True, "historicist style prior"))
        wall["appearance_details"].setdefault("balustrades", v(True, "historicist style prior"))
    return local

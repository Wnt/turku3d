"""What a description leaves out: defaults chosen by the building's style, era and register facts.

`resolve_*` return complete specifications, so the geometry code never asks whether a field exists. A prior is a
statement about buildings of a kind in Turku, never about one building.
"""

import colorsys
import copy
import math
import zlib

from .description import expand_storeys

WOODEN = {"wooden_house", "wooden_villa", "outbuilding"}
OLD_MASONRY = {"stone_apartment", "classicist_apartment", "public", "church"}

GROUPS = {
    "wooden": {
        "wall": {
            "family": "weatherboard",
            "colour": "#c9b78f",
            "orientation": "horizontal",
            "profile": "bevel",
            "scale_m": 0.13,
        },
        "plinth": {
            "height_m": 0.7,
            "projection_m": 0.04,
            "material": {"family": "rubble", "colour": "#7f7a72", "colour2": "#9c968b"},
        },
        "ground_h": 3.5,
        "upper_h": 3.0,
        "sill": 0.95,
        "pitch": 2.5,
        "window": {
            "w_m": 1.05,
            "h_m": 1.7,
            "recess_m": 0.03,
            "lights": {"pattern": "t_frame", "transom_at": 0.68},
            "surround": {
                "kind": "eared",
                "width_m": 0.13,
                "projection_m": 0.03,
                "colour": "#efece4",
            },
            "sill": {"kind": "wood", "projection_m": 0.05, "height_m": 0.05, "colour": "#efece4"},
        },
        "trim": "#efece4",
        "roof": {
            "material": {
                "family": "sheet_metal",
                "colour": "#4c4e51",
                "profile": "standing_seam",
                "scale_m": 0.55,
            },
            "eaves": {
                "overhang_m": 0.5,
                "fascia_m": 0.16,
                "soffit": "open_rafters",
                "gutter": True,
            },
            "verge": {"overhang_m": 0.4, "bargeboard": "plain"},
        },
    },
    "old_masonry": {
        "wall": {"family": "plaster", "colour": "#cdbfa6", "finish": "fine"},
        "plinth": {
            "height_m": 0.9,
            "projection_m": 0.05,
            "material": {
                "family": "granite",
                "colour": "#8f8a84",
                "finish": "ashlar",
                "scale_m": 0.45,
            },
        },
        "ground_h": 3.8,
        "upper_h": 3.3,
        "sill": 0.95,
        "pitch": 3.1,
        "window": {
            "w_m": 1.15,
            "h_m": 1.7,
            "recess_m": 0.14,
            "lights": {"pattern": "t_frame", "transom_at": 0.7},
            "surround": {"kind": "none"},
            "sill": {"kind": "plaster", "projection_m": 0.05, "height_m": 0.07},
        },
        "trim": "#e3dccb",
        "roof": {
            "material": {
                "family": "sheet_metal",
                "colour": "#4a4a48",
                "profile": "standing_seam",
                "scale_m": 0.6,
            },
            "eaves": {"overhang_m": 0.35, "fascia_m": 0.2, "soffit": "cornice", "gutter": True},
            "verge": {"overhang_m": 0.15, "bargeboard": "none"},
        },
    },
    "modern": {
        "wall": {"family": "plaster", "colour": "#d5cfc2", "finish": "fine"},
        "plinth": {
            "height_m": 0.5,
            "projection_m": 0.0,
            "material": {"family": "concrete", "colour": "#76726c", "finish": "cast"},
        },
        "ground_h": 3.0,
        "upper_h": 2.9,
        "sill": 0.9,
        "pitch": 3.3,
        "window": {
            "w_m": 1.5,
            "h_m": 1.4,
            "recess_m": 0.1,
            "lights": {"pattern": "side_vent", "vent": "left", "vent_share": 0.28},
            "surround": {"kind": "none"},
            "sill": {
                "kind": "sheet_metal",
                "projection_m": 0.04,
                "height_m": 0.03,
                "colour": "#8a8d8f",
            },
        },
        "trim": "#e8e6e0",
        "roof": {
            "material": {"family": "felt", "colour": "#3f3f41", "profile": "plain"},
            "eaves": {"overhang_m": 0.2, "fascia_m": 0.25, "soffit": "boxed", "gutter": False},
            "verge": {"overhang_m": 0.1, "bargeboard": "none"},
        },
    },
}

OPENING_DEFAULTS = {
    "window": {
        "head": "flat",
        "frame": {"colour": "#ece8de", "width_m": 0.07},
        "glass": {"reflectivity": 0.6, "curtains": 0.6, "blinds": 0.2, "interior": "dwelling"},
    },
    "door": {
        "w_m": 1.1,
        "h_m": 2.3,
        "head": "flat",
        "recess_m": 0.25,
        "frame": {"colour": "#5a4334", "width_m": 0.08},
        "leaf": {
            "leaves": 1,
            "style": "panelled",
            "colour": "#5a4334",
            "fanlight": "none",
            "steps": 0,
        },
        "glass": {"reflectivity": 0.6, "curtains": 0.0, "blinds": 0.0, "interior": "stair"},
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
    "gateway": {
        "w_m": 2.8,
        "h_m": 3.2,
        "head": "flat",
        "recess_m": 0.3,
        "frame": {"colour": "#2e2e2e", "width_m": 0.08},
        "leaf": {"leaves": 2, "style": "none", "colour": "#2e2e2e"},
        "passage": {"depth": "through", "depth_m": 3.5},
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
    "shopfront": {
        "w_m": 3.2,
        "h_m": 2.8,
        "head": "flat",
        "recess_m": 0.1,
        "frame": {"colour": "#3a3a3a", "width_m": 0.07},
        "shopfront": {
            "panes": 2,
            "stallriser_m": 0.45,
            "transom_h_m": 0.0,
            "door": "none",
            "door_recessed": False,
        },
        "glass": {"reflectivity": 0.6, "curtains": 0.0, "blinds": 0.0, "interior": "shop"},
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
    "garage": {
        "w_m": 2.6,
        "h_m": 2.3,
        "head": "flat",
        "recess_m": 0.15,
        "frame": {"colour": "#6c6c6a", "width_m": 0.06},
        "leaf": {"leaves": 1, "style": "flush", "colour": "#8c8c88"},
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
    "blind": {
        "head": "flat",
        "recess_m": 0.08,
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
    "vent": {
        "w_m": 0.5,
        "h_m": 0.5,
        "head": "flat",
        "recess_m": 0.05,
        "frame": {"colour": "#ece8de", "width_m": 0.05},
        "surround": {"kind": "none"},
        "sill": {"kind": "none"},
    },
}


def style_type(doc: dict) -> str:
    """The description's style, or one inferred from the register when nobody gave it."""
    given = doc.get("style", {}).get("type")
    if given:
        return given
    register = doc.get("register", {})
    year, storeys, height = (
        register.get("year"),
        register.get("storeys", 0),
        register.get("height_m", 0.0),
    )
    if register.get("use") == "outbuilding":
        return "outbuilding"
    if register.get("material") == "puu" and storeys <= 2:
        return "wooden_house"
    if year is None:
        return "detached_house" if height and height < 8 else "modern_apartment"
    if year < 1920:
        return "wooden_house" if storeys <= 2 and (not height or height < 10) else "stone_apartment"
    if year < 1940:
        if storeys <= 2 and (not height or height < 9):
            return "wooden_house"
        return "classicist_apartment" if year < 1932 else "functionalist_apartment"
    if year < 1980:
        return "tower_block" if storeys >= 8 else "slab_block"
    return "modern_apartment"


# Wall material and colours by the register's facade material (julkisivumateriaali): what a street of such
# buildings looks like in Turku, one colour per building picked by its id. sRGB, as in overcast daylight.
REGISTER_WALLS = {
    "puu": (
        {
            "family": "weatherboard",
            "orientation": "horizontal",
            "profile": "bevel",
            "scale_m": 0.13,
        },
        ["#d2bd84", "#c98b6d", "#b3bca6", "#d6d1c0", "#a3b1bf", "#e3dcc2", "#bfa58a"],
    ),
    "tiili": (
        {"family": "brick", "bond": "stretcher", "colour2": "#b9b2a5"},
        ["#9c5a45", "#a8694f", "#8c4e3e", "#b99673"],
    ),
    "betoni": (
        {"family": "concrete", "finish": "cast"},
        ["#c4c2bc", "#cfc9bc", "#b8b8b6", "#d6d2c6"],
    ),
    "kivi": (
        {"family": "plaster", "finish": "fine"},
        ["#d3c4a2", "#cfb69e", "#ccc9bc", "#c4a58f", "#bfc2b5"],
    ),
    "metallilevy": (
        {"family": "sheet_metal", "profile": "standing_seam", "scale_m": 0.4},
        ["#9aa0a6", "#7c848c", "#b9bcbd"],
    ),
    "lasi": ({"family": "metal", "finish": "gloss"}, ["#50606b", "#5c6a70"]),
}
OLD_PLASTER = ["#d3c4a2", "#cfb69e", "#ccc9bc", "#c4a58f", "#bfc2b5", "#d9c9a8", "#c9b9a0"]
NEW_PLASTER = ["#d3d0c7", "#ccc8b8", "#c6c0b1", "#dad6ca"]


def register_wall(doc: dict) -> dict:
    """The wall material an unobserved building gets: the register's facade material in one of its usual colours."""
    register = doc.get("register", {})
    pick = zlib.crc32(doc.get("id", "").encode())
    material, colours = REGISTER_WALLS.get(register.get("material", ""), (None, None))
    if material is None:
        base = group(doc)["wall"]
        if base["family"] != "plaster":
            return dict(base)
        material = {"family": "plaster", "finish": "fine"}
        colours = OLD_PLASTER if register.get("year", 1960) < 1940 else NEW_PLASTER
    return {**material, "colour": colours[pick % len(colours)]}


def group(doc: dict) -> dict:
    kind = style_type(doc)
    return GROUPS[
        "wooden" if kind in WOODEN else "old_masonry" if kind in OLD_MASONRY else "modern"
    ]


# The foot of a wooden building that is not a house of the old quarters: cast concrete, low.
CAST_PLINTH = {
    "height_m": 0.45,
    "projection_m": 0.0,
    "material": {"family": "concrete", "colour": "#8f8d88", "finish": "cast"},
}


def old_quarter(doc: dict) -> bool:
    """A wooden house of the old wooden quarters (dated before 1940, or undated): rubble plinth, one tall storey
    under a knee wall. Wooden houses built since stand on a low cast plinth with ordinary storeys."""
    year = doc.get("register", {}).get("year")
    return style_type(doc) in ("wooden_house", "wooden_villa") and (year is None or year < 1940)


def default_plinth(doc: dict) -> dict:
    kind = style_type(doc)
    if kind == "outbuilding":
        return {**CAST_PLINTH, "height_m": 0.2}
    if kind in WOODEN and not old_quarter(doc):
        return CAST_PLINTH
    return group(doc)["plinth"]


def merged(base: dict, over: dict) -> dict:
    """`over` laid on `base`, object fields merged one level at a time; lists and scalars replace."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merged(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def resolve_opening(doc: dict, name: str) -> dict:
    """An opening type with every field the geometry reads."""
    given = doc.get("opening_types", {}).get(name, {"kind": "window"})
    kind = given.get("kind", "window")
    base = copy.deepcopy(OPENING_DEFAULTS.get(kind, OPENING_DEFAULTS["window"]))
    if kind in ("window", "blind"):
        base = merged(base, group(doc)["window"])
        if kind == "blind":
            base.pop("lights", None)
    out = merged(base, given)
    out["kind"] = kind
    out.setdefault("w_m", 1.1)
    out.setdefault("h_m", 1.6)
    out.setdefault("recess_m", 0.1)
    out.setdefault("surround", {"kind": "none"})
    out.setdefault("sill", {"kind": "none"})
    out.setdefault("pediment", {"kind": "none"})
    return out


def default_wall(doc: dict) -> dict:
    """The automatic wall of a style: a regular grid sized from the register's storey count."""
    g = group(doc)
    storeys = max(1, int(doc.get("register", {}).get("storeys", 1)))
    wall = {
        "material": g["wall"],
        "plinth": copy.deepcopy(default_plinth(doc)),
        "bays": {"pitch_m": g["pitch"]},
        "storeys": [
            {"kind": "ground", "height_m": g["ground_h"], "sill_m": g["sill"], "window": "@default"}
        ],
    }
    if storeys > 1:
        wall["storeys"].append(
            {
                "kind": "upper",
                "count": storeys - 1,
                "height_m": g["upper_h"],
                "sill_m": g["sill"],
                "window": "@default",
            }
        )
    if style_type(doc) in WOODEN:
        wall["verticals"] = [
            {
                "kind": "corner_board",
                "where": "ends",
                "width_m": 0.2,
                "projection_m": 0.03,
                "colour": g["trim"],
            }
        ]
    return wall


ATTIC_WINDOW = {
    "kind": "window",
    "w_m": 0.55,
    "h_m": 0.55,
    "lights": {"pattern": "single"},
    "surround": {"kind": "flat", "width_m": 0.1, "projection_m": 0.03, "colour": "#efece4"},
    "sill": {"kind": "none"},
}


def automatic_openings(doc: dict) -> dict:
    """Opening types of the automatic walls: what the windows of an unobserved building of the style look like."""
    g = group(doc)
    out = {"@attic": ATTIC_WINDOW}
    if g is GROUPS["old_masonry"]:
        out["@default"] = {
            "kind": "window",
            "surround": {
                "kind": "flat",
                "width_m": 0.12,
                "projection_m": 0.03,
                "colour": g["trim"],
            },
        }
    elif old_quarter(doc):
        out["@default"] = {"kind": "window", "h_m": 1.9}
    steps = min(5, round(default_plinth(doc)["height_m"] / 0.17))
    out["@door"] = {
        "kind": "door",
        "w_m": 1.2,
        "h_m": 2.3,
        "leaf": {"leaves": 1, "style": "half_glazed", "fanlight": "rect", "steps": steps},
    }
    return out


def fitted_wall(doc: dict, wall: dict, height: float, entrance_width: float = 0.0) -> dict:
    """An automatic wall made to fit the wall it stands on.

    The register's storey count is kept when it gives believable storeys; otherwise the storeys are counted from
    the height. A low wooden house is one tall storey under a knee wall with small attic windows on a high plinth,
    which is what the wooden quarters of Turku are made of.
    """
    out = copy.deepcopy(wall)
    if "storeys" in doc.get("wall_defaults", {}):
        # Somebody described the building's usual wall: a lower wing keeps as many of its storeys as fit.
        room = height - out.get("ground_floor_m", out.get("plinth", {}).get("height_m", 0.0))
        storeys = expand_storeys(out.get("storeys", []))
        while storeys and sum(s.get("height_m", 3.0) for s in storeys) > room * 1.3:
            storeys.pop()
        out["storeys"] = storeys
        return out
    g = group(doc)
    plinth = out.get("plinth", {}).get("height_m", 0.0)
    available = height - plinth
    if available < 2.3:
        out["storeys"] = []
        return out
    sill = g["sill"]
    if (old_quarter(doc) or style_type(doc) == "outbuilding") and 4.9 <= height < 7.4:
        attic = 1.2
        main = min(4.2, available - attic)
        rest = available - attic - main
        if rest > 0 and "plinth" in out:
            out["plinth"]["height_m"] = round(plinth + min(rest, 1.0), 2)
            rest -= min(rest, 1.0)
        out["storeys"] = [
            {
                "kind": "ground",
                "height_m": round(main, 2),
                "sill_m": round(min(1.25, max(sill, main - 2.75)), 2),
                "window": "@default",
            },
            {
                "kind": "attic",
                "height_m": round(attic + rest, 2),
                "sill_m": 0.3,
                "window": "@attic",
            },
        ]
        return out
    count = max(1, int(doc.get("register", {}).get("storeys", 1)))
    if not 2.6 <= available / count <= 4.4:
        count = max(1, round(available / g["upper_h"]))
    each = available / count
    ground = min(max(each, g["ground_h"]), each * 1.2) if count > 1 else each
    upper = (available - ground) / (count - 1) if count > 1 else 0.0
    out["storeys"] = [
        {"kind": "ground", "height_m": round(ground, 2), "sill_m": sill, "window": "@default"}
    ]
    if entrance_width > 6.0:
        # The street door of a block of flats: in the bay nearest the middle of the building's longest wall.
        pitch = out.get("bays", {}).get("pitch_m", g["pitch"])
        bays = max(0, int((entrance_width - 2.4) / pitch) + 1)
        out["storeys"][0]["openings"] = [
            {"type": "@door", "u_m": round(entrance_width / 2 + (0 if bays % 2 else pitch / 2), 2)}
        ]
    if count > 1:
        out["storeys"].append(
            {
                "kind": "upper",
                "count": count - 1,
                "height_m": round(upper, 3),
                "sill_m": sill,
                "window": "@default",
            }
        )
        if g is GROUPS["old_masonry"] and "bands" not in out:
            out["bands"] = [
                {
                    "kind": "string_course",
                    "v_m": round(plinth + ground - 0.1, 2),
                    "height_m": 0.2,
                    "projection_m": 0.06,
                    "profile": "stepped",
                    "colour": g["trim"],
                }
            ]
    return out


def resolve_wall(doc: dict, wall: dict | None) -> dict:
    """A described wall (or none) completed with `like`, `wall_defaults` and the style's automatic wall."""
    base = merged(default_wall(doc), doc.get("wall_defaults", {}))
    if wall is None:
        return base
    chain = [wall]
    by_id = {w.get("id"): w for w in doc.get("walls", [])}
    while "like" in chain[-1] and chain[-1]["like"] in by_id and len(chain) < 8:
        chain.append(by_id[chain[-1]["like"]])
    out = {}
    for layer in reversed(chain):
        for key, value in layer.items():
            if key not in ("like", "id", "frame") or layer is wall:
                out[key] = copy.deepcopy(value)
    for key in ("material", "plinth"):
        if key not in out:
            out[key] = base[key]
        elif key == "plinth":
            out[key] = merged(base["plinth"], out[key])
    if "storeys" not in out:
        out["storeys"] = base["storeys"]
        out.setdefault("bays", base["bays"])
    out.setdefault("bays", base["bays"])
    return out


def raised_wall(wall: dict, described: bool) -> dict:
    """The wall of a facade that starts above a roof: no plinth; blank unless somebody described it."""
    out = {k: v for k, v in wall.items() if k not in ("plinth", "ground_floor_m")}
    if not described:
        out["storeys"], out["bays"] = [], []
        out.pop("verticals", None)
    return out


def roof_pitch_deg(doc: dict) -> float:
    """The area-weighted slope of the shell's roof surfaces."""
    total = slope = 0.0
    for surface in doc.get("shell", {}).get("surfaces", []):
        if surface.get("kind") != "roof":
            continue
        ring = surface["rings"][0]
        nx = ny = nz = 0.0
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        area = math.sqrt(nx * nx + ny * ny + nz * nz)
        if area > 1e-6:
            total += area
            slope += area * math.degrees(math.acos(min(1.0, abs(nz) / area)))
    return slope / total if total else 0.0


def measured_roof(doc: dict) -> dict | None:
    """The roofing the orthophoto shows on a building nobody has looked at. The photo is taken in full sun, where
    roofing reads paler and greyer than its paint: the hue is kept, the colour is deepened to a paint or tile colour.
    A red pitched roof is clay tile on a small house from 1940 on and painted sheet metal on older and bigger
    buildings, a green one painted sheet metal; any other roof keeps the roofing of its style in the measured grey.
    Leaf green is tree crowns: such a roof keeps the colour of its style."""
    colour = doc.get("register", {}).get("roof_colour")
    if not colour:
        return None
    hue, saturation, value = colorsys.rgb_to_hsv(
        *(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    )
    reddish = (hue > 345 / 360 or hue < 30 / 360) and saturation >= 0.2
    if 60 / 360 < hue < 110 / 360 and saturation >= 0.15:
        return None  # leaf green: tree crowns over the roof, not roofing
    greenish = 110 / 360 <= hue < 170 / 360 and saturation >= 0.12
    if reddish or hue > 345 / 360 or hue < 40 / 360:
        saturation, value = min(0.7, saturation * 2.0), min(0.62, max(0.35, value * 0.8))
    elif greenish:
        saturation, value = min(0.5, saturation * 1.6), min(0.55, max(0.3, value * 0.8))
    else:
        saturation, value = min(0.1, saturation), min(0.6, max(0.18, value * 0.75))
    red, green, blue = (round(v * 255) for v in colorsys.hsv_to_rgb(hue, saturation, value))
    colour = f"#{red:02x}{green:02x}{blue:02x}"
    register = doc.get("register", {})
    if roof_pitch_deg(doc) > 10 and (reddish or greenish):
        if reddish and (register.get("year") or 1900) >= 1940 and register.get("storeys", 1) <= 2:
            return {"family": "clay_tile", "colour": colour, "profile": "pantile", "scale_m": 0.3}
        return {
            "family": "sheet_metal",
            "colour": colour,
            "profile": "standing_seam",
            "scale_m": 0.55,
        }
    return {**group(doc)["roof"]["material"], "colour": colour}


def resolve_roof(doc: dict) -> dict:
    return merged(group(doc)["roof"], doc.get("roof", {}))


def trim_colour(doc: dict) -> str:
    return group(doc)["trim"]

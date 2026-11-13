"""What a description leaves out: defaults chosen by the building's style, era and register facts.

`resolve_*` return complete specifications, so the geometry code never asks whether a field exists. A prior is a
statement about buildings of a kind in Turku, never about one building.
"""

import copy
import math
import zlib

from . import archetypes
from .archetypes import HALLS, HOUSES, SHEDS
from .description import expand_storeys

WOODEN = {"wooden_house", "wooden_villa"}
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
            "material": {"family": "granite", "colour": "#8a857d", "finish": "rock_faced", "scale_m": 0.35},
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
                "kind": "moulded",
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

# A school, a museum, a church hall or an office of the stone town: the old masonry house with taller storeys
# and larger windows in small panes.
PUBLIC_OLD = {
    **GROUPS["old_masonry"],
    "ground_h": 4.4,
    "upper_h": 4.2,
    "pitch": 3.3,
    "window": {
        **GROUPS["old_masonry"]["window"],
        "w_m": 1.4,
        "h_m": 2.3,
        "lights": {"pattern": "grid", "cols": 2, "rows": 3},
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
    return doc.get("style", {}).get("type") or archetypes.infer(doc)


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
# Render of the stone town as read: yellows, creams and whites, some terracotta and pink, a green.
OLD_PLASTER = [
    "#e4c574", "#e6cd8a", "#e0bd6e", "#d8cbb2", "#cdbb9c", "#d3c4a2", "#d8d5ca", "#ecece9",
    "#cc9780", "#d7a29f", "#a8836f", "#a3b697", "#e2cbc9", "#d9c9a8",
]  # fmt: skip
NEW_PLASTER = ["#d3d0c7", "#ccc8b8", "#c6c0b1", "#dad6ca"]


def register_wall(doc: dict) -> dict:
    """The wall material an unobserved building gets: the register's facade material in one of its usual colours."""
    measured = doc.get("measured", {}).get("wall")
    typed = archetypes.wall(doc, style_type(doc))
    if typed is not None:
        return archetypes.measured_colour(typed, measured)
    register = doc.get("register", {})
    pick = zlib.crc32(doc.get("id", "").encode())
    material, colours = REGISTER_WALLS.get(register.get("material", ""), (None, None))
    if material is None:
        base = group(doc)["wall"]
        if base["family"] != "plaster":
            return archetypes.measured_colour(dict(base), measured)
        material = {"family": "plaster", "finish": "fine"}
        colours = OLD_PLASTER if archetypes.era_year(doc) < 1940 else NEW_PLASTER
    return archetypes.measured_colour({**material, "colour": colours[pick % len(colours)]}, measured)


def group(doc: dict) -> dict:
    kind = style_type(doc)
    if kind in WOODEN:
        return GROUPS["wooden"]
    typed = archetypes.group(doc, kind)
    if typed is not None:
        return typed
    if kind in OLD_MASONRY or kind in archetypes.WORKPLACES:
        return PUBLIC_OLD if kind in ("public", "church") else GROUPS["old_masonry"]
    return GROUPS["modern"]


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


def usual_wall_family(doc: dict) -> str | None:
    """The family of the building's usual wall material: the described one, else the register's."""
    material = doc.get("wall_defaults", {}).get("material")
    if isinstance(material, str):
        material = doc.get("materials", {}).get(material)
    if not isinstance(material, dict):
        material = register_wall(doc)
    return material.get("family")


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
    kind = style_type(doc)
    boarded = kind in HOUSES | SHEDS and usual_wall_family(doc) == "weatherboard"
    if kind in WOODEN or boarded:
        wall["verticals"] = [
            {
                "kind": "corner_board",
                "where": "ends",
                "width_m": 0.12 if boarded else 0.2,
                "projection_m": 0.025 if boarded else 0.03,
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
    """Opening types of the automatic walls: what the windows and doors of an unobserved building of the style
    look like."""
    g = group(doc)
    kind = style_type(doc)
    out = {"@attic": ATTIC_WINDOW}
    if g is GROUPS["old_masonry"] or g is PUBLIC_OLD:
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
    if "ground_window" in g:
        # A workplace: the glazed ground storey and a pair of glass doors in the frames of its windows.
        frame = g["window"]["frame"]["colour"]
        out["@ground"] = g["ground_window"]
        out["@door"] = {
            "kind": "door",
            "w_m": 1.9,
            "h_m": 2.4,
            "recess_m": 0.2,
            "frame": {"colour": frame, "width_m": 0.07},
            "leaf": {"leaves": 2, "style": "glazed", "colour": frame, "fanlight": "none", "steps": min(2, steps)},
        }
    if kind in HOUSES:
        window = g["window"]
        colours = ["#f0efe9", "#5a4334", "#2f3235", "#3f5a4a", "#7a2f28", "#e6e3d8"]
        out["@door"] = {
            "kind": "door",
            "w_m": 1.0,
            "h_m": 2.1,
            "recess_m": 0.12,
            "frame": {"colour": g["trim"], "width_m": 0.07},
            "leaf": {
                "leaves": 1,
                "style": "half_glazed",
                "colour": archetypes.pick(doc, "door", colours),
                "fanlight": "none",
                "steps": steps,
            },
        }
        # The window of a gable: a storey's window where the attic is lived in, else a small light.
        out["@gable"] = {
            "kind": "window",
            "w_m": min(1.0, window["w_m"]),
            "h_m": min(1.2, window["h_m"]),
        }
        out["@gable_small"] = {
            "kind": "window",
            "w_m": 0.6,
            "h_m": 0.6,
            "lights": {"pattern": "single"},
        }
    if kind not in HOUSES and kind not in SHEDS:
        # A shop bay of the ground storey: glass from a low riser to a sign band, every third with its door.
        sign = archetypes.pick(doc, "sign", ["#e6e3da", "#2f3a45", "#c9c5b8", "#3a3c3f", "#7a2e2a", "#2f5a4a", "#d9d2bd"])
        shop = {
            "kind": "shopfront",
            "w_m": round(min(4.2, g["pitch"] - 0.55), 2),
            "h_m": 2.55,
            "recess_m": 0.14,
            "frame": {"colour": "#3c4044", "width_m": 0.06},
            "shopfront": {
                "panes": 2,
                "stallriser_m": 0.35,
                "transom_h_m": 0.0,
                "door": "none",
                "fascia": {"height_m": 0.4, "colour": sign},
            },
        }
        out["@shop"] = shop
        out["@shop_door"] = merged(shop, {"shopfront": {"panes": 3, "door": "centre", "stallriser_m": 0.35}})
    if kind in SHEDS:
        out["@door"] = {
            "kind": "door",
            "w_m": 0.9,
            "h_m": 2.0,
            "recess_m": 0.04,
            "frame": {"colour": g["trim"], "width_m": 0.07},
            "leaf": {
                "leaves": 1,
                "style": "boarded",
                "colour": register_wall(doc).get("colour", "#7b3a2e"),
                "fanlight": "none",
                "steps": 0,
            },
        }
        colours = ["#e9e8e2", "#e9e8e2", "#8c8c88", "#5a4334", "#3a3c3f"]
        out["@garage"] = {
            "kind": "garage",
            "w_m": 2.5,
            "h_m": 2.1,
            "recess_m": 0.06,
            "frame": {"colour": g["trim"], "width_m": 0.07},
            "leaf": {"leaves": 1, "style": "flush", "colour": archetypes.pick(doc, "garage", colours)},
        }
    elif kind in HALLS:
        out["@door"] = {
            "kind": "door",
            "w_m": 1.0,
            "h_m": 2.1,
            "recess_m": 0.08,
            "frame": {"colour": "#5c6064", "width_m": 0.06},
            "leaf": {
                "leaves": 1,
                "style": "half_glazed",
                "colour": "#5c6064",
                "fanlight": "none",
                "steps": 0,
            },
        }
        colours = ["#c9cbc8", "#8c8c88", "#3f5a78", "#6c6c6a"]
        out["@loading"] = {
            "kind": "garage",
            "w_m": 3.6,
            "h_m": 3.6,
            "recess_m": 0.1,
            "frame": {"colour": "#6c6c6a", "width_m": 0.08},
            "leaf": {"leaves": 1, "style": "flush", "colour": archetypes.pick(doc, "garage", colours)},
        }
    return out


def fitted_wall(doc: dict, wall: dict, height: float, role: dict | None = None) -> dict:
    """An automatic wall made to fit the wall it stands on, `height` from its lowest ground to its eaves.

    The register's storey count is kept when it gives believable storeys; otherwise the storeys are counted from
    the height. A low wooden house is one tall storey under a knee wall with small attic windows on a high plinth,
    which is what the wooden quarters of Turku are made of. `role` (archetypes.wall_roles) says what this wall
    carries besides its windows: street doors under a porch, garage or loading doors, a gable window, or nothing.
    """
    role = role or {}
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
    kind = style_type(doc)
    plinth = out.get("plinth", {}).get("height_m", 0.0)
    available = height - plinth
    if available < (1.9 if kind in SHEDS else 2.3):
        out["storeys"] = []
        return out
    sill = g["sill"]
    if old_quarter(doc) and 4.9 <= height < 7.4:
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
        # A customer door of a cafe or shop in the house (the only role an old wooden house's wall has but a gable).
        role_parts(doc, out, role, main, out.get("plinth", {}).get("height_m", 0.0), g)
        return out
    count = max(1, int(doc.get("register", {}).get("storeys") or 1))
    knee = basement = 0.0
    if kind in SHEDS or (kind in HALLS and count <= 1 and "ground_window" not in g):
        count = 1  # one room to the eaves, however high
    elif kind in HOUSES and available < 5.4:
        # A house whose eaves are too low for two storeys is one storey, whatever the register counts (it counts
        # the rooms in the roof and the basement): the wall above the windows is the knee wall of the attic, and
        # what is left over is the basement standing out of a sloping site.
        count = 1
        spare = max(0.0, available - g["ground_h"])
        knee = min(spare, 1.4 if doc.get("register", {}).get("storeys", 1) >= 2 else 0.5)
        lift = min(spare - knee, 1.4)
        if lift > 0.05 and "plinth" in out:
            out["plinth"]["height_m"] = round(plinth + lift, 2)
            plinth, available = plinth + lift, available - lift
    elif (
        kind == "detached_house"
        and 1940 <= archetypes.era_year(doc) < 1980
        and available < 7.2
        and count <= 2
    ):
        # A post-war house of the forties to seventies with eaves this high is one storey on a basement that
        # stands out of the ground with small windows (as read: four of six such houses the register counts as
        # two storeys; Myllymäentie 33, Uudenpellonkatu 7 and 9, Luutnantintie 21), the attic behind a knee wall.
        count = 1
        knee = 0.5
        basement = available - g["ground_h"] - knee
        available -= basement
    elif not 2.6 <= available / count <= 4.4:
        count = max(1, round(available / g["upper_h"]))
    each = (available - knee) / count
    ground = min(max(each, g["ground_h"]), each * 1.2) if count > 1 else each
    window = None if role.get("blank") else "@default"
    shops = role.get("shops") if available - knee >= 3.0 else None
    if shops and count > 1:
        ground = min(max(ground, 3.5), available - (count - 1) * 2.5)
    upper = (available - knee - ground) / (count - 1) if count > 1 else 0.0
    out["storeys"] = [
        {"kind": "ground", "height_m": round(ground, 2), "sill_m": sill, "window": window}
    ]
    if window and count > 1 and "ground_window" in g and ground >= g["ground_window"]["h_m"] + g["ground_sill"] + 0.3:
        out["storeys"][0].update(sill_m=g["ground_sill"], window="@ground")
    if shops:
        out["storeys"][0] = {"kind": "ground", "height_m": round(ground, 2), "sill_m": 0.0, "row": list(shops)}
    role_parts(doc, out, role, available, plinth, g)
    if count > 1:
        out["storeys"].append(
            {
                "kind": "upper",
                "count": count - 1,
                "height_m": round(upper, 3),
                "sill_m": sill,
                "window": window,
            }
        )
        if role.get("balconies"):
            out["balconies"] = out.get("balconies", []) + copy.deepcopy(role["balconies"])
        if (g is GROUPS["old_masonry"] or g is PUBLIC_OLD) and "bands" not in out:
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
    if knee > 0.05:
        out["storeys"].append({"kind": "attic", "height_m": round(knee, 2), "sill_m": 0.2, "window": None})
    if basement > 0.05:
        # The doors stay at the ground: in the basement storey, under the main storey's windows.
        main = out["storeys"][0]
        out["storeys"].insert(0, {
            "kind": "basement", "height_m": round(basement, 2), "sill_m": round(max(0.3, basement - 0.95), 2),
            "window": None if role.get("blank") or basement < 1.5 else "@attic",
            **({"openings": main.pop("openings")} if "openings" in main else {}),
        })  # fmt: skip
    if "gable" in role:
        # A gable high enough for a room under the roof has that room's window (it stands on the attic floor,
        # which a knee wall puts below the eaves); a lower one a small light.
        apex, rise = role["gable"]
        name = "@gable" if rise + knee >= 2.6 else "@gable_small"
        lift = max(-0.5, 0.85 - knee) if rise + knee >= 2.6 else rise * 0.3
        out["openings"] = out.get("openings", []) + [
            {"type": name, "u_m": round(apex, 2), "v_m": round(height + lift, 2)}
        ]
    return out



def role_parts(doc: dict, out: dict, role: dict, available: float, plinth: float, g: dict) -> None:
    """What a wall's role (archetypes.wall_roles) adds to its ground storey: street, garage and loading doors, a
    porch or canopy over the doors, a name board over a customer door."""
    openings = []
    for name, key in (("@door", "doors"), ("@garage", "garage"), ("@loading", "loading")):
        spec = resolve_opening(doc, name)
        # A door is as high as the wall lets it be: a low garage has a low door.
        fits = min(spec["h_m"], available - 0.2)
        for u in role.get(key, []):
            if fits >= 1.8:
                placed = {"type": name, "u_m": round(u, 2)}
                openings.append(placed if fits == spec["h_m"] else {**placed, "h_m": round(fits, 2)})
    if openings:
        out["storeys"][0]["openings"] = openings
    if role.get("porch") and role.get("doors") and available >= 2.5:
        out["attached"] = out.get("attached", []) + [
            {
                "kind": "porch",
                "type": "open",
                "roof": "shed",
                "u_m": round(u, 2),
                "w_m": 2.2,
                "h_m": 2.3,
                "depth_m": 1.5,
                "supports": {"kind": "posts", "count": 2, "diameter_m": 0.1, "colour": g["trim"]},
            }
            for u in role["doors"]
        ]
    if role.get("signs") and available >= 3.0:
        # A name board over a customer door: the premise's sign, in a colour a shop sign has.
        door = resolve_opening(doc, "@door")
        sign = archetypes.pick(doc, "sign", ["#2f3a45", "#e6e3da", "#3a3c3f", "#7a2e2a", "#2f5a4a", "#d9d2bd"])
        out["attached"] = out.get("attached", []) + [
            {"kind": "sign", "type": "board", "u_m": round(u, 2), "v_m": round(plinth + door["h_m"] + 0.55, 2),
             "w_m": 1.8, "h_m": 0.45, "colour": sign}
            for u in role["signs"]
        ]
    if role.get("canopy") and role.get("doors") and available >= 3.4:
        door = resolve_opening(doc, "@door")
        out["attached"] = out.get("attached", []) + [
            {
                "kind": "canopy",
                "type": "slab",
                "u_m": round(u, 2),
                "v_m": round(plinth + door["h_m"] + 0.35, 2),
                "w_m": round(door["w_m"] + 1.8, 2),
                "h_m": 0.16,
                "depth_m": 1.8,
                "colour": door["frame"]["colour"],
            }
            for u in role["doors"]
        ]


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
    """The roofing the orthophoto shows on a building nobody has looked at (archetypes.roofing: the hue is kept,
    the lightness is that of roofing seen from the street). Leaf green is tree crowns: such a roof keeps the
    roofing of its style."""
    colour = doc.get("measured", {}).get("roof", {}).get("colour") or doc.get(
        "register", {}
    ).get("roof_colour")
    if not colour:
        return None
    return archetypes.roofing(
        colour,
        style_type(doc),
        archetypes.era_year(doc),
        roof_pitch_deg(doc),
        int(doc.get("register", {}).get("storeys") or 1),
        group(doc)["roof"]["material"],
    )


def resolve_roof(doc: dict) -> dict:
    return merged(group(doc)["roof"], doc.get("roof", {}))


def trim_colour(doc: dict) -> str:
    return group(doc)["trim"]

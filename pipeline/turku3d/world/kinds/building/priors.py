"""What a description leaves out: defaults chosen by the building's style, era and register facts.

`resolve_*` return complete specifications, so the geometry code never asks whether a field exists. A prior is a
statement about buildings of a kind in Turku, never about one building.
"""

import copy

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


def group(doc: dict) -> dict:
    kind = style_type(doc)
    return GROUPS[
        "wooden" if kind in WOODEN else "old_masonry" if kind in OLD_MASONRY else "modern"
    ]


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
        "plinth": g["plinth"],
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


def resolve_roof(doc: dict) -> dict:
    return merged(group(doc)["roof"], doc.get("roof", {}))


def trim_colour(doc: dict) -> str:
    return group(doc)["trim"]

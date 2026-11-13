"""Material records: a description's material becomes a family with parameters and a plain fallback colour.

The client turns a family and its parameters into a procedural PBR material at metric scale
(game/src/world/location/buildingMaterials.ts). The fallback colour and roughness render where a client does not
know the family.
"""

import json

# family -> (default colour, roughness, metalness, default parameters)
FAMILIES = {
    "plaster": ("#c9bfae", 0.92, 0.0, {"finish": "fine"}),
    "brick": ("#8f4a38", 0.9, 0.0, {"colour2": "#b3aa9c", "bond": "stretcher", "finish": "facing"}),
    "weatherboard": (
        "#b8ab8f",
        0.8,
        0.0,
        {"orientation": "horizontal", "profile": "bevel", "scale_m": 0.13},
    ),
    "log": ("#7a5c40", 0.85, 0.0, {"scale_m": 0.22}),
    "granite": ("#8f8a84", 0.75, 0.0, {"finish": "ashlar", "scale_m": 0.45, "colour2": "#5f5b56"}),
    "rubble": ("#86807a", 0.9, 0.0, {"scale_m": 0.45, "colour2": "#a39c90"}),
    "stone": ("#b5ab9a", 0.7, 0.0, {"finish": "slab", "scale_m": 0.6, "colour2": "#8d8576"}),
    "concrete": ("#a9a69f", 0.9, 0.0, {"finish": "cast", "scale_m": 2.8}),
    "ceramic_tile": ("#b9b3a3", 0.45, 0.0, {"scale_m": 0.2, "colour2": "#8e8a80"}),
    "sheet_metal": ("#4d4f52", 0.5, 0.6, {"profile": "standing_seam", "scale_m": 0.55}),
    "clay_tile": ("#9a4f36", 0.85, 0.0, {"profile": "pantile", "scale_m": 0.3}),
    "concrete_tile": ("#5d5552", 0.9, 0.0, {"profile": "pantile", "scale_m": 0.33}),
    "felt": ("#3d3d3f", 0.95, 0.0, {"profile": "plain", "scale_m": 1.0}),
    "slate": ("#4b4e55", 0.7, 0.0, {"scale_m": 0.3}),
    "glass": ("#20262b", 0.06, 0.0, {"reflectivity": 0.6}),
    "painted_wood": ("#ebe7dc", 0.6, 0.0, {"finish": "matt"}),
    "metal": ("#3c3f42", 0.45, 0.8, {"finish": "matt"}),
    "copper": ("#6fa08d", 0.6, 0.7, {"finish": "weathered"}),
    "flat": ("#808080", 0.9, 0.0, {}),
}
PARAMETERS = (
    "colour",
    "colour2",
    "finish",
    "bond",
    "orientation",
    "profile",
    "scale_m",
    "weathering",
    "relief",
    "course_m",
)


def linear(colour: str) -> tuple[float, float, float]:
    """sRGB hex to linear RGB."""
    out = []
    for k in (1, 3, 5):
        c = int(colour[k : k + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def shade(colour: str, factor: float) -> str:
    """A colour scaled in sRGB, for defaults derived from another colour."""
    return "#" + "".join(
        f"{max(0, min(255, round(int(colour[k : k + 2], 16) * factor))):02x}" for k in (1, 3, 5)
    )


class Palette:
    """Resolves the materials of one description and interns them as keys for the mesh soup."""

    def __init__(self, named: dict | None = None):
        self.named = named or {}
        self.records = {}

    def record(self, material, fallback=None, **overrides) -> dict:
        """The full record of a description material (a name, an inline object or None)."""
        if isinstance(material, str):
            material = self.named.get(material)
        if material is None:
            material = (
                fallback
                if isinstance(fallback, dict)
                else self.named.get(fallback)
                if fallback
                else None
            )
        if material is None:
            material = {"family": "plaster"}
        family = material.get("family", "plaster")
        colour, _, _, defaults = FAMILIES.get(family, FAMILIES["flat"])
        params = {"colour": colour, **defaults}
        params.update({k: v for k, v in material.items() if k in PARAMETERS})
        params.update({k: v for k, v in overrides.items() if v is not None})
        return {"family": family, **params}

    def occluded(self, key: str, factor: float) -> str:
        """The material of a key, darker: a surface little sky reaches (a window reveal, a balcony recess). It
        stands in for ambient occlusion until the renderer has it; then this is the one place to remove."""
        record = dict(self.records[key])
        for name in ("colour", "colour2"):
            if name in record:
                record[name] = shade(record[name], factor)
        return self.key(record)

    def key(self, material=None, fallback=None, **overrides) -> str:
        record = self.record(material, fallback, **overrides)
        key = json.dumps(record, sort_keys=True)
        self.records[key] = record
        return key

    def paint(
        self, colour: str | None, like: dict | str | None = None, family: str | None = None
    ) -> str:
        """A painted trim part in a colour: wood on a wooden wall, plaster on a masonry one."""
        base = self.record(like) if like is not None else {"family": "plaster"}
        if family is None:
            family = (
                "painted_wood"
                if base["family"] in ("weatherboard", "log", "painted_wood")
                else "plaster"
            )
        params = {"family": family}
        if colour:
            params["colour"] = colour
        if family == "plaster":
            params["finish"] = "smooth"
        return self.key(params)

    def flat(self, colour: str, roughness: float = 0.9, metalness: float = 0.0) -> str:
        return self.key({"family": "flat"}, colour=colour, roughness=roughness, metalness=metalness)


def fallback(record: dict) -> tuple[tuple[float, float, float], float, float]:
    """(linear colour, roughness, metalness) of a record for the glTF material."""
    _, roughness, metalness, _ = FAMILIES.get(record["family"], FAMILIES["flat"])
    return (
        linear(record.get("colour", "#808080")),
        record.get("roughness", roughness),
        record.get("metalness", metalness),
    )

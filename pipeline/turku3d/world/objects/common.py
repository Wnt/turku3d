"""What the street-object kinds share: materials, the interaction block, description merging and placement."""

from __future__ import annotations

import math

import numpy as np

from ..api import Instances, Material, Output, Part, Stub, envelope
from ..frame import to_engine
from .mesh import srgb

BY = "w2-objects"
ENVELOPE = ("schema", "id", "tier", "provenance")

# Material families of the street objects, as rows of `world/families.json`. The fallback colour is what a family
# nobody registered in the client renders with. A lens is its own primitive and carries what it emits.
GALVANISED = Material("metal", {"colour": "#969a9c", "finish": "matt"}, srgb(150, 154, 156), 0.55, 0.85)
ALUMINIUM = Material("metal", {"colour": "#a8aaac", "finish": "matt"}, srgb(168, 170, 172), 0.5, 0.8)
SIGN_BACK = Material("metal", {"colour": "#8a8d8f", "finish": "matt"}, srgb(138, 141, 143), 0.6, 0.6)
BLACK_STEEL = Material("metal", {"colour": "#1c1d1f", "finish": "matt"}, srgb(28, 29, 31), 0.5, 0.6)
DARK_PLASTIC = Material("plastic", {"colour": "#202224"}, srgb(32, 34, 36), 0.7, 0.0)
LENS = Material("lens", {"kind": "street-light", "colour": "#e8e8dc"}, srgb(232, 232, 220), 0.25, 0.0, emissive=(0.3, 0.3, 0.26))
# `stone`, not `granite`: the game's own material pass draws anything named granite as its paving granite.
GRANITE = Material("stone", {"colour": "#968c84", "finish": "rock_faced"}, srgb(150, 140, 132), 0.9, 0.0)
CONCRETE = Material("concrete", {"colour": "#a09e98", "finish": "cast"}, srgb(160, 158, 152), 0.9, 0.0)


def painted_metal(colour: str) -> Material:
    from .mesh import rgb_hex

    return Material("metal", {"colour": colour, "finish": "matt"}, rgb_hex(colour), 0.5, 0.5)


def painted_wood(colour: str, finish: str = "matt") -> Material:
    from .mesh import rgb_hex

    return Material("painted_wood", {"colour": colour, "finish": finish}, rgb_hex(colour), 0.8, 0.0)


def sign_material(code: str, width: float, height: float, atlas: str, mean=(1.0, 1.0, 1.0), text: str = "",
                  blue: bool = False) -> Material:
    """The sign family: retroreflective sheeting whose face is the picture stored at `atlas` (an image url of the
    object store). TEXCOORD_0 addresses the picture, 0..1 across the plate's bounds with v down (`uv: atlas`); the
    fallback colour is the picture's mean, for a client that draws no family."""
    params = {"code": code, "uv": "atlas", "atlas": atlas, "width": round(width, 3), "height": round(height, 3)}
    if text:
        params["text"] = text
    if blue:
        params["blue"] = True
    return Material("sign", params, tuple(round(float(v), 3) for v in mean), 0.45, 0.0)


def interaction(behaviour: str, shape: str, size: list[float], mass: float | None = None, **more) -> dict:
    """How the game may treat the object: `fixed`, `knock-over` or `breakable`, with a simple collision shape in the
    asset's frame (base at y = 0): box [x, y, z], cylinder or capsule [radius, height]."""
    block = {"behaviour": behaviour, "collision": {"shape": shape, "size": [round(float(v), 3) for v in size]}}
    if mass is not None:
        block["mass"] = round(float(mass), 1)
    block.update(more)
    return block


def describe(kind: str, version: int, stub: Stub, stored: dict | None) -> dict:
    """The automatic description from the stub's facts, with every top-level field of a stored description
    replacing the automatic one (the merge rule of all street-object kinds; `docs/specs/street-objects.md`)."""
    description = {**envelope(kind, version, stub, stored, BY), **stub.facts}
    for name, value in (stored or {}).items():
        if name not in ENVELOPE:
            description[name] = value
    return description


def yaw_of_bearing(bearing: float) -> float:
    """Instance yaw that turns an asset's +Z (its front) to a grid bearing (degrees clockwise from north)."""
    return math.pi - math.radians(bearing)


def yaw_of_run(bearing: float) -> float:
    """Instance yaw that turns an asset's +X (its length) to a grid bearing."""
    return math.pi / 2 - math.radians(bearing)


def single(description: dict, parts: list[Part], bearing: float = 0.0) -> Output:
    """One object as one instance row: the asset's base at the entity origin, turned to its bearing."""
    e, n, h = description["at"]
    block = description.get("interaction") or {}
    item = Instances(parts, np.array([[0.0, 0.0, 0.0, round(yaw_of_bearing(bearing), 4), 1.0, 1.0, 1.0]]),
                     ids=[description["id"]], behaviour=block.get("behaviour"), collision=block.get("collision"),
                     mass=block.get("mass"))
    return Output(tuple(float(v) for v in to_engine(e, n, h)), [], [item])


def box_of(e: float, n: float, reach: float = 1.0) -> tuple[float, float, float, float]:
    return (e - reach, n - reach, e + reach, n + reach)


def unique(stubs: list[Stub]) -> list[Stub]:
    """Stubs without repeated ids, the first of each kept: registers repeat rows (the same fence line under two
    surveys, the same pole in two layers), and an entity id is one object."""
    seen: set[str] = set()
    return [stub for stub in stubs if not (stub.id in seen or seen.add(stub.id))]

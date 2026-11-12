"""What the street-object kinds share: materials, the interaction block, description merging and placement."""

from __future__ import annotations

import math

import numpy as np

from ..api import Instances, Material, Output, Part, Stub, envelope
from ..frame import to_engine
from .mesh import srgb

BY = "w2-objects"
ENVELOPE = ("schema", "id", "tier", "provenance")

# Material families of the street objects. Parameters are the family's table row; the fallback colour is what an
# unknown family renders with.
GALVANISED = Material("metal", {"finish": "galvanised"}, srgb(150, 154, 156), 0.55, 0.85)
ALUMINIUM = Material("metal", {"finish": "aluminium"}, srgb(168, 170, 172), 0.5, 0.8)
SIGN_BACK = Material("metal", {"finish": "sign-back"}, srgb(138, 141, 143), 0.6, 0.6)
BLACK_STEEL = Material("metal", {"finish": "painted", "color": "#1c1d1f"}, srgb(28, 29, 31), 0.5, 0.6)
DARK_PLASTIC = Material("plastic", {"color": "#202224"}, srgb(32, 34, 36), 0.7, 0.0)
LENS = Material("lens", {"kind": "street-light"}, srgb(232, 232, 220), 0.25, 0.0)
GRANITE = Material("stone", {"kind": "granite", "finish": "split"}, srgb(150, 140, 132), 0.9, 0.0)
CONCRETE = Material("concrete", {"finish": "cast"}, srgb(160, 158, 152), 0.9, 0.0)


def painted_metal(colour: str) -> Material:
    from .mesh import rgb_hex

    return Material("metal", {"finish": "painted", "color": colour}, rgb_hex(colour), 0.5, 0.5)


def painted_wood(colour: str, finish: str = "painted") -> Material:
    from .mesh import rgb_hex

    return Material("wood", {"finish": finish, "color": colour, "boards": "vertical"}, rgb_hex(colour), 0.8, 0.0)


def sign_material(code: str, width: float, height: float, text: str = "", blue: bool = False) -> Material:
    """The sign family: retroreflective sheeting whose face is named by the sign code. TEXCOORD_0 addresses the
    face (0..1 across the plate, `uv: atlas`); the mesh also carries the face as vertex colours on a planar
    partition, so the fallback draws it without a texture."""
    params = {"code": code, "uv": "atlas", "width": round(width, 3), "height": round(height, 3)}
    if text:
        params["text"] = text
    if blue:
        params["blue"] = True
    return Material("sign", params, (1.0, 1.0, 1.0), 0.45, 0.0)


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

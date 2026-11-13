"""Types of the world pipeline contracts (docs/design/world-pipeline.md, section 6)."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from .sources import Sources

ENVELOPE = ("schema", "id", "tier", "provenance")
TIERS = ("auto", "observed", "reviewed")
BEHAVIOURS = ("fixed", "knock-over", "breakable")
SURFACES = ("opaque", "masked", "blend", "decal")
SHAPES = ("box", "capsule", "cylinder")


@dataclass(frozen=True)
class Area:
    """A location query: a circle in EPSG:3877."""

    e: float
    n: float
    radius: float

    def bbox(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        r = self.radius + margin
        return self.e - r, self.n - r, self.e + r, self.n + r

    def grown(self, radius: float) -> Area:
        return Area(self.e, self.n, radius)

    def box_distance(self, box: tuple[float, float, float, float]) -> float:
        """Distance from the centre to an (e0, n0, e1, n1) box; 0 inside it."""
        dx = max(box[0] - self.e, 0.0, self.e - box[2])
        dy = max(box[1] - self.n, 0.0, self.n - box[3])
        return math.hypot(dx, dy)

    def touches(self, box: tuple[float, float, float, float]) -> bool:
        return self.box_distance(box) <= self.radius


@dataclass
class Stub:
    """One discovered entity: its identity and every source fact its description needs, as JSON values."""

    id: str
    box: tuple[float, float, float, float]  # survey bounds e0, n0, e1, n1
    sources: list[dict]
    facts: dict

    @property
    def kind(self) -> str:
        return self.id.split("/", 1)[0]


@dataclass
class Material:
    family: str
    params: dict = field(default_factory=dict)
    color: tuple[float, float, float] = (0.6, 0.6, 0.6)  # fallback base colour, linear RGB
    roughness: float = 0.9
    metalness: float = 0.0
    double_sided: bool = False
    surface: str = "opaque"  # how it is drawn: "opaque" | "masked" | "blend" | "decal"
    emissive: tuple[float, float, float] | None = None  # linear RGB of a surface that emits light


@dataclass
class Part:
    name: str
    positions: np.ndarray  # (n, 3) engine axes, relative to Output.origin (or to the asset origin)
    triangles: np.ndarray  # (m, 3) counter-clockwise front faces
    material: Material
    normals: np.ndarray | None = None  # flat normals are computed when missing
    uvs: np.ndarray | None = None  # (n, 2) metres
    colors: np.ndarray | None = None  # (n, 3 | 4) linear
    attributes: dict[str, np.ndarray] = field(default_factory=dict)  # custom, written as _NAME
    lod: int = 0


@dataclass
class Instances:
    """Placed copies of one asset whose origin is the object's base. Every object a player can hit is its own row
    here (or its own entity), never part of a ground or building mesh."""

    asset: str | list[Part]  # a GLB url (store or /assets/...), or parts the driver packs into one
    transforms: np.ndarray  # (n, 7): x, y, z, yaw, sx, sy, sz relative to Output.origin
    lod: int = 0
    ids: list[str] | None = None  # one stable id per transform (a register key), for objects the game addresses
    behaviour: str | None = None  # "fixed" | "knock-over" | "breakable"; None: scenery the player passes through
    collision: dict | None = None  # {"shape": "box" | "capsule" | "cylinder", "size": [...]} asset-local, base at y = 0
    mass: float | None = None  # kilograms of one object, when it can be moved


@dataclass
class Heightfield:
    """The raster a ground entity's mesh was triangulated from: heights[row, column] in metres above N2000 at
    x = origin[0] + column * spacing, z = origin[1] + row * spacing (engine metres, absolute)."""

    origin: tuple[float, float]
    spacing: float
    heights: np.ndarray


@dataclass(frozen=True)
class Image:
    """A picture a material draws (a sign atlas, a text plate). It is stored like every object, under the hash of
    its bytes; a kind returns it in `Output.images` and writes `url` into the material's parameters."""

    data: bytes
    ext: str = "png"

    @property
    def url(self) -> str:
        digest = hashlib.sha256(self.data).hexdigest()
        return f"objects/{digest[:2]}/{digest}.{self.ext}"


@dataclass
class Output:
    origin: tuple[float, float, float]  # engine metres, absolute
    parts: list[Part]
    instances: list[Instances] = field(default_factory=list)
    heightfield: Heightfield | None = None
    # Largest deviation of a level from the description, metres, by lod; the driver assumes 0.01 for lod 0 and 0.5
    # for a proxy level that does not say.
    error_m: dict[int, float] = field(default_factory=dict)
    images: list[Image] = field(default_factory=list)  # pictures the materials name by url


class Kind(Protocol):
    name: str

    def discover(self, area: Area, sources: Sources) -> list[Stub]:
        """Entities of this kind that touch the area, each with its source facts. All source reading happens
        here. The result for one entity must not depend on the area beyond its selection."""

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """The effective description: source facts merged with the stored description. Pure."""

    def compile(self, description: dict) -> Output:
        """Description in; mesh parts, material references and instances out. Pure and deterministic."""


def envelope(kind: str, version: int, stub: Stub, stored: dict | None, by: str) -> dict:
    """The description envelope for an automatic fit, raised to the stored description's tier."""
    provenance = [{**ref, "by": by} for ref in stub.sources]
    return {
        "schema": f"turku-description/{kind}/{version}",
        "id": stub.id,
        "tier": (stored or {}).get("tier", "auto"),
        "provenance": provenance + list((stored or {}).get("provenance", [])),
    }

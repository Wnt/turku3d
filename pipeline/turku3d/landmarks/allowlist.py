"""The landmark allowlist (`landmarks.toml`): which objects get a lidar mesh, and its budgets."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("landmarks.toml")
METHODS = ("lidar-heightfield",)
WALLS = ("brick", "render")
BASES = ("ground", "water")
CLASSES = (1, 6, 12)  # unclassified (boats, cranes), building, overlap
MAX_VISUAL_TRIANGLES = 60_000
MAX_COLLISION_TRIANGLES = 2_000  # ARCHITECTURE.md D5: building collision shells
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
KEYS = {
    "id",
    "name",
    "building",
    "polygon_en",
    "method",
    "cell_m",
    "visual_triangles",
    "collision_triangles",
    "wall",
    "wall_rgb",
    "classes",
    "base",
}


@dataclass(frozen=True)
class Landmark:
    id: str
    name: str
    method: str
    cell_m: float
    visual_triangles: int
    collision_triangles: int
    wall: str
    wall_rgb: tuple[int, int, int]
    classes: tuple[int, ...] = (6,)
    base: str = "ground"
    building: str | None = None
    polygon_en: tuple[tuple[float, float], ...] | None = None


def parse(text: str) -> list[Landmark]:
    data = tomllib.loads(text)
    if data.get("version") != 1:
        raise ValueError("landmarks.toml: version must be 1")
    unknown = set(data) - {"version", "landmark"}
    if unknown:
        raise ValueError(f"landmarks.toml: unknown top-level keys {sorted(unknown)}")
    result, ids, buildings = [], set(), set()
    for entry in data.get("landmark", []):
        where = f"landmark {entry.get('id', '?')}"
        extra = set(entry) - KEYS
        if extra:
            raise ValueError(f"{where}: unknown keys {sorted(extra)}")
        ident = entry.get("id", "")
        if not isinstance(ident, str) or not ID.match(ident):
            raise ValueError(f"{where}: id must match {ID.pattern}")
        if ident in ids:
            raise ValueError(f"{where}: duplicate id")
        ids.add(ident)
        if ("building" in entry) == ("polygon_en" in entry):
            raise ValueError(f"{where}: give exactly one of building or polygon_en")
        polygon = None
        if "polygon_en" in entry:
            polygon = tuple((float(e), float(n)) for e, n in entry["polygon_en"])
            if len(polygon) < 3:
                raise ValueError(f"{where}: polygon_en needs at least three corners")
        building = entry.get("building")
        if building is not None:
            if not isinstance(building, str) or not building:
                raise ValueError(f"{where}: building must be a non-empty id")
            if building in buildings:
                raise ValueError(f"{where}: building {building} is claimed twice")
            buildings.add(building)
        method = entry.get("method")
        if method not in METHODS:
            raise ValueError(f"{where}: method must be one of {METHODS}")
        cell = float(entry.get("cell_m", 0.5))
        if not 0.2 <= cell <= 1.0:
            raise ValueError(f"{where}: cell_m must be within 0.2-1.0 m")
        visual = entry.get("visual_triangles")
        collision = entry.get("collision_triangles")
        for name, value, cap in (
            ("visual_triangles", visual, MAX_VISUAL_TRIANGLES),
            ("collision_triangles", collision, MAX_COLLISION_TRIANGLES),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= cap:
                raise ValueError(f"{where}: {name} must be an integer within 1-{cap}")
        wall = entry.get("wall")
        if wall not in WALLS:
            raise ValueError(f"{where}: wall must be one of {WALLS}")
        rgb = entry.get("wall_rgb")
        if (
            not isinstance(rgb, list)
            or len(rgb) != 3
            or not all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)
        ):
            raise ValueError(f"{where}: wall_rgb must be three integers within 0-255")
        classes = entry.get("classes", [6])
        if not isinstance(classes, list) or not classes or any(c not in CLASSES for c in classes):
            raise ValueError(f"{where}: classes must be a non-empty list from {CLASSES}")
        base = entry.get("base", "ground")
        if base not in BASES:
            raise ValueError(f"{where}: base must be one of {BASES}")
        result.append(
            Landmark(
                id=ident,
                name=str(entry.get("name", ident)),
                method=method,
                cell_m=cell,
                visual_triangles=visual,
                collision_triangles=collision,
                wall=wall,
                wall_rgb=tuple(rgb),
                classes=tuple(classes),
                base=base,
                building=building,
                polygon_en=polygon,
            )
        )
    return result


def load(path: Path = DEFAULT_PATH) -> list[Landmark]:
    return parse(Path(path).read_text())

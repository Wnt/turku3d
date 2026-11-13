"""compile: an effective building description becomes mesh parts and material records.

Pure and deterministic: everything it needs is in the description (its shell included). The result follows the
world-pipeline contract: engine axes relative to an origin, metric UVs, one part per (part name, material).
"""

import copy
from dataclasses import dataclass, field

import numpy as np

from . import facade as facades_module
from . import mesh, priors, roofs
from . import shell as shell_module
from .materials import Palette, fallback

ORIGIN_E, ORIGIN_N = 23459500.0, 6704500.0


@dataclass
class Material:
    family: str
    params: dict
    color: tuple = (0.6, 0.6, 0.6)
    roughness: float = 0.9
    metalness: float = 0.0


@dataclass
class Part:
    name: str
    positions: np.ndarray
    triangles: np.ndarray
    material: Material
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    colors: np.ndarray | None = None
    attributes: dict = field(default_factory=dict)
    lod: int = 0


@dataclass
class Output:
    origin: tuple
    parts: list
    instances: list = field(default_factory=list)
    report: list = field(default_factory=list)


def _localise(doc: dict, origin: np.ndarray) -> dict:
    """A copy of the description with every survey coordinate made relative to the building's origin."""
    doc = copy.deepcopy(doc)
    for surface in doc["shell"]["surfaces"]:
        surface["rings"] = [(np.asarray(r, float) - origin).tolist() for r in surface["rings"]]
    doc["shell"]["terrain"] = [
        (np.asarray(line, float) - origin).tolist() for line in doc["shell"].get("terrain", [])
    ]
    for neighbour in doc["shell"].get("neighbours", []):
        neighbour["footprint"] = (np.asarray(neighbour["footprint"], float) - origin[:2]).tolist()
        neighbour["top_h"] -= origin[2]
        if "base_h" in neighbour:
            neighbour["base_h"] -= origin[2]
    for wall in doc.get("walls", []):
        frame = wall.get("frame")
        if frame:
            frame["p0"] = (np.asarray(frame["p0"], float) - origin[:2]).tolist()
            frame["p1"] = (np.asarray(frame["p1"], float) - origin[:2]).tolist()
            if "base_h" in frame:
                frame["base_h"] -= origin[2]
    roof = doc.get("roof", {})
    for group in ("chimneys", "dormers", "details"):
        for part in roof.get(group, []):
            if "at" in part.get("anchor", {}):
                part["anchor"]["at"] = (
                    np.asarray(part["anchor"]["at"], float) - origin[:2]
                ).tolist()
    return doc


def building_origin(shell: dict) -> np.ndarray:
    """A stable origin: the middle of the shell's bounding box at its lowest height, to the millimetre."""
    points = np.vstack([np.asarray(r, float) for s in shell["surfaces"] for r in s["rings"]])
    low, high = points.min(axis=0), points.max(axis=0)
    return np.round(np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]]), 3)


def compile(description: dict) -> Output:  # noqa: A001 - the contract's name
    origin = building_origin(description["shell"])
    doc = _localise(description, origin)
    shell = shell_module.read(doc["shell"])
    palette = Palette(doc.get("materials"))
    soup = mesh.Soup()
    report = []
    described, facade_by_wall = {}, {}
    for wall in doc.get("walls", []):
        frame = wall.get("frame")
        if not frame:
            continue
        hit = shell_module.match(shell.facades, frame["p0"], frame["p1"])
        if hit is None:
            report.append(f"wall {wall.get('id', '?')}: no shell wall under its frame")
        elif hit.index in described:
            report.append(
                f"wall {wall.get('id', '?')}: the shell wall is already described by {described[hit.index].get('id', '?')}"
            )
        else:
            described[hit.index] = wall
            if "id" in wall:
                facade_by_wall[wall["id"]] = hit
    templates = {}
    neighbours = doc["shell"].get("neighbours", [])
    wall_family = "plaster"
    # An unobserved block of flats has its street door on its longest wall that stands on the ground.
    undescribed = [f for f in shell.facades if f.grounded and f.index not in described]
    entrance = max(undescribed, key=lambda f: f.width, default=None)
    if described or priors.style_type(doc) in priors.WOODEN:
        entrance = None
    for facade in shell.facades:
        wall = priors.resolve_wall(doc, described.get(facade.index))
        if not facade.grounded:
            wall = priors.raised_wall(wall, facade.index in described)
        elif facade.index not in described:
            foot = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
            wall = priors.fitted_wall(
                doc,
                wall,
                facades_module.eaves_height(facade.outline, facade.width) - foot,
                facade.width if facade is entrance else 0.0,
            )
        if facade.index == 0 or facade.index in described:
            wall_family = palette.record(wall.get("material"))["family"]
        blocked = facades_module.blocked_region(facade, neighbours) if neighbours else None
        facades_module.build(soup, facade, wall, doc, palette, report, blocked, templates)
    roofs.build(
        soup,
        shell,
        priors.resolve_roof(doc),
        palette,
        priors.trim_colour(doc),
        wall_family,
        facade_by_wall,
        report,
        doc,
    )

    parts = []
    for part, key, positions, normals, uvs, extra, triangles in soup.arrays():
        record = palette.records[key]
        colour, roughness, metalness = fallback(record)
        material = Material(
            record["family"],
            {k: v for k, v in record.items() if k != "family"},
            colour,
            roughness,
            metalness,
        )
        engine = np.column_stack([positions[:, 0], positions[:, 2], -positions[:, 1]])
        engine_normals = np.column_stack([normals[:, 0], normals[:, 2], -normals[:, 1]])
        attributes = {"GLASS": extra.astype(np.float32)} if extra is not None else {}
        parts.append(
            Part(
                part,
                engine.astype(np.float32),
                triangles.astype(np.uint32),
                material,
                engine_normals.astype(np.float32),
                uvs.astype(np.float32),
                None,
                attributes,
            )
        )
    engine_origin = (float(origin[0] - ORIGIN_E), float(origin[2]), float(-(origin[1] - ORIGIN_N)))
    return Output(engine_origin, parts, [], report)

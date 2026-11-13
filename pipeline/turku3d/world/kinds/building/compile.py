"""compile: an effective building description becomes mesh parts and material records.

Pure and deterministic: everything it needs is in the description (its shell included). The result follows the
world-pipeline contract: engine axes relative to an origin, metric UVs, one part per (part name, material).
"""

import copy
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import box

from . import archetypes, mesh, passages, priors, roofs
from . import facade as facades_module
from . import shell as shell_module
from .description import expand_storeys
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
    if doc["shell"].get("passages"):
        doc["shell"]["passages"] = passages.localise(doc["shell"]["passages"], origin)
    premises = doc.get("register", {}).get("premises")
    if premises:
        doc["register"]["premises"] = (np.asarray(premises, float) - origin[:2]).tolist()
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


def wall_roles(doc: dict, shell) -> dict:
    """{facade index: role} of a building nobody described a wall of (archetypes.wall_roles): what
    `priors.fitted_wall` takes as its `role`. `doc` and `shell` (shell.read of its shell) are in one frame."""
    free = []
    neighbours = doc["shell"].get("neighbours", [])
    for facade in shell.facades:
        if facade.grounded:
            eaves = facades_module.eaves_height(facade.outline, facade.width)
            # How much of the wall's ground storey stands against a touching neighbour (a party wall: no door
            # or shopfront opens there).
            blocked = facades_module.blocked_region(facade, neighbours) if neighbours else None
            party = 0.0
            if blocked is not None:
                party = float(blocked.intersection(box(0.0, 0.0, facade.width, 1.0)).area)
            corners = [c for part in mesh.polygons_of(facade.outline) for c in part.exterior.coords]
            if not corners:
                continue
            apex = max(corners, key=lambda c: c[1])
            free.append(
                {
                    "index": facade.index,
                    "width": facade.width,
                    "rise": float(apex[1] - eaves),
                    "apex": float(apex[0]),
                    "height": float(eaves - (facade.ground[:, 1].min() if len(facade.ground) else 0.0)),
                    "p0": [float(v) for v in facade.p0],
                    "p1": [float(v) for v in facade.p1],
                    "normal": [float(v) for v in facade.normal[:2]],
                    "party_m": round(party, 2),
                }
            )
    return archetypes.wall_roles(doc, priors.style_type(doc), free, priors.group(doc)["pitch"])


CORNER_M = 8.0  # the longest undescribed wall that takes its described neighbour's cladding
CORNER_RUN_M = 14.0  # how far such walls chain away from the described wall


def _corner_walls(shell, described: dict) -> dict:
    """{facade index: index of the described facade it continues}: undescribed grounded walls up to CORNER_M long
    whose end meets (within 0.6 m) the end of a described wall, or of such a wall, at most CORNER_RUN_M away."""
    if not described:
        return {}
    ends = {f.index: (np.asarray(f.p0[:2], float), np.asarray(f.p1[:2], float)) for f in shell.facades}
    reached = {index: (index, 0.0) for index in described}
    frontier = list(described)
    while frontier:
        nxt = []
        for index in frontier:
            source, run = reached[index]
            for facade in shell.facades:
                if facade.index in reached or not facade.grounded or facade.width > CORNER_M:
                    continue
                if run + facade.width > CORNER_RUN_M:
                    continue
                gap = min(float(np.linalg.norm(a - b)) for a in ends[facade.index] for b in ends[index])
                if gap <= 0.6:
                    reached[facade.index] = (source, run + facade.width)
                    nxt.append(facade.index)
        frontier = nxt
    return {index: source for index, (source, _run) in reached.items() if index not in described}


def _ground_storey_top(wall: dict) -> float | None:
    """The first floor's level over the wall base where a wall has more than one storey."""
    storeys = expand_storeys(wall.get("storeys", []))
    if len(storeys) < 2:
        return None
    return wall.get("ground_floor_m", wall.get("plinth", {}).get("height_m", 0.0)) + storeys[0].get("height_m", 3.0)


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
    # A frame read as one wall may run over further shell walls (a lower wing, a step in the eaves): each one
    # nobody described that lies mostly under the frame takes the same description, and the openings that fall on it.
    shared = set()
    for wall in doc.get("walls", []):
        frame = wall.get("frame")
        if (
            not frame
            or described.get(getattr(facade_by_wall.get(wall.get("id")), "index", None)) is not wall
        ):
            continue
        for other in shell_module.matches(shell.facades, frame["p0"], frame["p1"])[1:]:
            a = float((np.asarray(frame["p0"], float) - other.origin[:2]) @ other.u_dir[:2])
            b = float((np.asarray(frame["p1"], float) - other.origin[:2]) @ other.u_dir[:2])
            overlap = min(b, other.width) - max(a, 0.0)
            if other.index not in described and overlap >= max(1.5, 0.6 * other.width):
                described[other.index] = wall
                shared.update((other.index, facade_by_wall[wall["id"]].index))
    corners = _corner_walls(shell, described)
    # Passages and arcades through the ground storey (passages.py): where each facade opens.
    default = priors.resolve_wall(doc, None)
    opened = passages.read(doc, shell, _ground_storey_top(default))
    cuts = passages.cuts(shell, opened)
    templates = {}
    neighbours = doc["shell"].get("neighbours", [])
    wall_family = "plaster"
    # What each wall of a building nobody described carries besides its windows: the street door, garage doors,
    # a gable window (archetypes.wall_roles). A building somebody described in part keeps plain automatic walls.
    roles = {} if described else wall_roles(doc, shell)
    # A described wall's hidden rectangles (`observed.hidden_m`: nobody saw them) take the openings the automatic
    # tier would give that wall, with the roles it would give it (shopfronts at mapped premises, the street door).
    hiding = any((described[i].get("observed") or {}).get("hidden_m") for i in described)
    fill_roles = wall_roles(doc, shell) if hiding else {}
    for facade in shell.facades:
        wall = priors.resolve_wall(doc, described.get(facade.index))
        if facade.index in corners:
            # A short wall nobody described between or beside described ones (a rounded or cut corner, a step in
            # the front) is clad like the described wall it continues, not in the reader's default for the rest.
            like = priors.resolve_wall(doc, described[corners[facade.index]])
            wall = {**wall, "material": like["material"], "plinth": like["plinth"]}
        if not facade.grounded:
            wall = priors.raised_wall(wall, facade.index in described)
        elif facade.index not in described:
            foot = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
            wall = priors.fitted_wall(
                doc,
                wall,
                facades_module.eaves_height(facade.outline, facade.width) - foot,
                roles.get(facade.index),
            )
        elif (wall.get("observed") or {}).get("hidden_m"):
            foot = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
            wall = {
                **wall,
                "fill": priors.fitted_wall(
                    doc,
                    priors.resolve_wall(doc, None),
                    facades_module.eaves_height(facade.outline, facade.width) - foot,
                    fill_roles.get(facade.index),
                ),
            }
        if facade.index == 0 or facade.index in described:
            wall_family = palette.record(wall.get("material"))["family"]
        blocked = facades_module.blocked_region(facade, neighbours) if neighbours else None
        facades_module.build(
            soup, facade, wall, doc, palette, report, blocked, templates, facade.index in shared,
            cuts.get(facade.index),
        )
    passages.interior(soup, shell, opened, cuts, palette, palette.key(default.get("material")))
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

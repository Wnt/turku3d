"""Building sources and the stand-in building kind of the walking skeleton.

`discover` is the source side of the `building` kind and stays: one entity per LoD2 building, with the facts of
docs/specs/building-description.md that always come from sources (`shell`, `register`). `describe` and `compile`
here are the stand-in until the generator of `w2-buildings` (world/kinds/building/) takes the registry row: the
LoD2 wall and roof polygons as they are, coloured from the register's facade material and year.
"""

from __future__ import annotations

import re

import numpy as np
import shapely

from ..api import Area, Material, Output, Part, Stub, envelope
from ..frame import to_engine
from ..geometry import merge, seed, triangulate_planar

SURFACES = {"WallSurface": "wall", "RoofSurface": "roof", "GroundSurface": "ground", "ClosureSurface": "closure"}
TOUCH = 0.3  # buildings closer than this share a party wall
# Linear RGB wall colours by register facade material, a few per material: picked per building by its id.
WALLS = {
    "puu": ("wood-siding", [(0.62, 0.50, 0.24), (0.55, 0.20, 0.13), (0.42, 0.47, 0.36), (0.66, 0.62, 0.50), (0.35, 0.42, 0.50)]),
    "tiili": ("brick", [(0.36, 0.13, 0.08), (0.42, 0.19, 0.11), (0.50, 0.33, 0.20)]),
    "betoni": ("concrete", [(0.55, 0.54, 0.50), (0.62, 0.58, 0.50), (0.48, 0.48, 0.47)]),
    "kivi": ("plaster", [(0.60, 0.52, 0.40), (0.58, 0.56, 0.50)]),
    "metallilevy": ("metal-cladding", [(0.45, 0.47, 0.50), (0.30, 0.33, 0.36)]),
    "lasi": ("glass", [(0.20, 0.27, 0.32)]),
}
OLD_PLASTER = [(0.66, 0.55, 0.36), (0.62, 0.47, 0.34), (0.60, 0.58, 0.50), (0.55, 0.38, 0.28), (0.52, 0.54, 0.46)]
NEW_PLASTER = [(0.66, 0.64, 0.58), (0.60, 0.57, 0.48), (0.56, 0.52, 0.44)]


def ring_points(ring) -> list[list[float]]:
    """A shapely ring as open [E, N, H] rows in millimetres."""
    coords = np.asarray(ring.coords)[:-1]
    if coords.shape[1] == 2:
        coords = np.column_stack([coords, np.zeros(len(coords))])
    return np.round(coords, 3).tolist()


def year_of(attrs: dict) -> int | None:
    for name in ("yearOfConstruction", "valmistunut"):
        match = re.search(r"(1[5-9]\d\d|20\d\d)", attrs.get(name, ""))
        if match:
            return int(match.group(1))
    return None


def number(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def discover(area: Area, sources) -> list[Stub]:
    """One stub per LoD2 building that touches the area: `shell` and `register` as the building description has them."""
    # Neighbours of a building at the rim lie outside the circle: read a margin, select by the circle.
    box = area.bbox(60.0)
    register = {}
    for feature in sources.wfs.features("kanta:Rakennus", box):
        if feature.attrs.get("rakennustunnus"):
            register[feature.attrs["rakennustunnus"]] = feature.attrs
    found = []
    for feature in sources.wfs.features("bldg:Building_LOD2", box):
        surfaces, ground = [], []
        for geom in feature.geoms:
            kind = SURFACES.get(geom.surface_type)
            if geom.role != "lod2MultiSurface" or geom.type != "Polygon" or not kind:
                continue
            surfaces.append({"kind": kind, "rings": [ring_points(geom.shape.exterior), *(ring_points(r) for r in geom.shape.interiors)]})
            if kind == "ground":
                ground.append(shapely.make_valid(shapely.force_2d(geom.shape)))
        walls = [np.array(s["rings"][0]) for s in surfaces if s["kind"] == "wall"]
        if not walls:
            continue
        points = np.concatenate(walls)
        outline = shapely.unary_union(ground) if ground else shapely.MultiPoint(points[:, :2]).convex_hull
        if outline.is_empty:
            continue
        prt = feature.attrs.get("pysyvarakennustunnus")
        key = prt or "gml-" + re.sub(r"[^A-Za-z0-9._-]", "-", feature.gml_id or feature.key)
        found.append({"id": f"building/{key}", "prt": prt, "feature": feature, "surfaces": surfaces, "outline": outline,
                      "base": float(points[:, 2].min()), "top": float(points[:, 2].max())})
    tree = shapely.STRtree([b["outline"] for b in found])
    centre, stubs = shapely.Point(area.e, area.n), []
    for index, building in enumerate(found):
        outline, attrs = building["outline"], building["feature"].attrs
        if shapely.distance(outline, centre) > area.radius:
            continue
        terrain = []
        for part in shapely.get_parts(outline):
            if part.geom_type == "Polygon":
                edge = shapely.get_coordinates(shapely.segmentize(part.exterior, 2.0))[:-1]
                heights = sources.terrain.heights(edge[:, 0], edge[:, 1])
                if np.isfinite(heights).all():
                    terrain.append(np.round(np.column_stack([edge, heights]), 3).tolist())
        neighbours = []
        for other in tree.query(outline, predicate="dwithin", distance=TOUCH):
            if other != index:
                near = found[other]
                largest = max((p for p in shapely.get_parts(near["outline"]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
                if largest is not None:
                    neighbours.append({"id": near["id"], "footprint": np.round(np.asarray(largest.exterior.coords)[:-1], 3).tolist(),
                                       "base_h": round(near["base"], 3), "top_h": round(near["top"], 3)})
        listed = register.get(building["prt"], {})
        storeys, height = number(attrs.get("kerrosluku") or attrs.get("storeysAboveGround")), number(attrs.get("measuredHeight"))
        facts = {
            "shell": {"source": "turku-lod2", "surfaces": building["surfaces"], "terrain": terrain,
                      "neighbours": sorted(neighbours, key=lambda n: n["id"])},
            "register": {name: value for name, value in {
                "address": " ".join(filter(None, [attrs.get("osoitenimi_fi"), attrs.get("osoitenumero")])),
                "year": year_of(attrs),
                "storeys": int(storeys) if storeys and storeys >= 1 else None,
                "use": attrs.get("käyttötarkoitus") or attrs.get("function"),
                "material": listed.get("julkisivumateriaali"),
                "height_m": height if height and height > 0 else None,
            }.items() if value not in (None, "")},
        }
        refs = [{"source": "turku-lod2", "key": building["feature"].gml_id or building["feature"].key}]
        if listed:
            refs.append({"source": "turku-kanta-rakennus", "key": building["prt"]})
        stubs.append(Stub(building["id"], tuple(round(v, 3) for v in outline.bounds), refs, facts))
    return stubs


class ShellBuilding:
    """The stand-in: `turku-description/building/1` with only what the sources give, compiled as the bare shell."""

    name = "building"

    def discover(self, area: Area, sources) -> list[Stub]:
        return discover(area, sources)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        description = {**envelope(self.name, 1, stub, stored, "w2-core"), **stub.facts}
        for name, value in (stored or {}).items():
            if name not in ("schema", "id", "tier", "provenance", "shell", "register"):
                description[name] = value
        return description

    def compile(self, description: dict) -> Output:
        shell, register = description["shell"], description.get("register", {})
        pick = seed(description["id"])
        family, palette = WALLS.get(register.get("material", ""), (None, None))
        if not family:
            family, palette = "plaster", OLD_PLASTER if register.get("year", 1960) < 1940 else NEW_PLASTER
        looks = {"wall": (family, palette[pick % len(palette)], 0.9), "roof": ("roof-sheet", (0.10, 0.10, 0.11), 0.55)}
        drawn = [s for s in shell["surfaces"] if s["kind"] in looks]
        every = np.concatenate([np.array(r) for s in drawn for r in s["rings"]])
        ground = [p[2] for line in shell.get("terrain", []) for p in line]
        base = min([float(every[:, 2].min()), *ground])
        origin_e, origin_n = np.round(every[:, :2].mean(axis=0), 2)
        origin = to_engine(float(origin_e), float(origin_n), round(base, 2))
        meshes: dict[str, list] = {"wall": [], "roof": []}
        for surface in drawn:
            local = []
            for ring in surface["rings"]:
                x, y, z = to_engine(*np.array(ring).T)
                local.append(np.column_stack([x - origin[0], y - origin[1], z - origin[2]]))
            mesh = triangulate_planar(local)
            if mesh is None:
                continue
            points, faces, _normal, uv = mesh
            if surface["kind"] == "roof":
                uv = np.column_stack([points[:, 0] + origin[0], points[:, 2] + origin[2]])
            meshes[surface["kind"]].append((points, faces, uv))
        parts = []
        for kind, name in (("wall", "walls"), ("roof", "roof")):
            if meshes[kind]:
                family, color, roughness = looks[kind]
                positions, faces, uvs = merge(meshes[kind])
                parts.append(Part(name, positions, faces, Material(family, {"color": list(color)}, tuple(color), roughness), uvs=uvs))
        return Output(origin, parts)

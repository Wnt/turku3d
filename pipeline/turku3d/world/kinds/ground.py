"""Stand-in ground kind of the walking skeleton: lidar terrain per 62.5 m cell, split by the city's surface polygons.

`w2-street` replaces this module (kerbs, markings, measured cross-sections, water). A cell is the planar
arrangement of a 2.5 m grid and the street-part and green-area polygon edges; every face takes the surface of the
polygon that contains it and every vertex its lidar height, so cells and surfaces meet without gaps.
"""

from __future__ import annotations

import numpy as np
import shapely

from ..api import Area, Heightfield, Material, Output, Part, Stub, envelope
from ..frame import CELL, ORIGIN_E, ORIGIN_N, cell_at, cell_box_en, cell_from_key, cell_key
from ..geometry import earcut

STEP = 2.5
NODES = int(CELL / STEP) + 1  # 26 nodes per side; the height grid has one more ring around them for normals
LAYERS = (("GIS:Katualueet", "street", "Katuosalaji"), ("GIS:Viheralueet", "green", "Viheralueosalaji"))

# Surface material text of the registers -> (family, linear RGB, roughness). First match wins.
SURFACES = (
    ("punainen", "asphalt-red", (0.26, 0.09, 0.06), 0.9),
    ("asfaltti", "asphalt", (0.085, 0.085, 0.09), 0.92),
    ("betonilaatta", "paving-slab", (0.34, 0.33, 0.31), 0.85),
    ("betonikivi", "concrete-pavers", (0.30, 0.28, 0.26), 0.85),
    ("noppakivi", "setts", (0.27, 0.26, 0.25), 0.8),
    ("nupukivi", "cobble", (0.24, 0.23, 0.22), 0.8),
    ("kenttäkivi", "fieldstone", (0.26, 0.24, 0.21), 0.85),
    ("graniitti", "granite", (0.36, 0.34, 0.32), 0.7),
    ("kivituhka", "gravel", (0.36, 0.32, 0.26), 0.95),
    ("kivi", "granite", (0.36, 0.34, 0.32), 0.7),
    ("sora", "gravel", (0.36, 0.32, 0.26), 0.95),
    ("murske", "gravel", (0.36, 0.32, 0.26), 0.95),
    ("hiekka", "sand", (0.45, 0.39, 0.28), 0.95),
    ("betoni", "concrete", (0.40, 0.39, 0.37), 0.85),
    ("nurmikko", "grass", (0.085, 0.15, 0.04), 0.95),
    ("niitty", "meadow", (0.12, 0.17, 0.05), 0.95),
    ("metsä", "forest-floor", (0.07, 0.10, 0.04), 0.95),
    ("kasvillisuus", "planting", (0.07, 0.12, 0.04), 0.95),
)
DEFAULTS = {"street": ("asphalt", (0.085, 0.085, 0.09), 0.92), "green": ("grass", (0.085, 0.15, 0.04), 0.95),
            None: ("unmapped", (0.19, 0.18, 0.15), 0.95)}


def surface_material(source: str | None, use: str, material: str) -> Material:
    text = (material or "").lower()
    family, color, roughness = next(((f, c, r) for word, f, c, r in SURFACES if word in text), DEFAULTS[source])
    return Material(family, {"use": use, "surface": material} if source else {}, color, roughness)


def polygons(shape) -> list:
    return [p for p in shapely.get_parts(shape) if p.geom_type == "Polygon" and p.area > 0.01]


class Ground:
    name = "ground"

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL)
        shapes, rows = [], []
        for layer, source, use_field in LAYERS:
            for feature in sources.wfs.features(layer, box):
                for geom in feature.geoms:
                    if geom.type == "Polygon":
                        shapes.append(shapely.make_valid(shapely.force_2d(geom.shape)))
                        rows.append({"source": source, "use": feature.attrs.get(use_field, ""),
                                     "surface": feature.attrs.get("Pintamateriaali", "")})
        tree = shapely.STRtree(shapes)
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                # Height nodes: row 0 one step north of the cell, column 0 one step west of it.
                steps = (np.arange(NODES + 2) - 1) * STEP
                e, n = np.meshgrid(cell[0] + steps, cell[3] - steps)
                heights = sources.terrain.heights(e.ravel(), n.ravel())
                if not np.isfinite(heights).all():
                    continue
                clip, surfaces = shapely.box(*cell), []
                for k in sorted(tree.query(clip, predicate="intersects")):
                    for part in polygons(shapely.intersection(shapes[k], clip)):
                        rings = [np.round(np.asarray(r.coords)[:-1], 3).tolist() for r in (part.exterior, *part.interiors)]
                        surfaces.append({**rows[k], "rings": rings})
                facts = {"cell": [i, j], "step": STEP, "heights": np.round(heights, 3).tolist(), "surfaces": surfaces}
                refs = [{"source": "turku-lidar:maanpinta", "key": cell_key(i, j)},
                        *({"source": f"turku-wfs:{layer}", "key": cell_key(i, j)} for layer, _, _ in LAYERS)]
                stubs.append(Stub(f"ground/{cell_key(i, j)}", cell, refs, facts))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        description = {**envelope(self.name, 0, stub, stored, "w2-core stand-in"), **stub.facts}
        for name, value in (stored or {}).items():
            if name not in ("schema", "id", "tier", "provenance"):
                description[name] = value
        return description

    def compile(self, description: dict) -> Output:
        i, j = description["cell"] if "cell" in description else cell_from_key(description["id"].split("/")[1])
        e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
        grid = np.array(description["heights"]).reshape(NODES + 2, NODES + 2)
        base = float(np.floor(grid.min()))

        # Surface polygons in cell-local engine metres (x east, z south), streets before green areas.
        shapes, looks = [], []
        ordered = sorted(description["surfaces"], key=lambda s: s["source"] != "street")
        for surface in ordered:
            rings = [np.column_stack([np.array(r)[:, 0] - e0, n1 - np.array(r)[:, 1]]) for r in surface["rings"]]
            shape = shapely.set_precision(shapely.Polygon(rings[0], rings[1:]), 0.001)
            for part in polygons(shape):
                shapes.append(part)
                looks.append(surface)
        ticks = np.arange(NODES) * STEP
        lines = [shapely.LineString([(t, 0.0), (t, CELL)]) for t in ticks] + [shapely.LineString([(0.0, t), (CELL, t)]) for t in ticks]
        lines += [s.boundary for s in shapes]
        faces = shapely.get_parts(shapely.polygonize(list(shapely.get_parts(shapely.unary_union(lines)))))
        inside = shapely.box(0, 0, CELL, CELL)
        faces = faces[shapely.covered_by(shapely.point_on_surface(faces), inside)]
        owner = np.full(len(faces), -1)
        if shapes:
            hit_face, hit_shape = shapely.STRtree(shapes).query(shapely.point_on_surface(faces), predicate="within")
            for f, s in sorted(zip(hit_face.tolist(), hit_shape.tolist()), reverse=True):
                owner[f] = s  # the lowest shape index (streets first) wins

        groups: dict[int, list[np.ndarray]] = {}
        for face, look in zip(faces, owner):
            rings = [np.asarray(face.exterior.coords)[:-1], *(np.asarray(r.coords)[:-1] for r in face.interiors)]
            # shapely rings: exterior clockwise in a y-down plane is counter-clockwise seen from above; earcut fixes winding.
            points = np.concatenate(rings)
            groups.setdefault(int(look), []).append(points[earcut(rings)])

        def height(points: np.ndarray, source: np.ndarray) -> np.ndarray:
            u, v = points[:, 0] / STEP + 1, points[:, 1] / STEP + 1
            c, r = np.clip(np.floor(u).astype(int), 0, NODES), np.clip(np.floor(v).astype(int), 0, NODES)
            fu, fv = (u - c)[:, None], (v - r)[:, None]
            source = source.reshape(NODES + 2, NODES + 2, -1)
            out = (source[r, c] * (1 - fu) * (1 - fv) + source[r, c + 1] * fu * (1 - fv)
                   + source[r + 1, c] * (1 - fu) * fv + source[r + 1, c + 1] * fu * fv)
            return out[:, 0] if out.shape[1] == 1 else out

        slope_z, slope_x = np.gradient(grid, STEP)
        node_normals = np.dstack([-slope_x, np.ones_like(grid), -slope_z])
        by_material: dict[str, tuple[Material, list[np.ndarray]]] = {}
        for look, triangles in groups.items():
            row = looks[look] if look >= 0 else None
            material = surface_material(row and row["source"], row["use"] if row else "", row["surface"] if row else "")
            by_material.setdefault(material.family, (material, []))[1].extend(triangles)
        parts = []
        for family in sorted(by_material):
            material, triangles = by_material[family]
            corners = np.concatenate(triangles).reshape(-1, 2)
            # earcut's counter-clockwise is in (x, z) with z south: seen from above that is clockwise, so flip.
            corners = corners.reshape(-1, 3, 2)[:, ::-1].reshape(-1, 2)
            unique, index = np.unique(np.round(corners * 1000).astype(np.int64), axis=0, return_inverse=True)
            points = unique / 1000.0
            normals = height(points, node_normals)
            normals /= np.linalg.norm(normals, axis=1, keepdims=True)
            positions = np.column_stack([points[:, 0], height(points, grid) - base, points[:, 1]])
            uvs = np.column_stack([points[:, 0] + i * CELL, points[:, 1] + j * CELL])
            parts.append(Part(family, positions, index.reshape(-1, 3), material, normals=normals, uvs=uvs))
        return Output((i * CELL, base, j * CELL), parts, heightfield=Heightfield((i * CELL, j * CELL), STEP, grid[1:-1, 1:-1]))

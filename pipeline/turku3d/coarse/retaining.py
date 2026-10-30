"""Mapped retaining faces and continuous, disjoint terrain strips beside them."""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces

from .bridge_details import appearance
from .geometry import clip_mesh


def sections(line, spacing=2.0, radius=2.5):
    """Shared mitered cross sections, including every mapped bend."""
    coords = np.asarray(line.coords)[:, :2]
    lengths = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    coords = coords[np.r_[True, lengths > 1e-7]]
    lengths = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    if not len(lengths):
        return None
    directions = np.diff(coords, axis=0) / lengths[:, None]
    normals = np.c_[-directions[:, 1], directions[:, 0]]
    offsets = (
        np.vstack(
            [
                normals[0],
                (normals[:-1] + normals[1:])
                / np.maximum(1 + (normals[:-1] * normals[1:]).sum(axis=1), 0.1)[:, None],
                normals[-1],
            ]
        )
        * radius
    )
    centres, sides = [], []
    for i, length in enumerate(lengths):
        t = np.linspace(0, 1, max(1, int(np.ceil(length / spacing))) + 1)[:-1]
        centres.extend(coords[i] + t[:, None] * (coords[i + 1] - coords[i]))
        sides.extend(offsets[i] + t[:, None] * (offsets[i + 1] - offsets[i]))
    centres, sides = np.array([*centres, coords[-1]]), np.array([*sides, offsets[-1]])
    return centres, centres - sides, centres + sides


def produce(tile, records, sample, stairs=(), water=()):
    from .tunnels import _polygon_faces

    meshes, openings = [], []
    stair_mask = shapely.union_all(stairs)
    shore = shapely.union_all(water).buffer(2.5)
    window = shapely.box(*tile.bbox.as_tuple()).buffer(3)
    claimed = shapely.GeometryCollection()
    for index, (geom, tags, _) in enumerate(records):
        if tags.get("barrier") not in ("retaining_wall", "wall", "city_wall"):
            continue
        if geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        faces, ground, cuts = Faces(), Faces(), []
        for line in shapely.get_parts(geom.difference(shore)):
            if line.geom_type != "LineString" or line.is_empty:
                continue
            profile = sections(line)
            if profile is None:
                continue
            centres, left, right = profile
            levels = np.array([[sample(*p) for p in side] for side in (left, right)])
            for j, (p, q) in enumerate(zip(centres[:-1], centres[1:], strict=True)):
                segment = shapely.LineString([p, q])
                if not segment.intersects(window) or segment.length < 1e-7:
                    continue
                local = levels[:, j : j + 2]
                if abs(np.mean(local[1] - local[0])) < 0.6:
                    continue
                top, bottom = np.maximum(*local), np.minimum(*local)
                remaining = segment.difference(stair_mask)
                if remaining.is_empty:
                    continue
                for part in shapely.get_parts(remaining):
                    if part.geom_type != "LineString":
                        continue
                    ends = np.asarray(part.coords)[[0, -1]]
                    t = np.clip((ends - p) @ (q - p) / segment.length**2, 0, 1)
                    lo, hi = np.interp(t, [0, 1], bottom), np.interp(t, [0, 1], top)
                    wall = np.array(
                        [
                            [*ends[0, :1], lo[0], ends[0, 1]],
                            [*ends[1, :1], lo[1], ends[1, 1]],
                            [*ends[1, :1], hi[1], ends[1, 1]],
                            [*ends[0, :1], hi[0], ends[0, 1]],
                        ]
                    )
                    faces.face(wall)
                    faces.face(wall[::-1])
                for side, edge in enumerate((left, right)):
                    outer = edge[j : j + 2]
                    xz = np.array([p, q, outer[1], outer[0]])
                    heights = local[side, [0, 1, 1, 0]]
                    # Triangulate the original strip before clipping. Every new
                    # corner stays on its parent plane, including at a stair cut.
                    for ids in ([0, 1, 2], [0, 2, 3]):
                        tri = xz[ids]
                        polygon = shapely.Polygon(tri)
                        if polygon.area < 1e-8:
                            continue
                        polygon = polygon.difference(stair_mask).difference(claimed)
                        if polygon.is_empty:
                            continue
                        coeff = np.linalg.solve(np.c_[tri, np.ones(3)], heights[ids])
                        _polygon_faces(
                            ground, polygon, lambda v, c=coeff: np.c_[v, np.ones(len(v))] @ c
                        )
                        cuts.append(polygon)
                        claimed = shapely.union_all([claimed, polygon])
        if faces.indices:
            mesh = clip_mesh(
                appearance(
                    faces.mesh(f"retaining:{index}", "wall"),
                    "rock" if tags.get("natural") == "cliff" else "stone",
                ),
                tile.bbox,
            )
            if mesh is not None:
                meshes.append(mesh)
        if ground.indices:
            mesh = clip_mesh(
                ground.mesh(
                    f"retaining:{index}:terrain", "paving_slab", metadata={"terrain": True}
                ),
                tile.bbox,
            )
            if mesh is not None:
                meshes.append(mesh)
                openings.extend(cuts)
    return meshes, openings


def terrain(meshes, original):
    """Keep the ground's materials on wall-side strips; walls only own vertical faces."""
    from turku3d.ground.mesh import Mesh
    from .banks import join

    patches = [m for m in meshes if m.metadata.get("terrain")]
    if not patches:
        return None
    tree = shapely.STRtree(shapely.polygons(original.positions[original.indices][:, :, [0, 2]]))
    result = []
    for patch in patches:
        centre = patch.positions[patch.indices].mean(axis=1)
        owners = tree.nearest(shapely.points(centre[:, [0, 2]]))
        result.append(Mesh(patch.positions, patch.indices, original.material[owners]))
    return join(*result)

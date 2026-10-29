"""Mapped kerb lines: narrow granite strips with matching collision, never inferred road edges."""

import json
import math

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces

from .geometry import clip_mesh


def prepare(tile, path):
    from turku3d.spatial import rows

    lines, lowered = [], []
    window = shapely.box(*tile.bbox.buffered(2).as_tuple())
    for _, row in rows(path, tile.bbox.buffered(2)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("barrier") != "kerb":
            continue
        if (
            tags.get("bridge", "no") != "no"
            or tags.get("tunnel", "no") != "no"
            or tags.get("layer", "0") not in ("0", "")
        ):
            continue
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        if not geom.intersects(window):
            continue
        if tags.get("kerb") in ("lowered", "flush"):
            lowered.append(geom.buffer(1.5 if geom.geom_type == "Point" else 0.25))
            continue
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            geom = geom.boundary
        if geom.geom_type in ("LineString", "MultiLineString"):
            lines.append(geom)
    # OSM closed ways also occur as area records: union prevents duplicate stones/colliders.
    linework = shapely.union_all(lines)
    if lowered:
        linework = linework.difference(shapely.union_all(lowered))
    return linework


def height_sampler(tile, heights, root, sources):
    """Use the owning tile's measured grid on both sides of a visual-tile seam."""
    from turku3d import config as C

    from .inputs import _ABSENT, _entry, _load, _prepare_one, _save
    from .surfaces import grid_height

    grids = {tile.id: heights}

    def sample(x, z):
        b = tile.bbox
        owner = tile if b.xmin <= x <= b.xmax and b.zmin <= z <= b.zmax else C.VisualTile.at(x, z)
        if owner.id not in grids:
            paths, missing = sources.lidar_paths(owner)
            if missing:
                raise ValueError(f"Kerb seam needs ground inputs for {owner.id}")
            path, key = _entry(root, owner, "ground", paths)
            data = _load(path, key)
            if data is _ABSENT:
                data = _prepare_one(owner, "ground", paths)
                _save(path, key, data)
            grids[owner.id] = data["heights"]
        return grid_height(owner, grids[owner.id], x, z)

    return sample


def produce(tile, linework, height_at, water, buildings, markings):
    from .inputs import CELLS

    if linework is None or linework.is_empty:
        return []
    blocked = shapely.union_all(
        [*water, *(b.footprint for b in buildings), *(p.buffer(0.3) for p in markings)]
    )
    clipped = linework.difference(blocked)
    window = shapely.box(*tile.bbox.buffered(0.2).as_tuple())
    bounds = tile.bbox.as_tuple()
    clip_low, clip_high = np.array(bounds[:2]), np.array(bounds[2:])
    faces = Faces()
    step = tile.size / CELLS
    for line in getattr(clipped, "geoms", [clipped]):
        if line.geom_type != "LineString":
            continue
        coords = np.asarray(line.coords)
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            length = np.linalg.norm(b - a)
            if length < 0.01:
                continue
            direction = (b - a) / length
            normal = np.array([-direction[1], direction[0]]) * 0.09
            for low, high in zip(
                np.linspace(0, length, math.ceil(length / 2) + 1)[:-1],
                np.linspace(0, length, math.ceil(length / 2) + 1)[1:],
                strict=True,
            ):
                p, q = a + direction * low, a + direction * high
                if not shapely.LineString([p, q]).intersects(window):
                    continue
                # Mapped kerb semantics, with an explicitly unmeasured 12 cm raised profile.
                xz = np.array([p - normal, q - normal, q + normal, p + normal])
                # Each top patch stays on one terrain plane, rather than
                # bridging a grid diagonal or a sharp bank between corners.
                for patch in terrain_patches(xz, step, bounds):
                    patch = np.clip(patch, clip_low, clip_high)
                    top = np.c_[patch[:, 0], [height_at(*v) + 0.12 for v in patch], patch[:, 1]]
                    faces.face(top[::-1], top[::-1][:, [0, 2]])
                for i in range(4):
                    j = (i + 1) % 4
                    clipped_edge = shapely.LineString([xz[i], xz[j]]).intersection(
                        shapely.box(*bounds)
                    )
                    if clipped_edge.geom_type != "LineString" or clipped_edge.length < 1e-9:
                        continue
                    endpoints = np.clip(shapely.get_coordinates(clipped_edge), clip_low, clip_high)
                    edge = terrain_edge(endpoints[0], endpoints[-1], step)
                    for edge_start, edge_end in zip(edge[:-1], edge[1:], strict=True):
                        ha, hb = height_at(*edge_start), height_at(*edge_end)
                        wall = np.array(
                            [
                                [edge_start[0], ha - 0.02, edge_start[1]],
                                [edge_end[0], hb - 0.02, edge_end[1]],
                                [edge_end[0], hb + 0.12, edge_end[1]],
                                [edge_start[0], ha + 0.12, edge_start[1]],
                            ]
                        )
                        u0, u1 = (np.linalg.norm(v - xz[i]) for v in (edge_start, edge_end))
                        faces.face(
                            wall[::-1],
                            np.array([[u0, 0], [u1, 0], [u1, 0.14], [u0, 0.14]]),
                        )
    if not faces.indices:
        return []
    mesh = faces.mesh(
        f"kerbs_{tile.id}", "curb", metadata=dict(height_source="mapped kerb; default 0.12 m")
    )
    result = clip_mesh(mesh, tile.bbox)
    return [result] if result else []


def terrain_patches(corners, step, bounds=None):
    """Convex strip pieces inside the same regular triangles as visual/collision ground."""
    strip = shapely.make_valid(shapely.Polygon(corners))
    if bounds is not None:
        strip = strip.intersection(shapely.box(*bounds))
    if strip.is_empty:
        return
    extent = np.asarray(strip.bounds).reshape(2, 2)
    lo, hi = np.floor(extent / step).astype(int)
    i, j = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1), indexing="ij")
    a = np.c_[i.ravel(), j.ravel()] * step
    b, c, d = a + [step, 0], a + [0, step], a + step
    triangles = np.stack([np.stack([a, b, c], axis=1), np.stack([b, d, c], axis=1)], axis=1)
    clipped = shapely.intersection(strip, shapely.polygons(triangles.reshape(-1, 3, 2)))
    pieces = shapely.get_parts(clipped)
    pieces = pieces[(shapely.get_type_id(pieces) == 3) & (shapely.area(pieces) > 1e-9)]
    for piece in pieces:
        yield np.asarray(shapely.geometry.polygon.orient(piece, 1).exterior.coords)[:-1]


def terrain_edge(a, b, step):
    """Split an edge at grid rows, columns and their shared descending diagonal."""
    cuts = {0.0, 1.0}
    for start, end in zip([*a, a.sum()], [*b, b.sum()], strict=True):
        if abs(end - start) < 1e-12:
            continue
        for k in range(math.floor(min(start, end) / step) + 1, math.ceil(max(start, end) / step)):
            t = (k * step - start) / (end - start)
            if 1e-10 < t < 1 - 1e-10:
                cuts.add(t)
    return a + np.asarray(sorted(cuts))[:, None] * (b - a)


def visuals(tile, meshes):
    return [
        GlbMesh(
            m.object_id,
            m.positions - tile.origin,
            m.indices,
            uvs=m.uvs,
            color=(1, 1, 1, 1),
            extras=dict(
                layer="structures",
                object_id=m.object_id,
                material_key="draft_granite_curb",
                kind="curb",
            ),
        )
        for m in meshes
    ]


def collision(cell, meshes, pos, idx, mat):
    positions, indices, materials = [pos], [idx], [mat]
    count = len(pos)
    for mesh in meshes:
        piece = clip_mesh(mesh, cell.bbox)
        if piece is None:
            continue
        positions.append(piece.positions)
        indices.append(piece.indices + count)
        materials.append(np.full(len(piece.indices), mat_id("curb"), np.uint8))
        count += len(piece.positions)
    return np.concatenate(positions), np.concatenate(indices), np.concatenate(materials)

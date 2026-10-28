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
    import pyarrow.parquet as pq

    lines, lowered = [], []
    window = shapely.box(*tile.bbox.buffered(2).as_tuple())
    for row in pq.read_table(path, columns=["tags", "geometry_local"]).to_pylist():
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("barrier") != "kerb":
            continue
        if (
            tags.get("bridge", "no") != "no"
            or tags.get("tunnel", "no") != "no"
            or tags.get("layer", "0") not in ("0", "")
        ):
            continue
        geom = shapely.force_2d(shapely.make_valid(shapely.from_wkb(row["geometry_local"])))
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

    from .inputs import _ABSENT, CELLS, _entry, _load, _prepare_one, _save

    grids = {tile.id: heights}

    def sample(x, z):
        owner = C.VisualTile.at(x, z)
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
        grid = grids[owner.id]
        u, v = (
            (x - owner.origin[0]) / owner.size * CELLS,
            (z - owner.origin[2]) / owner.size * CELLS,
        )
        i, j = min(int(u), CELLS - 1), min(int(v), CELLS - 1)
        fu, fv = u - i, v - j
        return float(
            (grid[j, i] * (1 - fu) + grid[j, i + 1] * fu) * (1 - fv)
            + (grid[j + 1, i] * (1 - fu) + grid[j + 1, i + 1] * fu) * fv
        )

    return sample


def produce(tile, linework, height_at, water, buildings, markings):
    if linework is None or linework.is_empty:
        return []
    blocked = shapely.union_all(
        [*water, *(b.footprint for b in buildings), *(p.buffer(0.3) for p in markings)]
    )
    clipped = linework.difference(blocked)
    window = shapely.box(*tile.bbox.buffered(0.2).as_tuple())
    faces = Faces()
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
                hp, hq = height_at(*p), height_at(*q)
                # Mapped kerb semantics, with an explicitly unmeasured 12 cm raised profile.
                corners = np.array(
                    [
                        [*(p - normal), hp],
                        [*(q - normal), hq],
                        [*(q + normal), hq],
                        [*(p + normal), hp],
                    ]
                )[:, [0, 2, 1]]
                bottom = corners.copy()
                bottom[:, 1] -= 0.02
                top = corners.copy()
                top[:, 1] += 0.12
                faces.face(top[::-1], top[::-1][:, [0, 2]])
                for i in range(4):
                    j = (i + 1) % 4
                    wall = np.array([bottom[i], bottom[j], top[j], top[i]])
                    faces.face(
                        wall[::-1],
                        np.array([[0, 0], [high - low, 0], [high - low, 0.14], [0, 0.14]]),
                    )
    if not faces.indices:
        return []
    mesh = faces.mesh(
        f"kerbs_{tile.id}", "curb", metadata=dict(height_source="mapped kerb; default 0.12 m")
    )
    result = clip_mesh(mesh, tile.bbox)
    return [result] if result else []


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

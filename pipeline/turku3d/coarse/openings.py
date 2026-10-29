"""Subtract walkable tunnel volumes from later terrain and structure geometry."""

from dataclasses import dataclass, field

import numpy as np
import shapely


@dataclass
class Openings:
    # Each floor triangle owns a convex prism. The actual generated floor also
    # includes portal aprons, so terrain cuts and collision protect the same path.
    floors: list = field(default_factory=list)
    heights: list = field(default_factory=list)

    def add(self, mesh, height):
        triangles = mesh.positions[mesh.indices]
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        triangles = triangles[abs(normal[:, 1]) > 1e-10]
        self.floors.extend(triangles)
        self.heights.extend([height] * len(triangles))

    def carve(self, positions, indices, uvs=None):
        """Return clipped triangles and their original owners, including vertical walls.

        Clipping in 3D retains overburden and the ground below the walking floor.
        Polygon differences in X/Z alone either erase upper roads or miss vertical
        retaining faces entirely. Interpolated UVs keep the original materials.
        """
        if not self.floors or not len(indices):
            return positions, indices, uvs, np.arange(len(indices))
        floors = np.asarray(self.floors)
        low, high = floors.min(axis=1), floors.max(axis=1)
        high[:, 1] += self.heights
        low[:, 1] += 0.04
        footprints = shapely.polygons(floors[:, :, [0, 2]])
        tree = shapely.STRtree(footprints)
        triangles = positions[indices]
        tlo, thi = triangles.min(axis=1), triangles.max(axis=1)
        owners, volumes = tree.query(shapely.box(tlo[:, 0], tlo[:, 2], thi[:, 0], thi[:, 2]))
        relevant = (thi[owners, 1] > low[volumes, 1]) & (tlo[owners, 1] < high[volumes, 1])
        candidates = {}
        for owner, volume in zip(owners[relevant], volumes[relevant], strict=True):
            candidates.setdefault(int(owner), []).append(int(volume))
        if not candidates:
            return positions, indices, uvs, np.arange(len(indices))
        planes = {}
        for volume in set(v for values in candidates.values() for v in values):
            floor = floors[volume]
            xz = floor[:, [0, 2]]
            coeff = np.linalg.solve(np.c_[xz, np.ones(3)], floor[:, 1])
            # Inside is dot(vertex, normal) + constant >= 0.
            edges = np.roll(xz, -1, axis=0) - xz
            normal = np.c_[-edges[:, 1], np.zeros(3), edges[:, 0]]
            if np.cross(floor[1] - floor[0], floor[2] - floor[0])[1] > 0:
                normal = -normal
            side = np.c_[normal, -np.sum(normal * floor, axis=1)]
            bottom = [-coeff[0], 1, -coeff[1], -coeff[2] - 0.04]
            top = [coeff[0], -1, coeff[1], coeff[2] + self.heights[volume]]
            planes[volume] = np.vstack([side, bottom, top])
        untouched = np.array([i for i in range(len(indices)) if i not in candidates], dtype=int)
        result_pos = [positions]
        result_uv = [uvs] if uvs is not None else None
        result_idx = [indices[untouched]]
        result_owner = [untouched]
        count = len(positions)
        for owner, volumes in candidates.items():
            vertices = positions[indices[owner]]
            if uvs is not None:
                vertices = np.c_[vertices, uvs[indices[owner]]]
            pieces = [vertices]
            for volume in sorted(volumes):
                pieces = [part for piece in pieces for part in subtract(piece, planes[volume])]
                if not pieces:
                    break
            for piece in pieces:
                fan = np.c_[np.zeros(len(piece) - 2, int), np.arange(1, len(piece) - 1),
                            np.arange(2, len(piece))]
                tri = piece[fan, :3]
                valid = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 1e-10
                result_pos.append(piece[:, :3])
                if result_uv is not None:
                    result_uv.append(piece[:, 3:])
                result_idx.append(fan[valid] + count)
                result_owner.append(np.full(valid.sum(), owner, int))
                count += len(piece)
        return (np.concatenate(result_pos), np.concatenate(result_idx).astype(np.uint32),
                np.concatenate(result_uv) if result_uv is not None else None,
                np.concatenate(result_owner))

    def ground(self, mesh):
        from turku3d.ground.mesh import Mesh

        pos, idx, _, owner = self.carve(mesh.positions, mesh.indices)
        return Mesh(pos, idx, mesh.material[owner]).compact()

    def structures(self, meshes):
        from turku3d.structures.geometry import Mesh

        result = []
        for mesh in meshes:
            pos, idx, uv, _ = self.carve(mesh.positions, mesh.indices, mesh.uvs)
            if len(idx):
                result.append(Mesh(mesh.object_id, mesh.material, pos, idx, uv,
                                   visual=mesh.visual, metadata=mesh.metadata))
        return result


def half(poly, plane, inside):
    distances = poly[:, :3] @ plane[:3] + plane[3]
    accepted = distances >= -1e-10 if inside else distances <= 1e-10
    if accepted.all():
        return poly
    if not accepted.any():
        return np.empty((0, poly.shape[1]))
    result = []
    for k, a in enumerate(poly):
        j = (k + 1) % len(poly)
        b = poly[j]
        if accepted[k]:
            result.append(a)
        if accepted[k] != accepted[j]:
            # Fixed endpoint order makes shared-edge intersections deterministic.
            lo, hi = (a, b) if tuple(a[:3]) < tuple(b[:3]) else (b, a)
            dl, dh = lo[:3] @ plane[:3] + plane[3], hi[:3] @ plane[:3] + plane[3]
            if abs(dl - dh) > 1e-14:
                result.append(lo + (hi - lo) * (dl / (dl - dh)))
    return np.asarray(result)


def subtract(poly, planes):
    """Partition the outside of a convex volume without changing face winding."""
    # A separating plane avoids fragmenting triangles that merely share an edge.
    distances = poly[:, :3] @ planes[:, :3].T + planes[:, 3]
    if (distances.max(axis=0) <= 1e-10).any():
        return [poly]
    result = []
    for plane in planes:
        outside = half(poly, plane, False)
        if len(outside) >= 3:
            result.append(outside)
        poly = half(poly, plane, True)
        if len(poly) < 3:
            break
    return result


def stage(cache, openings, dry, river, bank, solid, structures):
    """Apply the shared opening after terrain and retaining enrichment."""
    from pathlib import Path

    from turku3d.stage_cache import source_key

    return cache.run(
        "opening_clearance", source_key(Path(__file__)),
        [openings, dry, river, bank, solid, structures],
        lambda: (*(openings.ground(mesh) for mesh in (dry, river, bank, solid)),
                 openings.structures(structures)),
    )

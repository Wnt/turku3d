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
        owners, volumes = owners[relevant], volumes[relevant]
        if not len(owners):
            return positions, indices, uvs, np.arange(len(indices))
        planes = {}
        for volume in np.unique(volumes).tolist():
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
        clear = separated(triangles, owners, volumes, planes)
        # Owners in first-query order, each with its candidate volumes (sorted below).
        order = np.argsort(owners, kind="stable")
        grouped, first, counts = np.unique(owners[order], return_index=True, return_counts=True)
        mask = np.ones(len(indices), bool)
        mask[grouped] = False
        untouched = np.flatnonzero(mask)
        pieces, piece_owners = [], []
        for group in np.argsort(order[first], kind="stable").tolist():
            owner = int(grouped[group])
            rows = order[first[group] : first[group] + counts[group]]
            vertices = positions[indices[owner]]
            if uvs is not None:
                vertices = np.c_[vertices, uvs[indices[owner]]]
            kept = [vertices]
            ordered = sorted(zip(volumes[rows].tolist(), clear[rows].tolist()))
            # subtract() returns a separated piece itself; skipping those calls is exact.
            for step, (volume, skip) in enumerate(ordered):
                if skip:
                    continue
                kept = subtract(vertices, planes[volume])
                if len(kept) != 1 or kept[0] is not vertices:
                    kept = subtract_all(kept, [planes[v] for v, _ in ordered[step + 1 :]])
                    break
            pieces.extend(kept)
            piece_owners.extend([owner] * len(kept))
        return emit(positions, indices, uvs, untouched, pieces, piece_owners)

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


# |error| of one 4-term product sum is <= 4.5e-16 * sum(|terms|) in any order, with
# or without FMA; two evaluations differ by at most twice that.
ROUNDING = 2e-15


def separated(triangles, owners, volumes, planes, chunk=1 << 15):
    """Pairs that subtract() is certain to return unchanged, decided in one batch.

    A pair is separated when one volume plane has every vertex within subtract's
    1e-10 tolerance outside. Bounding the rounding difference between this batched
    product and subtract's own keeps the decision exact; uncertain pairs stay False.
    """
    keys = np.unique(volumes)
    stacked = np.stack([planes[v] for v in keys.tolist()])
    slots = np.searchsorted(keys, volumes)
    result = np.empty(len(owners), bool)
    for start in range(0, len(owners), chunk):
        part = slice(start, start + chunk)
        p = stacked[slots[part]]
        t = triangles[owners[part]]
        d = np.einsum("nkj,nmj->nkm", t, p[:, :, :3]) + p[:, None, :, 3]
        scale = np.einsum("nkj,nmj->nkm", abs(t), abs(p[:, :, :3])) + abs(p[:, None, :, 3])
        result[part] = ((d + ROUNDING * scale).max(axis=1) <= 1e-10).any(axis=1)
    return result


def unchanged(pieces, planes):
    """separated() for each piece against each of the stacked (n, 5, 4) volume planes."""
    starts = np.cumsum([0, *[len(piece) for piece in pieces[:-1]]])
    vertices = np.concatenate([piece[:, :3] for piece in pieces])
    flat = planes.reshape(-1, 4)
    d = vertices @ flat[:, :3].T + flat[:, 3]
    scale = abs(vertices) @ abs(flat[:, :3]).T + abs(flat[:, 3])
    worst = np.maximum.reduceat(d + ROUNDING * scale, starts, axis=0)
    return (worst.reshape(len(pieces), len(planes), 5) <= 1e-10).any(axis=2)


def subtract_all(pieces, volumes):
    """Apply subtract() of each volume in order to every piece, skipping separated pieces."""
    if not pieces or not volumes:
        return pieces
    stacked = np.stack(volumes)
    flags = unchanged(pieces, stacked)
    for step, planes in enumerate(volumes):
        result, rows, fresh, last = [], [], [], 0
        for i in np.flatnonzero(~flags[:, step]).tolist():
            parts = subtract(pieces[i], planes)
            if len(parts) == 1 and parts[0] is pieces[i]:
                continue
            result.extend(pieces[last:i])
            rows.append(flags[last:i])
            fresh.extend(range(len(result), len(result) + len(parts)))
            result.extend(parts)
            rows.append(np.zeros((len(parts), len(volumes)), bool))
            last = i + 1
        if last == 0:
            continue
        result.extend(pieces[last:])
        rows.append(flags[last:])
        pieces, flags = result, np.concatenate(rows)
        if not pieces:
            break
        if fresh and step + 1 < len(volumes):
            flags[fresh, step + 1 :] = unchanged([pieces[i] for i in fresh], stacked[step + 1 :])
    return pieces


def emit(positions, indices, uvs, untouched, pieces, owners):
    """Untouched triangles, then each piece as a fan without zero-area triangles."""
    result_pos = [positions, *[piece[:, :3] for piece in pieces]]
    result_uv = [uvs, *[piece[:, 3:] for piece in pieces]] if uvs is not None else None
    result_idx = [indices[untouched]]
    result_owner = [untouched]
    if pieces:
        sizes = np.array([len(piece) for piece in pieces])
        base = len(positions) + np.cumsum(sizes) - sizes
        counts = sizes - 2
        which = np.repeat(np.arange(len(pieces)), counts)
        step = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts) + 1
        fan = np.c_[np.zeros(len(step), int), step, step + 1]
        valid = np.empty(len(fan), bool)
        # Zero-area tests keep each piece's own dtype; elementwise products and
        # three-term norms do not depend on how many triangles share a call.
        dtypes = [piece.dtype for piece in pieces]
        for dtype in sorted(set(dtypes), key=str):
            members = np.flatnonzero(np.array([d == dtype for d in dtypes]))
            rows = np.flatnonzero(np.isin(which, members))
            points = np.concatenate([pieces[i][:, :3] for i in members])
            offset = np.cumsum([0, *[len(pieces[i]) for i in members[:-1]]])
            local = np.empty(len(pieces), int)
            local[members] = offset
            tri = points[fan[rows] + local[which[rows], None]]
            valid[rows] = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 1e-10
        result_idx.append((fan + base[which, None])[valid])
        result_owner.append(np.asarray(owners, int)[which[valid]])
    return (np.concatenate(result_pos), np.concatenate(result_idx).astype(np.uint32),
            np.concatenate(result_uv) if result_uv is not None else None,
            np.concatenate(result_owner))


def half(poly, plane, inside):
    distances = poly[:, :3] @ plane[:3] + plane[3]
    accepted = (distances >= -1e-10 if inside else distances <= 1e-10).tolist()
    if all(accepted):
        return poly
    if not any(accepted):
        return np.empty((0, poly.shape[1]))
    keys = poly[:, :3].tolist()
    rows = list(poly)
    normal, offset = plane[:3], plane[3]
    result = []
    for k, a in enumerate(rows):
        j = k + 1 if k + 1 < len(rows) else 0
        if accepted[k]:
            result.append(a)
        if accepted[k] != accepted[j]:
            # Fixed endpoint order makes shared-edge intersections deterministic.
            lo, hi = (a, rows[j]) if keys[k] < keys[j] else (rows[j], a)
            dl, dh = lo[:3] @ normal + offset, hi[:3] @ normal + offset
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

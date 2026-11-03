"""Deterministic static visibility samples, independent of pigment and sun direction.

The caller supplies opaque triangles and a *complete* horizontal context extent in
the same metric frame. An ownership tile is not such an extent: neighbouring
owners can protrude into it. Incomplete vertices retain coverage zero.
"""

from dataclasses import replace
from importlib.metadata import version

import numpy as np

from turku3d.package.gltf import vertex_normals

BACKEND_VERSION = "4.4.0.post1"


class TriangleVisibility:
    """Two-sided first hits through the pinned native Embree backend.

    Keep metric float64 inputs, recenter/rescale before Embree's float32 query,
    and convert distances back to metres. No slow Python traversal fallback on
    a live producer: install the pipeline's bake extra before enabling a stage.
    """

    def __init__(self, triangles):
        if version("embreex") != BACKEND_VERSION:
            raise RuntimeError(f"visibility bake requires embreex=={BACKEND_VERSION}; install pipeline[bake]")
        from embreex.rtcore_scene import EmbreeScene
        from embreex.mesh_construction import TriangleMesh

        triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        if not np.isfinite(triangles).all():
            raise ValueError("non-finite occluder geometry")
        self.scene = EmbreeScene()
        self.mesh = None
        self.origin = np.zeros(3)
        self.scale = 1.
        if len(triangles):
            self.origin = triangles.min(axis=(0, 1))
            extent = np.max(triangles.max(axis=(0, 1)) - self.origin)
            self.scale = 100. / max(float(extent), 1.)
            self.mesh = TriangleMesh(self.scene, np.asarray((triangles - self.origin) * self.scale, np.float32))
        # Four float32 ULPs near a 100-unit scene bound, converted to metres.
        self.precision = float(4 * np.spacing(np.float32(100))) / self.scale

    def distances(self, origins, directions, limit):
        """Return metric first-hit distances (unit directions); infinity for misses."""
        origins, directions = np.asarray(origins, float), np.asarray(directions, float)
        if (origins.ndim != 2 or origins.shape[1:] != (3,) or directions.shape != origins.shape
                or not np.isfinite(origins).all() or not np.isfinite(directions).all()
                or not np.allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-6)
                or not np.isfinite(limit) or limit <= 0):
            raise ValueError("finite (N,3) origins, unit directions and positive limit required")
        result = np.full(len(origins), np.inf)
        if self.mesh is None:
            return result
        for begin in range(0, len(origins), 2048):
            end = min(begin + 2048, len(origins))
            hit = self.scene.run(
                np.asarray((origins[begin:end] - self.origin) * self.scale, np.float32),
                np.asarray(directions[begin:end], np.float32),
                dists=np.full(end-begin, limit * self.scale, np.float32), output=True)
            distance = hit["tfar"].astype(float) / self.scale
            result[begin:end] = np.where((hit["primID"] >= 0) & (distance < limit), distance, np.inf)
        return result


def sample_visibility(positions, normals, scene, *, context_bounds, samples=64,
                      local_radius=2., sky_radius=32., bias=.002, sample_origins=None):
    """R: cosine-weighted local visibility; G: upper-sky visibility; B: coverage.

    Local occlusion fades linearly to zero at local_radius. Sky visibility is
    bounded by sky_radius. A fixed equal-area sphere supplies cosine weights;
    no random seed, tile ID, wall clock or processing order affects the values.
    """
    positions, normals = np.asarray(positions, float), np.asarray(normals, float)
    bounds = np.asarray(context_bounds, float)
    if (positions.ndim != 2 or positions.shape[1:] != (3,) or normals.shape != positions.shape
            or not np.isfinite(positions).all() or not np.isfinite(normals).all()
            or bounds.shape != (2, 2) or not np.isfinite(bounds).all()
            or np.any(bounds[1] <= bounds[0]) or samples < 16 or samples != int(samples)
            or not 0 < bias < local_radius <= sky_radius or not np.isfinite(sky_radius)):
        raise ValueError("invalid visibility samples, radii or complete XZ context bounds")
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 1e-12)
    origins = positions + normals * bias if sample_origins is None else np.asarray(sample_origins, float)
    if origins.shape != positions.shape or not np.isfinite(origins).all():
        raise ValueError("finite (N,3) sample origins required")
    result = np.tile(np.array([1., 1., 0.], np.float32), (len(positions), 1))
    # Require the entire ray sphere at its resolved origin to be known.
    xz = origins[:, [0, 2]]
    covered = ((xz >= bounds[0] + sky_radius) & (xz <= bounds[1] - sky_radius)).all(axis=1)
    covered &= ((positions[:, [0, 2]] >= bounds[0]) & (positions[:, [0, 2]] <= bounds[1])).all(axis=1)
    covered &= lengths > 1e-12
    selected = np.flatnonzero(covered)
    if not len(selected):
        return result
    # Facade programmes often duplicate identical vertices; keep hard-normal seams.
    vertices, inverse = np.unique(np.c_[origins[selected], normals[selected]], axis=0, return_inverse=True)
    baked = np.ones((len(vertices), 3), np.float32)
    i = np.arange(int(samples))
    y = 1 - 2 * (i + .5) / samples
    angle = i * (np.pi * (3 - np.sqrt(5)))
    radius = np.sqrt(1 - y * y)
    directions = np.c_[np.cos(angle) * radius, y, np.sin(angle) * radius]
    for begin in range(0, len(vertices), 64):
        batch = vertices[begin:begin + 64]
        weights = np.maximum(batch[:, 3:] @ directions.T, 0)
        origins = batch[:, :3]
        active = weights > 0
        distances = np.full(weights.shape, np.inf)
        distances[active] = scene.distances(
            np.broadcast_to(origins[:, None], (*weights.shape, 3))[active],
            np.broadcast_to(directions, (*weights.shape, 3))[active], sky_radius)
        local = np.minimum(distances / local_radius, 1)
        baked[begin:begin + len(batch), 0] = (weights * local).sum(axis=1) / weights.sum(axis=1)
        sky_weights = weights * (directions[:, 1] > 0)
        total_sky = sky_weights.sum(axis=1)
        baked[begin:begin + len(batch), 1] = np.divide(
            (sky_weights * (distances >= sky_radius)).sum(axis=1), total_sky,
            out=np.ones(len(batch)), where=total_sky > 1e-12)
        # A ray origin still on an adjoining face has no unique free-space side.
        # Do not turn a t=0 hit into either black lighting or a fake open ray.
        unresolved = np.any(distances <= scene.precision, axis=1)
        baked[begin:begin + len(batch)][unresolved] = [1, 1, 0]
    result[selected] = np.clip(baked[inverse], 0, 1)
    return result


def surface_origins(positions, indices, normals, bias):
    """Inset into one incident face, then offset along the unchanged shading normal.

    The largest face supplies the interior direction; centroid tie-breaks make
    this independent of triangle order. This handles hard convex/concave seams
    without averaging away their shading normals. Unresolved intersections are
    explicitly uncovered by sample_visibility().
    """
    positions, indices = np.asarray(positions, float), np.asarray(indices)
    normals = np.asarray(normals, float)
    lengths = np.linalg.norm(normals, axis=1)
    unit = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 1e-12)
    origins = positions + unit * bias
    if not len(indices):
        return origins
    faces = positions[indices]
    centers = faces.mean(axis=1)
    areas = np.linalg.norm(np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]), axis=1)
    order = np.lexsort((centers[:, 2], centers[:, 1], centers[:, 0], areas))
    rank = np.empty(len(order), int)
    rank[order] = np.arange(len(order))
    choice = np.full(len(positions), -1)
    for corner in range(3):
        np.maximum.at(choice, indices[:, corner], rank)
    used = choice >= 0
    inward = centers[order[choice[used]]] - positions[used]
    distance = np.linalg.norm(inward, axis=1)
    amount = np.divide(np.minimum(bias, distance * .25), distance,
                       out=np.zeros_like(distance), where=distance > 1e-12)
    origins[used] += inward * amount[:, None]
    return origins


def bake_meshes(meshes, occluders, *, context_bounds, **recipe):
    """Copy static targets with visibility; caller owns opacity/context selection.

    Cache inputs must include target positions/indices/normals, all occluder
    positions and indices, context_bounds, recipe and this module's source. Never mutate a
    cached structural mesh or bake before its final cuts/retriangulation.
    """
    triangles = [np.asarray(m.positions)[np.asarray(m.indices)] for m in occluders]
    scene = TriangleVisibility(np.concatenate(triangles) if triangles else np.empty((0, 3, 3)))
    result = []
    for mesh in meshes:
        normals = mesh.normals if mesh.normals is not None else vertex_normals(mesh.positions, mesh.indices)
        origins = surface_origins(mesh.positions, mesh.indices, normals, recipe.get("bias", .002))
        values = sample_visibility(mesh.positions, normals, scene, context_bounds=context_bounds,
                                   sample_origins=origins, **recipe)
        result.append(replace(mesh, baked_visibility=values))
    return result

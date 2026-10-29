"""Mapped retaining edges and rock cuts replace the coarse terrain slope below grade."""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces

from .bridge_details import appearance
from .geometry import clip_mesh


def produce(tile, records, sample, stairs=()):
    from .inputs import CELLS
    from .kerbs import terrain_patches

    meshes, openings = [], []
    stair_mask = shapely.union_all(stairs)
    window = shapely.box(*tile.bbox.as_tuple()).buffer(3)
    for index, (geom, tags, _) in enumerate(records):
        if tags.get("barrier") != "retaining_wall" or geom.geom_type != "LineString":
            continue
        faces = Faces()
        cuts = []
        coords = np.asarray(geom.coords)
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            segment = shapely.LineString([a, b])
            if not segment.intersects(window) or segment.length < 0.1:
                continue
            direction = (b - a) / segment.length
            normal = np.array([-direction[1], direction[0]])
            stations = np.linspace(0, segment.length, max(2, int(np.ceil(segment.length / 2)) + 1))
            centre = a + stations[:, None] * direction
            radius = 2.5
            edges = [centre + sign * normal * radius for sign in (-1, 1)]
            levels = np.array([[sample(*p) for p in side] for side in edges])
            # A mapped edge with no measured crossfall does not invent a wall.
            for j in range(len(stations) - 1):
                local = levels[:, j : j + 2]
                if abs(np.mean(local[1] - local[0])) < 0.6:
                    continue
                top = np.maximum(*local)
                bottom = np.minimum(*local)
                p, q = centre[j : j + 2]
                if shapely.LineString([p, q]).intersects(stair_mask):
                    continue
                wall = np.array(
                    [
                        [p[0], bottom[0], p[1]],
                        [q[0], bottom[1], q[1]],
                        [q[0], top[1], q[1]],
                        [p[0], top[0], p[1]],
                    ]
                )
                faces.face(wall)
                faces.face(wall[::-1])
                for side in range(2):
                    outer = edges[side][j : j + 2]
                    polygon = shapely.Polygon([p, q, outer[1], outer[0]])
                    if polygon.intersects(stair_mask):
                        # Stair treads and their foundation own the overlapping band.
                        continue
                    cuts.append(polygon)
                    for patch in terrain_patches(
                        np.asarray(polygon.exterior.coords)[:-1],
                        tile.size / CELLS,
                        tile.bbox.as_tuple(),
                    ):
                        along = np.clip(
                            (patch - p) @ direction / (stations[j + 1] - stations[j]), 0, 1
                        )
                        at_wall = np.interp(along, [0, 1], local[side])
                        blend = np.clip(np.abs((patch - p) @ normal) / radius, 0, 1)
                        ground = np.array([sample(*v) for v in patch])
                        height = at_wall * (1 - blend) + ground * blend
                        xyz = np.c_[patch[:, 0], height, patch[:, 1]]
                        if np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])[1] < 0:
                            xyz, patch = xyz[::-1], patch[::-1]
                        faces.face(xyz, patch)
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
                openings.extend(cuts)
    return meshes, openings

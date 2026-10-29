"""Level courtyard terraces and exact masonry-rimmed pools, shared by both passes."""

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh
from turku3d.structures.geometry import Faces

from .banks import join


def grade(positions, scene):
    positions = positions.copy()
    query = shapely.points(positions[:, [0, 2]])
    # Small detailed patches override broad terrace cells.
    for region, level in sorted(
        scene.get("terraces", ()), key=lambda row: (-row[0].area, row[0].wkb, row[1])
    ):
        distance = shapely.distance(query, region)
        weight = np.clip(1 - distance / 1.5, 0, 1)
        positions[:, 1] = positions[:, 1] * (1 - weight) + level * weight
    return positions


def sampler(tile, sample, scene):
    """Neighbour samples receive the same canonical terrace grading as owned grids."""

    def height(x, z):
        value = sample(x, z)
        if tile.bbox.xmin <= x <= tile.bbox.xmax and tile.bbox.zmin <= z <= tile.bbox.zmax:
            return value
        return float(grade(np.array([[x, value, z]]), scene)[0, 1])

    return height


def horizontal(faces, region, height):
    import mapbox_earcut

    for polygon in shapely.get_parts(region):
        if polygon.geom_type != "Polygon" or polygon.area < 1e-8:
            continue
        rings = [np.asarray(r.coords)[:-1] for r in [polygon.exterior, *polygon.interiors]]
        xz = np.concatenate(rings)
        indices = mapbox_earcut.triangulate_float64(
            xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)
        ).reshape(-1, 3)
        xyz = np.c_[xz[:, 0], np.full(len(xz), height), xz[:, 1]]
        for tri in indices:
            p = xyz[tri]
            if np.cross(p[1] - p[0], p[2] - p[0])[1] < 0:
                p = p[::-1]
            faces.face(p, p[:, [0, 2]])


def mesh(faces, material):
    return Mesh(
        np.asarray(faces.positions).reshape(-1, 3),
        np.asarray(faces.indices, np.uint32).reshape(-1, 3),
        np.full(len(faces.indices), mat_id(material), np.uint8),
    )


def produce(tile, scene, dry, wet, bank, solid):
    from .stairs import cut_ground

    window = shapely.box(*tile.bbox.as_tuple())
    for body, level, _ in scene.get("pools", ()):
        body = shapely.orient_polygons(body)
        outer = shapely.orient_polygons(body.buffer(0.3, join_style="mitre"))
        if not outer.intersects(window):
            continue
        dry, solid = cut_ground(dry, [outer]), cut_ground(solid, [outer])
        stone, water, bed = Faces(), Faces(), Faces()
        for cell in tile.collision_tiles():
            cell_window = shapely.box(*cell.bbox.as_tuple())
            horizontal(stone, outer.difference(body).intersection(cell_window), level + 0.06)
            horizontal(water, body.intersection(cell_window), level - 0.12)
            horizontal(bed, body.intersection(cell_window), level - 0.65)
            # Real rim only, split at collision seams as well as visual tile edges.
            for ring, bottom, reverse in (
                (body.boundary, level - 0.65, False),
                (outer.boundary, level, True),
            ):
                for line in shapely.get_parts(ring.intersection(cell_window)):
                    if line.geom_type not in ("LineString", "LinearRing"):
                        continue
                    coords = np.asarray(line.coords)
                    for a, b in zip(coords[:-1], coords[1:], strict=True):
                        p = np.array(
                            [
                                [a[0], bottom, a[1]],
                                [b[0], bottom, b[1]],
                                [b[0], level + 0.06, b[1]],
                                [a[0], level + 0.06, a[1]],
                            ]
                        )
                        stone.face(p[::-1] if reverse else p)
        rim, surface, floor = mesh(stone, "quay"), mesh(water, "water"), mesh(bed, "soil")
        dry = join(dry, rim)
        wet = join(wet, surface)
        solid = join(solid, rim, surface, floor)
    return dry, wet, bank, solid


def stats(tile, scene):
    window = shapely.box(*tile.bbox.as_tuple())
    return dict(
        pools=[
            dict(
                area_m2=round(g.area, 3),
                terrace_m=round(h, 3),
                water_m=round(h - 0.12, 3),
                source=source,
            )
            for g, h, source in scene.get("pools", ())
            if g.intersects(window)
        ],
        terraces=sum(g.intersects(window) for g, _ in scene.get("terraces", ())),
    )

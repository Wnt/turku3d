"""Join reconstructed surface patches to the actual terrain, including bank crests."""

import numpy as np
import shapely

from turku3d.ground.mesh import HeightQuery, Mesh

REACH = 2.0


def footprint(mesh):
    triangles = mesh.positions[mesh.indices]
    polygons = shapely.polygons(triangles[:, :, [0, 2]])
    return polygons, shapely.area(polygons) > 1e-10


def on_terrain(patch, original):
    """Share the source TIN's edges/materials; never extend a patch into water or holes."""
    from .surfaces import split_boundaries

    polygons, planar = footprint(original)
    domain, _ = footprint(patch)
    ids = shapely.STRtree(polygons[planar]).query(shapely.union_all(domain), predicate="intersects")
    owners = np.flatnonzero(planar)[ids]
    # Only physical/material borders constrain a replacement. Cutting it on
    # every inherited TIN diagonal multiplies triangles without changing its
    # measured surface; the outside loft shares those vertices via conform().
    regions = [
        (
            shapely.union_all(
                polygons[owners[original.material[owners] == material]], grid_size=1e-8
            ),
            material,
        )
        for material in np.unique(original.material[owners])
    ]
    p, ix, materials, _ = split_boundaries(
        patch.positions,
        patch.indices,
        np.full(len(patch.indices), 255, np.uint8),
        np.ones(len(patch.indices), bool),
        regions,
    )
    return Mesh(p, ix[materials != 255], materials[materials != 255]).compact()


class Field:
    """A patch owns its surface; a narrow dry-side loft closes its external seams."""

    def __init__(self, original, patch):
        polygons, planar = footprint(patch)
        # Sub-nanometre overlay cracks are not physical inner boundaries.
        self.area = shapely.union_all(polygons[planar], grid_size=1e-8)
        self.boundary = self.area.boundary
        self.source = HeightQuery(original)
        self.target = HeightQuery(patch, tolerance=1e-7)

    def heights(self, positions):
        p = np.asarray(positions, np.float64)
        result = p[:, 1].copy()
        xy = shapely.points(p[:, [0, 2]])
        distance = shapely.distance(xy, self.area)
        base = self.source(p[:, [0, 2]], reference=p[:, 1])
        # Lower beds, water sensors and vertical face bottoms retain their levels.
        use = (distance < REACH) & np.isfinite(base) & (p[:, 1] >= base - 1e-5)
        ids = np.flatnonzero(use)
        if not len(ids):
            return result
        nearest = shapely.get_coordinates(
            shapely.get_point(shapely.shortest_line(xy[ids], self.area), -1)
        )
        target = self.target(nearest)
        valid = np.isfinite(target)
        ids, target = ids[valid], target[valid]
        blend = np.clip(1 - distance[ids] / REACH, 0, 1)
        result[ids] += blend * (target - result[ids])
        return result

    def sample(self, fallback):
        def height(x, z):
            y = self.source([[x, z]])[0]
            if not np.isfinite(y):
                return fallback(x, z)
            return float(self.heights([[x, y, z]])[0])

        return height


def stitch(original, patch, source):
    """Insert one patch boundary into visual/solid terrain and loft its outside edge."""
    from .surfaces import split_boundaries
    from .banks import join
    from .street_profiles import conform

    field = Field(source, patch)
    if field.area.is_empty:
        return original
    _, planar = footprint(original)
    p, ix, material, _ = split_boundaries(
        original.positions,
        original.indices,
        original.material,
        planar,
        [(field.area.buffer(distance), 0) for distance in (REACH, REACH / 2)] + [(field.area, 0)],
        preserve_materials=True,
    )
    centres = p[ix].mean(axis=1)
    inside = shapely.contains_xy(field.area, centres[:, 0], centres[:, 2])
    # The patch was clipped to dry land. It owns every old floor there; the
    # river's sensor and bed are outside this domain and remain untouched.
    remove = inside
    p[:, 1] = field.heights(p)
    unchanged = Mesh(
        p,
        np.concatenate([ix[~remove], original.indices[~planar]]),
        np.concatenate([material[~remove], original.material[~planar]]),
    )
    result = join(unchanged, patch)
    centres = result.positions[result.indices].mean(axis=1)
    local = shapely.distance(shapely.points(centres[:, [0, 2]]), field.area) < REACH + 1e-7
    # Repair hanging vertices only at this join. Conforming the whole city TIN
    # would unnecessarily subdivide unrelated inherited surface boundaries.
    p, ix, material = conform(result.positions, result.indices[local], result.material[local])
    return join(
        Mesh(p, ix, material),
        Mesh(result.positions, result.indices[~local], result.material[~local]),
    ).compact()


def align_upper(mesh, source, target):
    """Rebuild vertical crests at every final-floor break; preserve submerged bases."""
    if not len(mesh.indices):
        return mesh
    from turku3d.structures.geometry import Faces

    floor = target.positions[target.indices]
    normal = np.cross(floor[:, 1] - floor[:, 0], floor[:, 2] - floor[:, 0])
    floor = floor[normal[:, 1] > np.linalg.norm(normal, axis=1) * 0.01]
    if not len(floor):
        return mesh
    polygons = shapely.polygons(floor[:, :, [0, 2]])
    tree = shapely.STRtree(polygons)
    old_floor = HeightQuery(source, tolerance=1e-7)
    triangles = mesh.positions[mesh.indices]
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    vertical = abs(normal[:, 1]) < 1e-9
    groups = {}
    for index in np.flatnonzero(vertical):
        xz = np.unique(np.round(triangles[index][:, [0, 2]], 8), axis=0)
        if len(xz) == 2 and np.linalg.norm(xz[1] - xz[0]) > 1e-7:
            groups.setdefault(tuple(xz.ravel()), []).append(index)
    replaced = np.zeros(len(mesh.indices), bool)
    faces, materials = Faces(), []
    for key, ids in groups.items():
        ends = np.asarray(key).reshape(2, 2)
        line = shapely.LineString(ends)
        owners = tree.query(line, predicate="dwithin", distance=1e-7)
        if not len(owners):
            if np.isfinite(old_floor(shapely.get_coordinates(shapely.segmentize(line, 1)))).any():
                replaced[ids] = True
            continue
        crossings = shapely.get_coordinates(
            shapely.union_all(
                [
                    *shapely.intersection(line, shapely.boundary(polygons[owners])),
                    *shapely.intersection(
                        line, shapely.boundary(shapely.buffer(polygons[owners], 1e-7))
                    ),
                ]
            )
        )
        stations = shapely.line_locate_point(line, shapely.points(crossings))
        knots = np.unique(np.clip(np.r_[0, line.length, stations], 0, line.length))
        points = triangles[ids].reshape(-1, 3)
        bottom = np.array(
            [
                points[np.linalg.norm(points[:, [0, 2]] - end, axis=1) < 1e-7, 1].min()
                for end in ends
            ]
        )
        replaced[ids] = True
        for lo, hi in zip(knots[:-1], knots[1:], strict=True):
            if hi - lo < 1e-7:
                continue
            middle = line.interpolate((lo + hi) / 2)
            candidates = tree.query(middle, predicate="dwithin", distance=1e-7)
            if not len(candidates):
                continue  # Explicit end of floor coverage; never bridge a portal/gap.
            tri = floor[candidates]
            a = tri[:, 0, [0, 2]]
            b, c = tri[:, 1, [0, 2]] - a, tri[:, 2, [0, 2]] - a
            basis = np.stack([b, c], axis=2)
            coefficients = np.linalg.solve(
                basis.transpose(0, 2, 1), (tri[:, 1:, 1] - tri[:, :1, 1])[:, :, None]
            )[:, :, 0]
            query = np.asarray(middle.coords)[0]
            levels = tri[:, 0, 1] + np.sum((query - a) * coefficients, axis=1)
            owner = int(np.argmin(levels))
            xz = shapely.get_coordinates(shapely.line_interpolate_point(line, [lo, hi]))
            top = tri[owner, 0, 1] + (xz - a[owner]) @ coefficients[owner]
            low = np.interp([lo, hi], [0, line.length], bottom)
            upper = np.c_[xz[:, 0], np.maximum(top, low), xz[:, 1]]
            lower = np.c_[xz[:, 0], low, xz[:, 1]]
            before = len(faces.indices)
            corners = np.array([upper[0], upper[1], lower[1], lower[0]])
            if (
                np.dot(np.cross(corners[1] - corners[0], corners[2] - corners[0]), normal[ids[0]])
                < 0
            ):
                corners = corners[::-1]
            faces.face(corners)
            materials.extend([mesh.material[ids[0]]] * (len(faces.indices) - before))
    if not faces.indices:
        return Mesh(mesh.positions, mesh.indices[~replaced], mesh.material[~replaced]).compact()
    offset = len(mesh.positions)
    return Mesh(
        np.r_[mesh.positions, np.asarray(faces.positions)],
        np.r_[mesh.indices[~replaced], np.asarray(faces.indices, np.uint32) + offset],
        np.r_[mesh.material[~replaced], np.asarray(materials, np.uint8)],
    ).compact()

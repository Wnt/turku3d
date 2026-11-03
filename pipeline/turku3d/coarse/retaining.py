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


HILLSIDE = 0.5  # upper ground rising faster than this (27 deg) beyond the rail is a slope


def wall_levels(centres, left, right, sample):
    """Ground levels at an unsurveyed wall line and at its apron rails.

    The rails sit 2.5 m out, clear of the TIN smearing across the step. At a hill
    foot the upper rail lies far up the slope, so its height would build a wall
    metres too tall with a flat shelf behind it. Where the upper ground keeps
    rising steeply beyond its rail, that slope is extended back to the wall line
    (never below the lower rail); the apron then follows the slope down to the
    real wall top. Flat tops, embankment crests and the lower side keep the rails.
    """
    rails = np.array([[sample(*p) for p in side] for side in (left, right)])
    beyond = np.array([[sample(*(2 * p - c)) for p, c in zip(side, centres, strict=True)]
                       for side in (left, right)])
    beyond = np.where(np.isfinite(beyond), beyond, rails)
    levels = rails.copy()
    upper = np.argmax(rails, axis=0)
    columns = np.arange(rails.shape[1])
    top, low = rails[upper, columns], rails[1 - upper, columns]
    rise = beyond[upper, columns] - top
    hillside = rise > HILLSIDE * float(np.linalg.norm(left[0] - centres[0]))
    levels[upper, columns] = np.where(hillside, np.maximum(low, top - rise), top)
    return levels, rails


# A ledge survey drops the sections beside a mapped flight (their side windows
# take stair returns), which leaves the old bank between the ledge end and the
# flight. Real gaps reach 4.0-4.5 m (t0_1_0 quay stairs): three 2 m sections.
FLIGHT_REACH = 6.0


def reach_flights(profile, levels, flights):
    """Continue a measured run straight up to a flight its face line ends short of."""
    centres, left, right = (np.array(a, float) for a in profile)
    levels = np.asarray(levels, float)
    if flights.is_empty or len(centres) < 2:
        return (centres, left, right), levels
    for end, previous in ((0, 1), (-1, -2)):
        start = centres[end]
        direction = start - centres[previous]
        length = np.linalg.norm(direction)
        if length < 1e-7 or flights.intersects(shapely.Point(start)):
            continue
        direction /= length
        hit = shapely.LineString([start, start + direction * FLIGHT_REACH]).intersection(flights)
        if hit.is_empty:
            continue
        reach = (shapely.get_coordinates(hit) - start) @ direction
        # Overlap the flight; the flight cut clips faces and aprons exactly.
        step = direction * min(reach.min() + 0.5, FLIGHT_REACH)
        row = [centres[end] + step], [left[end] + step], [right[end] + step]
        column = levels[:, [end]]
        if end == 0:
            centres, left, right = (np.r_[r, a] for r, a in zip(row, (centres, left, right), strict=True))
            levels = np.c_[column, levels]
        else:
            centres, left, right = (np.r_[a, r] for r, a in zip(row, (centres, left, right), strict=True))
            levels = np.c_[levels, column]
    return (centres, left, right), levels


def _areas(geometry):
    """The polygonal part of an overlay result. A degenerate cut leaves line and point slivers in a
    GeometryCollection; a mask of mixed dimension makes the next GEOS overlay fail ("Unable to
    determine overlay result geometry dimension", t0_3_1)."""
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [p for p in shapely.get_parts(geometry) if p.geom_type == "Polygon" and not p.is_empty]
    if not parts:
        return shapely.Polygon()
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def produce(tile, records, sample, stairs=(), water=(), paving=(), surface=None):
    from .tunnels import _polygon_faces

    meshes, openings = [], []
    shore = shapely.union_all(water).buffer(2.5)
    wet = shapely.union_all(water)
    window = shapely.box(*tile.bbox.as_tuple()).buffer(3)
    claimed = shapely.Polygon()
    flights = shapely.union_all(list(stairs))
    for index, (geom, tags, _) in enumerate(records):
        if tags.get("barrier") not in ("retaining_wall", "wall", "city_wall"):
            continue
        if geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        measured = tags.get("_survey")
        # Surveyed inner quay ledges may be beside water or overlap the generic
        # path width. Flight openings remain authoritative, but those broad
        # exclusion masks must not erase a measured wall/landing.
        stair_mask = shapely.union_all([*stairs, *([] if measured else paving)])
        faces, ground, cuts = Faces(), Faces(), []
        for line in shapely.get_parts(geom if measured else geom.difference(shore)):
            if line.geom_type != "LineString" or line.is_empty:
                continue
            profile = ((measured["centres"], measured["left"], measured["right"])
                       if measured else sections(line))
            if profile is None:
                continue
            centres, left, right = profile
            if measured:
                levels = np.asarray(measured["heights"]).T
                (centres, left, right), levels = reach_flights(profile, levels, flights)
                # The new edge owns the discontinuity, but its aprons must meet the
                # surrounding ground exactly. Extrapolating the near-edge plane all
                # the way across a curved bank would leave a seam at the outer rail.
                outer_levels = np.array([[(surface or sample)(*p) for p in side]
                                         for side in (left, right)])
            else:
                levels, outer_levels = wall_levels(centres, left, right, sample)
            # An outer rail can lie in the river. Keep its measured dry-side
            # plane to the shoreline, rather than sloping the paving to water.
            outer_levels = np.where(np.isfinite(outer_levels), outer_levels, levels)
            for j, (p, q) in enumerate(zip(centres[:-1], centres[1:], strict=True)):
                segment = shapely.LineString([p, q])
                if not segment.intersects(window) or segment.length < 1e-7:
                    continue
                local = levels[:, j : j + 2]
                if abs(np.mean(local[1] - local[0])) < (0.3 if measured else 0.6):
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
                    heights = np.r_[local[side], outer_levels[side, j:j+2][::-1]]
                    # Triangulate the original strip before clipping. Every new
                    # corner stays on its parent plane, including at a stair cut.
                    for ids in ([0, 1, 2], [0, 2, 3]):
                        tri = xz[ids]
                        polygon = shapely.Polygon(tri)
                        if polygon.area < 1e-8:
                            continue
                        polygon = _areas(polygon.difference(stair_mask))
                        if measured and not wet.is_empty:
                            polygon = _areas(polygon.difference(wet))
                        if not polygon.is_empty:
                            polygon = _areas(polygon.difference(claimed))
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

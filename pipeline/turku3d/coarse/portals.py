"""Open-road floor anchors and hillside-flush box tunnel entrances."""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces, profile_wall

PATHS = {"footway", "path", "cycleway", "pedestrian", "steps"}


def merge_companions(ways, dimensions):
    """Adjacent road and path ways can describe lanes of one curved tunnel shell."""
    remaining = {key: (key, line, tags) for key, line, tags in ways}
    result = []
    for key, line, tags in ways:
        if key not in remaining or tags.get("highway") in PATHS or not tags.get("highway"):
            continue
        if tags.get("tunnel") != "yes" or tags.get("vault") or tags.get("_companions"):
            continue
        width, height = dimensions(tags)
        coords = np.asarray(line.coords)
        tangent = np.gradient(coords, axis=0)
        length = np.linalg.norm(tangent, axis=1)
        if np.any(length < 1e-6):
            continue
        tangent /= length[:, None]
        normal = np.c_[-tangent[:, 1], tangent[:, 0]]
        low, high = np.full(len(coords), -width / 2), np.full(len(coords), width / 2)
        companions = []
        for other_key, other, other_tags in list(remaining.values()):
            if (
                other_tags.get("highway") not in PATHS
                or other_tags.get("tunnel") != "yes"
                or other_tags.get("vault")
            ):
                continue
            if (
                other_tags.get("layer", "0") != tags.get("layer", "0")
                or not 0.85 < other.length / line.length < 1.15
            ):
                continue
            ow, oh = dimensions(other_tags)
            if "maxheight" in tags and "maxheight" in other_tags and abs(height - oh) > 0.5:
                continue
            projected = np.array([line.project(shapely.Point(p)) for p in other.coords])
            if abs(projected[-1] - projected[0]) < line.length * 0.85:
                continue
            query = shapely.line_interpolate_point(line, np.linspace(0, line.length, 16))
            distance = shapely.distance(query, other)
            if distance.min() < width / 2 or distance.max() > (width + ow) / 2 + 2:
                continue
            nearest = shapely.get_coordinates(
                shapely.line_interpolate_point(
                    other, shapely.line_locate_point(other, shapely.points(coords))
                )
            )
            lateral = ((nearest - coords) * normal).sum(axis=1)
            if not (np.all(lateral > 0) or np.all(lateral < 0)):
                continue
            low, high = np.minimum(low, lateral - ow / 2), np.maximum(high, lateral + ow / 2)
            companions.append(dict(id=other_key, line=other, width=ow))
        if not companions:
            continue
        axis = shapely.LineString(coords + normal * ((low + high) / 2)[:, None])
        combined = dict(
            tags,
            width=str(float(np.max(high - low))),
            maxheight=str(height),
            _road_axis=line,
            _companions=companions,
        )
        result.append(("+".join([key, *[p["id"] for p in companions]]), axis, combined))
        del remaining[key]
        for companion in companions:
            del remaining[companion["id"]]
    return [*result, *remaining.values()]


def approaches(ways, roads):
    """Keep mapped continuations, including their bends, for stable portal heights."""
    tree = shapely.STRtree([line.boundary for line, _ in roads])
    result = []
    for key, line, tags in ways:
        foot = tags.get("highway") in PATHS
        joined = []
        axis = tags.get("_road_axis", line)
        for point in (shapely.Point(axis.coords[0]), shapely.Point(axis.coords[-1])):
            candidates = [
                roads[i]
                for i in tree.query(point, predicate="dwithin", distance=0.05)
                if (roads[i][1].get("highway") in PATHS) == foot and roads[i][0].length >= 4
            ]
            joined.append(
                max(candidates, key=lambda item: item[0].length)[0] if candidates else None
            )
        result.append((key, line, dict(tags, _approaches=joined)))
    return result


def anchors(line, tags, sample, ends, rendered=None):
    """A mapped portal may lie under overburden; fit its floor from the open approach."""
    ends = np.array(ends, float, copy=True)
    repaired = np.zeros(2, bool)
    coords = np.asarray(line.coords)
    for i, end, neighbour in ((0, coords[0], coords[1]), (1, coords[-1], coords[-2])):
        outward = (end - neighbour) / np.linalg.norm(end - neighbour)
        road = tags.get("_approaches", [None, None])[i]
        distance = np.linspace(3, min(9, road.length - 0.2) if road is not None else 9, 4)
        if road is None:
            points = end + distance[:, None] * outward
        else:
            station = (
                distance
                if np.linalg.norm(np.asarray(road.coords[0]) - end)
                <= np.linalg.norm(np.asarray(road.coords[-1]) - end)
                else road.length - distance
            )
            points = shapely.get_coordinates(shapely.line_interpolate_point(road, station))
        height = np.array([sample(*p) for p in points])
        if not np.isfinite(height).all():
            continue
        slope, base = np.polyfit(distance, height, 1)
        if (
            abs(slope) <= 0.15
            and np.max(np.abs(height - (base + slope * distance))) < 0.2
            and (ends[i] > base + 0.35 or (rendered is not None and rendered(*end) > base + 0.35))
        ):
            ends[i], repaired[i] = min(ends[i], base), True
    return ends, repaired


def dividers(key, tags, floor_height):
    """A low curb separates mapped cycle/pedestrian lanes under a common roof."""
    result = []
    for companion in tags.get("_companions", []):
        axis = companion["line"]
        edges = [axis.offset_curve(sign * companion["width"] / 2) for sign in (-1, 1)]
        edge = min(edges, key=lambda g: g.distance(tags["_road_axis"]))
        if edge.geom_type != "LineString":
            continue
        points = np.asarray(edge.coords)
        floor = floor_height(points)
        mesh = profile_wall(
            f"tunnel:{key}:divider",
            points,
            floor - 0.05,
            floor + 0.12,
            thickness=0.15,
            material="curb",
        )
        mesh.metadata["portal"] = True
        result.append(mesh)
    return result


def grounded_wall(object_id, xz, bottom, top, sample, step, *, thickness=0.3):
    """A straight wall's foundation stays below every terrain plane under its width."""
    from .kerbs import terrain_patches

    xz = np.asarray(xz, float)
    direction = xz[1] - xz[0]
    normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction) * thickness / 2
    footprint = np.array([xz[0] + normal, xz[1] + normal, xz[1] - normal, xz[0] - normal])
    ground = [sample(x, z) for patch in terrain_patches(footprint, step) for x, z in patch]
    foundation = min(ground) - 0.2
    return profile_wall(object_id, xz, np.minimum(bottom, foundation), top, thickness=thickness)


def frames(
    key,
    left,
    right,
    offset,
    left_floor,
    right_floor,
    ground,
    step,
    clearance,
    road_top=False,
    deck_sample=None,
    repaired=(False, False),
):
    from .kerbs import terrain_edge

    result = []
    for i, end in enumerate((0, -1)):
        frame = Faces()
        # Jambs meet the tube lining; the facade terminates at the real bank,
        # rather than leaving a wide free-standing rectangular picture frame.
        jamb_width = 1.5 if road_top else 0.5
        header_line = np.array(
            [
                left[end] + offset[end] * (0.2 + jamb_width),
                right[end] - offset[end] * (0.2 + jamb_width),
            ]
        )
        bottom = (
            np.array([deck_sample(*q) for q in header_line]) - 0.3
            if road_top
            else np.array([left_floor[end], right_floor[end]]) + clearance
        )
        vector = header_line[1] - header_line[0]
        for sign, side, floor in ((1, left, left_floor), (-1, right, right_floor)):
            a = side[end] + sign * offset[end] * 0.2
            b = a + sign * offset[end] * jamb_width
            top = bottom[0] + ((np.array([a, b]) - header_line[0]) @ vector) / (vector @ vector) * (
                bottom[1] - bottom[0]
            )
            jamb = grounded_wall(
                "jamb", [a, b], [floor[end] - 0.2] * 2, top, ground, step, thickness=0.4
            )
            frame.triangles(jamb.positions, jamb.indices, jamb.uvs)
        header_edge = header_line
        if not road_top:
            header_edge = terrain_edge(header_line[0], header_line[1], step)
            along = ((header_edge - header_line[0]) @ vector) / (vector @ vector)
            bottom = np.interp(along, [0, 1], bottom)
        top = bottom + 0.3
        if not road_top:
            top = np.maximum(top, [ground(*q) for q in header_edge])
        header = profile_wall("header", header_edge, bottom, top, thickness=0.4)
        frame.triangles(header.positions, header.indices, header.uvs)
        mesh = frame.mesh(f"tunnel:{key}:portal:{end}", "wall")
        mesh.metadata.update(portal=True, approach_anchor=bool(repaired[i]))
        result.append(mesh)
    return result


def wings(key, xz, left, right, offset, direction, left_floor, right_floor, ground, step, lengths):
    """Retaining walls follow the sides of the cutting and taper into open ground."""
    from .kerbs import terrain_edge

    result = []
    for i, end, outward in ((0, 0, -direction[0]), (1, -1, direction[-1])):
        for sign, side, floor in ((1, left, left_floor), (-1, right, right_floor)):
            a = side[end] + sign * offset[end] * 0.2
            b = a + outward * lengths[i] + sign * offset[end] * 0.6
            edge = terrain_edge(a, b, step)
            if max(ground(*p) for p in edge) < floor[end] + 0.6:
                continue
            faces = Faces()
            for p, q in zip(edge[:-1], edge[1:], strict=True):
                h = np.array([ground(*p), ground(*q)])
                top = np.maximum(h + 0.05, floor[end] + 0.1)
                wall = grounded_wall(
                    "wing", [p, q], [floor[end] - 0.2] * 2, top, ground, step, thickness=0.4
                )
                faces.triangles(wall.positions, wall.indices, wall.uvs)
            if faces.indices:
                mesh = faces.mesh(f"tunnel:{key}:wing:{end}:{sign}", "wall")
                mesh.metadata["portal"] = True
                result.append(mesh)
    return result


def mouths(corridor, xz, left, right, direction, aprons, repaired):
    """Cut the entire portal width, including terrain triangles above the header."""
    bands = []
    for i, end, inward in ((0, 0, direction[0]), (1, -1, -direction[-1])):
        depth = 0.4 if repaired[i] else 2
        a, b = left[end], right[end]
        bands.append(shapely.Polygon([a, b, b + inward * depth, a + inward * depth]))
    return shapely.union_all([*(corridor.intersection(band) for band in bands), *aprons])


def terrain_visuals(meshes, tile, rgb, streets=(), regions=()):
    """Cut terrain keeps its ground imagery/materials, rather than tunnel concrete."""
    from . import surfaces

    result = []
    for mesh in meshes:
        if mesh.object_id != "tunnel:ground-edge":
            continue
        ids = surfaces.classify(
            mesh.positions[mesh.indices].mean(axis=1), rgb, tile, streets, regions
        )
        result.extend(
            surfaces.visuals(
                tile, mesh.positions, mesh.indices, ids, np.ones(len(ids), bool), ortho=rgb
            )
        )
    return result

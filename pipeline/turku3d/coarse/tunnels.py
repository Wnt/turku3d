"""Fast OSM box tunnels; measured open cuts keep their lidar road profile."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import lru_cache

import mapbox_earcut
import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces, Mesh, profile_wall

from .geometry import clip_mesh


@lru_cache(maxsize=8)
def _keypoints(path, size, mtime):
    """A bounded cache of complete raw blocks makes cross-tile tunnel profiles identical."""
    import laspy
    from scipy.spatial import cKDTree

    las = laspy.read(path)
    x, h, z = C.en_to_local(np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))
    return cKDTree(np.c_[x, z]), np.asarray(h)


def ground_sampler(fallback):
    """Sample full raw blocks at portals too, rather than clamping to this tile's edge."""

    def sample(x, z):
        e, n = C.ORIGIN_E + x, C.ORIGIN_N - z
        block = C.LazTile.at_en(e, n)
        path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        if not path.exists():
            return fallback(x, z)
        st = path.stat()
        tree, h = _keypoints(path, st.st_size, st.st_mtime_ns)
        distances, ids = tree.query([x, z], k=min(4, len(h)))
        weights = 1 / np.maximum(distances, 0.05) ** 2
        return float(np.sum(h[ids] * weights) / np.sum(weights))

    return sample


def dimensions(tags):
    """Clear box dimensions in metres, with conservative road/path defaults."""
    path = tags.get("highway") in {"footway", "path", "cycleway", "pedestrian", "steps"}
    try:
        lanes = float(tags.get("lanes", 2))
    except (TypeError, ValueError):
        lanes = 2
    width, height = (3.0, 2.5) if path else (max(4.0, lanes * 3.5 + 1.0), 4.5)
    for key, default in (("width", width), ("maxheight", height)):
        try:
            value = float(str(tags.get(key, default)).removesuffix(" m"))
        except ValueError:
            value = default
        if key == "width":
            width = float(np.clip(value, 1.5, 96))
        else:
            height = float(np.clip(value, 2, 8))
    return width, height


def selected(tags):
    if not (tags.get("highway") or tags.get("railway")):
        return False
    if tags.get("tunnel") in {"yes", "building_passage", "culvert"}:
        return True
    try:
        return float(tags.get("layer", 0)) < 0 or (
            tags.get("covered") == "yes" and float(tags.get("layer", 0)) <= 0
        )
    except ValueError:
        return False


def prepare(tile, path):
    """Keep full way extents for stable portals; clip only the emitted geometry to the tile."""
    from pathlib import Path

    from turku3d.spatial import rows

    from .area import cached, passages

    window = shapely.box(*tile.bbox.as_tuple()).buffer(100)

    def area_tunnels():
        from .road_top import carried_roads

        out, roads = [], []
        quay = passages(path)
        passage_ids = {key for key, _, _ in quay}
        for k, row in rows(path, columns=("osm_id", "tags", "geometry_local")):
            tags = json.loads(row["tags"]) if row["tags"] else {}
            line = shapely.force_2d(row["geometry_local"])
            if (
                tags.get("highway")
                and not selected(tags)
                and tags.get("bridge", "no") == "no"
                and tags.get("layer", "0") in ("0", "")
                and line.geom_type == "LineString"
            ):
                roads.append((line, tags))
            if not selected(tags) or str(row.get("osm_id", k)) in passage_ids:
                continue
            if line.geom_type == "LineString" and line.length > 0.5:
                out.append((str(row.get("osm_id", k)), line, tags))
        return carried_roads(merge_parallel([*out, *quay]), roads)

    ways = cached(
        path,
        "tunnels",
        [
            Path(__file__),
            Path(__file__).with_name("underpasses.py"),
            Path(__file__).with_name("road_top.py"),
            Path(__file__).with_name("bridge_details.py"),
        ],
        area_tunnels,
    )
    return [way for way in ways if way[1].intersects(window)]


def merge_parallel(ways):
    """Give adjacent parallel carriageways one clear box, including the space between lanes.

    Group complete OSM ways before tile filtering so neighbouring tiles use identical portals.
    Curved tunnels remain separate unless their full geometry fits the same near-straight axis.
    """
    groups = []
    for way in sorted(ways, key=lambda w: w[0]):
        key, line, tags = way
        width, clearance = dimensions(tags)
        coords = shapely.get_coordinates(line)
        axis = coords[-1] - coords[0]
        length = np.linalg.norm(axis)
        if length < 0.5:
            groups.append([way])
            continue
        axis /= length
        normal = np.array([-axis[1], axis[0]])
        motor = bool(tags.get("highway")) and tags["highway"] not in {
            "footway",
            "path",
            "cycleway",
            "pedestrian",
            "steps",
        }
        matches = []
        if motor and np.ptp(coords @ normal) < 2:
            for i, group in enumerate(groups):
                other = group[0]
                ot = other[2]
                oc = shapely.get_coordinates(other[1])
                oa = oc[-1] - oc[0]
                if np.linalg.norm(oa) < 0.5:
                    continue
                oa /= np.linalg.norm(oa)
                if (
                    ot.get("highway")
                    and ot["highway"] not in {"footway", "path", "cycleway", "pedestrian", "steps"}
                    and ot.get("layer", "0") == tags.get("layer", "0")
                    and (ot.get("tunnel") == "building_passage")
                    == (tags.get("tunnel") == "building_passage")
                    and abs(oa @ axis) > 0.98
                    and abs(dimensions(ot)[1] - clearance) < 0.5
                    and np.ptp(oc @ np.array([-oa[1], oa[0]])) < 2
                ):
                    a, b = coords @ axis, oc @ axis
                    overlap = min(a.max(), b.max()) - max(a.min(), b.min())
                    if overlap < 0.5 * min(np.ptp(a), np.ptp(b)):
                        continue
                    if any(
                        line.distance(w[1]) <= (width + dimensions(w[2])[0]) / 2 + 6 for w in group
                    ):
                        matches.append(i)
        merged = [way]
        for i in reversed(matches):
            merged.extend(groups.pop(i))
        groups.append(merged)
    out = []
    for group in groups:
        if len(group) == 1:
            out.extend(group)
            continue
        group.sort(key=lambda w: w[0])
        reference = max(group, key=lambda w: w[1].length)[1]
        origin = np.array(reference.coords[0])
        axis = np.array(reference.coords[-1]) - origin
        axis /= np.linalg.norm(axis)
        normal = np.array([-axis[1], axis[0]])
        footprints = [
            w[1].buffer(dimensions(w[2])[0] / 2, cap_style="flat", join_style="mitre")
            for w in group
        ]
        points = shapely.get_coordinates(shapely.union_all(footprints)) - origin
        along, across = points @ axis, points @ normal
        centre = origin + normal * (across.min() + across.max()) / 2
        line = shapely.LineString([centre + axis * along.min(), centre + axis * along.max()])
        tags = dict(
            group[0][2],
            width=str(across.max() - across.min()),
            maxheight=str(max(dimensions(w[2])[1] for w in group)),
        )
        out.append(("+".join(w[0] for w in group), line, tags))
    return out


def floor_profile(heights, distance):
    """Follow a lidar dip; interpolate buried portions between measured portal heights.

    A ground surface above the portal chord is the overburden, never the tunnel floor.
    """
    chord = np.interp(distance, [distance[0], distance[-1]], [heights[0], heights[-1]])
    return np.minimum(heights, chord)


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


def passage_footprint(polygon):
    """Remove submillimetre cut slivers before roof triangulation and float32 collision."""
    return polygon.simplify(0.0005, preserve_topology=True)


def _polygon_faces(faces, polygon, height):
    for poly in shapely.get_parts(polygon):
        if poly.geom_type != "Polygon" or poly.area < 1e-10:
            continue
        rings = [np.asarray(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]]
        flat = np.concatenate(rings)
        idx = mapbox_earcut.triangulate_float64(
            flat, np.cumsum([len(r) for r in rings]).astype(np.uint32)
        )
        for tri in idx.reshape(-1, 3):
            xz = flat[tri]
            p = np.c_[xz[:, 0], height(xz), xz[:, 1]]
            if np.cross(p[1] - p[0], p[2] - p[0])[1] < 0:
                p = p[::-1]
            faces.face(p)


def ground_cuts(tree, positions, indices, corridor, mouths, floor_height, clearance):
    """Intersect and classify candidate terrain triangles in native batches."""
    candidates = tree.query(corridor, predicate="intersects")
    triangles = positions[indices[candidates]]
    polygons = shapely.polygons(triangles[:, :, [0, 2]])
    overlap = shapely.intersection(polygons, corridor)
    valid = shapely.area(overlap) >= 1e-10
    candidates, triangles, polygons, overlap = (
        a[valid] for a in (candidates, triangles, polygons, overlap)
    )
    if not len(candidates):
        return []
    query, owner = shapely.get_coordinates(overlap, return_index=True)
    matrix = np.concatenate([triangles[:, :, [0, 2]], np.ones((*triangles.shape[:2], 1))], axis=2)
    coeff = np.linalg.solve(matrix, triangles[:, :, 1, None])[:, :, 0]
    delta = np.sum(query * coeff[owner, :2], axis=1) + coeff[owner, 2] - floor_height(query)
    low, high = np.full(len(candidates), np.inf), np.full(len(candidates), -np.inf)
    np.minimum.at(low, owner, delta)
    np.maximum.at(high, owner, delta)
    mouth_only = (high <= -0.25) | (low >= clearance + 0.35)
    accepted = np.ones(len(candidates), bool)
    accepted[mouth_only] = shapely.area(shapely.intersection(polygons[mouth_only], mouths)) >= 1e-10
    return [
        (int(tid), mouths if only else corridor)
        for tid, only, keep in zip(candidates, mouth_only, accepted, strict=True)
        if keep
    ]


@dataclass
class Tunnels:
    keep: np.ndarray
    meshes: list
    profiles: list
    shells: list
    count: int
    ceilings: list = field(default_factory=list)  # collision already belongs to the building shell

    def visuals(self, tile):
        from .bridge_details import texture

        return [
            GlbMesh(
                m.object_id,
                m.positions - tile.origin,
                m.indices,
                uvs=m.uvs,
                color=(1, 0.92, 0.65, 1)
                if m.metadata.get("lamp")
                else (1, 1, 1, 1)
                if m.metadata.get("vault")
                else (0.22, 0.23, 0.24, 1),
                image=texture("stone") if m.metadata.get("vault") else None,
                repeat=True,
                emissive=(1, 0.85, 0.5) if m.metadata.get("lamp") else (0, 0, 0),
                extras=dict(
                    layer="structures",
                    object_id=m.object_id,
                    material_key="draft_bridge_stone"
                    if m.metadata.get("vault")
                    else "tunnel_concrete",
                ),
            )
            for m in [*self.meshes, *self.ceilings]
            if m.visual
        ]

    def collision(self, ct, pos, idx, mat):
        positions, indices, materials = [pos], [idx], [mat]
        count = len(pos)
        for mesh in self.meshes:
            if mesh.metadata.get("lamp"):
                continue
            # Duplicate boundary triangles into both cells, so either streamed cell seals the tube.
            triangles = mesh.positions[mesh.indices][:, :, [0, 2]]
            b = ct.bbox
            keep = (triangles.max(axis=1) >= [b.xmin, b.zmin]).all(axis=1) & (
                triangles.min(axis=1) <= [b.xmax, b.zmax]
            ).all(axis=1)
            if not keep.any():
                continue
            positions.append(mesh.positions)
            indices.append(mesh.indices[keep] + count)
            materials.append(np.full(keep.sum(), mat_id(mesh.material), np.uint8))
            count += len(mesh.positions)
        return np.concatenate(positions), np.concatenate(indices), np.concatenate(materials)

    def roads(self, roads):
        if not roads or not self.profiles:
            return roads
        graph = json.loads(roads["json"])
        # Parallel optional field: one floor-height array per edge, in its vertex order.
        graph["edgeHeights"] = graph.get("edgeHeights", [None] * len(graph["edges"]))
        nodes = np.asarray(graph["nodes"]).reshape(-1, 2)
        for k, edge in enumerate(graph["edges"]):
            coords = np.vstack(
                [nodes[edge[0]], np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]]
            )
            line = shapely.LineString(coords)
            for centre, distances, heights, width in self.profiles:
                if centre.buffer(width / 2 + 0.2).covers(line):
                    ds = shapely.line_locate_point(centre, shapely.points(coords))
                    if np.abs(np.diff(ds)).sum() < line.length * 0.8:
                        continue  # a short road crossing above the tunnel is not its floor
                    dense = []
                    for a, b in zip(coords[:-1], coords[1:], strict=True):
                        steps = max(1, int(np.ceil(np.linalg.norm(b - a) / 2)))
                        dense.extend(np.linspace(a, b, steps + 1)[:-1])
                    coords = np.vstack([*dense, coords[-1]])
                    graph["edges"][k] = [*edge[:4], *coords[1:-1].ravel().tolist()]
                    ds = shapely.line_locate_point(centre, shapely.points(coords))
                    graph["edgeHeights"][k] = np.round(
                        np.interp(ds, distances, heights), 3
                    ).tolist()
                    break
        return {**roads, "json": (json.dumps(graph, separators=(",", ":")) + "\n").encode()}


def produce(
    tile, ways, grid_pos, grid_idx, shells, extrude, sample, surface_sample=None, deck_sample=None
):
    """Produce sealed tubes, exact ground remainders and passage openings in draft buildings."""
    from .inputs import CELLS
    from .kerbs import terrain_patches

    keep = np.ones(len(grid_idx), bool)
    original_bases = {b.key: b.base for b, _ in shells}
    meshes, profiles, passages = [], [], []
    cuts = {}
    if not ways:
        return Tunnels(keep, meshes, profiles, shells, 0)
    window = shapely.box(*tile.bbox.as_tuple())
    bounds = tile.bbox.as_tuple()
    clip_low, clip_high = np.array(bounds[:2]), np.array(bounds[2:])
    tree = None
    for key, line, tags in merge_parallel(ways):
        passage = tags.get("tunnel") == "building_passage"
        width, clearance = dimensions(tags)
        corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
        if (
            passage
            and not corridor.intersects(window)
            and not any(b.footprint.intersects(corridor) for b, _ in shells)
        ):
            # The area cache includes a 100 m margin. An unrelated neighbouring
            # passage must not prepare its surrounding ground tiles or lidar.
            continue
        road_top = "road_top" in tags and deck_sample is not None
        vaulted = tags.get("vault") == "barrel"
        spring = clearance - width / 2 if vaulted else clearance
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length / 2)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        height_at = surface_sample if passage and surface_sample is not None else sample
        terrain = np.array([height_at(x, z) for x, z in xz])
        floor = terrain if passage else floor_profile(terrain, distances)
        if vaulted:
            floor = np.interp(distances, [0, line.length], [terrain[0], terrain[-1]])
        # Mitered offset sections meet at bends without cracks.
        direction = np.diff(xz, axis=0)
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        normals = np.c_[-direction[:, 1], direction[:, 0]]
        offset = np.vstack(
            [
                normals[0],
                (normals[:-1] + normals[1:])
                / np.maximum(1 + (normals[:-1] * normals[1:]).sum(axis=1), 0.1)[:, None],
                normals[-1],
            ]
        )
        left, right = xz + offset * width / 2, xz - offset * width / 2
        if road_top:
            # Keep the measured floor unless a bank is too low for the stated clearance.
            cover = np.min([[deck_sample(*q) for q in side] for side in (left, xz, right)], axis=0)
            floor = np.minimum(floor, cover - clearance - 0.3)
        if passage:
            side_floors = (
                np.array([[surface_sample(x, z) for x, z in side] for side in (left, right)])
                if surface_sample is not None
                else np.array([floor, floor])
            )
            passages.append((corridor, float(np.max(side_floors) + clearance + 0.35)))
            # Exposed ground under a building keeps its original relief. Replacing
            # it with a one-dimensional slab opens gaps across sloping entrances.
            if corridor.intersects(window):
                profiles.append((line, distances, floor, width))
            continue

        # OSM also maps roads below buildings as tunnel=yes or covered/layer ways.
        # The tube and the building shell must leave the same full-width corridor clear.
        # The lining occupies 0.4 m outside the clear road corridor. Cutting
        # only the road creates a second inward-facing building wall on its
        # inner face. Both tile owners use the complete way and the same cut.
        passages.append(
            (corridor.buffer(0.406, join_style="mitre"), float(np.max(floor) + clearance + 0.35))
        )
        portal_offsets = (
            np.array([surface_sample(*xz[0]) - floor[0], surface_sample(*xz[-1]) - floor[-1]])
            if surface_sample is not None
            else np.zeros(2)
        )

        def floor_height(
            query,
            line=line,
            distances=distances,
            floor=floor,
            portal_offsets=portal_offsets,
            road_top=road_top,
            clearance=clearance,
        ):
            ds = shapely.line_locate_point(line, shapely.points(query))
            h = np.interp(ds, distances, floor)
            if surface_sample is not None and not road_top:
                blend = np.clip(
                    1 - np.minimum(ds, line.length - ds) / min(2, line.length / 2), 0, 1
                )
                near = blend > 0
                centre = shapely.get_coordinates(shapely.line_interpolate_point(line, ds[near]))
                ground = np.array([surface_sample(x, z) for x, z in query[near]])
                road_height = np.array([surface_sample(x, z) for x, z in centre])
                # Banks and overburden beside the road are not portal floor. Retain
                # ordinary crossfall, but never pull a floor up the enclosing hillside.
                crossfall = 0.1 * np.linalg.norm(query[near] - centre, axis=1)
                endpoint = np.where(ds[near] < line.length / 2, 0, 1)
                correction = portal_offsets[endpoint] + np.clip(
                    ground - road_height, -crossfall, crossfall
                )
                h[near] += correction * blend[near]
            if road_top:
                h = np.minimum(h, [deck_sample(*q) - clearance - 0.3 for q in query])
            return h

        left_floor, right_floor = (floor_height(side) for side in (left, right))
        tube, road, lamps = Faces(), Faces(), Faces()
        for i in range(len(xz) - 1):
            j = i + 1
            section = shapely.Polygon([left[i], left[j], right[j], right[i]])
            if not section.intersects(window):
                continue

            def point(side, k, h=0, left=left, left_floor=left_floor, right_floor=right_floor):
                side_floor = left_floor if side is left else right_floor
                return [side[k, 0], side_floor[k] + h, side[k, 1]]

            # Floor up, ceiling down; outer ceiling closes the walkable top as well.
            for patch in terrain_patches(
                np.array([left[i], left[j], right[j], right[i]]), tile.size / CELLS, bounds
            ):
                patch = np.clip(patch, clip_low, clip_high)
                p = np.c_[patch[:, 0], floor_height(patch), patch[:, 1]]
                road.face(p[::-1], patch[::-1])
            if road_top:
                for patch in terrain_patches(
                    np.array([left[i], left[j], right[j], right[i]]), tile.size / CELLS, bounds
                ):
                    patch = np.clip(patch, clip_low, clip_high)
                    top = np.array([deck_sample(*q) - 0.3 for q in patch])
                    tube.face(np.c_[patch[:, 0], top, patch[:, 1]], patch)
            elif vaulted:
                # Semicircular intrados, parallel outer shell and annular portal faces.
                def arch(
                    k,
                    angle,
                    outer=0,
                    width=width,
                    xz=xz,
                    offset=offset,
                    left_floor=left_floor,
                    right_floor=right_floor,
                    spring=spring,
                ):
                    across = np.cos(angle) * (width / 2 + outer)
                    q = xz[k] + offset[k] * across
                    h = (left_floor[k] + right_floor[k]) / 2 + spring
                    return [q[0], h + np.sin(angle) * (width / 2 + outer), q[1]]

                angles = np.linspace(0, np.pi, 17)
                for a, b in zip(angles[:-1], angles[1:], strict=True):
                    tube.face([arch(i, a), arch(i, b), arch(j, b), arch(j, a)])
                    tube.face([arch(i, a, 0.3), arch(j, a, 0.3), arch(j, b, 0.3), arch(i, b, 0.3)])
                    if i == 0:
                        tube.face([arch(i, a), arch(i, a, 0.3), arch(i, b, 0.3), arch(i, b)])
                    if j == len(xz) - 1:
                        tube.face([arch(j, a), arch(j, b), arch(j, b, 0.3), arch(j, a, 0.3)])
            else:
                tube.face(
                    [
                        point(left, i, clearance),
                        point(right, i, clearance),
                        point(right, j, clearance),
                        point(left, j, clearance),
                    ]
                )
                tube.face(
                    [
                        point(left, i, clearance + 0.3),
                        point(left, j, clearance + 0.3),
                        point(right, j, clearance + 0.3),
                        point(right, i, clearance + 0.3),
                    ]
                )
            # Bright strips are ordinary geometry, keeping the draft free of dynamic point lights.
            if i % 6 == 0:
                c = (xz[i] + xz[j]) / 2
                y = (
                    (left_floor[i] + right_floor[i] + left_floor[j] + right_floor[j]) / 4
                    + clearance
                    - 0.03
                )
                lamps.face(
                    [
                        [c[0] - 0.4, y, c[1] - 0.1],
                        [c[0] + 0.4, y, c[1] - 0.1],
                        [c[0] + 0.4, y, c[1] + 0.1],
                        [c[0] - 0.4, y, c[1] + 0.1],
                    ]
                )
        if not road.indices:
            continue
        # Join the open approach to the portal floor outside the roof and wing walls.
        # Carve the same apron from terrain; otherwise its old triangles can still
        # cross the opening even when the tube itself is clear.
        aprons = []
        for end, outward in ((0, -direction[0]), (-1, direction[-1])):
            inner = np.array([left[end], right[end]])
            apron_length = 4 if road_top else 2
            outer = inner + outward * apron_length
            apron = shapely.Polygon([inner[0], outer[0], outer[1], inner[1]])
            aprons.append(apron)
            for patch in terrain_patches(
                np.array(apron.exterior.coords)[:-1], tile.size / CELLS, bounds
            ):
                patch = np.clip(patch, clip_low, clip_high)
                along = np.clip((patch - xz[end]) @ outward / apron_length, 0, 1)
                base = floor_height(patch)
                ground = np.array([(surface_sample or sample)(x, z) for x, z in patch])
                height = base + (ground - base) * along
                road.face(np.c_[patch[:, 0], height, patch[:, 1]][::-1], patch[::-1])
        cut_corridor = shapely.union_all([corridor, *aprons])
        # At the mouth the frame replaces the bank, including high triangles
        # whose tips used to survive above the clearance test as rock slabs.
        mouths = shapely.union_all(
            [corridor.intersection(shapely.Point(*point).buffer(2)) for point in (xz[0], xz[-1])]
            + aprons
        )
        profiles.append((line, distances, floor_height(xz), width))
        meshes.append(
            road.mesh(
                f"tunnel:{key}:floor",
                "paving_slab"
                if tags.get("highway") in {"footway", "path", "pedestrian", "steps"}
                else "road",
            )
        )
        meshes.append(tube.mesh(f"tunnel:{key}:roof", "wall"))
        if lamps.indices:
            lamp = lamps.mesh(f"tunnel:{key}:lights", "wall")
            lamp.metadata["lamp"] = True
            meshes.append(lamp)
        for side, side_floor, name in (
            (left, left_floor, "left"),
            (right, right_floor, "right"),
        ):
            # Offset curves can reverse at tight OSM corners. Emit each measured section
            # into one mesh instead of mitering the already-mitered side a second time.
            wall = Faces()
            centres = side + (1 if name == "left" else -1) * offset * 0.2
            # A wall's 0.4 m footprint cannot reach this tile from outside this box.
            # Keep the complete original section wherever it can intersect, so
            # foundation heights and seam geometry stay identical.
            visible = np.all(np.maximum(centres[:-1], centres[1:]) + 0.2 >= clip_low, axis=1)
            visible &= np.all(np.minimum(centres[:-1], centres[1:]) - 0.2 <= clip_high, axis=1)
            visible &= np.linalg.norm(np.diff(side, axis=0), axis=1) >= 1e-6
            for i in np.flatnonzero(visible):
                section = grounded_wall(
                    f"tunnel:{key}:{name}",
                    centres[i : i + 2],
                    side_floor[i : i + 2] - 0.2,
                    np.array([deck_sample(*q) for q in centres[i : i + 2]])
                    if road_top
                    else side_floor[i : i + 2] + spring + 0.3,
                    surface_sample or sample,
                    tile.size / CELLS,
                    thickness=0.4,
                )
                wall.triangles(section.positions, section.indices, section.uvs)
            if wall.indices:
                meshes.append(wall.mesh(f"tunnel:{key}:{name}", "wall"))
        for end in () if vaulted else (0, -1):
            # A single U-shaped frame owns the mouth. Its jambs and thickness
            # stay outside the clear carriageway, including at skewed portals.
            frame = Faces()
            header_line = np.array([left[end] + offset[end] * 1.7, right[end] - offset[end] * 1.7])
            header_bottom = (
                np.array([deck_sample(*q) for q in header_line]) - 0.3
                if road_top
                else np.array([left_floor[end], right_floor[end]]) + clearance
            )
            header_vector = header_line[1] - header_line[0]
            for sign, side, side_floor in ((1, left, left_floor), (-1, right, right_floor)):
                a = side[end] + sign * offset[end] * 0.2
                b = a + sign * offset[end] * 1.5
                jamb = grounded_wall(
                    "jamb",
                    [a, b],
                    [side_floor[end] - 0.2] * 2,
                    header_bottom[0]
                    + ((np.array([a, b]) - header_line[0]) @ header_vector)
                    / (header_vector @ header_vector)
                    * (header_bottom[1] - header_bottom[0]),
                    surface_sample or sample,
                    tile.size / CELLS,
                    thickness=0.4,
                )
                frame.triangles(jamb.positions, jamb.indices, jamb.uvs)
            header = profile_wall(
                "header",
                header_line,
                header_bottom,
                header_bottom + 0.3,
                thickness=0.4,
            )
            frame.triangles(header.positions, header.indices, header.uvs)
            meshes.append(frame.mesh(f"tunnel:{key}:portal:{end}", "wall"))
        if road_top:
            for mesh in meshes:
                if mesh.object_id.startswith(f"tunnel:{key}:") and not mesh.object_id.endswith(
                    ":floor"
                ):
                    if mesh.metadata.get("lamp"):
                        mesh.positions[:, 1] = [
                            deck_sample(x, z) - 0.301 for x, _, z in mesh.positions
                        ]
                    mesh.positions[:, 1] = np.minimum(
                        mesh.positions[:, 1],
                        [deck_sample(x, z) - 0.03 for x, _, z in mesh.positions],
                    )
        if vaulted:
            for mesh in meshes:
                if mesh.object_id.startswith(f"tunnel:{key}:") and not mesh.metadata.get("lamp"):
                    mesh.metadata["vault"] = True
        if tree is None:
            tree = shapely.STRtree(shapely.polygons(grid_pos[grid_idx][:, :, [0, 2]]))
        for tid, cut in ground_cuts(
            tree,
            grid_pos,
            grid_idx,
            cut_corridor.difference(corridor) if road_top else cut_corridor,
            shapely.union_all(aprons) if road_top else mouths,
            floor_height,
            clearance,
        ):
            keep[tid] = False
            cuts[tid] = cuts[tid].union(cut) if tid in cuts else cut
    remainder = Faces()
    for tid, cut in cuts.items():
        p = grid_pos[grid_idx[tid]]
        poly = shapely.Polygon(p[:, [0, 2]]).difference(cut)
        coeff = np.linalg.solve(np.c_[p[:, [0, 2]], np.ones(3)], p[:, 1])
        _polygon_faces(remainder, poly, lambda q, coeff=coeff: np.c_[q, np.ones(len(q))] @ coeff)
    if remainder.indices:
        meshes.append(remainder.mesh("tunnel:ground-edge", "paving_slab"))
    for corridor, ceiling in passages:
        updated = []
        for b, shell in shells:
            if not b.footprint.intersects(corridor):
                updated.append((b, shell))
                continue
            for footprint, base in (
                (b.footprint.difference(corridor), b.base),
                (b.footprint.intersection(corridor), max(b.base, ceiling)),
            ):
                for poly in shapely.get_parts(footprint):
                    if poly.geom_type == "Polygon" and poly.area > 0.01 and base < b.top:
                        part = copy.copy(b)
                        part.footprint = shapely.geometry.polygon.orient(passage_footprint(poly), 1)
                        part.base = base
                        if base > b.base:
                            part.facade_passage = dict(floor=b.base, ceiling=base)
                        part.attributes = b.attributes.copy()
                        centre = b.footprint.centroid
                        part._collision_owner = getattr(
                            b, "_collision_owner", C.CollisionTile.at(centre.x, centre.y)
                        )
                        shell = extrude(part)
                        if len(shell[0]):
                            updated.append((part, shell))
        shells = updated
    meshes = [clipped for mesh in meshes if (clipped := clip_mesh(mesh, tile.bbox)) is not None]
    # The building visual producer exports roofs and walls. Expose precisely the
    # raised shell's downward floor here, sharing its existing collision surface.
    ceilings = [
        Mesh(
            f"passage:{b.key}:{i}:ceiling",
            "building",
            shell[2],
            shell[3],
            shell[2][:, [0, 2]],
        )
        for i, (b, shell) in enumerate(shells)
        if b.base > original_bases[b.key] + 1e-6 and getattr(b, "visual_owner", tile.id) == tile.id
    ]
    return Tunnels(keep, meshes, profiles, shells, len(profiles), ceilings)

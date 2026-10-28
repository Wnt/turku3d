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
    width, height = (3.0, 2.5) if path else (7.0, 4.5)
    for key, default in (("width", width), ("maxheight", height)):
        try:
            value = float(str(tags.get(key, default)).removesuffix(" m"))
        except ValueError:
            value = default
        if key == "width":
            width = float(np.clip(value, 1.5, 24))
        else:
            height = float(np.clip(value, 2, 8))
    return width, height


def selected(tags):
    if not (tags.get("highway") or tags.get("railway")):
        return False
    if tags.get("tunnel") in {"yes", "building_passage", "culvert"}:
        return True
    try:
        return tags.get("covered") == "yes" and float(tags.get("layer", 0)) < 0
    except ValueError:
        return False


def prepare(tile, path):
    """Keep full way extents for stable portals; clip only the emitted geometry to the tile."""
    import pyarrow.parquet as pq

    names = pq.read_schema(path).names
    table = pq.read_table(
        path, columns=[c for c in ("osm_id", "tags", "geometry_local") if c in names]
    )
    window = shapely.box(*tile.bbox.as_tuple()).buffer(25)
    out = []
    for k, row in enumerate(table.to_pylist()):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if not selected(tags):
            continue
        line = shapely.force_2d(shapely.from_wkb(row["geometry_local"]))
        if line.geom_type == "LineString" and line.length > 0.5 and line.intersects(window):
            out.append((str(row.get("osm_id", k)), line, tags))
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


@dataclass
class Tunnels:
    keep: np.ndarray
    meshes: list
    profiles: list
    shells: list
    count: int
    ceilings: list = field(default_factory=list)  # collision already belongs to the building shell

    def visuals(self, tile):
        return [
            GlbMesh(
                m.object_id,
                m.positions - tile.origin,
                m.indices,
                uvs=m.uvs,
                color=(1, 0.92, 0.65, 1) if m.metadata.get("lamp") else (0.22, 0.23, 0.24, 1),
                emissive=(1, 0.85, 0.5) if m.metadata.get("lamp") else (0, 0, 0),
                extras=dict(
                    layer="structures", object_id=m.object_id, material_key="tunnel_concrete"
                ),
            )
            for m in [*self.meshes, *self.ceilings]
            if m.visual
        ]

    def collision(self, ct, pos, idx, mat):
        positions, indices, materials = [pos], [idx], [mat]
        count = len(pos)
        for mesh in self.meshes:
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


def produce(tile, ways, grid_pos, grid_idx, shells, extrude, sample, surface_sample=None):
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
    tree = shapely.STRtree(shapely.polygons(grid_pos[grid_idx][:, :, [0, 2]]))
    for key, line, tags in ways:
        passage = tags.get("tunnel") == "building_passage"
        width, clearance = dimensions(tags)
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length / 2)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        terrain = np.array([sample(x, z) for x, z in xz])
        if passage and surface_sample is not None:
            terrain = np.array([surface_sample(x, z) for x, z in xz])
        floor = terrain if passage else floor_profile(terrain, distances)
        corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
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

        portal_offsets = (
            np.array([surface_sample(*xz[0]) - floor[0], surface_sample(*xz[-1]) - floor[-1]])
            if surface_sample is not None
            else np.zeros(2)
        )

        def floor_height(
            query, line=line, distances=distances, floor=floor, portal_offsets=portal_offsets
        ):
            ds = shapely.line_locate_point(line, shapely.points(query))
            h = np.interp(ds, distances, floor)
            if surface_sample is not None:
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
            outer = inner + outward * 2
            apron = shapely.Polygon([inner[0], outer[0], outer[1], inner[1]])
            aprons.append(apron)
            for patch in terrain_patches(
                np.array(apron.exterior.coords)[:-1], tile.size / CELLS, bounds
            ):
                patch = np.clip(patch, clip_low, clip_high)
                along = np.clip((patch - xz[end]) @ outward / 2, 0, 1)
                base = floor_height(patch)
                ground = np.array([(surface_sample or sample)(x, z) for x, z in patch])
                height = base + (ground - base) * along
                road.face(np.c_[patch[:, 0], height, patch[:, 1]][::-1], patch[::-1])
        cut_corridor = shapely.union_all([corridor, *aprons])
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
            for i in range(len(side) - 1):
                if np.linalg.norm(side[i + 1] - side[i]) < 1e-6:
                    continue
                section = grounded_wall(
                    f"tunnel:{key}:{name}",
                    side[i : i + 2],
                    side_floor[i : i + 2] - 0.2,
                    side_floor[i : i + 2] + clearance + 0.3,
                    surface_sample or sample,
                    tile.size / CELLS,
                    thickness=0.4,
                )
                for tri in section.indices:
                    wall.face(section.positions[tri], section.uvs[tri])
            if wall.indices:
                meshes.append(wall.mesh(f"tunnel:{key}:{name}", "wall"))
        for end in (0, -1):
            # Portal wing walls leave the whole clear box open.
            for sign, side, side_floor in ((1, left, left_floor), (-1, right, right_floor)):
                a = side[end]
                b = a + sign * offset[end] * 1.5
                meshes.append(
                    grounded_wall(
                        f"tunnel:{key}:portal:{end}:{sign}",
                        [a, b],
                        [side_floor[end] - 0.2] * 2,
                        [side_floor[end] + clearance + 0.3] * 2,
                        surface_sample or sample,
                        tile.size / CELLS,
                    )
                )
        candidates = tree.query(cut_corridor, predicate="intersects")
        for tid in candidates:
            p = grid_pos[grid_idx[tid]]
            overlap = shapely.Polygon(p[:, [0, 2]]).intersection(cut_corridor)
            if overlap.area < 1e-10:
                continue
            query = shapely.get_coordinates(overlap)
            coeff = np.linalg.solve(np.c_[p[:, [0, 2]], np.ones(3)], p[:, 1])
            delta = np.c_[query, np.ones(len(query))] @ coeff - floor_height(query)
            # A triangle can cross the entire clear box with all three vertices
            # outside it. Test the height range on the actual corridor intersection.
            if delta.max() <= -0.25 or delta.min() >= clearance + 0.35:
                continue
            keep[tid] = False
            cuts[tid] = cuts[tid].union(cut_corridor) if tid in cuts else cut_corridor
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
        if b.base > original_bases[b.key] + 1e-6
    ]
    return Tunnels(keep, meshes, profiles, shells, len(profiles), ceilings)

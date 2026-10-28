"""Fast OSM box tunnels; measured open cuts keep their lidar road profile."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import mapbox_earcut
import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces, profile_wall

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


def cut_required(ground, floor, clearance):
    """Cut only ground that obstructs the clear box; retain walkable overburden."""
    return (ground > floor - 0.25) & (ground < floor + clearance + 0.35)


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
            for m in self.meshes
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


def produce(tile, ways, grid_pos, grid_idx, shells, extrude, sample):
    """Produce sealed tubes, exact ground remainders and passage openings in draft buildings."""
    from .inputs import Building

    keep = np.ones(len(grid_idx), bool)
    meshes, profiles, passages = [], [], []
    cuts = {}
    if not ways:
        return Tunnels(keep, meshes, profiles, shells, 0)
    window = shapely.box(*tile.bbox.as_tuple())
    tree = shapely.STRtree(shapely.polygons(grid_pos[grid_idx][:, :, [0, 2]]))
    for key, line, tags in ways:
        width, clearance = dimensions(tags)
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length / 2)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        terrain = np.array([sample(x, z) for x, z in xz])
        floor = floor_profile(terrain, distances)
        corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
        if tags.get("tunnel") == "building_passage":
            passages.append((corridor, float(np.max(floor) + clearance + 0.35)))
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
        tube, road, lamps = Faces(), Faces(), Faces()
        for i in range(len(xz) - 1):
            j = i + 1
            section = shapely.Polygon([left[i], left[j], right[j], right[i]])
            if not section.intersects(window):
                continue

            def point(side, k, h=0, floor=floor):
                return [side[k, 0], floor[k] + h, side[k, 1]]

            # Floor up, ceiling down; outer ceiling closes the walkable top as well.
            road.face([point(left, i), point(left, j), point(right, j), point(right, i)])
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
                y = (floor[i] + floor[j]) / 2 + clearance - 0.03
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
        profiles.append((line, distances, floor, width))
        meshes.extend(
            [
                tube.mesh(f"tunnel:{key}:roof", "wall"),
                road.mesh(
                    f"tunnel:{key}:floor",
                    "paving_slab"
                    if tags.get("highway") in {"footway", "path", "pedestrian", "steps"}
                    else "road",
                ),
            ]
        )
        if lamps.indices:
            lamp = lamps.mesh(f"tunnel:{key}:lights", "wall")
            lamp.metadata["lamp"] = True
            meshes.append(lamp)
        for side, name in ((left, "left"), (right, "right")):
            # Offset curves can reverse at tight OSM corners. Emit each measured section
            # into one mesh instead of mitering the already-mitered side a second time.
            wall = Faces()
            for i in range(len(side) - 1):
                if np.linalg.norm(side[i + 1] - side[i]) < 1e-6:
                    continue
                section = profile_wall(
                    f"tunnel:{key}:{name}",
                    side[i : i + 2],
                    floor[i : i + 2] - 0.2,
                    floor[i : i + 2] + clearance + 0.3,
                    thickness=0.4,
                )
                for tri in section.indices:
                    wall.face(section.positions[tri], section.uvs[tri])
            if wall.indices:
                meshes.append(wall.mesh(f"tunnel:{key}:{name}", "wall"))
        for end in (0, -1):
            # Portal wing walls leave the whole clear box open.
            for sign, side in ((1, left), (-1, right)):
                a = side[end]
                b = a + sign * offset[end] * 1.5
                meshes.append(
                    profile_wall(
                        f"tunnel:{key}:portal:{end}:{sign}",
                        [a, b],
                        [floor[end] - 0.2] * 2,
                        [floor[end] + clearance + 0.3] * 2,
                    )
                )
        candidates = tree.query(corridor, predicate="intersects")
        for tid in candidates:
            p = grid_pos[grid_idx[tid]]
            ds = shapely.line_locate_point(line, shapely.points(p[:, [0, 2]]))
            h = np.interp(ds, distances, floor)
            if not cut_required(p[:, 1], h, clearance).any():
                continue
            keep[tid] = False
            cuts[tid] = cuts[tid].union(corridor) if tid in cuts else corridor
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
                        part = Building(
                            b.key,
                            shapely.geometry.polygon.orient(passage_footprint(poly), 1),
                            base,
                            b.top,
                            b.material_key,
                        )
                        part.facade_key = b.facade_key
                        part.attributes = b.attributes.copy()
                        part.roof_parts = b.roof_parts
                        part.height_evidence = b.height_evidence
                        shell = extrude(part)
                        if len(shell[0]):
                            updated.append((part, shell))
        shells = updated
    meshes = [clipped for mesh in meshes if (clipped := clip_mesh(mesh, tile.bbox)) is not None]
    return Tunnels(keep, meshes, profiles, shells, len(profiles))

"""Collision of a location: what the player walks and drives on, from the compiled entities.

The game's physics worker takes `.col` cells on the 62.5 m grid (pipeline/turku3d/col, the format the prepared
scenes ship), loads the ones near the player and closes every edge that has no neighbour, so a location that lists
such cells is playable with the old game's whole collision path. One cell per ground entity (`ground/c_i_j` is that
grid already):

- ground: a 2.5 m grid over the entity's height raster (every fifth sample), so the floor has no holes;
- buildings: the triangles of the drawn mesh with vertices snapped to a coarse grid and collapsed triangles
  dropped: walls, roofs and panes stay closed, frames and sills go;
- objects with a collision shape (fences, lamps, posts, furniture, signals, tree trunks): one fixed primitive per
  instance. Knock-over and breakable objects are fixed too until the game moves them.

`start` is where a player begins: the nearest street a car may use, from OpenStreetMap, with its direction.
"""

from __future__ import annotations

import io
import math

import numpy as np

from turku3d.col import ColTile, write_col
from turku3d.col.format import MAX_FILE_BYTES, ColFormatError, Primitive
from turku3d.col.materials import mat_id


CELL = 62.5
#: Raster samples per ground triangle edge: 5 x 0.5 m.
GROUND_STEP = 5
#: Vertex grids tried for a cell's building triangles, finest first, until the cell fits the format's byte budget.
SNAPS = (0.2, 0.35, 0.6, 1.0)
MATERIAL = {"fence": "fence", "lamp": "pole", "signpost": "pole", "signal": "pole", "trees": "tree_trunk", "furniture": "street_furniture"}
DRIVABLE = ("residential", "tertiary", "secondary", "primary", "unclassified", "living_street", "service")


def cell_of(x: float, z: float) -> tuple[int, int]:
    return math.floor(x / CELL), math.floor(z / CELL)


def ground_mesh(store, row: dict) -> tuple[np.ndarray, np.ndarray]:
    field = row["heightfield"]
    columns, lines = field["size"]
    heights = np.frombuffer((store.root / field["url"]).read_bytes(), "<f4").reshape(lines, columns)
    r = np.unique(np.append(np.arange(0, lines, GROUND_STEP), lines - 1))
    c = np.unique(np.append(np.arange(0, columns, GROUND_STEP), columns - 1))
    y = heights[np.ix_(r, c)].astype(float)
    y = np.where(np.isfinite(y), y, np.nanmean(y) if np.isfinite(y).any() else 0.0)
    x = field["origin"][0] + c * field["spacing"]
    z = field["origin"][1] + r * field["spacing"]
    positions = np.column_stack([np.tile(x, len(r)), y.ravel(), np.repeat(z, len(c))])
    a = (np.arange(len(r) - 1)[:, None] * len(c) + np.arange(len(c) - 1)[None, :]).ravel()
    triangles = np.concatenate([np.column_stack([a, a + len(c), a + 1]), np.column_stack([a + len(c), a + len(c) + 1, a + 1])])
    return positions, triangles


def mesh_triangles(store, row: dict) -> np.ndarray:
    """The drawn mesh of an entity as triangle corners (T, 3, 3) in engine metres."""
    import trimesh

    scene = trimesh.load(io.BytesIO((store.root / row["url"]).read_bytes()), file_type="glb", process=False)
    mesh = scene.dump(concatenate=True) if isinstance(scene, trimesh.Scene) else scene
    return np.asarray(mesh.vertices, dtype=float)[np.asarray(mesh.faces)] + np.asarray(row["origin"], dtype=float)


def welded(corners: np.ndarray, snap: float) -> tuple[np.ndarray, np.ndarray]:
    """Triangle corners snapped to a grid: unique vertices and the triangles that keep an area, each once."""
    keys = np.round(corners.reshape(-1, 3) / snap).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    triangles = inverse.reshape(-1, 3)
    positions = unique * snap
    a, b, c = positions[triangles[:, 0]], positions[triangles[:, 1]], positions[triangles[:, 2]]
    keep = np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-4
    triangles = triangles[keep]
    _, first = np.unique(np.sort(triangles, axis=1), axis=0, return_index=True)
    triangles = triangles[np.sort(first)]
    used, remapped = np.unique(triangles, return_inverse=True)
    return positions[used], remapped.reshape(-1, 3)


def primitives(row: dict) -> list[tuple[float, float, Primitive]]:
    """(x, z, primitive in engine metres) for every instance of the entity that has a collision shape."""
    material = mat_id(MATERIAL.get(row["kind"], "street_furniture"))
    origin = np.asarray(row["origin"], dtype=float)
    out = []
    for item in row["instances"]:
        shape = item.get("collision")
        if not shape:
            continue
        size = shape["size"]
        for x, y, z, yaw, sx, sy, sz in item["transforms"]:
            turn = (0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2))
            if shape["shape"] == "box":
                half = (max(0.01, size[0] * abs(sx) / 2), max(0.01, size[1] * abs(sy) / 2), max(0.01, size[2] * abs(sz) / 2))
                centre = origin + (x, y + half[1], z)
                prim = Primitive.box(material, centre, half, turn)
            else:
                radius, half = max(0.02, size[0] * max(abs(sx), abs(sz))), max(0.02, size[1] * abs(sy) / 2)
                centre = origin + (x, y + half, z)
                make = Primitive.capsule if shape["shape"] == "capsule" else Primitive.cylinder
                prim = make(material, centre, max(0.01, half - radius) if shape["shape"] == "capsule" else half, radius, turn)
            out.append((float(centre[0]), float(centre[2]), prim))
    return out


def collision_cells(store, rows: list[dict], log=print) -> dict:
    """Writes the location's collision cells into the store; returns {cell id: {url, sha256, bytes, tris, h}}."""
    ground = {cell_of(row["origin"][0] + 1, row["origin"][2] + 1): row for row in rows if row["kind"] == "ground" and "heightfield" in row}
    solid: dict[tuple[int, int], list[np.ndarray]] = {}
    prims: dict[tuple[int, int], list[Primitive]] = {}
    for row in rows:
        if row["kind"] == "building" and row.get("url"):
            try:
                corners = mesh_triangles(store, row)
            except Exception as error:  # noqa: BLE001 - one unreadable mesh must not cost the location its collision
                log(f"  collision: {row['id']} left out: {type(error).__name__}: {error}")
                continue
            centres = corners.mean(axis=1)
            index = np.floor(centres[:, [0, 2]] / CELL).astype(int)
            for i, j in np.unique(index, axis=0):
                if (int(i), int(j)) in ground:
                    solid.setdefault((int(i), int(j)), []).append(corners[(index[:, 0] == i) & (index[:, 1] == j)])
        for x, z, prim in primitives(row):
            if cell_of(x, z) in ground:
                prims.setdefault(cell_of(x, z), []).append(prim)
    road, building = mat_id("road"), mat_id("building")
    cells, triangles, total = {}, 0, 0
    for (i, j), row in sorted(ground.items()):
        base = np.array([i * CELL, 0.0, j * CELL])
        floor, floor_triangles = ground_mesh(store, row)
        local = [Primitive(p.kind, p.material, tuple(np.asarray(p.center) - base), p.rotation, p.params) for p in prims.get((i, j), [])]
        raw = None
        for snap in SNAPS if (i, j) in solid else SNAPS[:1]:
            positions, faces, materials = [floor - base], [floor_triangles], [np.full(len(floor_triangles), road, np.uint8)]
            if (i, j) in solid:
                walls, wall_triangles = welded(np.concatenate(solid[(i, j)]) - base, snap)
                positions.append(walls)
                faces.append(wall_triangles + len(floor))
                materials.append(np.full(len(wall_triangles), building, np.uint8))
            col = ColTile(i, j, np.concatenate(positions), np.concatenate(faces), np.concatenate(materials), primitives=local, aoi_border=True)
            try:
                raw = write_col(col, encoding="raw")
            except ColFormatError as error:
                if "budget" not in str(error):
                    raise
                continue
            if len(raw) <= MAX_FILE_BYTES:
                break
        if raw is None:
            log(f"  collision: cell c_{i}_{j} does not fit the format's budget: left without collision")
            continue
        heights = col.positions[:, 1]
        cells[col.id] = {**store.put(raw, "col"), "tris": int(len(col.indices)), "h": [float(heights.min()), float(heights.max())]}
        triangles += len(col.indices)
        total += len(raw)
    log(f"  collision {len(cells)} cells, {triangles} triangles, {sum(len(v) for v in prims.values())} primitives, {total / 1e6:.1f} MB")
    return cells


def street_start(area, sources, log=print) -> dict | None:
    """Where a player starts: the point of a street for cars nearest to the query point, heading along the street."""
    import shapely

    reach = max(area.radius, 150.0)
    try:
        rows = sources.osm.features((area.e - reach, area.n - reach, area.e + reach, area.n + reach))
    except Exception as error:  # noqa: BLE001 - a location without OpenStreetMap starts at its query point
        log(f"  start: no streets: {type(error).__name__}: {error}")
        return None
    here, best = shapely.Point(area.e, area.n), None
    for rank, kinds in enumerate((DRIVABLE[:-1], DRIVABLE[-1:])):
        for line in rows[rows["highway"].isin(kinds)].geometry:
            if line.geom_type != "LineString" or line.length < 12:
                continue
            distance = line.distance(here) + rank * 60
            if best is None or distance < best[0]:
                best = (distance, line)
    if best is None:
        return None
    line = best[1]
    along = min(max(line.project(here), 6.0), line.length - 6.0)
    a, b = line.interpolate(along - 3), line.interpolate(along + 3)
    at = line.interpolate(along)
    heading = math.degrees(math.atan2(b.x - a.x, b.y - a.y)) % 360
    return {"E": round(at.x, 2), "N": round(at.y, 2), "headingDeg": round(heading, 1)}


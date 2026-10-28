"""Coarse 250 m tile in the published tile format, built in seconds from the fast sources.

Ground is a regular TIN with 250/128 m (~2 m) spacing interpolated from the city's ``maanpinta``
ground key points and textured with one true-ortho image. Buildings are CityGML LOD2 footprints
extruded to their mean roof height, walls with the library render material and roofs with the
same ortho. Street-area polygons only set collision material classes; water polygons become the
water sensor over a synthetic bed and a visible water surface (``material_key`` water) at the level.
OSM bridges become separate drivable slabs with clear underpasses and ground-blended approaches;
without OSM, street-area water bridges retain the abutment lift (``bridge_decks``).
OSM ``natural=tree`` points become GPU-instanced
vegetation prototypes (unmeasured, 10 m unless tagged) with trunk cylinders in the collision. The
tile has no curbs, stairs, structures, props, unique facade maps or LOD parents: the full pipeline
replaces it when the tile is published.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import mapbox_earcut
import numpy as np
import shapely
from PIL import Image

from turku3d import config as C
from turku3d.col.format import ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.facade_kit import candidates, ensure_kit, parameter_hash, parameters
from turku3d.facade_kit import code_hash as kit_hash
from turku3d.ground.mesh import Mesh
from turku3d.ground.water import add_river_bed
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh

from . import bridges, fences
from .inputs import CELLS, Building, Inputs, gather

FORMAT = "turku3d-coarse-tile"
VERSION = 1
BRIDGE = Path(C.REPO_ROOT / "pipeline/turku3d/package/visual.mjs")
WIRE_CAP = 8_000_000


def code_hash() -> str:
    """Producer identity: the coarse sources plus the shared visual packager they call."""
    h = hashlib.sha256(kit_hash().encode())
    for p in sorted([*Path(__file__).parent.glob("*.py"), *BRIDGE.parent.glob("*.mjs")]):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def grid_mesh(tile: C.VisualTile, heights: np.ndarray):
    n = CELLS + 1
    step = tile.size / CELLS
    x0, _, z0 = tile.origin
    gx, gz = np.meshgrid(x0 + np.arange(n) * step, z0 + np.arange(n) * step)
    positions = np.column_stack([gx.ravel(), heights.ravel(), gz.ravel()])
    r, c = np.meshgrid(np.arange(CELLS), np.arange(CELLS), indexing="ij")
    v00 = (r * n + c).ravel()
    v01, v10 = v00 + 1, v00 + n
    v11 = v10 + 1
    # Two up-facing triangles per cell; triangle 2k and 2k+1 belong to cell k (row-major).
    indices = np.stack([np.c_[v00, v10, v01], np.c_[v01, v10, v11]], axis=1).reshape(-1, 3)
    return positions, indices.astype(np.uint32)


def ground_materials(
    centroids: np.ndarray, rgb: np.ndarray, tile: C.VisualTile, streets
) -> np.ndarray:
    """Street-area polygons win; otherwise green ortho pixels are grass, the rest paving."""
    out = np.full(len(centroids), mat_id("paving_slab"), np.uint8)
    px = rgb.shape[0]
    col = np.clip(((centroids[:, 0] - tile.origin[0]) / tile.size * px).astype(int), 0, px - 1)
    row = np.clip(((centroids[:, 2] - tile.origin[2]) / tile.size * px).astype(int), 0, px - 1)
    r, g, b = (rgb[row, col, k].astype(float) for k in range(3))
    out[(g > 1.08 * r) & (g > 1.08 * b) & (g > 25)] = mat_id("grass")
    if streets:
        geoms = [g for g, _ in streets]
        ids = np.array([m for _, m in streets], np.uint8)
        qi, pi = shapely.STRtree(geoms).query(
            shapely.points(centroids[:, [0, 2]]), predicate="within"
        )
        out[qi] = ids[pi]
    return out


def water_mask(centroids: np.ndarray, water) -> np.ndarray:
    wet = np.zeros(len(centroids), bool)
    if water:
        qi, _ = shapely.STRtree(water).query(
            shapely.points(centroids[:, [0, 2]]), predicate="within"
        )
        wet[qi] = True
    return wet


def water_level(points: np.ndarray, water) -> float:
    """Low shoreline key points approximate the surface; no class-9 lidar in maanpinta."""
    if not water:
        return 0.0
    shore = shapely.union_all(water).buffer(10.0)
    near = shapely.contains_xy(shore, points[:, 0], points[:, 2])
    return float(max(-1.0, np.percentile(points[near, 1], 2))) if near.any() else 0.0


ABUTMENT_M = (
    5.0,
    25.0,
)  # dry ground this far beyond a deck end (m, along the deck) sets that end's height


def bridge_decks(
    grid_pos: np.ndarray, grid_idx: np.ndarray, materials: np.ndarray, wet: np.ndarray
) -> int:
    """Lift each connected bridge-deck patch that borders water to the line between its abutments; returns the count.

    ``maanpinta`` has no points on a deck: under it the key points are the river surface and the sloping banks, so
    the drawn and the collision deck sit at water level between quay-high road ends. Each end's height is the 90th
    percentile of the dry ground ``ABUTMENT_M`` beyond it inside the deck's corridor (its long axis, as wide as the
    deck); every dry vertex in the corridor between the two ends is raised to the line through them (never lowered).
    Decks that do not touch water (a viaduct over a street) stay put.
    """
    deck = materials == mat_id("bridge_deck")
    if not deck.any():
        return 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    tris = grid_idx[deck]
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    n = len(grid_pos)
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(n, n))
    _, label = connected_components(graph, directed=False)
    deck_vertices = np.unique(tris)
    dry = np.zeros(n, bool)
    dry[grid_idx[~wet].ravel()] = True
    water = np.zeros(n, bool)
    water[grid_idx[wet].ravel()] = True
    xz = grid_pos[:, [0, 2]]
    lifted = 0
    for comp in np.unique(label[deck_vertices]):
        verts = deck_vertices[label[deck_vertices] == comp]
        if not water[verts].any() or len(verts) < 3:
            continue
        centre = xz[verts].mean(axis=0)
        axis = np.linalg.svd(xz[verts] - centre, full_matrices=False)[2][0]
        t = (xz - centre) @ axis
        side = np.abs((xz - centre) @ np.array([-axis[1], axis[0]]))
        width = float(np.percentile(side[verts], 95)) + 1.0
        t0, t1 = t[verts].min(), t[verts].max()
        corridor = dry & (side <= width)
        lo, hi = ABUTMENT_M
        ends = []
        for sign, edge in ((-1, t0), (1, t1)):
            near = corridor & (sign * (t - edge) >= lo) & (sign * (t - edge) <= hi)
            if near.any():
                ends.append((edge + sign * lo, float(np.percentile(grid_pos[near, 1], 90))))
        if not ends:
            continue
        (ta, ha), (tb, hb) = (ends[0], ends[-1]) if len(ends) == 2 else (ends[0], ends[0])
        span = (
            corridor & (t >= min(ta, tb)) & (t <= max(ta, tb))
            if len(ends) == 2
            else np.isin(np.arange(n), verts)
        )
        line = ha + (hb - ha) * (t[span] - ta) / (tb - ta) if tb != ta else np.full(span.sum(), ha)
        grid_pos[span, 1] = np.maximum(grid_pos[span, 1], line)
        lifted += 1
    return lifted


def extrude(b: Building):
    """Compose closed roof-part solids while retaining the building ownership footprint."""
    from .roofs import above, plane_height, polygons

    centroid = b.footprint.centroid
    collision_origin = np.asarray(C.CollisionTile.at(centroid.x, centroid.y).origin)[[0, 2]]
    parts = []
    for polygon, plane in b.roof_parts:
        for piece in polygons(
            above(polygon.intersection(b.footprint), plane, b.base + 0.01).simplify(
                0.005, preserve_topology=True
            )
        ):
            part = Building(
                b.key, shapely.geometry.polygon.orient(piece, 1), b.base, b.top, b.material_key
            )
            parts.append(_extrude(part, lambda xz, p=plane: plane_height(p, xz), collision_origin))
    if not parts:
        if not b.roof_parts:
            return _extrude(b, lambda xz: np.full(len(xz), b.top), collision_origin)
        return tuple(
            np.empty((0, 2 if k == 5 else 3), dtype=np.uint32 if k in (1, 3, 6) else float)
            for k in range(7)
        )
    result = []
    for index in range(7):
        if index in (1, 3, 6):
            vertex = {1: 0, 3: 2, 6: 4}[index]
            offsets = np.cumsum([0] + [len(p[vertex]) for p in parts[:-1]])
            result.append(
                np.concatenate(
                    [p[index] + offset for p, offset in zip(parts, offsets, strict=True)]
                )
            )
        else:
            result.append(np.concatenate([p[index] for p in parts]))
    return tuple(result)


def _extrude(b: Building, height, collision_origin):
    """Closed shell: walls (with metre UVs), roof and floor. Returns parts in world-local metres."""
    rings = [np.asarray(r.coords)[:-1, :2] for r in [b.footprint.exterior, *b.footprint.interiors]]
    flat = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = mapbox_earcut.triangulate_float64(flat, ends).reshape(-1, 3).astype(np.uint32)
    roof = np.column_stack([flat[:, 0], height(flat), flat[:, 1]])
    a, bb, cc = roof[tri[:, 0]], roof[tri[:, 1]], roof[tri[:, 2]]
    up = np.cross(bb - a, cc - a)[:, 1] > 0
    roof_idx = np.where(up[:, None], tri, tri[:, [0, 2, 1]])
    # Polygon differences can leave collinear earcut ears. Test in export precision
    # before using the triangulation for both roof and floor. These have no usable area.
    for coords in (flat, flat.astype(np.float32), (flat - collision_origin).astype(np.float32)):
        projected = coords[roof_idx]
        ab, ac = projected[:, 1] - projected[:, 0], projected[:, 2] - projected[:, 0]
        area = np.abs(ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]) / 2
        roof_idx = roof_idx[area >= 2e-6]
    floor = roof.copy()
    floor[:, 1] = b.base
    wall_pos, wall_uv, wall_idx = [], [], []
    for ring in rings:
        nxt = np.roll(ring, -1, axis=0)
        for p, q in zip(ring, nxt, strict=True):
            length = float(np.hypot(*(q - p)))
            if length < 1e-6:
                continue
            k = len(wall_pos) * 4
            hp, hq = height(np.array([p, q]))
            wall_pos.append(
                [
                    [p[0], b.base, p[1]],
                    [q[0], b.base, q[1]],
                    [q[0], hq, q[1]],
                    [p[0], hp, p[1]],
                ]
            )
            wall_uv.append([[0, b.base], [length, b.base], [length, hq], [0, hp]])
            # The footprint is oriented counter-clockwise in (x, z), so these face outwards.
            wall_idx += [[k, k + 3, k + 2], [k, k + 2, k + 1]]
    wall_pos = np.asarray(wall_pos, float).reshape(-1, 3)
    wall_uv = np.asarray(wall_uv, float).reshape(-1, 2)
    wall_idx = np.asarray(wall_idx, np.uint32).reshape(-1, 3)
    return roof, roof_idx, floor, roof_idx[:, [0, 2, 1]], wall_pos, wall_uv, wall_idx


def jpeg(rgb: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(rgb).save(out, format="JPEG", quality=88)
    return out.getvalue()


def ortho_uv(tile: C.VisualTile, positions: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [
            (positions[:, 0] - tile.origin[0]) / tile.size,
            (positions[:, 2] - tile.origin[2]) / tile.size,
        ]
    )


def collision_tiles(tile, grid_pos, grid_idx, materials, level, shells, ground_keep=None):
    """Split the ground grid along the 62.5 m grid; each building goes to its centroid's tile."""
    per = CELLS // C.COLLISION_PER_VISUAL
    out = {}
    for ct in tile.collision_tiles():
        ci, cj = ct.i - tile.i * C.COLLISION_PER_VISUAL, ct.j - tile.j * C.COLLISION_PER_VISUAL
        rows = np.arange(cj * per, (cj + 1) * per)
        cols = np.arange(ci * per, (ci + 1) * per)
        cells = (rows[:, None] * CELLS + cols[None, :]).ravel()
        tris = np.stack([2 * cells, 2 * cells + 1], axis=1).ravel()
        if ground_keep is not None:
            tris = tris[ground_keep[tris]]
        mesh = Mesh(grid_pos, grid_idx[tris], materials[tris]).compact()
        mesh = add_river_bed(mesh, level)
        positions, indices, material = [mesh.positions], [mesh.indices], [mesh.material]
        count = len(mesh.positions)
        for b, (roof, roof_idx, floor, floor_idx, walls, _, wall_idx) in shells:
            c = b.footprint.centroid
            if C.CollisionTile.at(c.x, c.y) != ct:
                continue
            for pos, idx, mid in (
                (roof, roof_idx, mat_id("roof")),
                (floor, floor_idx, mat_id("building")),
                (walls, wall_idx, mat_id("building")),
            ):
                positions.append(pos)
                indices.append(idx + count)
                material.append(np.full(len(idx), mid, np.uint8))
                count += len(pos)
        out[ct] = (np.concatenate(positions), np.concatenate(indices), np.concatenate(material))
    return out


def grid_height(tile: C.VisualTile, grid_pos: np.ndarray, x: float, z: float) -> float:
    """Bilinear height of the ground grid at (x, z) inside the tile."""
    n, step = CELLS + 1, tile.size / CELLS
    heights = grid_pos[:, 1].reshape(n, n)
    u = min(max((x - tile.origin[0]) / step, 0.0), CELLS - 1e-9)
    v = min(max((z - tile.origin[2]) / step, 0.0), CELLS - 1e-9)
    i, j = int(u), int(v)
    fu, fv = u - i, v - j
    top = heights[j, i] * (1 - fu) + heights[j, i + 1] * fu
    bottom = heights[j + 1, i] * (1 - fu) + heights[j + 1, i + 1] * fu
    return float(top * (1 - fv) + bottom * fv)


def draft_trees(tile, grid_pos, trees, water, shells):
    """OSM trees on dry ground outside buildings, as vegetation layer trees (unmeasured)."""
    from turku3d.vegetation.trees import VARIANTS, Tree, variant

    blocked = shapely.union_all([*water, *(b.footprint for b, _ in shells)])
    result = []
    for object_id, x, z, height, conifer in trees:
        if not blocked.is_empty and blocked.covers(shapely.Point(x, z)):
            continue
        tree = Tree(object_id, x, z, "osm", y=grid_height(tile, grid_pos, x, z), height=height)
        tree.crown_radius = float(np.clip(height * 0.3, 1.5, 7.0))
        tree.trunk_radius = float(np.clip(height * 0.018, 0.09, 0.55))
        tree.variant = VARIANTS[3] if conifer else variant("", height)
        result.append(tree)
    return result


def trunks(trees, ct):
    """Trunk cylinders of the trees standing in collision tile ct (tile-local centres)."""
    from turku3d.col.format import Primitive

    result = []
    for t in trees:
        if C.CollisionTile.at(t.x, t.z) != ct:
            continue
        height = min(t.height * 0.55, 8.0)
        centre = (t.x - ct.origin[0], t.y + height / 2, t.z - ct.origin[2])
        result.append(Primitive.cylinder(mat_id("tree_trunk"), centre, height / 2, t.trunk_radius))
    return result


def default_library() -> Path:
    """The sandbox's assembled material library, else the published one."""
    library = C.work_root() / "textures/assembly/library"
    return library if library.exists() else C.data_root() / "live/textures/assembly/library"


class Packager:
    """One persistent ``node visual.mjs --serve`` per process: a build worker packages every tile
    without starting node and loading glTF-Transform again (about 0.3 s CPU, seconds of wall time
    on a loaded host). A packager that died is started again on the next tile."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def package(self, requests: Path) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.proc = subprocess.Popen(
                    ["node", str(BRIDGE), "--serve"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
            self.proc.stdin.write(f"{requests}\n")
            self.proc.stdin.flush()
            answer = self.proc.stdout.readline().strip()
        if answer != "ok":
            message = answer.removeprefix("error ")
            if answer.startswith("error "):
                message = json.loads(message)
            raise RuntimeError(f"visual packaging failed\n{message or 'the packager exited'}")


PACKAGER = Packager()


def package_visual(
    tile: C.VisualTile, layer_files: list[Path], stage: Path, work: Path, library: Path
) -> tuple[bytes, dict]:
    request = dict(
        tile=tile.id,
        level=0,
        inputs=[str(p) for p in layer_files],
        output=str(stage / "out" / "tile.glb"),
        stats=str(stage / "out" / "stats.json"),
        library=str(library),
        facades=str(stage / "no-facades"),
        facadeBindings=[],
        facadeDensity={},
        groundBinding=None,
        textureCache=str(work / ".texture-cache"),
        coarseTerrain=False,
        draftTextures=True,
        facadeKit=ensure_kit(work / "shared"),
    )
    (stage / "out").mkdir(parents=True, exist_ok=True)
    req = stage / "requests.json"
    req.write_text(json.dumps([request]))
    PACKAGER.package(req)
    stats = json.loads((stage / "out" / "stats.json").read_text())
    shared = stage / "shared"
    if shared.exists():
        (work / "shared").mkdir(parents=True, exist_ok=True)
        for asset in shared.iterdir():
            dest = work / "shared" / asset.name
            if not dest.exists():
                shutil.copyfile(asset, dest)
    return (stage / "out" / "tile.glb").read_bytes(), stats


def _entry(url: str, data: bytes, **extra) -> dict:
    return dict(url=url, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), **extra)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def build(
    tile_id: str,
    out: Path,
    *,
    inputs: Inputs | None = None,
    library: Path | None = None,
    sources=None,
) -> dict:
    """Write ``out/{manifest.json, vis.glb, col/c_i_j.col, roads.json}``; shared KTX2 go to out/../shared.

    ``roads.json`` is the tile's OSM street graph (``turku3d.roads`` format, clipped to the tile, every
    connected piece kept); it is absent without an OSM extract.
    """
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError("coarse tiles are L0 (250 m) tiles")
    started = time.perf_counter()
    inputs = inputs or gather(tile, out.parent, sources)
    seconds = dict(inputs.seconds)

    t = time.perf_counter()
    grid_pos, grid_idx = grid_mesh(tile, inputs.heights)
    centroids = grid_pos[grid_idx].mean(axis=1)
    water = inputs.water
    materials = ground_materials(centroids, inputs.ortho, tile, inputs.streets)
    # A bridge deck stays solid where a water polygon runs under it.
    separate_decks = bool(inputs.roads and inputs.roads.get("bridge_ways"))
    wet = water_mask(centroids, water)
    if not separate_decks:
        wet &= materials != mat_id("bridge_deck")
    level = water_level(inputs.points, water)
    if wet.any():
        materials[wet] = mat_id("water")
        wet_vertices = np.unique(grid_idx[wet])
        dry_vertices = np.unique(grid_idx[~wet])
        inner = np.setdiff1d(wet_vertices, dry_vertices)
        grid_pos[inner, 1] = np.minimum(grid_pos[inner, 1], level)
    # OSM decks are separate solids: lifting the span's ground blocks underpasses.
    t_bridges = time.perf_counter()
    osm_decks = bridges.produce(tile, grid_pos, inputs)
    decks = 0 if osm_decks else bridge_decks(grid_pos, grid_idx, materials, wet)
    seconds["bridges"] = time.perf_counter() - t_bridges
    seconds["ground"] = time.perf_counter() - t

    t = time.perf_counter()
    blds = inputs.buildings
    facade_params = parameter_hash(b.facade_key for b in blds)
    shells = [(b, extrude(b)) for b in blds]
    seconds["buildings"] = time.perf_counter() - t

    from .tunnels import ground_sampler
    from .tunnels import produce as produce_tunnels

    t = time.perf_counter()
    tunnels = produce_tunnels(
        tile,
        inputs.tunnels,
        grid_pos,
        grid_idx,
        shells,
        extrude,
        ground_sampler(lambda x, z: grid_height(tile, grid_pos, x, z)),
    )
    shells = tunnels.shells
    seconds["tunnels"] = time.perf_counter() - t

    t = time.perf_counter()
    image = jpeg(inputs.ortho)
    origin = np.asarray(tile.origin)
    stage = out / ".stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    ground_meshes = [
        GlbMesh(
            f"ground_{tile.id}",
            grid_pos - origin,
            grid_idx[~wet & tunnels.keep],
            uvs=ortho_uv(tile, grid_pos),
            image=image,
            extras=dict(
                layer="ground",
                object_id=f"ground_{tile.id}",
                material_key="ortho",
                texture_uv="normalized",
            ),
        )
    ]
    if wet.any():
        # A visible water surface at the level; the game draws material_key water as a river.
        ids, inverse = np.unique(grid_idx[wet], return_inverse=True)
        surface = grid_pos[ids].copy()
        surface[:, 1] = level
        ground_meshes.append(
            GlbMesh(
                f"water_{tile.id}",
                surface - origin,
                inverse.reshape(-1, 3).astype(np.uint32),
                uvs=surface[:, [0, 2]] * [1, -1],
                extras=dict(
                    layer="ground", object_id=f"water_{tile.id}", material_key="water", sensor=True
                ),
            )
        )
    write_visual(stage / "ground.vis.glb", ground_meshes, root_translation=tile.origin)
    layer_files = [stage / "ground.vis.glb"]
    bridges.visual(osm_decks, tile, stage, layer_files)
    if tunnels.meshes:
        write_visual(stage / "tunnels.vis.glb", tunnels.visuals(tile), root_translation=tile.origin)
        layer_files.append(stage / "tunnels.vis.glb")
    from .forests import BUSH_CAP, TREE_CAP, produce

    vegetation_start = time.perf_counter()
    trees, bushes = produce(
        tile,
        inputs,
        lambda x, z: grid_height(tile, grid_pos, x, z),
        draft_trees(tile, grid_pos, inputs.trees, water, shells),
    )
    seconds["vegetation_scatter"] = time.perf_counter() - vegetation_start
    if trees or bushes:
        from turku3d.vegetation.visual import write_instances

        write_instances(stage / "vegetation.vis.glb", tile, trees + bushes)
        layer_files.append(stage / "vegetation.vis.glb")
    if shells:
        roof_pos, roof_idx = [], []
        rc = 0
        for _, (roof, ridx, _, _, _walls, _wuv, _widx) in shells:
            roof_pos.append(roof)
            roof_idx.append(ridx + rc)
            rc += len(roof)
        roof_pos = np.concatenate(roof_pos)
        meshes = [
            GlbMesh(
                f"roofs_{tile.id}",
                roof_pos - origin,
                np.concatenate(roof_idx),
                uvs=ortho_uv(tile, roof_pos),
                image=image,
                extras=dict(
                    layer="buildings",
                    object_id=f"roofs_{tile.id}",
                    material_key="roof_ortho",
                    surface="roof",
                    texture_uv="normalized",
                ),
            ),
        ]
        for b, (_, _, _, _, walls, wuv, widx) in shells:
            width = max(float(wuv[:, 0].max()), 1.0)
            params = parameters(b.facade_key, b.top - b.base, width, getattr(b, "attributes", {}))
            meshes.append(
                GlbMesh(
                    f"walls_{b.key}",
                    walls - origin,
                    widx,
                    uvs=wuv,
                    color=(1, 1, 1, 1),
                    extras=dict(
                        layer="buildings",
                        object_id=f"walls_{b.key}",
                        building_id=b.facade_key,
                        material_key="facade_kit",
                        base_height=b.base,
                        top_height=b.top,
                        facade_kit=params,
                    ),
                )
            )
        write_visual(stage / "buildings.vis.glb", meshes, root_translation=tile.origin)
        layer_files.append(stage / "buildings.vis.glb")
    barrier_cols, barrier_stats = fences.produce(
        tile,
        inputs,
        stage,
        layer_files,
        lambda x, z: grid_height(tile, grid_pos, x, z),
    )
    seconds["fences"] = barrier_stats["seconds"]
    vis, stats = package_visual(tile, layer_files, stage, out.parent, library or default_library())
    seconds["visual"] = time.perf_counter() - t
    if stats["brotli_bytes"] > WIRE_CAP:
        raise ValueError(
            f"{tile.id}: coarse visual exceeds 8 MB on the wire: {stats['brotli_bytes']}"
        )

    t = time.perf_counter()
    collision = {}
    cols = {}
    for ct, (pos, idx, mat) in collision_tiles(
        tile, grid_pos, grid_idx, materials, level, shells, tunnels.keep
    ).items():
        pos, idx, mat = bridges.collision(osm_decks, tile, ct, pos, idx, mat)
        pos, idx, mat = tunnels.collision(ct, pos, idx, mat)
        primitives = trunks(trees, ct) + barrier_cols[ct]
        col = ColTile.for_tile(ct.i, ct.j, pos, idx, mat, primitives=primitives, aoi_border=False)
        data = write_col(col, encoding="meshopt", optimize=False)
        bounds_lo, bounds_hi = col.compute_bbox()
        lo, hi = bounds_lo[1], bounds_hi[1]
        cols[ct.id] = data
        collision[ct.id] = _entry(
            f"col/{ct.id}.col",
            data,
            tris=len(idx),
            prims=len(primitives),
            h=[float(lo), float(hi)],
            flags=col.computed_flags(),
        )
    seconds["collision"] = time.perf_counter() - t

    roads = tunnels.roads(bridges.road_heights(inputs.roads, osm_decks))

    _write(out / "facade-candidates.json", json.dumps(candidates(blds)).encode())
    _write(out / "vis.glb", vis)
    if roads is not None:
        _write(out / "roads.json", roads["json"])
    for cid, data in cols.items():
        _write(out / "col" / f"{cid}.col", data)
    seconds["total"] = time.perf_counter() - started
    manifest = dict(
        format=FORMAT,
        version=VERSION,
        coarse=True,
        tile=tile.id,
        code=code_hash(),
        built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        origin_en=list(C.ORIGIN),
        grid=dict(visual_l0=C.VISUAL_TILE_SIZE, collision=C.COLLISION_TILE_SIZE, cells=CELLS),
        visual={
            tile.id: _entry(
                "vis.glb",
                vis,
                level=0,
                tris=stats["tris"],
                h=stats["h"],
                brotli_bytes=stats["brotli_bytes"],
                coarse=True,
            )
        },
        collision=collision,
        roads=(
            _entry("roads.json", roads["json"], nodes=roads["nodes"], edges=roads["edges"])
            if roads is not None
            else None
        ),
        buildings=len(blds),
        building_keys=[b.facade_key for b in blds],
        roof_heights=[
            dict(building=b.key, parts=len(b.roof_parts), evidence=b.height_evidence) for b in blds
        ],
        facade_params=facade_params,
        trees=len(trees),
        barriers=barrier_stats,
        vegetation=dict(
            tree_cap=TREE_CAP,
            bush_cap=BUSH_CAP,
            bushes=len(bushes),
            measured=sum(t.measured for t in trees),
        ),
        bridges=decks + len(osm_decks),
        tunnels=tunnels.count,
        water_level=level,
        sources=inputs.sources,
        inputs=inputs.key,
        seconds={k: round(v, 3) for k, v in seconds.items()},
    )
    _write(out / "manifest.json", (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
    shutil.rmtree(stage, ignore_errors=True)
    return manifest

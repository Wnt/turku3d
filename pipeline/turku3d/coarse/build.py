"""Coarse 250 m tile in the published tile format, built in seconds from the fast sources.

Ground is a regular TIN with 250/128 m (~2 m) spacing interpolated from city ground key points.
Own-source surface polygons select repeating PBR materials; exact visual boundary cuts preserve
that terrain plane. Orthophoto supplies fallback vegetation classification and roof imagery only.
Water polygons become a sensor over a synthetic bed and a separate visible water surface.
OSM bridges become separate drivable slabs with clear underpasses and ground-blended approaches;
without OSM, street-area water bridges retain the abutment lift (``bridge_decks``).
OSM ``natural=tree`` points become GPU-instanced vegetation (10 m unless tagged) with trunk colliders. The
tile has mapped kerbs/paint but no surveyed curb profiles, stairs, unique facade maps or LOD parents: the full pipeline
replaces it when the tile is published.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import time
from pathlib import Path

import numpy as np
import shapely
from PIL import Image

from turku3d import config as C
from turku3d.col.format import MIN_TRI_AREA, ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.facade_kit import candidates, parameter_hash
from turku3d.ground.mesh import Mesh
from turku3d.ground.water import add_river_bed
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.stage_cache import StageCache, source_key

from . import banks, bridges, fences, kerbs, parked, surfaces
from .extrusion import extrude
from .inputs import CELLS, Inputs, gather
from .packaging import code_hash, package_visual  # noqa: F401 (producer API)
from .sources import Sources
from .stages import building_visual, kerb_inputs, props_visual

FORMAT = "turku3d-coarse-tile"
VERSION = 1
WIRE_CAP = 8_000_000


def solid_triangles(pos, idx, mat):
    """Drop the slivers and duplicates that successive bank, tunnel and kerb cuts can leave (measured on the stored
    tile-local float32 positions); the .col format rejects both, which would fail the whole tile upgrade."""
    idx = np.asarray(idx).reshape(-1, 3)
    mat = np.asarray(mat)
    p = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    a, b, c = p[idx[:, 0]], p[idx[:, 1]], p[idx[:, 2]]
    keep = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) >= MIN_TRI_AREA
    _, first = np.unique(np.sort(idx, axis=1), axis=0, return_index=True)
    unique = np.zeros(len(idx), bool)
    unique[first] = True
    keep &= unique
    return idx[keep], mat[keep]


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


def collision_tiles(
    tile, grid_pos, grid_idx, materials, level, shells, ground_keep=None, shore=None
):
    """Split ground along the 62.5 m grid; carved building parts retain the original owner."""
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
        if shore is None:
            mesh = add_river_bed(mesh, level)
        else:
            centres = shore.positions[shore.indices].mean(axis=1)
            owners = np.floor(centres[:, [0, 2]] / C.COLLISION_TILE_SIZE).astype(int)
            mesh = Mesh(
                shore.positions,
                shore.indices[(owners[:, 0] == ct.i) & (owners[:, 1] == ct.j)],
                shore.material[(owners[:, 0] == ct.i) & (owners[:, 1] == ct.j)],
            ).compact()
        positions, indices, material = [mesh.positions], [mesh.indices], [mesh.material]
        count = len(mesh.positions)
        for b, (roof, roof_idx, floor, floor_idx, walls, _, wall_idx) in shells:
            c = b.footprint.centroid
            if getattr(b, "_collision_owner", C.CollisionTile.at(c.x, c.y)) != ct:
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
    """Height on the same triangle used by ground visuals and collision."""
    n = CELLS + 1
    heights = grid_pos[:, 1].reshape(n, n)
    return surfaces.grid_height(tile, heights, x, z)


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


def default_library() -> Path:
    """Shared generated materials, independent of full-tile releases."""
    from turku3d.facade_assets import material_library

    return material_library()


def _entry(url: str, data: bytes, **extra) -> dict:
    return dict(url=url, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), **extra)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def terrain_stage(tile, inputs):
    """Terrain and deck outputs, including the approaches that mutate the ground grid."""
    grid_pos, grid_idx = grid_mesh(tile, inputs.heights)
    centroids = grid_pos[grid_idx].mean(axis=1)
    water = inputs.water
    materials = surfaces.classify(centroids, inputs.ortho, tile, inputs.streets, inputs.surfaces)
    # A bridge deck stays solid where a water polygon runs under it.
    separate_decks = bool(inputs.roads and inputs.roads.get("bridge_ways"))
    wet = water_mask(centroids, water)
    if not separate_decks:
        wet &= materials != mat_id("bridge_deck")
    level = water_level(inputs.points, water)
    if wet.any():
        materials[wet] = mat_id("water")
    return grid_pos, grid_idx, materials, wet, level


def deck_stage(tile, grid_pos, grid_idx, materials, wet, inputs):
    grid_pos = grid_pos.copy()
    osm_decks = bridges.produce(tile, grid_pos, inputs)
    decks = 0 if osm_decks else bridge_decks(grid_pos, grid_idx, materials, wet)
    roads = bridges.road_heights(inputs.roads, osm_decks)
    # Samplers are build-local closures. Consume them before caching the resulting mesh/road data.
    for deck in osm_decks:
        deck.height_at = None
    return grid_pos, osm_decks, decks, roads


def build(
    tile_id: str,
    out: Path,
    *,
    inputs: Inputs | None = None,
    library: Path | None = None,
    sources=None,
    first_pass: bool = False,
) -> dict:
    """Write ``out/{manifest.json, vis.glb, col/c_i_j.col, roads.json}``; shared KTX2 go to out/../shared.

    ``roads.json`` is the tile's OSM street graph (``turku3d.roads`` format, clipped to the tile, every
    connected piece kept); it is absent without an OSM extract.
    """
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError("coarse tiles are L0 (250 m) tiles")
    started = time.perf_counter()
    producer_code = code_hash()
    inputs = inputs or gather(tile, out.parent, sources, first_pass=first_pass)
    seconds = dict(inputs.seconds)

    cache = StageCache(out.parent / ".stages" / tile.id)
    base = Path(__file__).parents[1]
    common = [Path(C.__file__), base / "col/materials_v1.json"]
    t = time.perf_counter()
    grid_pos, grid_idx, materials, wet, level = cache.run(
        "terrain",
        source_key(terrain_stage, grid_mesh, water_mask, water_level, surfaces.classify, *common),
        [
            tile,
            inputs.heights,
            inputs.points,
            inputs.ortho,
            inputs.streets,
            inputs.surfaces,
            inputs.water,
            bool(inputs.roads and inputs.roads.get("bridge_ways")),
        ],
        lambda: terrain_stage(tile, inputs),
    )
    grid_pos, osm_decks, decks, bridge_roads = cache.run(
        "bridges",
        source_key(
            deck_stage,
            bridge_decks,
            *common,
            Path(bridges.__file__),
            Path(__file__).with_name("tunnels.py"),
            Path(__file__).with_name("bridge_details.py"),
            Path(__file__).with_name("geometry.py"),
            base / "structures/geometry.py",
            base / "structures/layer.py",
        ),
        [
            tile,
            grid_pos,
            grid_idx,
            materials,
            wet,
            inputs.points,
            inputs.roads,
            [b.footprint for b in inputs.buildings],
        ],
        lambda: deck_stage(tile, grid_pos, grid_idx, materials, wet, inputs),
    )
    water = inputs.water
    seconds["ground"] = time.perf_counter() - t

    t = time.perf_counter()
    blds = inputs.buildings
    facade_params = parameter_hash(b.facade_key for b in blds)
    shells = cache.run(
        "buildings",
        source_key(
            *common, Path(__file__).with_name("extrusion.py"), Path(__file__).with_name("roofs.py")
        ),
        blds,
        lambda: [(b, shell) for b in blds if len((shell := extrude(b))[1])],
    )
    seconds["buildings"] = time.perf_counter() - t

    from .tunnels import ground_sampler
    from .tunnels import produce as produce_tunnels

    t = time.perf_counter()
    ground_sources = sources or Sources()
    surface_sample = kerbs.height_sampler(
        tile, grid_pos[:, 1].reshape(CELLS + 1, CELLS + 1), out.parent, ground_sources
    )
    tunnels = cache.run(
        "tunnels",
        source_key(
            grid_height,
            *common,
            Path(__file__).with_name("tunnels.py"),
            Path(kerbs.__file__),
            Path(surfaces.__file__),
            Path(__file__).with_name("inputs.py"),
            Path(__file__).with_name("geometry.py"),
            base / "structures/geometry.py",
            Path(__file__).with_name("extrusion.py"),
            Path(__file__).with_name("roofs.py"),
        ),
        [
            tile,
            inputs.tunnels,
            grid_pos,
            grid_idx,
            shells,
            kerb_inputs(tile, ground_sources) if inputs.tunnels else None,
        ],
        lambda: produce_tunnels(
            tile,
            inputs.tunnels,
            grid_pos,
            grid_idx,
            shells,
            extrude,
            ground_sampler(lambda x, z: grid_height(tile, grid_pos, x, z)),
            surface_sample,
        ),
    )
    shells = tunnels.shells
    seconds["tunnels"] = time.perf_counter() - t

    t = time.perf_counter()
    origin = np.asarray(tile.origin)
    stage = out / ".stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    dry, river, bank, solid = banks.stage(
        cache, tile, grid_pos, grid_idx, inputs, tunnels.keep, level, bool(osm_decks)
    )
    dry_keep = np.ones(len(dry.indices), bool)
    visual_materials = surfaces.slope_fallback(dry.positions, dry.indices, dry.material)
    fallback_materials = surfaces.slope_fallback(
        dry.positions,
        dry.indices,
        surfaces.classify(dry.positions[dry.indices].mean(axis=1), inputs.ortho, tile, []),
    )
    t_surface = time.perf_counter()
    if first_pass:
        surface_pos, surface_idx, surface_ids = (
            dry.positions,
            dry.indices,
            visual_materials,
        )
        surface_normals = None
    else:
        surface_args = (
            dry.positions,
            dry.indices,
            visual_materials,
            dry_keep,
            [*inputs.surfaces, *inputs.streets],
            fallback_materials,
        )
        surface_pos, surface_idx, surface_ids, surface_normals = cache.run(
            "surface_boundaries",
            source_key(surfaces.split_boundaries, base / "package/gltf.py", *common),
            surface_args,
            lambda: surfaces.split_boundaries(*surface_args),
        )
    seconds["surface_boundaries"] = time.perf_counter() - t_surface
    visual_args = (
        tile,
        surface_pos,
        surface_idx,
        surface_ids,
        np.ones(len(surface_idx), bool),
        surface_normals,
    )
    ground_meshes = cache.run(
        "ground_visual",
        source_key(surfaces.visuals, base / "package/gltf.py", *common),
        [visual_args, surfaces.KEYS, surfaces.BINDINGS],
        lambda: surfaces.visuals(*visual_args),
    )
    paint_args = (
        tile,
        surface_pos,
        surface_idx,
        surface_ids,
        np.ones(len(surface_idx), bool),
        inputs.markings,
    )
    paint = (
        None
        if first_pass
        else cache.run(
            "markings",
            source_key(surfaces.paint_visual, base / "package/gltf.py", *common),
            paint_args,
            lambda: surfaces.paint_visual(*paint_args),
        )
    )
    if paint is not None:
        ground_meshes.append(paint)
    kerb_meshes = cache.run(
        "kerbs",
        source_key(
            Path(kerbs.__file__),
            Path(surfaces.__file__),
            Path(__file__).with_name("inputs.py"),
            Path(__file__).with_name("geometry.py"),
            base / "structures/geometry.py",
            *common,
        ),
        [
            tile,
            inputs.kerbs,
            grid_pos[:, 1],
            water,
            [b.footprint for b in blds],
            inputs.markings,
            kerb_inputs(tile, ground_sources)
            if inputs.kerbs is not None and not inputs.kerbs.is_empty
            else None,
        ],
        lambda: kerbs.produce(
            tile,
            inputs.kerbs,
            surface_sample,
            water,
            blds,
            inputs.markings,
        ),
    )
    ground_meshes.extend(kerbs.visuals(tile, kerb_meshes))
    ground_meshes.extend(
        surfaces.visuals(
            tile, bank.positions, bank.indices, bank.material, np.ones(len(bank.indices), bool)
        )
    )
    if len(river.indices):
        ground_meshes.append(
            GlbMesh(
                f"water_{tile.id}",
                river.positions - origin,
                river.indices,
                uvs=river.positions[:, [0, 2]] * [1, -1],
                extras=dict(
                    layer="ground", object_id=f"water_{tile.id}", material_key="water", sensor=True
                ),
            )
        )
    write_visual(stage / "ground.vis.glb", ground_meshes, root_translation=tile.origin)
    layer_files = [stage / "ground.vis.glb"]
    bridges.visual(osm_decks, tile, stage, layer_files)
    if tunnels.meshes or tunnels.ceilings:
        write_visual(stage / "tunnels.vis.glb", tunnels.visuals(tile), root_translation=tile.origin)
        layer_files.append(stage / "tunnels.vis.glb")
    from .forests import BUSH_CAP, TREE_CAP, produce, trunks

    vegetation_start = time.perf_counter()
    trees, bushes = (
        ([], [])
        if first_pass
        else cache.run(
            "vegetation",
            source_key(
                Path(__file__).with_name("forests.py"),
                base / "vegetation/trees.py",
                draft_trees,
                grid_height,
                *common,
            ),
            [
                tile,
                grid_pos,
                inputs.water,
                inputs.buildings,
                inputs.streets,
                inputs.roads,
                inputs.canopy,
                inputs.forests,
                inputs.trees,
                shells,
            ],
            lambda: produce(
                tile,
                inputs,
                lambda x, z: grid_height(tile, grid_pos, x, z),
                draft_trees(tile, grid_pos, inputs.trees, water, shells),
            ),
        )
    )
    seconds["vegetation_scatter"] = time.perf_counter() - vegetation_start
    if trees or bushes:
        from turku3d.vegetation.visual import write_instances

        write_instances(stage / "vegetation.vis.glb", tile, trees + bushes)
        layer_files.append(stage / "vegetation.vis.glb")
    poles = list(inputs.poles)
    if poles:
        data = cache.run(
            "props",
            source_key(
                props_visual,
                grid_height,
                base / "props/__init__.py",
                base / "props/models.py",
                base / "package/gltf.py",
                *common,
            ),
            [tile, poles, grid_pos],
            lambda: props_visual(tile, poles, grid_pos),
        )
        _write(stage / "props.vis.glb", data)
        layer_files.append(stage / "props.vis.glb")
    building_meshes = cache.run(
        "building_visual",
        source_key(
            building_visual,
            Path(__file__).with_name("stored_facades.py"),
            base / "facade_assets.py",
            base / "facade_kit.py",
            jpeg,
            ortho_uv,
            *common,
        ),
        [tile, shells, inputs.ortho if not first_pass else None, first_pass, facade_params],
        lambda: building_visual(tile, shells, inputs.ortho, first_pass),
    )
    if building_meshes:
        write_visual(stage / "buildings.vis.glb", building_meshes, root_translation=tile.origin)
        layer_files.append(stage / "buildings.vis.glb")

    def barrier_stage(directory):
        files = []
        value = fences.produce(
            tile, inputs, directory, files, lambda x, z: grid_height(tile, grid_pos, x, z)
        )
        return value

    barrier_cols, barrier_stats = cache.files(
        "fences",
        source_key(
            barrier_stage,
            Path(fences.__file__),
            Path(__file__).with_name("tunnels.py"),
            grid_height,
            base / "structures/geometry.py",
            base / "package/gltf.py",
            *common,
        ),
        [tile, inputs.barriers, inputs.streets, inputs.water, inputs.tunnels, grid_pos],
        stage,
        barrier_stage,
    )
    if (stage / "fences.vis.glb").exists():
        layer_files.append(stage / "fences.vis.glb")
    seconds["fences"] = barrier_stats["seconds"]
    surface_channels = {
        surfaces.KEYS.index(m.extras["material_key"].removeprefix("draft_"))
        for m in ground_meshes
        if m.extras["material_key"].startswith("draft_")
    }
    seconds["visual_geometry"] = time.perf_counter() - t
    t_library = time.perf_counter()
    surface_library = library or default_library()
    if not first_pass:
        surface_library = surfaces.library(out.parent, surface_library, surface_channels)
    seconds["surface_library"] = time.perf_counter() - t_library
    t_package = time.perf_counter()
    vis, stats = package_visual(
        tile, layer_files, stage, out.parent, surface_library, first_pass=first_pass
    )
    seconds["package"] = time.perf_counter() - t_package
    seconds.update({"package_" + k: v for k, v in stats.get("seconds", {}).items()})
    seconds["visual"] = time.perf_counter() - t
    if stats["brotli_bytes"] > WIRE_CAP:
        raise ValueError(
            f"{tile.id}: coarse visual exceeds 8 MB on the wire: {stats['brotli_bytes']}"
        )

    t = time.perf_counter()
    collision = {}
    cols = {}
    for ct, (pos, idx, mat) in collision_tiles(
        tile, grid_pos, grid_idx, materials, level, shells, tunnels.keep, shore=solid
    ).items():
        pos, idx, mat = bridges.collision(osm_decks, tile, ct, pos, idx, mat)
        pos, idx, mat = tunnels.collision(ct, pos, idx, mat)
        pos, idx, mat = kerbs.collision(ct, kerb_meshes, pos, idx, mat)
        primitives = trunks(trees, ct) + barrier_cols[ct]
        col = ColTile.for_tile(ct.i, ct.j, pos, idx, mat, primitives=primitives, aoi_border=False)
        col.indices, col.materials = solid_triangles(col.positions, col.indices, col.materials)
        data = cache.run(
            "collision_" + ct.id,
            source_key(
                *sorted((base / "col").glob("*.py")),
                *sorted((base / "col").glob("*.mjs")),
                *common,
                C.REPO_ROOT / "pipeline/env/package-lock.json",
            ),
            col,
            lambda col=col: write_col(col, encoding="meshopt", optimize=False),
        )
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

    t = time.perf_counter()
    cars = parked.stage(cache, tile, inputs, grid_pos, trees, poles, osm_decks)
    vehicle_models = parked.publish_models(cars, out.parent)
    seconds["parked"] = time.perf_counter() - t
    roads = tunnels.roads(bridge_roads)

    _write(out / "facade-candidates.json", json.dumps(candidates(blds)).encode())
    visual_url = f"vis.{hashlib.sha256(vis).hexdigest()}.glb"
    _write(out / visual_url, vis)
    _write(out / "vis.glb", vis)
    if roads is not None:
        roads_url = f"roads.{hashlib.sha256(roads['json']).hexdigest()}.json"
        _write(out / roads_url, roads["json"])
        _write(out / "roads.json", roads["json"])
    for cid, data in cols.items():
        url = f"col/{cid}.{hashlib.sha256(data).hexdigest()}.col"
        _write(out / url, data)
        collision[cid]["url"] = url
        _write(out / "col" / f"{cid}.col", data)
    seconds["total"] = time.perf_counter() - started
    manifest = dict(
        format=FORMAT,
        version=VERSION,
        coarse=True,
        pass_kind="first" if first_pass else "full",
        tile=tile.id,
        code=producer_code,
        built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        origin_en=list(C.ORIGIN),
        grid=dict(visual_l0=C.VISUAL_TILE_SIZE, collision=C.COLLISION_TILE_SIZE, cells=CELLS),
        visual={
            tile.id: _entry(
                visual_url,
                vis,
                level=0,
                tris=stats["tris"],
                h=stats["h"],
                brotli_bytes=stats["brotli_bytes"],
                coarse=True,
            )
        },
        collision=collision,
        vehicles=cars,
        vehicle_models=vehicle_models,
        roads=(
            _entry(roads_url, roads["json"], nodes=roads["nodes"], edges=roads["edges"])
            if roads is not None
            else None
        ),
        kerb_triangles=sum(len(m.indices) for m in kerb_meshes),
        marking_triangles=len(paint.indices) if paint is not None else 0,
        ground_surfaces={
            surfaces.KEYS[c]: int(
                sum(
                    len(m.indices)
                    for m in ground_meshes
                    if m.extras["material_key"] == "draft_" + surfaces.KEYS[c]
                )
            )
            for c in surface_channels
        },
        buildings=len(blds),
        building_keys=[b.facade_key for b in blds],
        roof_heights=[
            dict(building=b.key, parts=len(b.roof_parts), evidence=b.height_evidence) for b in blds
        ],
        facade_params=facade_params,
        trees=len(trees),
        poles=len(poles),
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
    cache.metrics.update(json.loads((stage / "package-times.json").read_text()))
    cache.report(out / "stage-times.json")
    shutil.rmtree(stage, ignore_errors=True)
    return manifest

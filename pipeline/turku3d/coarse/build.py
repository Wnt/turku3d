"""Coarse 250 m tile in the published tile format, built in seconds from the fast sources.

Ground is a regular TIN with 250/128 m (~2 m) spacing interpolated from city ground key points.
Own-source surface polygons select repeating PBR materials; exact visual boundary cuts preserve
that terrain plane. Orthophoto supplies fallback vegetation classification and roof imagery only.
Water polygons become a sensor over a synthetic bed and a separate visible water surface.
OSM bridges become separate drivable slabs with clear underpasses and ground-blended approaches;
without OSM, street-area water bridges retain the abutment lift (``bridge_decks``).
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
from turku3d.col.format import ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.facade_kit import candidates, palette_sources, parameter_hash
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.stage_cache import StageCache, source_key
from turku3d.wall_surfaces import paint_partition, trapezoids

from . import banks, boats, bridges, fences, gardens, kerbs, parked, road_top, surfaces, walkable
from .collision import collision_tiles, compact_collision  # noqa: F401 (producer API)
from .collision_key import code_hash as collision_code_hash
from .extrusion import extrude
from .forests import draft_trees
from .inputs import CELLS, Inputs, gather
from .packaging import code_hash, package_visual  # noqa: F401 (producer API)
from .retention import managed_build
from .sources import Sources
from .stages import (
    building_visual,
    cached_terrain,
    kerb_inputs,
    roof_crop,
    roof_fallback,
    structures_stage,
    valid_roof_pixels,
)

FORMAT = "turku3d-coarse-tile"
VERSION = 1
WIRE_CAP = 8_000_000


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
    """Open water shares one datum; small ponds receive their own measured levels."""
    from .water import LEVEL

    return LEVEL


# Dry ground this far beyond a deck end (m, along the deck) sets its height.
ABUTMENT_M = (5.0, 25.0)


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


def grid_height(tile: C.VisualTile, grid_pos: np.ndarray, x: float, z: float) -> float:
    """Height on the same triangle used by ground visuals and collision."""
    n = CELLS + 1
    heights = grid_pos[:, 1].reshape(n, n)
    return surfaces.grid_height(tile, heights, x, z)


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


@managed_build
def build(
    tile_id: str,
    out: Path,
    *,
    inputs: Inputs | None = None,
    library: Path | None = None,
    sources=None,
    first_pass: bool = False,
) -> dict:
    """Write tile visuals, collision and manifest; shared KTX2 go to out/../shared.
    roads.json contains every clipped OSM street component, when an extract is available.
    """
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError("coarse tiles are L0 (250 m) tiles")
    started = time.perf_counter()
    producer_code = code_hash()
    cache = StageCache(
        out.parent / ".stages" / tile.id, retain=True, report_path=out / "stage-times.json"
    )
    with cache.measure("inputs"):
        inputs = inputs or gather(tile, out.parent, sources, first_pass=first_pass)
    seconds = dict(inputs.seconds)
    seconds.update({"input_" + k: v for k, v in inputs.seconds.items()})
    seconds["inputs_total"] = time.perf_counter() - started

    base = Path(__file__).parents[1]
    common = [Path(C.__file__), base / "col/materials_v1.json"]
    t = time.perf_counter()
    grid_pos, grid_idx, materials, wet, level = cached_terrain(cache, tile, inputs, common)
    from .bridge_access import cached_stage

    grid_pos, osm_decks, decks, bridge_roads = cached_stage(
        cache, tile, grid_pos, grid_idx, materials, wet, inputs
    )
    water = inputs.water
    seconds["ground"] = time.perf_counter() - t
    seconds.update({name: cache.metrics[name]["seconds"] for name in ("terrain", "bridges")})

    t = time.perf_counter()
    blds = inputs.buildings
    shells = cache.run(
        "buildings",
        source_key(
            *common, Path(__file__).with_name("extrusion.py"), Path(__file__).with_name("roofs.py")
        ),
        blds + list(inputs.wall_context),
        lambda: [
            (b, shell) for b in [*blds, *inputs.wall_context] if len((shell := extrude(b))[1])
        ],
    )
    seconds["buildings"] = time.perf_counter() - t

    t = time.perf_counter()
    ground_sources = sources or Sources()
    tunnels, grid_pos, surface_sample, carried_mask = road_top.stage(
        cache, tile, inputs, grid_pos, grid_idx, shells, ground_sources, out.parent
    )
    surface_sample = gardens.sampler(tile, surface_sample, inputs.gardens)
    surface_sample = walkable.sampler(surface_sample, inputs.walkable)
    own_keys = {b.key for b in blds}
    wall_context = [(b, shell) for b, shell in tunnels.shells if b.key not in own_keys]
    shells = [(b, shell) for b, shell in tunnels.shells if b.key in own_keys]
    seconds["tunnels"] = time.perf_counter() - t

    t = time.perf_counter()
    origin = np.asarray(tile.origin)
    stage = out / ".stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    terrain_pos, terrain_idx, terrain_keep = tunnels.terrain(grid_pos, grid_idx)
    dry, river, bank, solid = banks.stage(
        cache, tile, terrain_pos, terrain_idx, inputs, terrain_keep, level, bool(osm_decks)
    )
    seconds["river_banks"] = cache.metrics["river_banks"]["seconds"]
    dry, river, bank, solid = cache.run(
        "gardens",
        source_key(Path(gardens.__file__), Path(__file__).with_name("stairs.py")),
        [tile, inputs.gardens, dry, river, bank, solid],
        lambda: gardens.produce(tile, inputs.gardens, dry, river, bank, solid),
    )
    from . import stairs

    dry, river, bank, solid, stair_meshes, boat_meshes, structure_seconds = structures_stage(
        cache,
        tile,
        inputs,
        grid_pos,
        surface_sample,
        dry,
        river,
        bank,
        solid,
        ground_sources,
        osm_decks,
        tunnels.openings,
    )
    seconds.update(structure_seconds)
    kerb_meshes = cache.run(
        "kerbs",
        source_key(
            Path(kerbs.__file__),
            surfaces.grid_height,
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
    from .forest_paths import path_surfaces
    from .forests import BUSH_CAP, TREE_CAP, produce, trunks

    seconds["kerbs_geometry"] = cache.metrics["kerbs"]["seconds"]

    vegetation_start = time.perf_counter()
    trees, bushes = (
        ([], [])
        if first_pass
        else cache.run(
            "vegetation",
            source_key(
                Path(__file__).with_name("forests.py"),
                Path(__file__).with_name("forest_paths.py"),
                base / "vegetation/trees.py",
                base / "vegetation/visual.py",
                *sorted((base / "vegetation/assets").glob("*.glb")),
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
                inputs.vegetation_paths,
                inputs.trees,
                shells,
                carried_mask,
            ],
            lambda: produce(
                tile,
                inputs,
                lambda x, z: grid_height(tile, grid_pos, x, z),
                draft_trees(tile, grid_pos, inputs.trees, water, shells),
                carried_mask,
            ),
        )
    )
    seconds["vegetation_scatter"] = time.perf_counter() - vegetation_start

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
    seconds["fences"] = cache.metrics["fences"]["seconds"]
    # Street furniture and lights (G1): before collision, which merges their static primitives.
    from . import furniture

    street = (
        furniture.EMPTY
        if first_pass
        else furniture.stage(cache, tile, inputs, grid_pos, carried_mask, common)
    )
    seconds["furniture"] = cache.metrics.get("furniture", {}).get("seconds", 0.0)
    t = time.perf_counter()
    collision = {}
    cols = {}
    basic_cells = []
    for ct, (pos, idx, mat) in collision_tiles(
        tile,
        grid_pos,
        grid_idx,
        materials,
        level,
        shells,
        tunnels.keep,
        shore=solid,
        facade_components=not first_pass,
    ).items():
        pos, idx, mat = bridges.collision(osm_decks, tile, ct, pos, idx, mat)
        pos, idx, mat = tunnels.collision(ct, pos, idx, mat)
        pos, idx, mat = kerbs.collision(ct, kerb_meshes, pos, idx, mat)
        pos, idx, mat = stairs.collision(ct, [*stair_meshes, *boat_meshes], pos, idx, mat)
        primitives = trunks(trees, ct) + barrier_cols[ct] + street["collision"].get(ct.id, [])
        col = ColTile.for_tile(ct.i, ct.j, pos, idx, mat, primitives=primitives, aoi_border=False)
        compact_collision(col)
        if first_pass:
            basic_cells.append((ct, col))
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
            tris=len(col.indices),
            prims=len(primitives),
            h=[float(lo), float(hi)],
            flags=col.computed_flags(),
        )
    seconds["collision"] = time.perf_counter() - t

    for cid, data in cols.items():
        url = f"col/{cid}.{hashlib.sha256(data).hexdigest()}.col"
        _write(out / url, data)
        collision[cid]["url"] = url
        _write(out / "col" / f"{cid}.col", data)
    seconds["collision_ready"] = time.perf_counter() - started
    if first_pass:
        from turku3d.roads.tile_graph import with_heights

        from .basic import publish

        first_roads = tunnels.roads(bridge_roads)
        return publish(
            tile,
            out,
            basic_cells,
            collision,
            with_heights(first_roads, surface_sample) if first_roads is not None else None,
            producer_code,
            inputs.key,
            len(blds),
            seconds,
            started,
            shells,
            wall_context,
            cache=cache,
            gardens=gardens.stats(tile, inputs.gardens),
            boats=boats.stats(tile, inputs.boats, boat_meshes),
        )
    facade_params = parameter_hash(b.facade_key for b in blds)
    t = time.perf_counter()
    dry_keep = np.ones(len(dry.indices), bool)
    visual_materials = surfaces.slope_fallback(dry.positions, dry.indices, dry.material)
    fallback_materials = surfaces.slope_fallback(
        dry.positions,
        dry.indices,
        surfaces.classify(dry.positions[dry.indices].mean(axis=1), inputs.ortho, tile, []),
    )
    t_surface = time.perf_counter()
    forest_paths = path_surfaces(inputs)
    visual_regions = [*inputs.surfaces, *forest_paths]
    surface_args = (
        dry.positions,
        dry.indices,
        visual_materials,
        dry_keep,
        [*visual_regions, *inputs.streets, *inputs.street_finishes],
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
    # City/OSM surface classes select metre-scale PBR materials. The ortho remains
    # classifier input only: its parked cars and baked shadows are not albedo.
    ground_meshes = cache.run(
        "ground_visual",
        source_key(surfaces.visuals, base / "package/gltf.py", *common),
        [visual_args, surfaces.KEYS, surfaces.BINDINGS],
        lambda: surfaces.visuals(*visual_args),
    )
    from turku3d.roads import paint as road_paint

    paint_args = (
        tile,
        surface_pos,
        surface_idx,
        surface_ids,
        np.ones(len(surface_idx), bool),
        inputs.markings,
    )
    road_graph = inputs.roads["json"] if inputs.roads else None

    def draped_paint():
        rule = road_paint.markings(json.loads(road_graph) if road_graph else None, inputs.markings)
        paint = surfaces.paint_visual(*paint_args[:-1], [*inputs.markings, *rule], osm_decks)
        return paint, road_paint.summary(rule)

    paint, paint_counts = cache.run(
        "markings",
        source_key(
            surfaces.paint_visual,
            Path(road_paint.__file__),
            base / "roads/attributes.py",
            base / "package/gltf.py",
            *common,
        ),
        [
            *paint_args,
            road_graph,
            [(d.way["id"], d.line.wkb, d.stations, d.heights) for d in osm_decks],
        ],
        draped_paint,
    )
    if paint is not None:
        ground_meshes.append(paint)
    ground_meshes.extend(kerbs.visuals(tile, kerb_meshes))
    stair_visuals = stairs.visuals(tile, stair_meshes)
    ground_meshes.extend(stair_visuals)
    ground_meshes.extend(boats.visuals(tile, boat_meshes))
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
    if (stage / "fences.vis.glb").exists():
        layer_files.append(stage / "fences.vis.glb")
    bridges.visual(osm_decks, tile, stage, layer_files)
    if tunnels.meshes or tunnels.ceilings:
        write_visual(stage / "tunnels.vis.glb", tunnels.visuals(tile), root_translation=tile.origin)
        layer_files.append(stage / "tunnels.vis.glb")
    if trees or bushes:
        from turku3d.vegetation.visual import write_instances

        write_instances(stage / "vegetation.vis.glb", tile, trees + bushes)
        layer_files.append(stage / "vegetation.vis.glb")
    poles = street["poles"]
    if street["glb"]:
        _write(stage / "props.vis.glb", street["glb"])
        layer_files.append(stage / "props.vis.glb")
    from turku3d.facade_programs import source_paths as facade_program_sources

    building_meshes = cache.run(
        "building_visual",
        source_key(
            building_visual,
            cached_terrain,
            roof_crop,
            roof_fallback,
            valid_roof_pixels,
            Path(__file__).with_name("stored_facades.py"),
            base / "facade_assets.py",
            base / "facade_kit.py",
            *palette_sources(),
            *facade_program_sources(),
            paint_partition,
            trapezoids,
            jpeg,
            ortho_uv,
            *common,
        ),
        [tile, shells, wall_context, inputs.ortho, False, facade_params],
        lambda: building_visual(tile, shells, inputs.ortho, False, wall_context),
    )
    from turku3d.wall_assembly import assemble_layers

    with cache.measure("wall_assembly"):
        layer_files = assemble_layers(tile, layer_files, building_meshes, shells, out, stage, cache)

    surface_channels = surfaces.channels(ground_meshes)
    seconds["visual_geometry"] = time.perf_counter() - t
    t_library = time.perf_counter()
    surface_library = library or default_library()
    surface_library = surfaces.library(out.parent, surface_library, surface_channels)
    seconds["surface_library"] = time.perf_counter() - t_library
    t_package = time.perf_counter()
    with cache.measure("package"):
        vis, stats = package_visual(
            tile, layer_files, stage, out.parent, surface_library, first_pass=False
        )
    seconds["package"] = time.perf_counter() - t_package
    seconds.update({"package_" + k: v for k, v in stats.get("seconds", {}).items()})
    seconds["visual"] = time.perf_counter() - t
    if stats["brotli_bytes"] > WIRE_CAP:
        raise ValueError(
            f"{tile.id}: coarse visual exceeds 8 MB on the wire: {stats['brotli_bytes']}"
        )

    t = time.perf_counter()
    cars = parked.stage(cache, tile, inputs, grid_pos, trees, poles, osm_decks, carried_mask)
    vehicle_models = parked.publish_models(cars, out.parent)
    seconds["parked"] = time.perf_counter() - t
    roads = tunnels.roads(bridge_roads)
    if roads is not None:
        from turku3d.roads.tile_graph import with_heights

        t_heights = time.perf_counter()
        roads = with_heights(roads, surface_sample)
        seconds["road_heights"] = time.perf_counter() - t_heights

    _write(out / "facade-candidates.json", json.dumps(candidates(blds)).encode())
    visual_url = f"vis.{hashlib.sha256(vis).hexdigest()}.glb"
    _write(out / visual_url, vis)
    _write(out / "vis.glb", vis)
    if roads is not None:
        roads_url = f"roads.{hashlib.sha256(roads['json']).hexdigest()}.json"
        _write(out / roads_url, roads["json"])
        _write(out / "roads.json", roads["json"])
    seconds["total"] = time.perf_counter() - started
    manifest = dict(
        format=FORMAT,
        version=VERSION,
        coarse=True,
        pass_kind="full",
        tile=tile.id,
        code=producer_code,
        collision_code=collision_code_hash(),
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
                wall_surfaces=stats["wall_surfaces"],
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
        stairs=stairs.stats(stair_meshes, inputs.stairs),
        walkable=walkable.stats(tile, inputs.walkable),
        gardens=gardens.stats(tile, inputs.gardens),
        boats=boats.stats(tile, inputs.boats, boat_meshes),
        marking_triangles=len(paint.indices) if paint is not None else 0,
        rule_paint=paint_counts,
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
        lights=street["lights"],
        furniture=street["stats"],
        barriers=barrier_stats,
        vegetation=dict(
            tree_cap=TREE_CAP,
            bush_cap=BUSH_CAP,
            bushes=len(bushes),
            measured=sum(t.measured for t in trees),
        ),
        bridges=decks + len(osm_decks),
        tunnels=tunnels.count,
        underpass_road_decks=sum(
            "road_top" in tags and line.intersects(shapely.box(*tile.bbox.as_tuple()))
            for _, line, tags in inputs.tunnels
        ),
        water_level=level,
        sources=inputs.sources,
        inputs=inputs.key,
        seconds={k: round(v, 3) for k, v in seconds.items()},
    )
    _write(out / "manifest.json", (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
    _write(out / "collision.json", json.dumps(manifest).encode())
    from .audit import submit

    submit(out, manifest["visual"][tile.id], require_snapshot=True)
    cache.metrics.update(json.loads((stage / "package-times.json").read_text()))
    cache.report(out / "stage-times.json")
    shutil.rmtree(stage, ignore_errors=True)
    return manifest

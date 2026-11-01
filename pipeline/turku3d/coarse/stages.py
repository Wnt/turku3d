"""Independent draft stage functions and explicit dependencies for compiler-cache reuse."""

import numpy as np

from turku3d import config as C
from turku3d.package.gltf import GlbMesh
from turku3d.tile_layer import identities

from .stored_facades import assemble_walls, wall_meshes


def building_visual(tile, shells, ortho, first_pass, wall_context=()):
    from .build import jpeg, ortho_uv

    if not shells:
        return []
    origin = np.asarray(tile.origin)
    meshes = []
    photo = None if first_pass or ortho is None else np.asarray(ortho)
    image = jpeg(photo) if photo is not None and np.all(valid_roof_pixels(photo)) else None
    for b, (roof, ridx, _, _, _walls, _wuv, _widx) in shells:
        if not len(ridx):
            continue
        uv = ortho_uv(tile, roof)
        # OSM material/heritage tags must never bypass usable roof imagery.
        if image is not None and np.all((uv >= 0) & (uv <= 1)):
            roof_image, roof_uv = image, uv
        else:
            roof_image, roof_uv = roof_crop(tile, roof, photo, roof_fallback(b))
        meshes.append(
            GlbMesh(
                f"roof_{b.key}",
                roof - origin,
                ridx,
                uvs=roof_uv,
                image=roof_image,
                extras=dict(
                    layer="buildings",
                    object_id=f"roof_{b.key}",
                    building_id=b.facade_key,
                    address=b.attributes.get("address"),
                    material_key="roof_ortho",
                    surface="roof",
                    texture_uv="normalized",
                ),
            )
        )
    for (b, _), walls in zip(shells, assemble_walls(shells, wall_context), strict=True):
        walls = wall_meshes(b, walls, origin)
        for mesh in walls:
            if b.attributes.get("address"):
                mesh.extras["address"] = b.attributes["address"]
        meshes.extend(walls)
    return meshes


def valid_roof_pixels(rgb):
    """Black nodata and white empty WMS pixels are not roof imagery."""
    return (rgb.max(axis=-1) > 3) & (rgb.min(axis=-1) < 252)


def roof_fallback(building):
    """Stable material families for roofs without usable own-source imagery."""
    import hashlib

    seed = int.from_bytes(hashlib.sha256(building.key.encode()).digest()[:4], "big")
    material = str(getattr(building, "attributes", {}).get("roof_material", "")).lower()
    families = {
        "tile": [(144, 76, 55), (158, 88, 62), (125, 67, 52)],
        "copper": [(72, 111, 97), (83, 122, 109), (103, 95, 75)],
        "slate": [(82, 88, 94), (95, 100, 105)],
        "metal": [(114, 121, 124), (138, 143, 143), (91, 104, 100)],
        "tar_paper": [(73, 76, 78), (89, 92, 94)],
    }
    palette = families.get(material, sum(families.values(), []))
    return np.asarray(palette[seed % len(palette)], np.uint8)


def roof_crop(tile, roof, photo, fallback):
    """Crop in tile pixel coordinates; uncovered roof parts retain their material colour.

    Whole buildings belong to their centroid tile, so LOD2 parts/passages can extend
    beyond its orthophoto. Pad those pixels instead of stretching the tile edge.
    """
    import io

    from PIL import Image

    from .build import ortho_uv

    uv = ortho_uv(tile, roof)
    size = np.array([photo.shape[1], photo.shape[0]]) if photo is not None else np.array([1, 1])
    low = np.floor(uv.min(axis=0) * size).astype(int)
    high = np.maximum(low + 1, np.ceil(uv.max(axis=0) * size).astype(int))
    width, height = high - low
    crop = np.broadcast_to(fallback, (height, width, 3)).copy()
    if photo is not None:
        start, end = np.maximum(low, 0), np.minimum(high, size)
        if np.all(end > start):
            source = photo[start[1] : end[1], start[0] : end[0]]
            target = crop[start[1] - low[1] : end[1] - low[1], start[0] - low[0] : end[0] - low[0]]
            good = valid_roof_pixels(source)
            target[good] = source[good]
    out = io.BytesIO()
    Image.fromarray(crop).save(out, format="PNG")
    return out.getvalue(), (uv * size - low) / (high - low)


def kerb_inputs(tile, sources):
    """The neighbouring ground grids a seam-crossing kerb's height sampler may read."""
    from .sources import NoCoverage

    paths = {}
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            try:
                found, _ = sources.lidar_paths(C.VisualTile(0, tile.i + di, tile.j + dj))
            except NoCoverage:
                continue
            paths.update({p.name: p for p in found})
    return identities(paths)


def deck_stage(tile, grid_pos, grid_idx, materials, wet, inputs):
    from . import bridges
    from .build import bridge_decks

    grid_pos = grid_pos.copy()
    osm_decks = bridges.produce(tile, grid_pos, inputs)
    decks = 0 if osm_decks else bridge_decks(grid_pos, grid_idx, materials, wet)
    roads = bridges.road_heights(inputs.roads, osm_decks)
    # Samplers are build-local closures. Consume them before caching the resulting mesh/road data.
    for deck in osm_decks:
        deck.height_at = None
    return grid_pos, osm_decks, decks, roads


def terrain_stage(tile, inputs):
    """Terrain and deck outputs, including the approaches that mutate the ground grid."""
    from turku3d.col.materials import mat_id

    from . import gardens, surfaces, walkable
    from .build import grid_mesh, water_level, water_mask

    grid_pos, grid_idx = grid_mesh(tile, inputs.heights)
    grid_pos = gardens.grade(grid_pos, inputs.gardens)
    grid_pos = walkable.grade(grid_pos, inputs.walkable)
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


def cached_terrain(cache, tile, inputs, common):
    """Keep the structural terrain cache orchestration outside the tile build body."""
    from pathlib import Path

    from turku3d.stage_cache import source_key

    from . import gardens, surfaces, walkable
    from .build import grid_mesh, water_level, water_mask

    return cache.run(
        "terrain",
        source_key(
            terrain_stage,
            grid_mesh,
            water_mask,
            water_level,
            Path(__file__).with_name("water.py"),
            surfaces.classify,
            Path(gardens.__file__),
            Path(walkable.__file__),
            *common,
        ),
        [
            tile,
            inputs.heights,
            inputs.gardens,
            inputs.walkable,
            inputs.points,
            inputs.ortho,
            inputs.streets,
            inputs.surfaces,
            inputs.water,
            bool(inputs.roads and inputs.roads.get("bridge_ways")),
        ],
        lambda: terrain_stage(tile, inputs),
    )


def structures_stage(
    cache, tile, inputs, grid_pos, sample, dry, river, bank, solid, sources, decks, openings
):
    """Compose mapped stairs, retaining edges and moored boats, then preserve passages."""
    from . import boats, stairs
    from .openings import stage as opening_stage

    stair_meshes, dry, solid = stairs.from_inputs(
        cache, tile, inputs, grid_pos, sample, dry, solid, sources, decks
    )
    boat_meshes = boats.stage(cache, tile, inputs.boats)
    dry, river, bank, solid, stair_meshes = opening_stage(
        cache, openings, dry, river, bank, solid, stair_meshes
    )
    boat_meshes = openings.structures(boat_meshes)
    seconds = {
        dest: cache.metrics[src]["seconds"]
        for dest, src in (
            ("stairs_geometry", "stairs"),
            ("stair_ground", "stair_ground"),
            ("opening_clearance", "opening_clearance"),
            ("boats", "boats"),
        )
    }
    return dry, river, bank, solid, stair_meshes, boat_meshes, seconds

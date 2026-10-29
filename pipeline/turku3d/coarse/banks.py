"""Exact water-outline terrain cuts and shared visible/solid river banks."""

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh

from . import surfaces


def join(*meshes):
    offsets = np.cumsum([0, *[len(m.positions) for m in meshes[:-1]]])
    return Mesh(
        np.concatenate([m.positions for m in meshes]),
        np.concatenate([m.indices + offset for m, offset in zip(meshes, offsets, strict=True)]),
        np.concatenate([m.material for m in meshes]),
    )


def small_bodies(water):
    """Complete, isolated compact polygons; never classify clipped river reaches."""
    body = shapely.union_all(water)
    return [
        g for g in shapely.get_parts(body)
        if g.geom_type == "Polygon" and 5 <= g.area <= 5000
        and max(np.ptp(np.asarray(g.exterior.coords), axis=0)) <= 120
        and 4 * np.pi * g.area / g.length**2 >= 0.15
    ]


def pond_levels(water, paths):
    """Sample each entire shoreline, so tile boundaries cannot change its level."""
    from turku3d import config as C
    from turku3d.ground.inputs import read_returns

    result = []
    available = {str(p) for p in paths}
    for body in small_bodies(water):
        ring = body.buffer(3).difference(body)
        bounds = C.BBoxLocal(*ring.bounds)
        needed = [C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz"
                  for b in C.laz_tiles_for_bbox(bounds)]
        if any(str(p) not in available for p in needed):
            continue  # Never estimate a cross-tile pond from a partial shoreline.
        points, _ = read_returns(needed, bounds, [1, 2, 8, 9, 12])
        near = shapely.contains_xy(ring, points[:, 0], points[:, 2])
        if near.sum() >= 5:
            result.append((body, float(np.median(points[near, 1])) - 0.35))
    return result


def produce(positions, indices, materials, keep, water, level, regions=(), barriers=(), ponds=()):
    """Cut both sides on the same outline, keeping grid and collision-tile constraints.

    Mapped quays/walls retain the surveyed rim and descend vertically. Other banks
    blend over three metres to 40 cm below water. Small ponds instead have a
    two-metre grassy lip and a 65 cm bed without natural vertical faces. The bed is collision
    only; the bank faces are identical in the visual and solid meshes.
    """
    empty = Mesh(np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
    if not water:
        dry = Mesh(positions, indices[keep], materials[keep]).compact()
        return dry, empty, empty, dry
    body = shapely.union_all(water)
    mapped = [g for g, m in regions if m == mat_id("quay")]
    mapped.extend(
        g
        for g, tags, *_ in barriers
        if tags.get("barrier") in ("wall", "retaining_wall", "city_wall")
        or tags.get("man_made") in ("quay", "embankment")
        or tags.get("embankment") == "yes"
    )
    quay = shapely.union_all(mapped).buffer(2.0)
    def grade_ponds(points):
        height = points[:, 1].copy()
        distances = [shapely.distance(shapely.points(points[:, [0, 2]]), pond) for pond, _ in ponds]
        nearest = np.argmin(distances, axis=0) if distances else None
        for number, ((pond, surface), d) in enumerate(zip(ponds, distances)):
            # A two-metre grassy lip, then a gradual return to the surveyed hillside.
            lip = surface - 0.15 + 0.25 * np.minimum(d, 2)
            width = np.maximum(2, 1.5 * np.abs(points[:, 1] - lip) / 0.45)
            t = np.clip((d - 2) / width, 0, 1)
            t = t * t * (3 - 2 * t)
            graded = lip * (1 - t) + points[:, 1] * t
            masonry = (np.zeros(len(points)) if quay.is_empty else
                       np.clip(1 - shapely.distance(shapely.points(points[:, [0, 2]]), quay), 0, 1))
            natural = graded * (1 - masonry) + points[:, 1] * masonry
            inside = shapely.contains_xy(pond, points[:, 0], points[:, 2])
            depth = 0.15 + 0.5 * np.clip(
                shapely.distance(shapely.points(points[:, [0, 2]]), pond.boundary) / 1.25, 0, 1
            )
            natural[inside] = ((surface - depth[inside]) * (1 - masonry[inside])
                               + points[inside, 1] * masonry[inside])
            use = (d < 2 + width) & (nearest == number)
            height[use] = natural[use]
        return height

    # Grade the original TIN before vector cuts: interpolating new cut vertices
    # preserves its planes, avoiding steep slivers at concave shoreline corners.
    positions = positions.copy()
    positions[:, 1] = grade_ponds(positions)
    cuts = [(body.buffer(3), mat_id("soil"))]
    for pond, _ in ponds:
        cuts.extend([(pond.buffer(2), mat_id("grass")), (pond.buffer(-1), mat_id("water"))])
    cuts.append((body, mat_id("water")))
    materials = materials.copy()
    centres = positions[indices].mean(axis=1)
    for polygon, material in cuts:
        materials[shapely.contains_xy(polygon, centres[:, 0], centres[:, 2])] = material
    p, ix, ids, _ = surfaces.split_boundaries(positions, indices, materials, keep, cuts)
    wet = ids == mat_id("water")
    if not wet.any():
        dry = Mesh(p, ix, ids).compact()
        return dry, empty, empty, dry
    wet_mesh = Mesh(p, ix[wet], ids[wet]).compact()
    dry = Mesh(p, ix[~wet], ids[~wet]).compact()

    def rim(points):
        distance = shapely.distance(shapely.points(points[:, [0, 2]]), body)
        # A continuous transition at the end of each mapped retaining edge.
        masonry = (
            np.zeros(len(points))
            if quay.is_empty
            else np.clip(1 - shapely.distance(shapely.points(points[:, [0, 2]]), quay), 0, 1)
        )
        blend = np.clip(distance / 3, 0, 1)
        target = (level - 0.4) * (1 - blend) + points[:, 1] * blend
        height = np.minimum(points[:, 1], target * (1 - masonry) + points[:, 1] * masonry)
        for pond, _ in ponds:
            d = shapely.distance(shapely.points(points[:, [0, 2]]), pond)
            nearest = d <= distance + 1e-7
            height[nearest] = points[nearest, 1]
        return height

    dry.positions[:, 1] = rim(dry.positions)
    # Cancel internal wet edges by coordinates: boundary cutting duplicates vertices.
    edges = np.concatenate([wet_mesh.indices[:, pair] for pair in ([0, 1], [1, 2], [2, 0])])
    xz = np.round(wet_mesh.positions[:, [0, 2]], 9)
    _, vertex = np.unique(xz, axis=0, return_inverse=True)
    _, first, counts = np.unique(
        np.sort(vertex[edges], axis=1), axis=0, return_index=True, return_counts=True
    )
    edge = edges[first[counts == 1]]
    ends = wet_mesh.positions[edge]
    # Window and tunnel cuts are not shorelines. Test both ends and the midpoint.
    probe = shapely.points(
        np.concatenate([ends[:, 0, [0, 2]], ends[:, 1, [0, 2]], ends.mean(axis=1)[:, [0, 2]]])
    )
    shore = (shapely.distance(probe, body.boundary).reshape(3, -1) < 1e-7).all(axis=0)
    ends = ends[shore]
    top = ends.reshape(-1, 3).copy()
    top[:, 1] = rim(top)
    top = top.reshape(-1, 2, 3)
    bottom = top.copy()
    bottom[:, :, 1] = level - 3
    pond_edge = np.zeros(len(ends), bool)
    for pond, surface in ponds:
        near = shapely.distance(shapely.points(ends.mean(axis=1)[:, [0, 2]]), pond) < 1e-7
        bottom[near, :, 1] = surface - 0.15
        pond_edge |= near
    bank_pos = np.stack([top[:, 0], top[:, 1], bottom[:, 0], bottom[:, 1]], axis=1).reshape(-1, 3)
    # Wet triangles face up; their boundary direction makes the bank face into water.
    bank_ix = (np.arange(len(top))[:, None, None] * 4 + np.array([[0, 1, 2], [1, 3, 2]])).reshape(
        -1, 3
    )
    stone = shapely.distance(shapely.points(ends.mean(axis=1)[:, [0, 2]]), quay) < 1e-7
    bank_ids = np.repeat(np.where(stone, mat_id("quay"), mat_id("soil")), 2).astype(np.uint8)
    # Natural pond edges join the shallow bed continuously; only mapped stone has a face.
    visible = np.repeat(~pond_edge | stone, 2)
    bank = Mesh(bank_pos, bank_ix.astype(np.uint32)[visible], bank_ids[visible]).compact()
    bed = Mesh(
        wet_mesh.positions.copy(),
        wet_mesh.indices,
        np.full(len(wet_mesh.indices), mat_id("soil"), np.uint8),
    )
    shore_heights = wet_mesh.positions[:, 1].copy()
    bed.positions[:, 1] = level - 3
    pond_triangles = np.zeros(len(bed.indices), bool)
    wet_mesh.positions[:, 1] = level
    for pond, surface in ponds:
        xy = shapely.points(wet_mesh.positions[:, [0, 2]])
        inside = shapely.distance(xy, pond) < 1e-7
        bed.positions[inside, 1] = shore_heights[inside]
        stone_bed = inside & (shapely.distance(xy, quay) < 1e-7)
        bed.positions[stone_bed, 1] = np.minimum(bed.positions[stone_bed, 1], surface - 0.15)
        pond_triangles |= inside[bed.indices].all(axis=1)
        wet_mesh.positions[inside, 1] = surface
    solid = join(dry, wet_mesh, bank, bed)
    # The shallow bed can emerge at the grassy lip. Draw it as well as collide
    # with it; this closes the shore without any natural vertical wall.
    bank = join(bank, Mesh(bed.positions, bed.indices[pond_triangles],
                           np.full(pond_triangles.sum(), mat_id("grass"), np.uint8)).compact())
    return dry, wet_mesh, bank, solid


def stage(cache, tile, grid_pos, grid_idx, inputs, keep, level, separate_decks):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    # Only pond tiles read extra ground keys. Read whole shorelines from canonical
    # blocks, including the part beyond the tile's normal twenty-metre margin.
    bounds = [C.BBoxLocal(*p.buffer(3).bounds) for p in small_bodies(inputs.water)]
    paths = sorted({C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz"
                    for box in bounds for b in C.laz_tiles_for_bbox(box)
                    if (C.raw_dir("laz") / "maanpinta" / f"{b.name}.laz").exists()})
    ponds = cache.run("pond_levels", source_key(Path(__file__)),
                      [inputs.water, [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]], lambda: pond_levels(inputs.water, paths)) if paths else []
    centres = grid_pos[grid_idx].mean(axis=1)
    ids = surfaces.classify(centres, inputs.ortho, tile, inputs.streets, inputs.surfaces)
    water = inputs.water
    # Legacy decks stay solid; separate bridge slabs span the exact water cut.
    if not separate_decks and np.any(ids == mat_id("bridge_deck")):
        decks = shapely.union_all(
            shapely.polygons(grid_pos[grid_idx[ids == mat_id("bridge_deck")]][:, :, [0, 2]])
        )
        water = [g.difference(decks) for g in water]
    return cache.run(
        "river_banks",
        source_key(
            Path(__file__),
            surfaces.split_boundaries,
            Path(C.__file__),
            Path(__file__).parents[1] / "col/materials_v1.json",
        ),
        [grid_pos, grid_idx, ids, keep, water, level, inputs.surfaces, inputs.barriers, ponds],
        lambda: produce(
            grid_pos, grid_idx, ids, keep, water, level, inputs.surfaces, inputs.barriers, ponds
        ),
    )

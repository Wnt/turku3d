"""Road-owned terrain above box underpasses; no second visible/collidable deck."""

from functools import lru_cache

import numpy as np
import shapely

from .tunnels import dimensions


def carried_roads(ways, roads):
    """Join full source ways once per area, before tile clipping (including split carriageways)."""
    tree = shapely.STRtree([line for line, _ in roads])
    result = []
    for key, line, tags in ways:
        carried = []
        if (
            tags.get("highway") in {"footway", "path", "cycleway", "pedestrian", "steps"}
            and tags.get("tunnel") != "building_passage"
            and tags.get("vault") != "barrel"
        ):
            for index in tree.query(line, predicate="crosses"):
                road, road_tags = roads[index]
                # End connections and parallel roads are not a carried surface.
                crossing = line.intersection(road)
                if (
                    crossing.geom_type != "Point"
                    or min(line.project(crossing), line.length - line.project(crossing)) < 1
                ):
                    continue
                width, _ = dimensions(road_tags)
                carried.append(road.buffer(width / 2, cap_style="flat", join_style="mitre"))
        result.append(
            (key, line, dict(tags, road_top=shapely.union_all(carried)) if carried else tags)
        )
    return result


def exclusion(ways):
    """Carried carriageways near each underpass, including crowns reaching onto the deck."""
    return shapely.union_all(
        [
            tags["road_top"].intersection(line.buffer(dimensions(tags)[0] / 2 + 12))
            for _, line, tags in ways
            if "road_top" in tags
        ]
    )


def repair(tile, grid_pos, ways, bank_sample):
    """Bridge lower passage lidar with the two banks, changing only a narrow terrain strip.

    Full-way bank profiles and world-aligned grid vertices give adjacent tiles identical heights.
    Sample intact terrain ten metres beyond each wall: its coarse TIN includes
    a wider trough than the clear passage. Interpolate the complete strip to those banks.
    """
    from .inputs import CELLS

    step = tile.size / CELLS
    profiles = []
    for _, line, tags in ways:
        if "road_top" not in tags:
            continue
        width, _ = dimensions(tags)
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length / 2)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        direction = np.gradient(xz, axis=0)
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        normal = np.c_[-direction[:, 1], direction[:, 0]]
        radius = width / 2 + 10
        banks = np.array(
            [[bank_sample(*q) for q in xz + normal * sign * radius] for sign in (-1, 1)]
        )
        # Include every vertex supporting the roof; the open apron removes this
        # short extension beyond each portal again, without cutting the deck.
        footprint = line.buffer(radius, cap_style="flat").union(
            line.buffer(width / 2 + 0.4, cap_style="flat").buffer(step * np.sqrt(2))
        )
        profiles.append((line, distances, xz, normal, banks, radius, footprint))

    def lifted(query, heights):
        heights = np.array(heights, copy=True)
        points = shapely.points(query)
        for line, distances, xz, normals, banks, radius, footprint in profiles:
            use = shapely.covers(footprint, points)
            if not use.any():
                continue
            ds = shapely.line_locate_point(line, points[use])
            centre = np.column_stack([np.interp(ds, distances, xz[:, i]) for i in range(2)])
            normal = np.column_stack([np.interp(ds, distances, normals[:, i]) for i in range(2)])
            across = np.clip(((query[use] - centre) * normal).sum(axis=1) / radius, -1, 1)
            left, right = [np.interp(ds, distances, side) for side in banks]
            target = left * (1 - across) / 2 + right * (1 + across) / 2
            heights[use] = np.maximum(target, heights[use])
        return heights

    positions = grid_pos.copy()
    positions[:, 1] = lifted(positions[:, [0, 2]], positions[:, 1])
    return positions, lifted


def sampler(tile, positions, original, lifted):
    """Sample repaired terrain triangles, including the same repaired vertices across tile seams."""
    from .inputs import CELLS
    from .surfaces import grid_height

    step = tile.size / CELLS
    heights = positions[:, 1].reshape(CELLS + 1, CELLS + 1)

    @lru_cache(maxsize=4096)
    def vertex(i, j):
        x, z = i * step, j * step
        return lifted(np.array([[x, z]]), [original(x, z)])[0]

    def sample(x, z):
        if tile.bbox.xmin <= x <= tile.bbox.xmax and tile.bbox.zmin <= z <= tile.bbox.zmax:
            return grid_height(tile, heights, x, z)
        u, v = x / step, z / step
        i, j = int(np.floor(u)), int(np.floor(v))
        a, b = u - i, v - j
        if a + b <= 1:
            return vertex(i, j) * (1 - a - b) + vertex(i + 1, j) * a + vertex(i, j + 1) * b
        return (
            vertex(i + 1, j + 1) * (a + b - 1)
            + vertex(i + 1, j) * (1 - b)
            + vertex(i, j + 1) * (1 - a)
        )

    return sample


def stage(cache, tile, inputs, grid_pos, grid_idx, shells, sources, root):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    from . import kerbs, surfaces
    from .build import grid_height
    from .extrusion import extrude
    from .inputs import CELLS
    from .stages import kerb_inputs

    base = Path(__file__).parents[1]
    from .tunnels import ground_sampler
    from .tunnels import produce as produce_tunnels

    surface_sample = kerbs.height_sampler(
        tile, grid_pos[:, 1].reshape(CELLS + 1, CELLS + 1), root, sources
    )
    original_surface = surface_sample
    carried_mask = exclusion(inputs.tunnels)
    if not carried_mask.is_empty:
        grid_pos, lifted = repair(tile, grid_pos, inputs.tunnels, original_surface)
        surface_sample = sampler(tile, grid_pos, original_surface, lifted)
    tunnels = cache.run(
        "tunnels",
        source_key(
            grid_height,
            Path(C.__file__),
            base / "col/materials_v1.json",
            Path(__file__).with_name("tunnels.py"),
            Path(__file__).with_name("openings.py"),
            Path(__file__),
            Path(__file__).with_name("bridge_details.py"),
            Path(kerbs.__file__),
            surfaces.grid_height,
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
            kerb_inputs(tile, sources) if inputs.tunnels else None,
        ],
        lambda: produce_tunnels(
            tile,
            inputs.tunnels,
            grid_pos,
            grid_idx,
            shells,
            extrude,
            ground_sampler(original_surface),
            original_surface,
            surface_sample,
        ),
    )
    return tunnels, grid_pos, surface_sample, carried_mask

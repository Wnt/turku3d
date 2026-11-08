"""Height-field ambient visibility: open ground stays lit, canyons and creases darken."""

from types import SimpleNamespace

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import visibility as V
from turku3d.package.gltf import GlbMesh, accessor_array, build_glb, read_glb

TILE = C.VisualTile.parse("t0_1_0")
FLAT = np.zeros((129, 129))
UP, EAST, WEST = [0, 1, 0], [1, 0, 0], [-1, 0, 0]


def block(x0, z0, x1, z1, top):
    """A flat-roofed LOD2 building in engine coordinates around TILE (tile-local + origin)."""
    ox, oz = TILE.origin[0], TILE.origin[2]
    footprint = shapely.box(x0 + ox, z0 + oz, x1 + ox, z1 + oz)
    return SimpleNamespace(footprint=footprint, base=0.0, top=top, roof_parts=[(footprint, np.array([0, 0, top]))])


def visibility(buildings, points, normals):
    return V.sample(V.height_field(TILE, buildings, FLAT), np.asarray(points, float), np.asarray(normals, float))


def test_open_ground_and_roofs_are_fully_visible():
    rgb = visibility([block(100, 100, 120, 120, 12)], [[50, 0, 50], [110, 12, 110]], [UP, UP])
    np.testing.assert_allclose(rgb[:, :2], 1, atol=.02)


def test_street_canyon_floor_loses_sky_but_no_local_crease():
    # Two 20 m blocks 12 m apart along x; a street-centre ground vertex 6 m from each wall.
    canyon = [block(80, 60, 100, 200, 20), block(112, 60, 132, 200, 20)]
    (r, g, _), = visibility(canyon, [[106, 0, 130]], [UP])
    assert r > .97 and .2 < g < .6
    # Higher up the same wall sees more sky than at its base.
    low, high = visibility(canyon, [[100, 1, 130], [100, 18, 130]], [EAST, EAST])
    assert low[1] < high[1] and low[0] < high[0]


def test_wall_base_and_adjacent_ground_form_a_crease():
    wall = [block(100, 0, 140, 250, 15)]
    ground, base, open_ground = visibility(wall, [[99.8, 0, 125], [100, .2, 125], [60, 0, 125]], [UP, WEST, UP])
    assert .3 < min(ground[:2]) < .7  # half the sky behind a 15 m wall
    assert .3 < base[0] < .7  # ground fills the lower half of the wall's hemisphere
    assert min(open_ground[:2]) > .97


def test_tile_border_vertices_see_neighbour_context_identically():
    # A neighbour building just west of the tile shades a vertex at the shared border.
    neighbour = [block(-20, 100, -2, 150, 25)]
    (r, g, _), = visibility(neighbour, [[0.5, 0, 125]], [UP])
    assert g < .75


def test_chunking_is_deterministic_and_bounded():
    rng = np.random.default_rng(1)
    points = rng.uniform([0, 0, 0], [250, 10, 250], (V.CHUNK + 37, 3))
    normals = rng.normal(size=points.shape)
    city = [block(100, 100, 130, 140, 18), block(20, 200, 60, 230, 9)]
    whole = visibility(city, points, normals)
    parts = np.concatenate([visibility(city, points[:500], normals[:500]), visibility(city, points[500:], normals[500:])])
    np.testing.assert_array_equal(whole, parts)
    assert whole.dtype == np.float32 and np.all((whole >= 0) & (whole <= 1)) and np.all(whole[:, 2] == 1)


def test_bake_skips_unlit_and_instanced_surfaces_and_ships_unorm8():
    tri = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], np.float32)
    meshes = [GlbMesh(name, tri, np.array([[0, 2, 1]], np.uint32), extras=extras) for name, extras in (
        ("street", {"layer": "ground", "material_key": "draft_asphalt"}),
        ("tunnel", {"layer": "tunnels", "material_key": "tunnel_concrete"}),
        ("window", {"layer": "buildings", "component_box": {}}),
    )]
    street, tunnel, window = V.bake(meshes, V.height_field(TILE, [], FLAT))
    assert street.baked_visibility.shape == (3, 3)
    assert tunnel.baked_visibility is None and window.baked_visibility is None
    js, binary = read_glb(build_glb([street]))
    attribute = js["meshes"][0]["primitives"][0]["attributes"]["COLOR_1"]
    assert js["accessors"][attribute]["componentType"] == 5121
    np.testing.assert_allclose(accessor_array(js, binary, attribute)[:, :3], street.baked_visibility, atol=.5 / 255)


def reference_march(field, positions, normals):
    """The per-vertex march this bake replaced (31de046e): the R/G contract of COLOR_1."""
    theta = (np.linspace(-np.pi / 2, np.pi / 2, V.ELEVATIONS + 1)[:-1] + np.linspace(-np.pi / 2, np.pi / 2, V.ELEVATIONS + 1)[1:]) / 2
    phi = (np.arange(V.AZIMUTHS) + .5) * 2 * np.pi / V.AZIMUTHS
    directions = np.stack([np.cos(phi), np.sin(phi)], axis=1)
    p, n = np.asarray(positions, float), np.asarray(normals, float)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(length > 1e-6, n / np.maximum(length, 1e-9), [0.0, 1.0, 0.0])
    flat = np.hypot(n[:, 0], n[:, 2])
    lateral = np.where(flat[:, None] > 1e-3, n[:, [0, 2]] / np.maximum(flat, 1e-9)[:, None], 0)
    start = p[:, [0, 2]] + lateral * (V.WALL_OFFSET * flat)[:, None]
    ground = field.sample(field.terrain, start[:, 0], start[:, 1])
    height = np.maximum(p[:, 1] + .1 * np.maximum(n[:, 1], 0), ground + V.GROUND_CLEARANCE)
    x = start[:, 0, None, None] + directions[None, :, 0, None] * V.STEPS[None, None, :]
    z = start[:, 1, None, None] + directions[None, :, 1, None] * V.STEPS[None, None, :]
    elevation = np.arctan2(field.sample(field.heights, x, z) - height[:, None, None], V.STEPS[None, None, :])
    near, far = elevation[:, :, :V.STEPS_LOCAL].max(axis=2), elevation.max(axis=2)
    weight = (lateral @ directions.T * flat[:, None])[:, :, None] * np.cos(theta) + n[:, 1, None, None] * np.sin(theta)
    bins = np.maximum(weight, 0) * np.cos(theta) * (np.pi / V.ELEVATIONS)

    def above(horizon):
        return np.where(theta > horizon[:, :, None], bins, 0).sum(axis=(1, 2))

    whole, local, open_sky, sky = bins.sum(axis=(1, 2)), above(near), above(0 * far), above(np.maximum(far, 0))
    return np.clip(np.stack([np.where(whole > 1e-6, local / np.maximum(whole, 1e-9), 1),
                             np.where(open_sky > 1e-6, sky / np.maximum(open_sky, 1e-9), 1)], axis=1), 0, 1)


def test_bake_matches_the_reference_march_within_tolerance():
    """Deduplicated marches, padded lookups and prefix-sum bins stay within ±0.02 of the march.

    Covers sloped terrain, pitched roofs, walls, ground, downward and degenerate normals,
    repeated rows, shared start points and points beyond the padded margin."""
    rng = np.random.default_rng(7)
    g = np.arange(129) * (TILE.size / 128)
    terrain = 0.03 * g[None, :] + 2 * np.sin(g[:, None] / 17.0)
    ox, oz = TILE.origin[0], TILE.origin[2]
    city = [block(100, 100, 130, 140, 18), block(20, 200, 60, 230, 9), block(-30, 40, -5, 90, 22),
            block(150, -20, 180, 30, 14), block(200, 150, 240, 260, 30)]
    pitched = shapely.box(60 + ox, 60 + oz, 90 + ox, 80 + oz)
    city.append(SimpleNamespace(footprint=pitched, base=0.0, top=16.0,
                                roof_parts=[(pitched, np.array([0.4, 0.0, 10 - 0.4 * (60 + ox)]))]))
    field = V.height_field(TILE, city, terrain)
    count = 6000
    points = rng.uniform([-70, 0, -70], [320, 32, 320], (count, 3))
    normals = rng.normal(size=(count, 3))
    axes = np.array([UP, EAST, WEST, [0, 0, 1], [0, 0, -1], [0, -1, 0], [0, 0, 0], [1e-4, 1, 0]], float)
    normals[: 8 * 200] = np.repeat(axes, 200, axis=0)
    points[1000:1400, [0, 2]] = points[1400:1800, [0, 2]]  # wall columns: one start, many heights
    points, normals = np.concatenate([points, points[:500]]), np.concatenate([normals, normals[:500]])
    got = V.sample(field, points, normals)
    expected = reference_march(field, points, normals)
    error = np.abs(got[:, :2] - expected)
    assert error.max() <= .02 and error.mean() < 1e-4
    assert np.all(got[:, 2] == 1) and got.dtype == np.float32


def test_stage_caches_only_the_bake_and_matches_bake(tmp_path):
    from turku3d.stage_cache import StageCache

    tri = np.array([[10, 0, 10], [12, 0, 10], [10, 3, 10]], np.float32)
    meshes = [
        GlbMesh("wall", tri, np.array([[0, 2, 1]], np.uint32), extras={"layer": "buildings"}),
        GlbMesh("tunnel", tri, np.array([[0, 2, 1]], np.uint32), extras={"layer": "tunnels"}),
        GlbMesh("ground", tri + [5, 0, 5], np.array([[0, 2, 1]], np.uint32),
                normals=np.tile(np.float32([0, 1, 0]), (3, 1)), extras={"layer": "ground"}),
    ]
    city = [block(14, 5, 30, 20, 12)]
    expected = V.bake(meshes, V.height_field(TILE, city, FLAT))
    cache = StageCache(tmp_path / ".stages" / TILE.id)
    for cached in (False, True):
        baked = V.stage(cache, TILE, meshes, city, FLAT)
        assert cache.metrics["visibility"]["cached"] is cached
        for a, b in zip(baked, expected, strict=True):
            assert (a.baked_visibility is None) == (b.baked_visibility is None)
            if a.baked_visibility is not None:
                np.testing.assert_array_equal(a.baked_visibility, b.baked_visibility)
    assert baked[1].baked_visibility is None and baked[0].positions is meshes[0].positions
    empty = V.stage(cache, TILE, meshes[1:2], city, FLAT)
    assert empty[0].baked_visibility is None and "visibility" in cache.metrics


def crowns(x0, z0, x1, z1, top, step=0.5):
    """Class-5 returns of a closed crown layer over a tile-local box, in engine coordinates."""
    x, z = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
    return np.column_stack([x.ravel() + TILE.origin[0], np.full(x.size, top), z.ravel() + TILE.origin[2]])


def test_crowns_shade_the_sky_term_only_and_reach_across_the_tile_border():
    forest = crowns(-30, 60, 80, 200, 18.0)
    field = V.height_field(TILE, [], FLAT, forest)
    points = [[20, 0, 130], [-0.5, 0, 130], [0.5, 0, 130], [140, 0, 130], [20, 0, 130]]
    rgb = V.sample(field, np.asarray(points, float), np.asarray([UP, UP, UP, UP, EAST], float))
    under, west, east, open_ground, wall = rgb
    # Under a closed canopy the sky term drops to about 1 - CANOPY_OPACITY; the crease term is untouched.
    assert 0.25 <= under[1] <= 0.4 and under[0] == 1
    # Both sides of the tile border see the same crowns (the canopy is read FAR beyond the tile).
    assert abs(west[1] - east[1]) < 0.02
    assert open_ground[1] > 0.9 and wall[1] < under[1] + 0.05
    # Low returns (undergrowth) do not count, and no crowns leave the field as before.
    shrubs = V.height_field(TILE, [], FLAT, crowns(-30, 60, 80, 200, 1.5))
    assert shrubs.canopy is None
    assert V.height_field(TILE, [], FLAT).canopy is None


def test_canopy_crop_reaches_the_bake_horizon(monkeypatch):
    from turku3d.coarse import canopy_trees
    from turku3d.vegetation import trees

    bbox = TILE.bbox
    points = np.array([[bbox.xmin - 41, 10, bbox.zmin + 5], [bbox.xmin - 43, 10, bbox.zmin + 5], [bbox.xmax + 20, 10, bbox.zmax - 1]])
    monkeypatch.setattr(trees, "reduced_points", lambda path: (None, points))
    kept = canopy_trees.canopy_points(TILE, ["a.laz"])
    assert len(kept) == 2 and kept[:, 0].min() == bbox.xmin - 41

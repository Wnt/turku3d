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

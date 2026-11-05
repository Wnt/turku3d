"""Building exterior decks: measured surfaces, roof/noise rejection, and shared collision."""

from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.config import VisualTile
from turku3d.coarse.attachments import measure, produce, raised
from turku3d.coarse.stairs import collision


def points(x0, x1, z0, z1, height, cls=2):
    x, z = np.meshgrid(np.arange(x0, x1, 0.1), np.arange(z0, z1, 0.1))
    return np.c_[x.ravel(), np.full(x.size, height), z.ravel()], np.full(x.size, cls)


def inventory(*parts):
    b = SimpleNamespace(key="fixture", footprint=shapely.box(0, 0, 10, 10), base=0, top=12)
    return measure(
        [b], np.concatenate([p for p, _ in parts]), np.concatenate([c for _, c in parts])
    )


def test_ground_class_terrace_keeps_raw_level_and_lower_landing():
    result = inventory(points(10.05, 14, 2, 8, 2), points(14.2, 18, 1, 9, 0.4))
    decks = [r for r in result if r["kind"] == "terrace" and abs(r["height"] - 2) < 0.01]
    assert decks and sum(r["region"].area for r in decks) > 18
    assert all(abs(r["bottom"] - 0.4) < 0.01 for r in decks)


def test_balcony_keeps_clear_space_below_slab():
    result = inventory(points(10.05, 13, 2, 8, 4, 6), points(10.05, 18, 1, 9, 0.4))
    slabs = [r for r in result if r["kind"] == "balcony"]
    assert slabs and all(abs(r["bottom"] - 3.78) < 0.01 for r in slabs)


def test_roofs_vegetation_facades_and_level_pavement_do_not_make_decks():
    for surface in [
        points(10.05, 14, 2, 8, 12, 6),
        points(10.05, 14, 2, 8, 2, 5),
        points(10.05, 10.2, 2, 8, 2, 6),
        points(10.05, 18, 1, 9, 0.4),
    ]:
        assert inventory(surface, points(14.2, 18, 1, 9, 0.4)) == []


def test_regular_measured_treads_connect_to_a_supported_deck():
    parts = [points(10.05, 12.5, 2, 8, 2), points(15.8, 18, 1, 9, 0.3)]
    parts.extend(points(12.5 + i * 0.3, 12.8 + i * 0.3, 2, 8, 1.84 - i * 0.16) for i in range(9))
    result = inventory(*parts)
    treads = [r for r in result if r["kind"] == "tread"]
    assert len(treads) >= 3
    assert all(0.3 < r["height"] < 2 for r in treads)


def test_visual_mesh_and_collision_share_top_and_preserve_tile_edges():
    record = dict(
        building="fixture",
        region=shapely.box(248, 20, 252, 24),
        height=3,
        bottom=1,
        kind="terrace",
        support=100,
    )
    pieces = []
    for tile in [VisualTile(0, 0, 0), VisualTile(0, 1, 0)]:
        meshes, openings = produce(tile, [record])
        assert len(meshes) == len(openings) == 1
        mesh = meshes[0]
        assert mesh.positions[:, 1].max() == 3
        cell = next(
            c
            for c in tile.collision_tiles()
            if c.bbox.xmin <= mesh.positions[:, 0].mean() < c.bbox.xmax
            and c.bbox.zmin <= 22 < c.bbox.zmax
        )
        p, i, _ = collision(
            cell, meshes, np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8)
        )
        assert len(i) and p[:, 1].max() == 3
        pieces.append(np.unique(mesh.positions[mesh.positions[:, 0] == 250], axis=0))
    np.testing.assert_array_equal(pieces[0], pieces[1])


def test_continuous_ramp_and_unclassified_car_roof_are_rejected():
    ramp, classes = points(10.05, 18, 1, 9, 0.4)
    ramp[:, 1] += (ramp[:, 0] - 10) * 0.3
    assert inventory((ramp, classes)) == []
    assert inventory(points(10.05, 14, 2, 8, 2, 1), points(14.2, 18, 1, 9, 0.4)) == []


def test_neighbour_building_roof_is_excluded_by_complete_context():
    from turku3d.coarse.attachments import measure

    b = SimpleNamespace(key="owner", footprint=shapely.box(0, 0, 10, 10), base=0, top=12)
    neighbour = SimpleNamespace(key="annex", footprint=shapely.box(10, 1, 14, 9), base=0.4, top=2)
    roof, classes = points(10.05, 14, 2, 8, 2, 6)
    ground, labels = points(14.2, 18, 1, 9, 0.4)
    assert measure([b], np.r_[roof, ground], np.r_[classes, labels], context=[neighbour]) == []


def test_stacked_balcony_does_not_erase_the_terrace_below():
    result = inventory(
        points(10.05, 14, 2, 8, 2), points(10.05, 13, 2, 8, 5, 6), points(14.2, 18, 1, 9, 0.4)
    )
    assert any(r["kind"] == "terrace" and abs(r["height"] - 2) < 0.01 for r in result)
    assert any(r["kind"] == "balcony" and abs(r["height"] - 5) < 0.01 for r in result)


def test_ground_sampler_uses_decks_but_preserves_walkable_void_under_balcony():
    from turku3d.coarse.attachments import sampler

    records = [
        dict(region=shapely.box(1, 1, 3, 3), height=2, kind="terrace"),
        dict(region=shapely.box(1, 1, 5, 5), height=5, kind="balcony"),
    ]
    height = sampler(lambda x, z: 0.4, records)
    assert height(2, 2) == 2
    assert height(4, 4) == height(8, 8) == 0.4


def test_mapped_stairs_reach_the_reconstructed_terrace_level():
    from turku3d.coarse.attachments import sampler
    from turku3d.coarse.stairs import produce as stair_meshes

    height = sampler(
        lambda x, z: 0.4, [dict(region=shapely.box(10, 2, 13, 8), height=2, kind="terrace")]
    )
    meshes, cuts = stair_meshes(
        VisualTile(0, 0, 0),
        ([("mapped", shapely.LineString([(12, 5), (18, 5)]), {"width": "2"})], []),
        height,
    )
    assert meshes and cuts
    assert max(m.positions[:, 1].max() for m in meshes) == 2
    assert min(m.positions[:, 1].min() for m in meshes) <= 0.4


def test_float32_height_at_rounded_histogram_boundary_keeps_nonnegative_bins():
    # 1.8f32 rounds below a lower edge computed as floor(h/.05)*.05 in float32.
    # Farm source shards use that dtype, unlike the original double fixtures.
    deck, classes = points(10.05, 14, 2, 8, 1.8)
    ground, labels = points(14.2, 18, 1, 9, 0.1)
    result = inventory((deck.astype(np.float32), classes), (ground.astype(np.float32), labels))
    assert any(abs(r["height"] - 1.8) < 1e-5 and r["region"].area > 18 for r in result)


def test_support_boundary_tracing_preserves_rotated_grid_area_and_holes():
    from turku3d.coarse.attachments import CELL, support_region

    mask = np.ones((20, 12), bool)
    mask[4:8, 3:7] = False
    mask[12:, 8:] = False
    angle = 0.63
    along = np.array([np.cos(angle), np.sin(angle)])
    outward = np.array([along[1], -along[0]])
    region = support_region(mask, np.array([1234.125, 4567.875]), along, outward)
    assert region.is_valid
    assert abs(region.area - mask.sum() * CELL**2) < 1e-9
    assert len(region.interiors) == 1


def test_decks_at_the_terrain_height_are_terrain_not_platforms():
    # A level contour band of a slope beside a building: the deck lies on the terrain it would replace.
    slope = lambda x, z: 10.0 + 0.25 * (np.asarray(z) - 10.0)  # noqa: E731
    band = dict(building="b", region=shapely.box(0, 10, 10, 12), height=10.1, bottom=9.86, kind="terrace")
    porch = dict(building="b", region=shapely.box(0, -3, 4, 0), height=8.0, bottom=6.0, kind="terrace")
    step = dict(building="b", region=shapely.box(0, 12, 10, 12.4), height=10.6, bottom=10.36, kind="tread")
    balcony = dict(building="b", region=shapely.box(0, -3, 4, 0), height=4.0, bottom=3.78, kind="balcony")
    assert raised([band, porch, step, balcony], slope) == [porch, balcony]

"""Small synthetic roof-selection fixtures; real-data validation is a manual audit."""

from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from turku3d.buildings.facades import ortho_roof_key, roof_colour
from turku3d.coarse.build import extrude
from turku3d.coarse.inputs import Building
from turku3d.coarse.roofs import envelope, plane_height, supported_offset


def surface(x0, x1, height):
    return shapely.Polygon([(x0, 0, height), (x1, 0, height), (x1, 10, height), (x0, 10, height)])


def test_upper_decks_replace_mean_without_losing_terraces_or_holes():
    footprint = shapely.box(0, 0, 20, 10).difference(shapely.box(2, 2, 4, 4))
    parts = envelope(footprint, [surface(0, 20, 8), surface(0, 10, 20)], 14)
    assert sum(p.area for p, _ in parts) == pytest.approx(footprint.area)
    assert shapely.union_all([p for p, _ in parts]).equals(footprint)
    assert next(
        plane_height(h, [5, 5]) for p, h in parts if p.covers(shapely.Point(5, 5))
    ) == pytest.approx(20)
    assert next(
        plane_height(h, [15, 5]) for p, h in parts if p.covers(shapely.Point(15, 5))
    ) == pytest.approx(8)


def test_lidar_requires_broad_coherent_interior_support():
    x, z = np.meshgrid(np.arange(1.1, 19, 0.3), np.arange(1.1, 19, 0.3))
    points = np.c_[x.ravel(), 10 + 0.1 * x.ravel(), z.ravel()]
    plane = np.array([0.1, 0, 7])
    polygon = shapely.box(0, 0, 20, 20)
    offset, evidence = supported_offset(polygon, plane, points, 0)
    assert offset == pytest.approx(3)
    assert evidence["source"] == "class6_plane_offset"
    assert supported_offset(polygon, plane, points[:20], 0)[0] == 0
    # A tiny dense roof fixture must not move a whole terrace.
    assert supported_offset(polygon, plane, np.repeat(points[:20], 10, axis=0), 0)[0] == 0
    mixed = points.copy()
    mixed[::2, 1] += 4
    # Equal-area cell residuals with two incompatible levels cannot move a plane.
    mixed[:, 1] += np.where(mixed[:, 0] > 10, 5, 0)
    assert supported_offset(polygon, plane, mixed, 0)[0] == 0


def test_roof_colour_keeps_dark_roof_and_rejects_shadow_and_green():
    bright = np.repeat([[205, 210, 210]], 70, axis=0)
    samples = np.r_[
        bright, np.repeat([[25, 30, 30]], 20, axis=0), np.repeat([[50, 130, 50]], 10, axis=0)
    ]
    assert roof_colour(samples)[0] == pytest.approx([205, 210, 210])
    assert roof_colour(np.full((100, 3), 40))[0] == pytest.approx([40] * 3)


def test_roof_without_valid_ortho_pixels_has_explicit_fallback():
    building = SimpleNamespace(footprint=shapely.box(0, 0, 4, 4))
    key, colour = ortho_roof_key(building, [])
    assert key == "roof_metal_gray"
    assert colour["pixels"] == 0
    assert colour["source"] == "no_valid_true_ortho_pixels"


def test_roof_parts_below_base_fall_back_to_closed_flat_shell():
    footprint = shapely.box(0, 0, 8, 6)
    building = Building("missing-roof", footprint, 2.0, 10.0, "roof_metal_gray")
    building.roof_parts = [(footprint, np.array([0.0, 0.0, 1.0]))]
    roof, roof_idx, _, _, walls, wall_uv, wall_idx = extrude(building)
    assert len(roof_idx) > 0
    assert len(wall_idx) > 0
    assert len(wall_uv) == len(walls)
    assert roof[:, 1].max() == pytest.approx(10.0)


def test_passage_keeps_upper_terrace_and_never_resurrects_lower_roof():
    from turku3d.coarse.build import extrude
    from turku3d.coarse.inputs import Building

    building = Building("passage", shapely.box(0, 0, 20, 10), 12, 20, "facade_plaster")
    building.roof_parts = [
        (shapely.box(0, 0, 10, 10), np.array([0.0, 0.0, 8.0])),
        (shapely.box(10, 0, 20, 10), np.array([0.0, 0.0, 20.0])),
    ]
    shell = extrude(building)
    assert shell[0][:, 0].min() == pytest.approx(10)
    assert shell[0][:, 1].min() == pytest.approx(20)
    building.footprint = shapely.box(0, 0, 10, 10)
    assert len(extrude(building)[0]) == 0


def test_folded_surveyed_roof_keeps_its_vertex_heights():
    folded = shapely.Polygon([(0, 0, 10), (10, 0, 12), (10, 10, 10), (0, 10, 12)])
    parts = envelope(shapely.box(0, 0, 10, 10), [folded], 5)
    assert sum(p.area for p, _ in parts) == pytest.approx(100)
    for x, z, height in folded.exterior.coords:
        values = [plane_height(h, [x, z]) for p, h in parts if p.covers(shapely.Point(x, z))]
        assert values and values == pytest.approx([height] * len(values))


def test_tagged_roofs_keep_photo_and_missing_pixels_use_stable_material(monkeypatch):
    from turku3d import config as C
    from turku3d.coarse import stages
    from PIL import Image
    import io

    monkeypatch.setattr(stages, "wall_meshes", lambda *args: [])
    tile = C.VisualTile(0, 0, 0)
    b = Building("tagged", shapely.box(10, 10, 20, 20), 0, 10, "facade_plaster")
    b.attributes = {"monumental": True, "roof_material": "copper"}
    photo = np.full((64, 64, 3), [165, 130, 110], np.uint8)
    mesh = stages.building_visual(tile, [(b, extrude(b))], photo, False)[0]
    assert mesh.extras["material_key"] == "roof_ortho"
    assert np.asarray(Image.open(io.BytesIO(mesh.image))).mean(axis=(0, 1)) == pytest.approx([165, 130, 110], abs=2)
    missing = stages.building_visual(tile, [(b, extrude(b))], photo * 0, False)[0]
    rgb = np.asarray(Image.open(io.BytesIO(missing.image))).mean(axis=(0, 1))
    assert rgb == pytest.approx(stages.roof_fallback(b), abs=2)
    assert not np.all(rgb == 64)
    first = stages.building_visual(tile, [(b, extrude(b))], photo, True)[0]
    assert np.asarray(Image.open(io.BytesIO(first.image))).mean(axis=(0, 1)) == pytest.approx(rgb)


def test_roof_crossing_tile_uses_photo_inside_and_material_outside():
    from turku3d import config as C
    from turku3d.coarse.stages import roof_crop
    from PIL import Image
    import io

    tile = C.VisualTile(0, 0, 0)
    roof = np.array([[240, 10, 100], [260, 10, 100], [260, 10, 120]])
    photo = np.full((250, 250, 3), [190, 160, 130], np.uint8)
    image, uv = roof_crop(tile, roof, photo, np.array([80, 110, 100], np.uint8))
    pixels = np.asarray(Image.open(io.BytesIO(image)))
    assert pixels[5, 2] == pytest.approx([190, 160, 130], abs=3)
    assert pixels[5, -3] == pytest.approx([80, 110, 100], abs=3)
    assert uv.min() == 0 and uv.max() == 1

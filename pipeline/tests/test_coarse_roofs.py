"""Small synthetic roof-selection fixtures; real-data validation is a manual audit."""

import numpy as np
import pytest
import shapely
from turku3d.buildings.facades import roof_colour
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

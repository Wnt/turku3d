"""Regressions for suspended road footbridges, measured stairs and rock cuts."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.coarse import bridges, retaining, stairs, surfaces
from turku3d.coarse.bridge_details import roadside_supports, style
from turku3d.coarse.stair_survey import measure


def crossing():
    return dict(
        id="walk",
        line=shapely.LineString([(20, 30), (50, 30)]),
        width=3,
        layer=1,
        tags={"highway": "footway"},
        stair_access=True,
        approaches=[
            dict(
                line=shapely.LineString([(50, 30), (57, 30)]), width=3, tags={"highway": "footway"}
            ),
            dict(
                line=shapely.LineString([(20, 20), (20, 30)]), width=2.5, tags={"highway": "steps"}
            ),
        ],
        under=[
            dict(
                line=shapely.LineString([(35, 0), (35, 70)]),
                width=18,
                tags={"highway": "secondary"},
            )
        ],
        survey={"stations": [0, 30], "heights": [6, 6]},
    )


def test_road_footbridge_keeps_level_landing_clear_lanes_and_unraised_ground(monkeypatch):
    monkeypatch.setattr("turku3d.coarse.tunnels.ground_sampler", lambda fallback: lambda x, z: 0)
    way = crossing()
    inputs = SimpleNamespace(
        points=np.array([[0, 0, 0], [100, 0, 0], [0, 0, 100], [100, 0, 100]]),
        buildings=[],
        roads={"bridge_ways": [way]},
    )
    grid = np.array([[51.0, 0, 30], [54.0, 0, 30]])
    deck = bridges.produce(C.VisualTile(0, 0, 0), grid, inputs)[0]
    np.testing.assert_array_equal(grid[:, 1], 0)
    assert deck.line.length == 37
    np.testing.assert_allclose(deck.height_at(np.array([[20, 30], [57, 30]])), 6.02)
    assert np.min(deck.meshes[1].positions[:, 1]) >= 4.5
    assert style(way)["thickness"] == 0.32
    columns = roadside_supports(
        way,
        deck.height_at,
        lambda p: np.zeros(len(p)),
        way["line"].buffer(1.5, cap_style="flat"),
        shapely.GeometryCollection(),
    )
    assert len(columns) == 2
    road = way["under"][0]["line"].buffer(9)
    for column in columns:
        assert not shapely.MultiPoint(column.positions[:, [0, 2]]).convex_hull.intersects(road)
        assert np.ptp(column.positions[:, 0]) <= 0.53


def test_measured_stair_preserves_landing_and_meets_deck_with_smooth_collision():
    line = shapely.LineString([(20, 20), (20, 30)])
    way = crossing()
    deck = bridges.Deck(way, way["line"], np.array([0, 30]), np.full(2, 6.02), [])
    tags = {"handrail": "yes", "_profile": dict(stations=[0, 4, 6, 10], heights=[2, 4, 4, 6])}
    meshes, openings = stairs.produce(
        C.VisualTile(0, 0, 0), ([("flight", line, tags)], []), lambda x, z: 2, [deck]
    )
    ramp = next(m for m in meshes if not m.visual)
    assert ramp.metadata["collision"]
    assert ramp.positions[:, 1].max() == 6.02
    # The central two-metre landing has a flat walking surface.
    triangles = ramp.positions[ramp.indices]
    centre = triangles.mean(axis=1)
    landing = triangles[(centre[:, 2] > 24) & (centre[:, 2] < 26)]
    assert len(landing) > 0
    np.testing.assert_allclose(landing[:, :, 1], 4)
    assert any(m.metadata["surface"] == "paint" for m in meshes)
    # No apron cuts a hole past the endpoint already continued by the deck.
    assert openings[0].bounds[3] == 30
    walking = shapely.union_all(shapely.polygons(triangles[:, :, [0, 2]]))
    assert openings[0].difference(walking).area < 1e-8
    assert not any(
        np.ptp(t[:, 1]) > 0.31
        for m in meshes
        if m.visual and m.material == "curb"
        for t in m.positions[m.indices]
    )


def test_lidar_profile_keeps_plateau_and_ignores_tree_returns():
    line = shapely.LineString([(0, 0), (12, 0)])
    x, z = np.meshgrid(np.arange(0, 12.01, 0.08), np.arange(-0.5, 0.51, 0.1))
    y = np.interp(x, [0, 4, 7, 12], [2, 4, 4, 6])
    points = np.c_[x.ravel(), y.ravel(), z.ravel()]
    points = np.r_[points, points + [0, 12, 0]]
    classes = np.r_[np.full(len(points) // 2, 2), np.full(len(points) // 2, 5)]
    profile = measure(line, 2.5, points, classes)
    assert profile is not None
    np.testing.assert_allclose(
        np.interp([4.5, 6.5], profile["stations"], profile["heights"]), 4, atol=0.04
    )
    np.testing.assert_allclose(
        np.interp([0, 12], profile["stations"], profile["heights"]), [2, 6], atol=0.15
    )


def test_stair_at_interior_landing_node_receives_survey(monkeypatch):
    from turku3d.coarse import stair_survey

    cloud = SimpleNamespace(points=np.zeros((1, 3)), records={"classification": [2]})
    monkeypatch.setattr(
        "turku3d.lidar.TileStore", lambda **kwargs: SimpleNamespace(query=lambda box: cloud)
    )
    monkeypatch.setattr(C, "laz_tiles_for_bbox", lambda box: [])
    monkeypatch.setattr(stair_survey, "measure", lambda *args: {"surveyed": True})
    row = ("stairs", shapely.LineString([(7, 0), (7, 10)]), {})
    stair_survey.attach(
        [row],
        [Path("source.laz")],
        [shapely.LineString([(0, 0), (5, 0)])],
        [shapely.LineString([(5, 0), (7, 0), (10, 0)])],
    )
    assert row[2]["_profile"] == {"surveyed": True}


def test_full_pass_palette_keeps_structural_textures_outside_terrain_channels():
    meshes = [
        SimpleNamespace(extras=dict(layer=layer, material_key=key))
        for layer, key in (
            ("ground", "draft_grass"),
            ("structures", "draft_granite_curb"),
            ("structures", "draft_bridge_concrete"),
            ("structures", "draft_bridge_rock"),
            ("structures", "draft_bridge_paint"),
        )
    ]
    assert surfaces.channels(meshes) == {
        surfaces.KEYS.index("grass"),
        surfaces.KEYS.index("granite_curb"),
    }
    with pytest.raises(ValueError, match="Unknown draft ground surface"):
        surfaces.channels(
            [SimpleNamespace(extras=dict(layer="ground", material_key="draft_unknown"))]
        )


def test_retaining_edge_is_below_grade_and_its_cut_has_complete_walkable_bands():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(20, 20), (40, 20)])

    def sample(x, z):
        return 3 + np.clip((z - 20) / 2.5, -1, 1) * 2

    records = [(line, {"barrier": "retaining_wall", "natural": "cliff"}, False)]
    meshes, cuts = retaining.produce(tile, records, sample)
    assert len(meshes) == 1
    mesh = meshes[0]
    assert mesh.metadata["surface"] == "rock"
    assert mesh.positions[:, 1].max() <= 5
    tris = mesh.positions[mesh.indices]
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ground = shapely.union_all(shapely.polygons(tris[normal[:, 1] > 1e-8][:, :, [0, 2]]))
    assert shapely.union_all(cuts).difference(ground).area < 1e-8
    assert retaining.produce(tile, records, lambda x, z: 3) == ([], [])

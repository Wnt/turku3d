"""Bridge component geometry must stay attached, outward-facing and collidable."""

import numpy as np
import shapely

from turku3d.coarse.bridge_kit import (
    cross_section, entrance_points, entrances, rounded_pier, stay_clear, stayed_span, tapered_post,
)


def test_tapered_shaft_has_outward_normals_and_nonzero_triangles():
    mesh = tapered_post("post", [10, 4, 20], [3, 4], [(0, 2, 2), (4, .8, .8)])
    tri = mesh.positions[mesh.indices]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (np.linalg.norm(normals, axis=1) > 1e-6).all()
    assert (np.sum(normals * (tri.mean(axis=1) - [10, 6, 20]), axis=1) > 0).all()
    assert mesh.metadata["collision"]


def crossing():
    return dict(id="skew", line=shapely.LineString([(0, 0), (50, 0)]), width=12,
                outline=shapely.Polygon([(-3, -6), (47, -6), (53, 6), (3, 6)]))


def test_entrances_follow_skew_outline_instead_of_nominal_width():
    way = crossing()
    points = entrance_points(way, 1.2)
    assert len(points) == 4
    assert all(way["outline"].boundary.distance(shapely.Point(p)) < 1e-8 for p, _ in points)
    assert all(np.isclose(np.linalg.norm(d), 1) for _, d in points)
    assert all(np.isclose(abs(p[1]), 6) for p, _ in points)


def test_estimated_entrance_respects_deck_obstacles_and_uses_deck_elevation():
    way = crossing()
    height = lambda p: 5 + p[:, 0] * .01
    spec = {"entrance_height": 4.8}
    all_meshes = entrances(way, height, [], spec)
    shafts = [m for m in all_meshes if m.metadata["surface"] == "granite"]
    assert len(shafts) == 4
    for shaft, (p, _) in zip(shafts, entrance_points(way, 1.2, 1.05 * 1.22 / 2 + .02), strict=True):
        assert np.isclose(shaft.positions[:, 1].min(), height(np.array([p]))[0])
        assert np.isclose(np.ptp(shaft.positions[:, 1]), 4.8)
        assert shaft.metadata["collision"]
    blocked = [shapely.box(-5, -10, 5, 10)]
    kept = entrances(way, height, blocked, spec)
    assert len([m for m in kept if m.metadata["surface"] == "granite"]) == 2
    assert entrances(way, height, [], {}) == []


def test_lower_quay_passages_do_not_remove_pylons_above_deck():
    from turku3d.coarse.bridge_details import furniture

    way = crossing()
    way["tags"] = {"highway": "secondary", "wikidata": "Q11853512"}
    meshes = furniture(way, shapely.MultiLineString([]), lambda p: np.full(len(p), 6.),
                       lambda p: np.zeros(len(p)),
                       [shapely.box(-5, -10, 5, 10), shapely.box(45, -10, 55, 10)],
                       way["outline"])
    shafts = [m for m in meshes if m.metadata["surface"] == "granite"]
    assert shafts
    assert min(m.positions[:, 1].min() for m in shafts) == 6


def test_reversing_axis_keeps_the_same_entrance_positions():
    way = crossing()
    points = entrance_points(way, 1.2, .66)
    way["line"] = shapely.reverse(way["line"])
    reverse = entrance_points(way, 1.2, .66)
    assert sorted(tuple(p.round(6)) for p, _ in points) == sorted(tuple(p.round(6)) for p, _ in reverse)


def test_kit_changes_invalidate_both_visual_and_collision_freshness():
    from turku3d.coarse import bridge_kit, collision_key, packaging
    from pathlib import Path

    source = Path(bridge_kit.__file__)
    assert source in packaging.producer_paths()
    assert source in collision_key.paths()


def test_rounded_cutwater_is_closed_outward_and_has_planar_cap_uvs():
    mesh = rounded_pier("pier", [-2, 0], [2, 0], -.5, 1.5)
    tri = mesh.positions[mesh.indices]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (np.sum(normals * (tri.mean(axis=1) - [0, .5, 0]), axis=1) > 0).all()
    uv = mesh.uvs[mesh.indices]
    uv_cross = ((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1])
                - (uv[:, 1, 1] - uv[:, 0, 1]) * (uv[:, 2, 0] - uv[:, 0, 0]))
    assert (abs(uv_cross) > 1e-8).all()
    vertices = np.unique(mesh.positions, axis=0)
    assert np.isclose(vertices[:, 0].min(), -2.7)
    assert np.isclose(vertices[:, 2].max(), .7)


def stayed_crossing():
    axis = shapely.LineString([(0, 0), (60, 0)])
    return dict(id="stayed", line=axis, width=6,
                tags={"highway": "footway", "bridge:structure": "cable-stayed"},
                outline=axis.buffer(3, cap_style="flat"),
                survey={"pier_stations": [13, 47], "pier_bases": [.6, .7]})


def test_stayed_kit_uses_measured_stations_and_keeps_pedestrian_head_clearance():
    from turku3d.coarse.bridge_details import style

    way = stayed_crossing()
    height = lambda p: 4 + .5 * np.sin(p[:, 0] / 60 * np.pi)
    spec = style(way)
    meshes = stayed_span(way, height, lambda p: np.zeros(len(p)), [], way["outline"], spec)
    assert len([m for m in meshes if m.metadata["surface"] == spec["support"]]) == 4
    # Four cylindrical towers, exactly on measured support stations, clear of the path.
    towers = [m for m in meshes if np.ptp(m.positions[:, 1]) > 7]
    assert len(towers) == 4
    assert sorted(np.round(m.positions[:, 0].mean(), 5) for m in towers) == [13, 13, 47, 47]
    for mesh in meshes:
        assert mesh.metadata["collision"]
        # Check complete triangle edges, not only vertices outside the height band.
        tri = mesh.positions[mesh.indices]
        for edge in [(0, 1), (1, 2), (2, 0)]:
            a, b = tri[:, edge[0]], tri[:, edge[1]]
            points = (a[:, None] + np.linspace(0, 1, 80)[None, :, None]
                      * (b - a)[:, None]).reshape(-1, 3)
            above = points[:, 1] - height(points[:, [0, 2]])
            head_zone = points[(above > .4) & (above < 2.2)]
            assert (abs(head_zone[:, 2]) > 2.4).all()
    blocked = [shapely.box(11, -5, 15, 5)]
    safe = stayed_span(way, height, lambda p: np.zeros(len(p)), blocked, way["outline"], spec)
    assert safe == []  # Incomplete tower layouts abstain rather than dangling stays.


def test_cross_section_follows_local_bend_and_actual_wider_deck():
    way = stayed_crossing()
    way["line"] = shapely.LineString([(0, 0), (30, 0), (50, 20)])
    way["outline"] = way["line"].buffer(4, cap_style="flat")
    centre, tangent, normal, edges = cross_section(way, 40, .25)
    assert np.allclose(tangent, np.array([1, 1]) / np.sqrt(2))
    assert np.isclose(tangent @ normal, 0)
    assert all(np.isclose(np.linalg.norm(edge - centre), 3.75) for edge in edges)


def test_theatre_reference_selects_stayed_type_without_changing_the_deck_survey():
    from turku3d.coarse.bridge_details import style

    way = stayed_crossing()
    way["tags"].update(wikidata="Q5408563", **{"bridge:structure": "beam"})
    assert style(way)["structure"] == "cable-stayed"
    assert way["survey"]["pier_stations"] == [13, 47]


def test_stay_sweep_rejects_midspan_pedestrian_and_building_intrusions():
    height = lambda p: np.full(len(p), 4.)
    polygon = shapely.box(0, -3, 60, 3)
    # Both endpoints are outside the pedestrian head band, but the middle cuts it.
    assert not stay_clear([12, 9, 2.75], [32, 4.2, -2.75], .035, height, polygon, [])
    assert stay_clear([12, 9, 2.75], [32, 4.2, 2.75], .035, height, polygon, [])
    assert not stay_clear([12, 9, 2.75], [32, 4.2, 2.75], .035, height, polygon,
                          [shapely.box(20, 2.5, 22, 3)])


def test_partial_pier_evidence_retains_two_tower_stations_and_water_relative_caps():
    from turku3d.coarse.bridge_details import style
    from turku3d.coarse.water import LEVEL

    way = stayed_crossing()
    way['survey'] = {'pier_stations': [13], 'pier_bases': [.6]}
    spec = style(way)
    caps = []
    for bed in (0., -2.):
        meshes = stayed_span(way, lambda p: np.full(len(p), 4.5),
                             lambda p: np.full(len(p), bed), [], way['outline'], spec)
        footings = [m for m in meshes if m.metadata['surface'] == spec['support']]
        assert len(footings) == 4
        caps.append([m.positions[:, 1].max() for m in footings])
    assert np.allclose(caps, LEVEL + 1.)


def test_cross_section_reaches_actual_edges_even_for_wide_nominal_mismatch():
    way = stayed_crossing()
    way['outline'] = shapely.box(0, -15, 60, 15)
    _, _, _, edges = cross_section(way, 30, .25)
    assert all(np.isclose(abs(p[1]), 14.75) for p in edges)
    _, _, _, cut = cross_section(way, 30, .25, shapely.box(0, -3, 60, 2))
    assert np.allclose([p[1] for p in cut], [-2.75, 1.75])


def test_tube_uvs_have_area_at_every_orientation():
    from turku3d.coarse.bridge_details import tube

    for end in ([0, 5, 0], [5, 0, 0], [3, 4, 5]):
        mesh = tube('tube', [0, 0, 0], end, .2)
        uv = mesh.uvs[mesh.indices]
        a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
        assert (abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) > 1e-8).all()


def test_stays_fit_before_a_widened_midspan_viewing_platform():
    from turku3d.coarse.bridge_details import style

    way = stayed_crossing()
    way['outline'] = shapely.union_all([way['outline'], shapely.box(22, -5, 40, 5)])
    meshes = stayed_span(way, lambda p: np.full(len(p), 4.5),
                         lambda p: np.zeros(len(p)), [], way['outline'], style(way))
    rods = [m for m in meshes if m.material == 'railing']
    assert len(rods) == 16
    # Attachment search retracts the inner stays before the platform, leaving
    # its whole widened walking area free of head-height diagonal barriers.
    for mesh in rods:
        assert mesh.positions[:, 0].max() < 22 or mesh.positions[:, 0].min() > 40


def test_floor_beams_follow_bends_and_avoid_obstacle_cuts_and_lower_passages():
    from turku3d.coarse.bridge_kit import deck_framing
    from turku3d.coarse.bridge_details import style

    way = stayed_crossing()
    way['line'] = shapely.LineString([(0, 0), (30, 0), (30, 30)])
    way['outline'] = way['line'].buffer(3, cap_style='flat', join_style='mitre')
    obstacle = shapely.box(20, -1, 24, 1)
    effective = way['outline'].difference(obstacle)
    lower = shapely.box(28, 10, 32, 14)
    spec = style(way) | {'body': 'steel'}
    meshes = deck_framing(way, lambda p: np.full(len(p), 6.), [lower], effective, spec)
    assert meshes
    for mesh in meshes:
        footprint = shapely.MultiPoint(mesh.positions[:, [0, 2]]).convex_hull
        assert effective.covers(footprint)
        assert not footprint.intersects(lower)
        # Vertical leg framing runs across X, horizontal leg across Z.
        if np.ptp(mesh.positions[:, 2]) > 1:
            assert np.ptp(mesh.positions[:, 0]) < .2
        if np.ptp(mesh.positions[:, 0]) > 1:
            assert np.ptp(mesh.positions[:, 2]) < .2


def test_girder_profiles_replace_mylly_arch_and_fit_sparse_support_evidence():
    from turku3d.coarse.bridge_details import style
    from turku3d.coarse.bridge_kit import support_stations

    way = dict(id='girder', line=shapely.LineString([(0, 0), (94, 0)]), width=21,
               tags={'highway': 'secondary', 'wikidata': 'Q6947737', 'bridge:structure': 'humpback'},
               survey={'pier_stations': [81.6], 'pier_bases': [.6]})
    spec = style(way)
    assert spec['structure'] == 'beam'
    assert np.allclose(support_stations(way, spec), [94 / 3, 188 / 3])
    way['survey']['pier_stations'] = [32, 62, 93]
    assert support_stations(way, spec) == [32, 62]


def test_box_girder_is_closed_outward_below_deck_and_respects_underpass():
    from turku3d.coarse.bridge_details import style
    from turku3d.coarse.bridge_kit import box_girder

    way = crossing()
    way['tags'] = {'highway': 'secondary', 'wikidata': 'Q6947737'}
    spec = style(way)
    height = lambda p: np.full(len(p), 5.)
    meshes = box_girder(way, height, [], way['outline'], spec)
    assert len(meshes) == 1
    mesh = meshes[0]
    assert mesh.metadata['collision']
    assert mesh.positions[:, 1].max() <= 5 - spec['thickness'] + 1e-6
    assert np.isclose(mesh.positions[:, 1].min(), 5 - spec['thickness'] - spec['box_depth'])
    tri = mesh.positions[mesh.indices]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (np.sum(normals * (tri.mean(axis=1) - [25, 4.275, 0]), axis=1) > 0).all()
    uv = mesh.uvs[mesh.indices]
    determinant = np.linalg.det(np.stack([uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]], axis=1))
    assert (abs(determinant) > 1e-8).all()
    assert len(mesh.indices) < 450  # no hidden caps at every station
    blocked = shapely.box(20, -20, 30, 20)
    safe = box_girder(way, height, [blocked], way['outline'], spec)
    for triangle in safe[0].positions[safe[0].indices]:
        footprint = shapely.MultiPoint(triangle[:, [0, 2]]).convex_hull
        assert not footprint.intersects(blocked)


def test_beam_cutwaters_follow_curved_deck_and_avoid_lower_paths():
    from turku3d.coarse.bridge_details import style
    from turku3d.coarse.bridge_kit import beam_supports

    axis = shapely.LineString([(0, 0), (30, 0), (55, 20), (90, 20)])
    way = dict(id='bent', line=axis, width=16, outline=axis.buffer(8, cap_style='flat'),
               tags={'highway': 'secondary', 'wikidata': 'Q5404019'})
    spec = style(way)
    height = lambda p: 5 + p[:, 0] * .01
    ground = lambda p: np.zeros(len(p))
    all_meshes = beam_supports(way, height, ground, [], way['outline'], spec)
    assert len(all_meshes) == 4
    for mesh in all_meshes:
        assert mesh.metadata['collision']
        assert way['outline'].covers(shapely.MultiPoint(mesh.positions[:, [0, 2]]).convex_hull)
        assert np.all(mesh.positions[:, 1] <= height(mesh.positions[:, [0, 2]]) - spec['thickness'] + 1e-6)
    blocked = [shapely.box(0, -30, 45, 30)]
    safe = beam_supports(way, height, ground, blocked, way['outline'], spec)
    assert len(safe) == 2


def test_generic_supports_preserve_measured_topology_and_fill_only_long_gaps():
    from turku3d.coarse.bridge_kit import support_stations
    spec = dict(foot=False)
    way = dict(line=shapely.LineString([(0, 0), (30, 0)]), survey={'pier_stations': [15]})
    assert support_stations(way, spec) == [15]
    way['line'] = shapely.LineString([(0, 0), (120, 0)])
    way['survey']['pier_stations'] = [30, 60, 90]
    assert support_stations(way, spec) == [30, 60, 90]
    way['survey']['pier_stations'] = [30]
    assert support_stations(way, spec) == [30, 60, 90]


def test_girder_uvs_survive_rotation_grade_bends_and_include_deck_knots():
    from turku3d.coarse.bridge_kit import box_girder
    for axis in [shapely.LineString([(0, 0), (94, 0)]),
                 shapely.LineString([(0, 0), (60, 72)]),
                 shapely.LineString([(0, 0), (40, 0), (70, 30)])]:
        way = dict(id='uv', line=axis, width=12, outline=axis.buffer(6, cap_style='flat'))
        def height(p):
            station = shapely.line_locate_point(axis, shapely.points(p))
            return 5 + .01 * station + .2 * np.maximum(0, 1 - abs(station - 17.3))
        meshes = box_girder(way, height, [], way['outline'], dict(box_depth=.65, thickness=.4),
                            profile_stations=[16.3, 17.3, 18.3])
        assert meshes
        for mesh in meshes:
            uv = mesh.uvs[mesh.indices]
            assert (abs(np.linalg.det(np.stack([uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]], axis=1))) > 1e-8).all()
            assert np.isclose(mesh.uvs[:, 0], 17.3).any()


def test_pier_cap_fits_sloped_soffit_without_a_daylight_wedge():
    from turku3d.coarse.bridge_kit import beam_supports
    axis = shapely.LineString([(0, 0), (100, 0)])
    way = dict(id='slope', line=axis, width=16, outline=axis.buffer(8, cap_style='flat'))
    spec = dict(structure='beam', foot=False, thickness=.4, support='stone', box_depth=.65)
    height = lambda p: 5 + p[:, 0] * .03
    meshes = beam_supports(way, height, lambda p: np.zeros(len(p)), [], way['outline'], spec)
    assert len(meshes) == 4
    for cap in meshes[1::2]:
        expected = height(cap.positions[:, [0, 2]]) - 1.05
        top = cap.positions[:, 1] > cap.positions[:, 1].min() + .01
        assert np.allclose(cap.positions[top, 1], expected[top])

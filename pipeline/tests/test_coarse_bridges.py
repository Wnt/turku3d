"""Physical crossing identity, true underpasses and tile ownership on tiny synthetic inputs."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.bridges import merge_crossings, passes_under, slab
from turku3d.coarse.fences import place
from turku3d.coarse.geometry import clip_mesh
from turku3d.structures.geometry import profile_wall


def way(key, coords, *, layer=1, bridge=True, width=3):
    return dict(
        id=key,
        line=shapely.LineString(coords),
        layer=layer,
        bridge=bridge,
        width=width,
        tags={"highway": "footway"},
    )


def test_outline_unifies_carriageway_and_sidewalk_without_merging_nearby_crossing():
    road = way("road", [(0, 0), (80, 0)], width=8)
    walk = way("walk", [(0, 5), (80, 5)])
    next_bridge = way("next", [(0, 40), (80, 40)])
    outline = shapely.box(-1, -5, 81, 7)
    decks = merge_crossings(
        [road, walk, next_bridge], [("outline", outline, {"man_made": "bridge"})]
    )
    assert len(decks) == 2
    merged = next(d for d in decks if d["id"] == "outline")
    assert set(merged["members"]) == {"road", "walk"}
    assert merged["outline"].equals(outline)
    assert merged["width"] == 12


def test_parallel_fallback_keeps_transverse_and_separate_level_bridges():
    a = way("a", [(0, 0), (80, 0)])
    b = way("b", [(0, 3), (80, 3)])
    cross = way("cross", [(40, -40), (40, 40)])
    upper = way("upper", [(0, 0), (80, 0)], layer=2)
    decks = merge_crossings([a, b, cross, upper], [])
    assert len(decks) == 3
    assert any(set(d["members"]) == {"a", "b"} for d in decks)


def test_clearance_requires_interior_transverse_crossing_below():
    deck = way("bridge", [(0, 0), (80, 0)])
    assert passes_under(deck, way("road", [(40, -20), (40, 20)], layer=0, bridge=False))
    assert not passes_under(deck, way("approach", [(-20, 0), (0, 0)], layer=0, bridge=False))
    assert not passes_under(deck, way("bank", [(0, -20), (0, 20)], layer=0, bridge=False))
    assert not passes_under(deck, way("parallel", [(0, 1), (80, 1)], layer=0, bridge=False))
    assert not passes_under(deck, way("sidewalk", [(0, 0), (80, 0)], layer=1))
    assert not passes_under(deck, way("upper", [(40, -20), (40, 20)], layer=2))


def test_clipped_neighbour_decks_share_boundary_and_never_emit_full_duplicate():
    mesh = profile_wall("bridge", [[240, 10], [260, 10]], [4, 5], [4.4, 5.4], thickness=3)
    left = clip_mesh(mesh, C.VisualTile(0, 0, 0).bbox)
    right = clip_mesh(mesh, C.VisualTile(0, 1, 0).bbox)
    assert left.positions[:, 0].max() == 250
    assert right.positions[:, 0].min() == 250

    def boundary(m):
        return set(map(tuple, m.positions[m.positions[:, 0] == 250]))

    assert boundary(left) == boundary(right)


def test_deck_outline_leaves_ship_footprint_empty():
    ship = shapely.box(8, -2, 12, 2)
    polygon = shapely.box(0, -4, 20, 4).difference(ship.buffer(0.15))
    axis = shapely.LineString([(0, 0), (20, 0)])
    mesh = slab("landing", polygon, axis, np.array([0, 20]), np.array([2, 2]), "sidewalk")[0]
    for tri in mesh.indices:
        footprint = shapely.Polygon(mesh.positions[tri][:, [0, 2]])
        assert footprint.intersection(ship).area == 0


def test_bank_retaining_edge_is_not_invented_above_ground_and_water_clips_fence():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(10, 20), (90, 20)])
    assert place(tile, [(line, {"barrier": "retaining_wall"}, False)], [], lambda x, z: 3) == []
    parapet = place(
        tile, [(line, {"barrier": "retaining_wall", "wall": "parapet"}, False)], [], lambda x, z: 3
    )
    assert all(s.height == 0.5 for s in parapet)
    water = shapely.box(40, 0, 60, 40)
    fence = place(tile, [(line, {"barrier": "fence"}, False)], [], lambda x, z: 3, [water])
    assert fence and all(not 39.65 < s.x < 60.35 for s in fence)


def test_branch_outline_axis_follows_carriageway_not_long_ramp():
    road = way("road", [(0, 0), (80, 0)], width=10)
    ramp = way("ramp", [(40, 0), (45, 90)], width=3)
    outline = shapely.box(-1, -6, 81, 6).union(ramp["line"].buffer(2, cap_style="flat"))
    deck = merge_crossings([road, ramp], [("outline", outline, {"layer": "1"})])[0]
    coords = np.asarray(deck["line"].coords)
    assert abs(coords[-1, 1] - coords[0, 1]) < 0.01
    assert deck["width"] == 12
    assert deck["line"].length == 82
    assert set(deck["members"]) == {"road", "ramp"}


def test_passage_cut_removes_collinear_submillimetre_sliver():
    from turku3d.coarse.build import extrude
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.tunnels import passage_footprint

    polygon = shapely.Polygon([(0, 0), (5, 0), (5, 5), (3.0002, 5.000001), (3, 5), (0, 5)])
    building = Building("passage", passage_footprint(polygon), 0, 5, "facade_plaster")
    roof, indices, *_ = extrude(building)
    triangles = roof.astype(np.float32)[indices]
    area = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        )
        / 2
    )
    assert (area >= 1e-6).all()
    assert abs(area.sum() - polygon.area) < 1e-4


def test_underpass_height_does_not_sample_upper_deck_as_railway_ground():
    from turku3d.coarse.bridges import under_height

    deck = way("bridge", [(0, 0), (80, 0)], width=12)
    rail = way("rail", [(40, -40), (40, 40)], layer=0, bridge=False)

    def sample(x, z):
        return 25 if abs(z) < 10 else 10 + z / 100

    assert under_height(deck, rail, shapely.Point(40, 0), sample) == 10

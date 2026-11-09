import shapely

from turku3d import config as C
from turku3d.coarse import span_lamp_seams as S


def wire(a, b):
    return {"version": 1, "kinds": ["span"], "wires": [[*a, *b, 0.1, 0]], "lamps": []}


def test_both_tiles_use_the_same_edge_and_only_the_crossing_tile_keeps_the_lamp(monkeypatch):
    point = [251.8, 10, 680]
    key = S.seam(point[0], point[2])
    assert key in S.edges(C.VisualTile(0, 0, 2))
    assert key in S.edges(C.VisualTile(0, 1, 2))
    monkeypatch.setattr(S, "candidates", lambda k: [point] if k == key else [])
    street = [shapely.LineString([(200, 680), (300, 680)])]
    west = S.infer(C.VisualTile(0, 0, 2), wire((245, 10, 694), (251, 10, 681)), street)
    east = S.infer(C.VisualTile(0, 1, 2), wire((252, 10, 678), (254, 10, 668)), street)
    assert west is None
    assert east["lamps"] == [[*point, 0]]
    assert east["wires"][0][:3] == point


def test_a_corner_crossing_can_belong_to_neither_fragment_tile(monkeypatch):
    point = [-251.1, 18, 251.2]
    key = S.seam(point[0], point[2])
    monkeypatch.setattr(S, "candidates", lambda k: [point] if k == key else [])
    # The source window includes both fragments even though the owner has no wire.
    for tile in [C.VisualTile(0, -1, 1), C.VisualTile(0, -2, 0)]:
        assert S.infer(tile, None, []) is None
    result = S.infer(C.VisualTile(0, -2, 1), None, [])
    assert result["lamps"] == [[*point, -1]]


def test_exact_edge_uses_half_open_bounds_and_surveyed_lamps_are_preserved(monkeypatch):
    point = [250, 10, -100]
    key = S.seam(point[0], point[2])
    monkeypatch.setattr(S, "candidates", lambda k: [point] if k == key else [])
    west = C.VisualTile(0, 0, -1)
    east = C.VisualTile(0, 1, -1)
    assert S.infer(west, None, []) is None
    assert S.infer(east, None, [])["lamps"] == [[*point, -1]]
    record = wire((260, 10, -115), (260, 10, -85))
    record["lamps"] = [[250.1, 10, -100, -1]]
    assert S.infer(east, record, []) is None


def test_negative_grid_and_nearest_edge_have_a_stable_tie_break():
    assert S.seam(-250, -250) == ("x", -1, -1)
    assert S.seam(-251.1, 251.2) == ("x", -1, 1)
    assert S.seam(125, 125) is None

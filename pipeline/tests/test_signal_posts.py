"""Signal posts at the kerb (roads.signal_posts, signals@1)."""

import json
import math

import shapely
from turku3d.roads import signal_posts as sp

# A two-way street along x (z = 0) with a signal at s = 40 for both directions. OSM says 7 m wide;
# the surveyed carriageway is 13 m wide (z -6.5..6.5), so the nominal heads (3.85 m off) stand on it.
GRAPH = {
    "bits": {"car": 2}, "nodes": [0, 0, 100, 0], "edges": [[0, 1, 2 | (2 << 3), 100]],
    "edgeAttrColumns": ["way", "lanes_fwd", "lanes_bwd", "width"], "edgeAttrs": [[777, 1, 1, 7]],
    "edgeHeights": [[4, 4]], "edgeEvents": [[40, 1 | 16 | 32, 0]],
}
ROAD = shapely.box(-50, -6.5, 150, 6.5)


def test_heads_mirror_the_client_side_offset():
    heads = list(sp.heads(GRAPH))
    assert [(h[0], h[1], h[2]) for h in heads] == [(0, 0, 1), (0, 0, -1)]
    # Travel +x: right of the approach is +z (engine z points south); backward is mirrored.
    assert heads[0][3:5] == (40, 3.85) and heads[1][3:5] == (40, -3.85)


def test_a_head_on_the_carriageway_moves_just_past_its_nearest_kerb():
    rows, stats = sp.place(GRAPH, ROAD, ground=lambda x, z: 4.15)
    assert stats == {"signal_kerb": 2}
    assert rows[0] == [0, 0, 1, 40, round(6.5 + sp.KERB_CLEARANCE, 2), 4.15]
    assert rows[1][4] == round(-6.5 - sp.KERB_CLEARANCE, 2)
    assert all(not ROAD.covers(shapely.Point(r[3], r[4])) for r in rows)


def test_a_median_nearer_than_the_outer_kerb_takes_the_post():
    # The far-side lanes are a separate polygon: a 1 m median at z 4.5..5.5 lies 0.65 m from the head.
    road = shapely.union_all([shapely.box(-50, -6.5, 150, 4.5), shapely.box(-50, 5.5, 150, 12)])
    x, z, rule = sp.kerb_post(40, 3.85, road)
    assert rule == "kerb" and math.isclose(z, 4.5 + sp.KERB_CLEARANCE)


def test_a_narrow_median_takes_the_post_in_its_middle():
    road = shapely.union_all([shapely.box(-50, -6.5, 150, 4.5), shapely.box(-50, 4.8, 150, 12)])
    x, z, rule = sp.kerb_post(40, 3.85, road)
    assert rule == "kerb" and 4.5 < z < 4.8 and not road.covers(shapely.Point(x, z))


def test_heads_off_the_carriageway_deep_inside_or_without_survey_stay():
    assert sp.kerb_post(40, 3.85, shapely.box(-50, -3, 150, 3)) == (40, 3.85, "off_carriageway")
    assert sp.kerb_post(40, 3.85, shapely.box(-50, -30, 150, 30)) == (40, 3.85, "deep")
    assert sp.kerb_post(40, 3.85, shapely.Polygon()) == (40, 3.85, "no_survey")


def test_carriageway_takes_only_the_carriageway_parts():
    rows = [(shapely.box(0, 0, 10, 10), "Ajorata"), (shapely.box(10, 0, 12, 10), "Pyöräkaista"),
            (shapely.box(12, 0, 15, 10), "Jalkakäytävä"), (shapely.box(-3, 0, 0, 10), "Välikaista")]
    assert math.isclose(sp.carriageway(rows).area, 120)


def test_the_migration_writes_posts_into_the_graph(tmp_path, monkeypatch):
    from turku3d.migrations import signals
    from turku3d.migrations.core import Product
    from turku3d.roads import encode

    graph = {**GRAPH, "revision": 2}
    data = encode(graph)
    import hashlib

    name = f"roads.{hashlib.sha256(data).hexdigest()}.json"
    (tmp_path / name).write_bytes(data)
    product = Product(tmp_path, {"tile": "t0_0_0", "roads": {"url": name, "sha256": "", "bytes": len(data)}})

    class Found:
        def wfs_source(self, name, tile):
            return True, "katualueet.parquet"

    monkeypatch.setattr("turku3d.coarse.sources.Sources", Found)
    monkeypatch.setattr(sp, "load_carriageway", lambda path, bbox: ROAD)
    signals.transform(product)
    out = json.loads(product.read(product.manifest["roads"]["url"]))
    assert [r[:3] for r in out["signalPosts"]] == [[0, 0, 1], [0, 0, -1]]
    assert product.manifest["furniture"]["signals"] == {"signal_kerb": 2}
    assert {k: v for k, v in out.items() if k != "signalPosts"} == graph


def test_a_median_the_register_overlaps_with_the_carriageway_is_not_carriageway():
    """Kaskenkatu: register refuge islands (Välikaista) lie inside the Ajorata polygon; posts on them stay."""
    island = shapely.box(4, 4, 6, 6)
    rows = [(shapely.box(0, 0, 10, 10), "Ajorata"), (island, "Välikaista")]
    road = sp.carriageway(rows)
    assert math.isclose(road.area, 96)
    assert sp.kerb_post(5, 5, road) == (5, 5, "off_carriageway")

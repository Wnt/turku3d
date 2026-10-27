"""Coarse tiles: published tile format from synthetic inputs, and the localhost build service."""

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import geopandas as gpd
import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.coarse.build import CELLS, WIRE_CAP, bridge_decks, build, grid_mesh
from turku3d.coarse.inputs import Inputs, prepare_ground, prepare_roads, prepare_wfs
from turku3d.coarse.service import Builder, serve
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id
from turku3d.package.gltf import read_glb
from turku3d.package.seams import verify_tile_seams
from turku3d.roads import validate

TILE = C.VisualTile(0, 3, -2)


def one_shot(command):
    """A Builder runner that starts command(tile, out) once per tile."""
    return lambda tile, out: subprocess.run(command(tile, out), capture_output=True, text=True)


def _frame(rows, path):
    """WFS-cache-like GeoParquet from engine (x, z[, H]) polygons: EN `geometry`, local `geometry_local`."""
    local = [r.pop("geometry") for r in rows]

    def to_en(c):
        c = c.copy()
        c[:, 0] += C.ORIGIN_E
        c[:, 1] = C.ORIGIN_N - c[:, 1]
        return c

    en = [shapely.transform(g, to_en, include_z=g.has_z) for g in local]
    frame = gpd.GeoDataFrame(rows, geometry=en, crs="EPSG:3877")
    frame["geometry_local"] = gpd.GeoSeries(local)
    frame["geom_type"] = "Polygon"
    frame.to_parquet(path)
    return path


def _box_en(x0, z0, x1, z1, h=None):
    ring = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    if h is not None:
        ring = [(x, z, h) for x, z in ring]
    return shapely.Polygon(ring)


@pytest.fixture
def inputs(tmp_path):
    x0, _, z0 = TILE.origin
    gx, gz = np.meshgrid(np.arange(x0 - 20, x0 + 271, 3.0), np.arange(z0 - 20, z0 + 271, 3.0))
    points = np.c_[gx.ravel(), 10 + 0.01 * gx.ravel(), gz.ravel()]
    ortho = np.full((64, 64, 3), 120, np.uint8)
    ortho[:16, :16] = (40, 140, 40)  # grass in the NW corner
    bx, bz = x0 + 100, z0 + 100
    buildings = _frame(
        [
            {
                "feature_key": "B1",
                "surface_type": "GroundSurface",
                "measuredHeight": "12",
                "geometry": shapely.Polygon(
                    [
                        (bx, bz, 11.0),
                        (bx + 20, bz, 11.0),
                        (bx + 20, bz + 10, 11.0),
                        (bx, bz + 10, 11.0),
                    ]
                ),
            },
            {
                "feature_key": "B1",
                "surface_type": "RoofSurface",
                "measuredHeight": "12",
                "geometry": shapely.Polygon(
                    [
                        (bx, bz, 23.0),
                        (bx + 20, bz, 23.0),
                        (bx + 20, bz + 10, 23.0),
                        (bx, bz + 10, 23.0),
                    ]
                ),
            },
        ],
        tmp_path / "bldg.parquet",
    )
    streets = _frame(
        [
            {
                "Katuosalaji": "Ajorata",
                "Pintamateriaali": "Asfalttibetoni",
                "geometry": _box_en(x0 - 10, z0 + 40, x0 + 260, z0 + 52),
            }
        ],
        tmp_path / "streets.parquet",
    )
    water = _frame(
        [{"kplkoodi": "water", "geometry": _box_en(x0 + 180, z0 + 180, x0 + 260, z0 + 260)}],
        tmp_path / "water.parquet",
    )
    # A street crossing the whole tile west to east (clipped at both edges) and a footway inside it.
    osm = tmp_path / "osm.parquet"
    gpd.GeoDataFrame(
        {
            "highway": ["residential", "footway"],
            "tags": [json.dumps({"highway": "residential"}), json.dumps({"highway": "footway"})],
            "geometry_local": gpd.GeoSeries(
                [
                    shapely.LineString(
                        [(x0 - 30, z0 + 46), (x0 + 125, z0 + 46), (x0 + 280, z0 + 46)]
                    ),
                    shapely.LineString([(x0 + 125, z0 + 46), (x0 + 125, z0 + 90)]),
                ]
            ),
        },
        geometry="geometry_local",
    ).to_parquet(osm)
    ground = prepare_ground(TILE, points)
    wfs = prepare_wfs(TILE, buildings, streets, water)
    return Inputs(
        points,
        ground["heights"],
        ortho,
        wfs["buildings"],
        wfs["streets"],
        wfs["water"],
        prepare_roads(TILE, osm),
        {"synthetic": True},
        {},
    )


def test_coarse_tile_is_a_published_tile(tmp_path, inputs):
    out = tmp_path / "coarse" / TILE.id
    m = build(TILE.id, out, inputs=inputs, library=tmp_path / "no-library")
    assert m["coarse"] is True and m["tile"] == TILE.id and m["buildings"] == 1
    vis = (out / "vis.glb").read_bytes()
    entry = m["visual"][TILE.id]
    assert entry["coarse"] and entry["bytes"] == len(vis) and entry["brotli_bytes"] < WIRE_CAP
    js, _ = read_glb(vis)
    assert {"EXT_meshopt_compression", "KHR_texture_basisu"} <= set(js["extensionsRequired"])
    assert js["nodes"][0]["translation"][0] == TILE.origin[0]
    cols = {cid: read_col((out / e["url"]).read_bytes()) for cid, e in m["collision"].items()}
    assert sorted(cols) == sorted(c.id for c in TILE.collision_tiles())
    assert verify_tile_seams(cols) == []
    materials = np.concatenate([c.materials for c in cols.values()])
    for name in ("road", "grass", "water", "roof", "building", "paving_slab"):
        assert (materials == mat_id(name)).any(), name
    # Every grid cell is two ground triangles; the building lives only in its centroid's tile.
    ground = sum(
        int(
            np.isin(
                c.materials, [mat_id(n) for n in ("road", "grass", "water", "paving_slab")]
            ).sum()
        )
        for c in cols.values()
    )
    assert ground == 2 * CELLS * CELLS
    owner = C.CollisionTile.at(TILE.origin[0] + 110, TILE.origin[2] + 105).id
    assert [cid for cid, c in cols.items() if (c.materials == mat_id("roof")).any()] == [owner]
    roof = cols[owner]
    top = roof.world_positions()[np.unique(roof.indices[roof.materials == mat_id("roof")])][:, 1]
    assert np.allclose(top, 23.0, atol=1e-3)
    assert json.loads((out / "manifest.json").read_text())["code"] == m["code"]
    # The street graph, clipped to the tile: the street splits at the footway junction; both clip points
    # lie on the tile edges, where the neighbouring tiles' graphs meet it.
    graph = json.loads((out / "roads.json").read_bytes())
    assert m["roads"]["url"] == "roads.json" and m["roads"]["edges"] == len(graph["edges"]) == 3
    validate(graph)
    xs = graph["nodes"][0::2]
    assert min(xs) == TILE.origin[0] and max(xs) == TILE.origin[0] + 250


def test_bridge_deck_over_water_is_lifted_to_its_abutments():
    """A 50 m river at H 0 between 6 m quays, a deck across it and a viaduct-like deck on dry land."""
    tile = C.VisualTile(0, 0, 0)
    step = tile.size / CELLS
    xs = np.arange(CELLS + 1) * step
    heights = np.where((xs[None, :] > 100) & (xs[None, :] < 150), 0.0, 6.0) * np.ones(
        (CELLS + 1, 1)
    )
    pos, idx = grid_mesh(tile, heights)
    centroid = pos[idx].mean(axis=1)
    river = (centroid[:, 0] > 100) & (centroid[:, 0] < 150)
    on_deck = (np.abs(centroid[:, 2] - 125) < 5) & (centroid[:, 0] > 95) & (centroid[:, 0] < 155)
    viaduct = (np.abs(centroid[:, 2] - 40) < 5) & (centroid[:, 0] > 20) & (centroid[:, 0] < 60)
    materials = np.full(len(idx), mat_id("road"), np.uint8)
    materials[river] = mat_id("water")
    materials[on_deck | viaduct] = mat_id("bridge_deck")
    wet = river & ~on_deck
    before = pos.copy()
    assert bridge_decks(pos, idx, materials, wet) == 1
    deck = np.unique(idx[on_deck])
    assert np.allclose(pos[deck, 1], 6.0)
    # The river beside the deck and the viaduct on land keep their heights.
    beside = np.unique(idx[river & (np.abs(centroid[:, 2] - 125) > 12)])
    assert np.array_equal(pos[beside], before[beside])
    assert np.array_equal(pos[np.unique(idx[viaduct])], before[np.unique(idx[viaduct])])


def _fake_command(order, seconds=0.2):
    script = (
        f"import json,sys,pathlib,time; time.sleep({seconds}); out=pathlib.Path(sys.argv[2]);"
        "out.mkdir(parents=True,exist_ok=True); (out/'vis.glb').write_bytes(b'glb');"
        "(out/'manifest.json').write_text(json.dumps({'coarse':True,'tile':sys.argv[1],"
        "'code':'test','seconds':{'total':0.2}})) if sys.argv[1]!='t0_9_9' else sys.exit('no coverage')"
    )

    def command(tile, out):
        order.append(tile)
        return [sys.executable, "-c", script, tile, str(out)]

    return command


def _wait(predicate, timeout=20.0):
    end = time.time() + timeout
    while not predicate():
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.02)


def test_builder_queues_nearest_first_and_reports_failures(tmp_path):
    order = []
    builder = Builder(tmp_path, run=one_shot(_fake_command(order)), code="test", workers=1)
    try:
        assert builder.request("t0_5_5", 900)["state"] == "queued"
        _wait(lambda: builder.building)
        for tile, d in (("t0_6_5", 700), ("t0_7_5", 100), ("t0_8_5", 300)):
            builder.request(tile, d)
        builder.request("t0_6_5", 50)  # the player moved: the latest distance wins
        _wait(lambda: builder.status()["built"] == 4)
        assert order == ["t0_5_5", "t0_6_5", "t0_7_5", "t0_8_5"]
        assert builder.request("t0_7_5", 0) == {"state": "built"}
        builder.request("t0_9_9", 1)
        _wait(lambda: "t0_9_9" in builder.status()["failed"])
        assert builder.request("t0_9_9", 1)["state"] == "failed"
    finally:
        builder.stop()


def test_builder_drops_tiles_the_game_stopped_asking_for(tmp_path, monkeypatch):
    order = []
    builder = Builder(tmp_path, run=one_shot(_fake_command(order)), code="test", workers=1)
    try:
        builder.request("t0_1_1", 10)
        _wait(lambda: builder.building)
        builder.request("t0_2_2", 20)
        builder.request("t0_3_3", 30)
        builder.requested["t0_2_2"] -= 60
        _wait(lambda: builder.status()["built"] == 2)
        assert order == ["t0_1_1", "t0_3_3"]
    finally:
        builder.stop()


def test_builder_runs_four_builds_at_once(tmp_path):
    order = []
    builder = Builder(tmp_path, run=one_shot(_fake_command(order, seconds=2.0)), code="test")
    try:
        tiles = [f"t0_{i}_0" for i in range(6)]
        for d, tile in enumerate(tiles):
            builder.request(tile, d)
        _wait(lambda: len(builder.status()["building"]) == 4)
        assert builder.status()["building"] == tiles[:4]
        assert builder.request("t0_0_0", 0)["state"] == "building"
        _wait(lambda: builder.status()["built"] == 6)
    finally:
        builder.stop()


def test_service_http_routes(tmp_path):
    order = []
    builder = Builder(tmp_path, run=one_shot(_fake_command(order)), code="test")
    server, _, _ = serve(0, tmp_path, builder=builder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/coarse/"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        assert get("t0_4_4/vis.glb")[0] == 404
        status, body = get("t0_4_4/manifest.json?d=12")
        assert status == 202 and json.loads(body)["state"] in ("queued", "building")
        _wait(lambda: get("t0_4_4/manifest.json?d=12")[0] == 200)
        assert get("t0_4_4/vis.glb") == (200, b"glb")
        assert json.loads(get("status")[1])["built"] == 1
        for bad in ("../secret", "t0_4_4/../../x", "t1_0_0/manifest.json", "shared/zz.ktx2"):
            assert get(bad)[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        builder.stop()


def test_service_drain_lets_running_builds_finish_and_starts_no_more(tmp_path):
    order = []
    builder = Builder(
        tmp_path, run=one_shot(_fake_command(order, seconds=0.5)), code="test", workers=1
    )
    server, _, _ = serve(0, tmp_path, builder=builder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/coarse/"

    def status():
        with urllib.request.urlopen(base + "status") as r:
            return json.loads(r.read())

    try:
        builder.request("t0_1_0", 10)
        _wait(lambda: builder.building)
        drain = urllib.request.Request(base + "drain", method="POST")
        with urllib.request.urlopen(drain) as r:
            assert json.loads(r.read()) == {"draining": True, "building": ["t0_1_0"]}
        assert builder.request("t0_2_0", 20)["state"] == "queued"
        _wait(lambda: not status()["building"])
        time.sleep(0.2)  # a worker that would take t0_2_0 has had its chance
        s = status()
        assert s["built"] == 1 and s["queue"] == ["t0_2_0"] and s["draining"] is True
        assert order == ["t0_1_0"]
        assert isinstance(s["started"], float)
        assert "pipeline/turku3d/coarse/service.py" in s["modules"]
        try:
            urllib.request.urlopen(urllib.request.Request(base + "elsewhere", method="POST"))
            raise AssertionError("POST to an unknown route must be 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
        server.server_close()
        builder.stop()


def test_draft_has_osm_trees_with_trunks_and_a_water_surface(tmp_path, inputs, monkeypatch):
    import dataclasses

    import producer_world as world
    from turku3d.coarse.inputs import prepare_trees

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    x0, _, z0 = TILE.origin
    osm = world.write_osm(
        [
            ({"natural": "tree", "leaf_type": "needleleaved"}, shapely.Point(x0 + 30, z0 + 20)),
            ({"natural": "tree", "height": "18"}, shapely.Point(x0 + 60, z0 + 20)),
            ({"natural": "tree"}, shapely.Point(x0 + 105, z0 + 105)),  # inside the building
            ({"natural": "tree"}, shapely.Point(x0 + 300, z0 + 20)),  # outside the tile
        ]
    )
    trees = prepare_trees(TILE, osm)
    assert [(t[3], t[4]) for t in trees] == [(10.0, True), (18.0, False), (10.0, False)]
    out = tmp_path / "coarse" / TILE.id
    m = build(
        TILE.id, out, inputs=dataclasses.replace(inputs, trees=trees), library=tmp_path / "lib"
    )
    assert m["trees"] == 2
    cols = {cid: read_col((out / e["url"]).read_bytes()) for cid, e in m["collision"].items()}
    trunks = [p for c in cols.values() for p in c.primitives]
    assert len(trunks) == 2 and {p.material for p in trunks} == {mat_id("tree_trunk")}
    js, _ = read_glb((out / "vis.glb").read_bytes())
    assert "EXT_mesh_gpu_instancing" in js["extensionsRequired"]
    keys = {n.get("extras", {}).get("material_key") for n in js["nodes"]}
    assert "water" in keys


@pytest.mark.parametrize(
    "tags,gap",
    [({"highway": "residential"}, 4.5), ({"railway": "rail"}, 6.0), ({"highway": "cycleway"}, 2.5)],
)
def test_osm_deck_height_clears_lower_way(tags, gap):
    from turku3d.coarse.bridges import THICKNESS, clearance, deck_profile

    stations = np.array([0.0, 20.0, 40.0])
    heights = deck_profile(
        stations, np.array([2.0, 3.0, 4.0]), (2.0, 4.0), [(20.0, 5.0, clearance(tags))]
    )
    assert heights[1] - THICKNESS == pytest.approx(5.0 + gap)
    assert np.diff(heights) == pytest.approx([1.0, 1.0])


def test_osm_deck_height_keeps_high_abutments_and_clears_ground():
    from turku3d.coarse.bridges import deck_profile

    s = np.array([0.0, 10.0, 20.0])
    assert deck_profile(s, np.array([1, 2, 3]), (12, 14), [(10, 1, 6)]) == pytest.approx(
        [12, 13, 14]
    )
    assert deck_profile(s, np.array([1, 18, 3]), (12, 14)) == pytest.approx([17, 18, 19])


def test_forest_scatter_density_spacing_and_exclusions():
    from scipy.spatial.distance import pdist
    from turku3d.coarse.forests import scatter

    area = shapely.box(0, 0, 100, 100)
    building = shapely.box(10, 10, 30, 30)
    road = shapely.box(45, 0, 55, 100)
    water = shapely.box(70, 60, 100, 100)
    blocked = shapely.union_all([building, road, water])
    points = scatter(area, 0.01, blocked, 42)
    assert len(points) == 100
    assert min(pdist(points)) >= 5
    assert all(
        area.contains(shapely.Point(p)) and not blocked.covers(shapely.Point(p)) for p in points
    )
    assert points == scatter(area, 0.01, blocked, 42)
    assert len(scatter(area, 0.005, blocked, 42)) == 50
    occupied = [(35, 35), (39, 35)]
    samples = scatter(area, 0.01, blocked, 43, occupied=occupied)
    assert all(np.hypot(x - a, z - b) >= 5 for x, z in samples for a, b in occupied)


def test_forest_rows_canopy_and_bushes():
    from dataclasses import replace

    from turku3d.coarse.forests import BUSH_CAP, TREE_CAP, produce
    from turku3d.coarse.inputs import Inputs

    tile = C.VisualTile(0, 0, 0)
    empty = Inputs(
        np.empty((0, 3)), np.empty((0, 0)), np.empty((0, 0, 3)), [], [], [], None, {}, {}
    )
    forest = ("wood", shapely.box(0, 0, 100, 100), "wood", "needleleaved")
    row = ("row", shapely.LineString([(120, 20), (200, 20)]), "tree_row", "broadleaved")
    inputs = replace(empty, forests=[forest, row], canopy=np.array([[20, 25, 20], [40, 18, 40]]))
    trees, bushes = produce(tile, inputs, lambda x, z: 5.0, [])
    measured = [t for t in trees if t.measured]
    assert [t.height for t in measured] == [20, 13]
    assert all(t.variant == "tree_pineDefaultA" for t in measured)
    assert len([t for t in trees if t.object_id.startswith("row:")]) == 10
    assert len(trees) <= TREE_CAP and 0 < len(bushes) <= BUSH_CAP
    assert all(t.trunk_radius == 0 and t.height < 2 for t in bushes)


def test_dense_forest_uses_the_raised_budget():
    from turku3d.coarse.forests import TREE_CAP, scatter

    points = scatter(shapely.box(0, 0, 250, 250), 0.018, shapely.GeometryCollection(), 42)
    assert 600 < len(points) <= TREE_CAP


def test_forest_osm_types_clip_to_tile(tmp_path, monkeypatch):
    import producer_world as world
    from turku3d.coarse.forests import read_areas

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    path = world.write_osm(
        [
            ({"landuse": "forest"}, shapely.box(-10, -10, 20, 20)),
            ({"natural": "wood"}, shapely.box(30, 30, 50, 50)),
            ({"natural": "scrub"}, shapely.box(60, 60, 70, 70)),
            ({"natural": "heath"}, shapely.box(80, 80, 90, 90)),
            ({"leisure": "park"}, shapely.box(100, 100, 120, 120)),
            ({"leisure": "garden"}, shapely.box(130, 130, 140, 140)),
            (
                {"natural": "tree_row", "leaf_type": "broadleaved"},
                shapely.LineString([(-10, 10), (20, 10)]),
            ),
            ({"landuse": "residential"}, shapely.box(0, 0, 250, 250)),
        ]
    )
    areas = read_areas(C.VisualTile(0, 0, 0), path)
    assert {a[2] for a in areas} == {
        "forest",
        "wood",
        "scrub",
        "heath",
        "park",
        "garden",
        "tree_row",
    }
    assert areas[0][1].bounds == (0, 0, 20, 20)
    assert areas[-1][3] == "broadleaved"

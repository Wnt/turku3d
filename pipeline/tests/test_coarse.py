"""Coarse tiles: published tile format from synthetic inputs, and the localhost build service."""

import json
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
from turku3d.coarse.build import CELLS, WIRE_CAP, build
from turku3d.coarse.inputs import Inputs
from turku3d.coarse.service import Builder, serve
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id
from turku3d.package.gltf import read_glb
from turku3d.package.seams import verify_tile_seams

TILE = C.VisualTile(0, 3, -2)


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
    return Inputs(points, ortho, buildings, streets, water, {"synthetic": True}, {})


def test_coarse_tile_is_a_published_tile(tmp_path, inputs):
    out = tmp_path / "coarse" / TILE.id
    m = build(TILE.id, out, inputs=inputs, library=tmp_path / "no-library", log=lambda *_: None)
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


def _fake_command(order):
    script = (
        "import json,sys,pathlib,time; time.sleep(0.2); out=pathlib.Path(sys.argv[2]);"
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
    builder = Builder(tmp_path, command=_fake_command(order), code="test")
    try:
        assert builder.request("t0_5_5", 900)["state"] == "queued"
        _wait(lambda: builder.building is not None)
        for tile, d in (("t0_6_5", 700), ("t0_7_5", 100), ("t0_8_5", 300)):
            builder.request(tile, d)
        builder.request("t0_6_5", 50)  # the player moved: the latest distance wins
        _wait(lambda: len(builder.status()["built"]) == 4)
        assert order == ["t0_5_5", "t0_6_5", "t0_7_5", "t0_8_5"]
        assert builder.request("t0_7_5", 0) == {"state": "built"}
        builder.request("t0_9_9", 1)
        _wait(lambda: "t0_9_9" in builder.status()["failed"])
        assert builder.request("t0_9_9", 1)["state"] == "failed"
    finally:
        builder.stop()


def test_builder_drops_tiles_the_game_stopped_asking_for(tmp_path, monkeypatch):
    order = []
    builder = Builder(tmp_path, command=_fake_command(order), code="test")
    try:
        builder.request("t0_1_1", 10)
        _wait(lambda: builder.building is not None)
        builder.request("t0_2_2", 20)
        builder.request("t0_3_3", 30)
        builder.requested["t0_2_2"] -= 60
        _wait(lambda: len(builder.status()["built"]) == 2)
        assert order == ["t0_1_1", "t0_3_3"]
    finally:
        builder.stop()


def test_service_http_routes(tmp_path):
    order = []
    builder = Builder(tmp_path, command=_fake_command(order), code="test")
    server, _ = serve(0, tmp_path, builder=builder)
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
        assert json.loads(get("status")[1])["built"] == ["t0_4_4"]
        for bad in ("../secret", "t0_4_4/../../x", "t1_0_0/manifest.json", "shared/zz.ktx2"):
            assert get(bad)[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        builder.stop()

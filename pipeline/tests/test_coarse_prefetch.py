"""Coarse raw-data prefetch: cone geometry, unit selection and order, disk lookups, the fetch queue,
the LRU prune, and a service whose builds never wait for a download (fake fetcher, fake builds)."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np
import pytest
from turku3d import config as C
from turku3d.coarse import prefetch as P
from turku3d.coarse.service import Builder, serve
from turku3d.coarse.sources import (
    WFS_LAYERS,
    NoCoverage,
    Sources,
    Unit,
    block_of,
    block_tiles,
    osm_area,
    tile_blocks,
    unit_paths,
)
from turku3d.fetch.common import Registry

BLOCK = C.LazTile(int(C.ORIGIN_E), int(C.ORIGIN_N))  # engine x 0..500, z 0..500


def one_shot(command):
    """A Builder runner that starts command(tile, out) once per tile."""
    return lambda tile, out: subprocess.run(command(tile, out), capture_output=True, text=True)


@pytest.fixture
def raw(tmp_path, monkeypatch):
    """A private raw cache. Every prefetch thread must be gone before the environment is restored,
    or a late landing would write into the shared $TURKU3D_DATA/raw."""
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    assert C.raw_dir().is_relative_to(tmp_path)
    yield tmp_path / "data" / "raw"
    for thread in threading.enumerate():
        if thread.name.startswith("coarse-prefetch"):
            thread.join(30)
            assert not thread.is_alive(), f"{thread.name} outlived its test"


def _wait(predicate, timeout=20.0):
    end = time.time() + timeout
    while not predicate():
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.02)


# -- synthetic raw cache --------------------------------------------------------------------------


def put_lidar(block: C.LazTile, data=b"laz" * 100):
    path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    Registry(C.raw_dir("laz")).record(path, url="test")
    return path


def put_ortho(block: C.LazTile, status="ok"):
    root = C.raw_dir("ortho") / "ortho2022to"
    name = f"coarse-{block.name}"
    (root / name).mkdir(parents=True, exist_ok=True)
    state = {}
    for t in block_tiles(block):
        b = t.bbox.to_en().as_tuple()
        state[f"{t.i}_{t.j}"] = {"status": status, "bbox": list(b)}
        if status == "ok":
            (root / name / f"{t.i}_{t.j}.tif").write_bytes(b"tif" * 50)
    (root / f"{name}.chunks.json").write_text(json.dumps(state))
    if status == "ok":
        b = block.bbox_en
        (root / f"{name}.vrt").write_text(
            '<VRTDataset rasterXSize="1024" rasterYSize="1024"><GeoTransform>'
            f"{b.emin}, 0.48828125, 0, {b.nmax}, 0, -0.48828125</GeoTransform></VRTDataset>"
        )


def put_wfs(block: C.LazTile):
    adir = C.raw_dir("wfs") / f"coarse-{block.name}"
    adir.mkdir(parents=True, exist_ok=True)
    bbox = list(block.bbox_en.as_tuple())
    for name in WFS_LAYERS:
        slug = name.replace(":", "_")
        (adir / f"{slug}.parquet").write_bytes(b"pq" * 40)
        (adir / f"{slug}.summary.json").write_text(json.dumps({"status": "ok", "bbox_en": bbox}))


def put_osm(block: C.LazTile):
    name = osm_area(block).name
    adir = C.raw_dir("osm") / name
    adir.mkdir(parents=True, exist_ok=True)
    (adir / f"{name}-261027.parquet").write_bytes(b"pq" * 40)


# -- geometry -------------------------------------------------------------------------------------


def test_cone_depth_follows_speed():
    assert P.depth(0) == P.depth(P.FOOT_SPEED) == 500
    assert P.depth(P.CAR_SPEED) == P.depth(60) == 1500
    assert P.depth((P.FOOT_SPEED + P.CAR_SPEED) / 2) == pytest.approx(1000)
    assert P.area(0, 0).area == pytest.approx(3.1416 * 500**2, rel=0.01)  # standing: ring only
    car = P.area(0, 0, P.CAR_SPEED, 0)  # east at top speed
    assert car.contains(P.shapely.Point(1490, 0)) and not car.contains(P.shapely.Point(-600, 0))
    assert car.contains(P.shapely.Point(1200, 400)) and not car.contains(P.shapely.Point(1200, 500))
    walk = P.area(0, 0, 0, -P.FOOT_SPEED)  # north on foot: the cone ends with the ring
    assert not walk.contains(P.shapely.Point(0, -520))


def test_plan_selects_blocks_under_the_area_in_fetch_order():
    still = P.plan(250, 250, 0, 0)
    blocks = {u.block for u in still}
    assert len(blocks) == 9  # a 500 m ring from a block centre touches its eight neighbours
    # One band (all within 500 m): every dataset in turn, nearest block first; one OSM clip per 2 km.
    assert [u.dataset for u in still] == ["lidar"] * 9 + ["ortho"] * 9 + ["wfs"] * 9 + ["osm"] * 4
    assert still[0].block == still[9].block == still[27].block == BLOCK
    assert len({osm_area(u.block).name for u in still if u.dataset == "osm"}) == 4
    east = P.plan(250, 250, P.CAR_SPEED, 0)
    bands = [
        int(
            P.reach(P.shapely.box(*P.block_bbox(u.block).as_tuple()), 250, 250, P.CAR_SPEED, 0)
            // P.BAND
        )
        for u in east
    ]
    assert bands == sorted(bands)  # the path's every dataset before lidar farther on
    assert east.index(Unit("wfs", BLOCK)) < min(i for i, b in enumerate(bands) if b == 1)
    ahead, beside = (
        C.LazTile(BLOCK.xmin + 500, BLOCK.ymax),
        C.LazTile(BLOCK.xmin + 500, BLOCK.ymax + 500),
    )
    assert east.index(Unit("lidar", ahead)) < east.index(Unit("lidar", beside))  # on the path first
    reach = max(u.block.xmin for u in east) - BLOCK.xmin
    assert reach == 1500  # 1.5 km ahead: blocks starting 500, 1000 and 1500 m east of this one
    assert min(u.block.xmin for u in east) - BLOCK.xmin == -500  # the ring behind
    uncovered = P.plan(250, 250, 0, 0, covered=lambda b: b != BLOCK)
    assert BLOCK not in {u.block for u in uncovered}


# -- disk lookups ---------------------------------------------------------------------------------


def test_sources_list_missing_units_and_see_them_land(raw):
    tile = C.VisualTile(0, 0, 0)
    sources = Sources()
    sources._listed = {BLOCK.name}
    assert block_of(tile) == BLOCK
    assert [u.key for u in sources.missing(tile)] == [
        f"lidar/{BLOCK.name}",
        f"ortho/{BLOCK.name}",
        f"wfs/{BLOCK.name}",
    ]  # OSM is optional: the draft is rebuilt with streets once it lands
    put_lidar(BLOCK)
    put_ortho(BLOCK)
    put_wfs(BLOCK)
    put_osm(BLOCK)
    sources.invalidate()
    assert sources.missing(tile) == []
    assert all(sources.unit_present(Unit(d, BLOCK)) for d in ("lidar", "ortho", "wfs", "osm"))
    assert sources.osm_source(tile).parent.name == osm_area(BLOCK).name
    assert sources.ortho_source(tile).name == f"coarse-{BLOCK.name}.vrt"
    assert sources.wfs_source("bldg:Building_LOD2", tile)[1].name == "bldg_Building_LOD2.parquet"
    with pytest.raises(NoCoverage):
        sources.missing(C.VisualTile(0, 40, 40))  # outside the lidar index


def test_ortho_without_imagery_is_present_but_no_coverage(raw):
    sources = Sources()
    sources._listed = {BLOCK.name}
    put_ortho(BLOCK, status="empty")
    assert sources.unit_present(Unit("ortho", BLOCK))
    with pytest.raises(NoCoverage):
        sources.ortho_source(C.VisualTile(0, 0, 0))


# -- fetch queue ----------------------------------------------------------------------------------


class FakeFetch:
    """Sleeps like a download (until ``release`` when given), then writes the unit's files."""

    def __init__(self, seconds=0.3, fail=(), release: threading.Event | None = None):
        self.seconds, self.fail, self.release = seconds, set(fail), release
        self.root = C.raw_dir()
        self.started: list[str] = []
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, unit: Unit) -> dict:
        with self.lock:
            self.started.append(unit.key)
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.seconds)
        if self.release:
            self.release.wait(30)
        with self.lock:
            self.active -= 1
        if unit.key in self.fail:
            raise RuntimeError("network error")
        assert C.raw_dir() == self.root, "landing outside the test's raw cache"
        land(unit)
        return {"bytes": 1000, "paths": [str(p) for p in unit_paths(unit)]}


def land(unit: Unit):
    {"lidar": put_lidar, "ortho": put_ortho, "wfs": put_wfs, "osm": put_osm}[unit.dataset](
        unit.block
    )


def _prefetcher(fake, ledger_path, after=lambda unit: None, **kw):
    sources = Sources()
    sources._listed = {b.name for b in C.laz_tiles_for_bbox(C.BBoxLocal(-3000, -3000, 3000, 3000))}

    return P.Prefetcher(
        sources, fetcher=fake, ledger=P.Ledger(ledger_path, protected=set), on_landed=after, **kw
    )


def test_prefetcher_fetches_lidar_first_two_at_a_time_and_never_twice(raw):
    fake = FakeFetch(seconds=0.05)
    p = _prefetcher(fake, raw / "prefetch" / "ledger.json")
    try:
        put_wfs(BLOCK)  # already on disk: never fetched
        p.want(250, 250, 0, 0)
        _wait(lambda: p.status()["queued"] == 0 and not p.status()["fetching"])
        started = fake.started
        assert f"wfs/{BLOCK.name}" not in started
        assert len(started) == len(set(started)) == 9 + 9 + 8 + 4
        datasets = [k.split("/")[0] for k in started]
        assert datasets == sorted(datasets, key=("lidar", "ortho", "wfs", "osm").index)
        assert f"lidar/{BLOCK.name}" in started[: P.DOWNLOADS]  # nearest first
        assert fake.peak == P.DOWNLOADS
        s = p.status()
        assert s["datasets"]["lidar"]["units"] == 9 and s["datasets"]["lidar"]["bytes"] == 9000
        assert s["cache"]["bytes"] == 30 * 1000
        fake.started.clear()
        p.planned_at = None
        p.want(250, 250, 0, 0)  # everything is on disk now
        time.sleep(0.3)
        assert fake.started == []
    finally:
        p.stop()


def test_urgent_units_jump_the_queue_and_failures_wait(raw):
    fake = FakeFetch(seconds=0.3, fail={f"ortho/{BLOCK.name}"})
    p = _prefetcher(fake, raw / "prefetch" / "ledger.json", downloads=1)
    try:
        far = C.LazTile(BLOCK.xmin + 2000, BLOCK.ymax)
        p.want(2250, 250, 0, 0)  # plans around another block
        _wait(lambda: len(fake.started) == 1)
        p.need([Unit("ortho", BLOCK), Unit("lidar", BLOCK)])
        _wait(lambda: len(fake.started) == 3)
        assert fake.started[0].endswith(far.name)
        assert fake.started[1:] == [f"lidar/{BLOCK.name}", f"ortho/{BLOCK.name}"]
        _wait(lambda: f"ortho/{BLOCK.name}" in p.status()["failed"])
        assert "network error" in p.status()["failed"][f"ortho/{BLOCK.name}"]
        p.need([Unit("ortho", BLOCK)])  # retried only after RETRY_SECONDS
        assert f"ortho/{BLOCK.name}" not in p.status()["urgent"]
    finally:
        p.stop()


def test_a_fetch_that_leaves_its_unit_incomplete_is_not_repeated(raw):
    class Broken(FakeFetch):
        def __call__(self, unit):
            self.started.append(unit.key)
            return {
                "bytes": 0,
                "paths": [],
            }  # "cached" by the fetcher, yet nothing covers the tiles

    fake = Broken()
    p = _prefetcher(fake, raw / "prefetch" / "ledger.json", downloads=1)
    try:
        p.need([Unit("ortho", BLOCK)])
        _wait(lambda: f"ortho/{BLOCK.name}" in p.status()["failed"])
        p.need([Unit("ortho", BLOCK)])
        time.sleep(0.2)
        assert fake.started == [f"ortho/{BLOCK.name}"]
        assert "not covered" in p.status()["failed"][f"ortho/{BLOCK.name}"]
    finally:
        p.stop()


# -- LRU prune ------------------------------------------------------------------------------------


def test_ledger_prunes_least_recently_used_prefetched_units(raw):
    blocks = [C.LazTile(BLOCK.xmin + 500 * k, BLOCK.ymax) for k in range(4)]
    ledger = P.Ledger(
        raw / "prefetch" / "ledger.json", cap=3000, protected=lambda: {blocks[2].name}
    )
    for k, block in enumerate(blocks):
        path = put_lidar(block, data=b"x" * 1000)
        ledger.add(Unit("lidar", block), [path], 1000, now=1000.0 + k)
    untracked = put_lidar(C.LazTile(BLOCK.xmin, BLOCK.ymax + 500))  # not prefetched: never pruned
    ledger.touch([Unit("lidar", blocks[0])], now=2000.0)  # used again: newest
    assert ledger.prune(keep={f"lidar/{blocks[1].name}"}) == [f"lidar/{blocks[3].name}"]
    # blocks[2] is protected (under a playable tile), blocks[1] is being fetched, blocks[0] is newest.
    assert ledger.total() == 3000
    ledger.cap = 1000
    assert ledger.prune() == [f"lidar/{blocks[1].name}", f"lidar/{blocks[0].name}"]
    reg = Registry(C.raw_dir("laz"))
    for block in blocks[:2] + blocks[3:]:
        path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        assert not path.exists() and reg.get(path) is None
    assert untracked.exists() and reg.is_complete(untracked)
    assert (raw / "laz" / "SHA256SUMS").read_text().count("\n") == 2


def test_playable_blocks_cover_the_listed_tiles_input_areas(raw):
    C.write_playable_tiles(C.work_root() / C.PLAYABLE_TILES_FILE, [*C.CORE_TILES, "t0_3_0"])
    names = P.playable_blocks()
    assert block_of(C.VisualTile(0, 3, 0)).name in names
    assert C.LazTile(BLOCK.xmin + 4000, BLOCK.ymax).name not in names


# -- service: builds never wait for downloads -----------------------------------------------------


def _fake_build(order):
    script = (
        "import json,sys,pathlib; out=pathlib.Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True);"
        "(out/'vis.glb').write_bytes(b'glb'); (out/'manifest.json').write_text(json.dumps("
        "{'coarse':True,'tile':sys.argv[1],'code':'test','seconds':{'total':0.1}}))"
    )

    def command(tile, out):
        order.append(tile)
        return [sys.executable, "-c", script, tile, str(out)]

    return command


def test_service_builds_ready_tiles_while_another_tiles_data_downloads(raw, tmp_path):
    release = threading.Event()  # downloads hang until the test lets them land
    fake = FakeFetch(seconds=0.0, release=release)
    builder = None
    p = _prefetcher(fake, raw / "prefetch" / "ledger.json", after=lambda unit: builder.landed())
    ready = C.VisualTile(0, 4, 0)  # block 2 east: on disk
    for block in tile_blocks(ready):
        put_lidar(block)
    put_ortho(block_of(ready))
    put_wfs(block_of(ready))
    put_osm(block_of(ready))
    p.sources.invalidate()
    order = []
    builder = Builder(
        tmp_path / "coarse",
        run=one_shot(_fake_build(order)),
        code="test",
        inputs=lambda tile: [u.key for u in p.sources.missing(C.VisualTile.parse(tile))],
        on_missing=lambda units: p.need([Unit.parse(u) for u in units]),
    )
    server, _, _ = serve(0, tmp_path, builder=builder, prefetcher=p)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/coarse/"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        status, body = get("t0_0_0/manifest.json?d=0")  # answered while its download hangs
        assert status == 202 and body["state"] == "pending-data"
        lidar = [f"lidar/{b.name}" for b in C.laz_tiles_for_bbox(C.BBoxLocal(-20, -20, 270, 270))]
        rest = [f"{d}/{BLOCK.name}" for d in ("ortho", "wfs")]
        assert body["missing"] == [*lidar, *rest]
        assert get(f"{ready.id}/manifest.json?d=300")[0] == 202
        _wait(lambda: get(f"{ready.id}/manifest.json?d=300")[0] == 200)
        assert order == [ready.id] and p.status()["fetching"]  # built while downloads still run
        assert "t0_0_0" in get("status")[1]["pending"]
        status, body = get("want?x=10&z=20&vx=27&vz=0")
        assert status == 200 and "queued" in body
        assert get("want?x=nan&z=0&vx=0&vz=0")[0] == 400
        assert "t0_0_0" not in order
        release.set()
        _wait(lambda: "t0_0_0" in order)  # queued again as soon as its data landed
        _wait(lambda: get("t0_0_0/manifest.json?d=0")[0] == 200)
        s = get("status")[1]
        assert s["pending"] == {} and s["prefetch"]["datasets"]["lidar"]["units"] >= 1
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        builder.stop()
        p.stop()


def test_a_draft_built_from_other_raw_files_is_stale(raw, tmp_path):
    key = ["a"]
    builder = Builder(
        tmp_path / "coarse",
        run=one_shot(_fake_build([])),
        code="test",
        workers=0,
        input_key=lambda tile: key[0],
    )
    (tmp_path / "coarse" / "t0_0_0").mkdir(parents=True)
    manifest = {"coarse": True, "tile": "t0_0_0", "code": "test", "inputs": "a"}
    (tmp_path / "coarse" / "t0_0_0" / "manifest.json").write_text(json.dumps(manifest))
    assert builder.built("t0_0_0")
    key[0] = "b"  # a raw file under the tile was replaced (refetched, repaired)
    assert not builder.built("t0_0_0")
    key[0] = None  # inputs pruned from the cache: the built draft stays valid
    assert builder.built("t0_0_0")
    put_lidar(BLOCK)
    sources = Sources()
    sources._listed = {b.name for b in tile_blocks(C.VisualTile(0, 1, 1))}
    for block in tile_blocks(C.VisualTile(0, 1, 1)):
        put_lidar(block)
    put_ortho(BLOCK), put_wfs(BLOCK)
    without_osm = sources.input_key(C.VisualTile(0, 1, 1))
    assert without_osm and sources.missing(C.VisualTile(0, 1, 1)) == []  # buildable, no streets
    put_osm(BLOCK)
    first = sources.input_key(C.VisualTile(0, 1, 1))
    assert first != without_osm  # the street-less draft is rebuilt once OSM lands
    assert first and first == sources.input_key(C.VisualTile(0, 1, 1))
    lidar = C.raw_dir("laz") / "maanpinta" / f"{BLOCK.name}.laz"
    lidar.write_bytes(b"LASF" * 200)
    assert sources.input_key(C.VisualTile(0, 1, 1)) != first


# -- prepared inputs ------------------------------------------------------------------------------


def _real_raw(tile: C.VisualTile):
    """Readable synthetic raw data for one tile: key-point LAZ, an ortho mosaic, WFS and OSM."""
    import subprocess

    import geopandas as gpd
    import laspy
    import rasterio
    from rasterio.transform import from_origin

    for block in tile_blocks(tile):
        b = block.bbox_en
        e, n = np.meshgrid(np.arange(b.emin, b.emax, 10.0), np.arange(b.nmin, b.nmax, 10.0))
        las = laspy.create(point_format=0, file_version="1.2")
        las.header.scales, las.header.offsets = [0.01] * 3, [b.emin, b.nmin, 0]
        las.x, las.y, las.z = e.ravel(), n.ravel(), np.full(e.size, 5.0)
        path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        path.parent.mkdir(parents=True, exist_ok=True)
        las.write(path)
        Registry(C.raw_dir("laz")).record(path, url="https://example.invalid/test.laz")
    block = block_of(tile)
    put_ortho(block)  # chunk state; the mosaic below replaces the stub tiles
    root = C.raw_dir("ortho") / "ortho2022to"
    tif = root / f"coarse-{block.name}" / "mosaic.tif"
    b = block.bbox_en
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=3,
        dtype="uint8",
        crs="EPSG:3877",
        transform=from_origin(b.emin, b.nmax, 500 / 64, 500 / 64),
    ) as ds:
        ds.write(np.full((3, 64, 64), 90, np.uint8))
    vrt = root / f"coarse-{block.name}.vrt"
    vrt.unlink()
    subprocess.run(["gdalbuildvrt", "-q", str(vrt), str(tif)], check=True)
    adir = C.raw_dir("wfs") / f"coarse-{block.name}"
    adir.mkdir(parents=True)
    x0, _, z0 = tile.origin
    poly = P.shapely.Polygon([(x0 + 10, z0 + 10, 3), (x0 + 30, z0 + 10, 3), (x0 + 30, z0 + 30, 3)])
    for name, rows in (
        (
            "bldg_Building_LOD2",
            {"feature_key": ["B"], "surface_type": ["GroundSurface"], "measuredHeight": ["9"]},
        ),
        ("GIS_Katualueet", {"Katuosalaji": ["Ajorata"], "Pintamateriaali": ["Asfalttibetoni"]}),
        ("kanta_Vesikohde", {"kplkoodi": ["w"]}),
    ):
        frame = gpd.GeoDataFrame(rows, geometry=[poly], crs="EPSG:3877")
        frame["geometry_local"] = gpd.GeoSeries([poly])
        frame["geom_type"] = "Polygon"
        frame.to_parquet(adir / f"{name}.parquet")
        (adir / f"{name}.summary.json").write_text(
            json.dumps({"status": "ok", "bbox_en": list(block.bbox_en.as_tuple())})
        )
    odir = C.raw_dir("osm") / osm_area(block).name
    odir.mkdir(parents=True)
    gpd.GeoDataFrame(
        {
            "highway": ["residential"],
            "tags": [json.dumps({"highway": "residential"})],
            "geometry_local": gpd.GeoSeries(
                [P.shapely.LineString([(x0, z0 + 50), (x0 + 250, z0 + 50)])]
            ),
        },
        geometry="geometry_local",
    ).to_parquet(odir / f"{odir.name}-261027.parquet")


def test_inputs_are_prepared_once_and_again_when_a_raw_file_changes(raw, tmp_path):
    from turku3d.coarse.inputs import gather, prepare, unprepared

    tile = C.VisualTile(0, 1, 1)
    _real_raw(tile)
    sources = Sources()
    sources._listed = {b.name for b in tile_blocks(tile)}
    root = tmp_path / "coarse"
    assert unprepared(tile, root, sources)
    first = prepare(tile, root, sources)
    assert first["missing"] == [] and all(
        isinstance(first[d], float) for d in ("ground", "ortho", "wfs", "roads")
    )
    assert not unprepared(tile, root, sources)
    assert {
        k: v for k, v in prepare(tile, root, sources).items() if k != "missing"
    } == dict.fromkeys(("ground", "ortho", "wfs", "roads"), "cached")
    inputs = gather(tile, root, sources)
    assert inputs.heights.shape == (129, 129) and np.allclose(inputs.heights, 5.0)
    assert inputs.ortho.shape == (512, 512, 3) and inputs.roads["edges"] >= 1
    assert inputs.key == sources.input_key(tile) and inputs.key
    laz = C.raw_dir("laz") / "maanpinta" / f"{block_of(tile).name}.laz"
    os.utime(laz, ns=(laz.stat().st_atime_ns, laz.stat().st_mtime_ns + 10**9))
    assert unprepared(tile, root, sources)
    assert isinstance(prepare(tile, root, sources)["ground"], float)
    assert len(list((root / ".inputs" / tile.id).glob("ground.*.pkl"))) == 1  # the old key is gone


def test_prefetcher_prepares_complete_tiles_near_the_player(raw, tmp_path):
    for block in C.laz_tiles_for_bbox(C.BBoxLocal(-600, -600, 1100, 1100)):
        (
            land(Unit("lidar", block)),
            land(Unit("ortho", block)),
            land(Unit("wfs", block)),
            land(Unit("osm", block)),
        )
    prepared = []
    root = tmp_path / "coarse"
    (root / "t0_1_1").mkdir(parents=True)
    (root / "t0_1_1" / "manifest.json").write_text("{}")  # built: not prepared again
    p = _prefetcher(
        FakeFetch(), raw / "prefetch" / "ledger.json", root=root, preparer=prepared.append
    )
    try:
        p.want(375, 375, 0, 0)
        _wait(lambda: len(prepared) == P.PREPARE_MAX)
        nearest = {"t0_0_1", "t0_1_0", "t0_2_1", "t0_1_2"}  # after the built one
        assert set(prepared[:4]) == nearest
        assert "t0_1_1" not in prepared and not p.fetching
        assert p.status()["prepare"]["tiles"] == P.PREPARE_MAX
    finally:
        p.stop()
    tiles = P.prepare_tiles(375, 375, P.CAR_SPEED, 0)
    assert tiles[0] == C.VisualTile(0, 1, 1)
    assert (
        max(t.i for t in tiles) == 5 and min(t.i for t in tiles) == -1
    )  # 1 km ahead, 500 m behind


def test_queue_builds_the_path_ahead_first_and_estimates_when_tiles_are_ready(tmp_path):
    from turku3d.coarse.service import score

    builder = Builder(tmp_path / "coarse", run=one_shot(_fake_build([])), code="test", workers=0)
    for tile, d in (
        ("t0_1_0", 0),
        ("t0_0_1", 0),
        ("t0_2_0", 125),
        ("t0_-1_0", 125),
        ("t0_3_1", 400),
    ):
        builder.request(tile, d)
    assert builder.order()[:2] in (["t0_0_1", "t0_1_0"], ["t0_1_0", "t0_0_1"])  # standing: nearest
    builder.steer(260, 125, 27, 0)  # driving east through t0_1_0
    assert builder.order() == ["t0_1_0", "t0_2_0", "t0_3_1", "t0_0_1", "t0_-1_0"]
    assert score("t0_3_1", 400, builder.motion) < score(
        "t0_0_1", 0, builder.motion
    )  # ahead beats beside
    builder.workers = 2
    builder.recent = [4.0, 6.0]
    eta = builder.eta()
    assert [eta[t] for t in builder.order()] == [5.0, 5.0, 10.0, 10.0, 15.0]
    assert builder.request("t0_2_0", 125) == {"state": "queued", "position": 1, "eta": 5.0}
    assert builder.status()["version"] == 0


def test_a_build_that_is_stale_on_arrival_is_built_again(tmp_path):
    key = ["a"]
    order = []
    script = (
        "import json,sys,pathlib; out=pathlib.Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True);"
        "(out/'manifest.json').write_text(json.dumps({'coarse':True,'tile':sys.argv[1],'code':'test',"
        "'inputs':sys.argv[3],'seconds':{'total':0.1}}))"
    )

    def command(tile, out):
        order.append(tile)
        used = key[0]
        key[0] = "b" if len(order) == 1 else key[0]  # data lands during the first build
        return [sys.executable, "-c", script, tile, str(out), used]

    builder = Builder(
        tmp_path / "coarse",
        run=one_shot(command),
        code="test",
        workers=1,
        input_key=lambda tile: key[0],
    )
    try:
        builder.request("t0_0_0", 0)
        _wait(lambda: builder.status()["built"] == ["t0_0_0"])
        assert order == ["t0_0_0", "t0_0_0"] and "t0_0_0" not in builder.status()["failed"]
    finally:
        builder.stop()


def test_a_tile_without_buildings_is_prepared_with_none(tmp_path):
    import geopandas as gpd
    from turku3d.coarse.inputs import prepare_wfs

    tile = C.VisualTile(0, 1, 1)
    assert prepare_wfs(tile, None, None, None) == {"buildings": [], "streets": [], "water": []}
    empty = tmp_path / "bldg.parquet"  # what the WFS cache writes for pages without features
    gpd.GeoDataFrame({"gml_id": []}, geometry=[]).to_parquet(empty)
    assert prepare_wfs(tile, empty, None, None)["buildings"] == []


def test_persistent_workers_build_tiles_and_report_pending_ones(raw, tmp_path):
    from turku3d.coarse.service import Workers

    _real_raw(C.VisualTile(0, 1, 1))  # block (0, 0): tiles t0_0_0..t0_1_1, lidar of its neighbours
    sources = Sources()
    workers = Workers()
    try:
        first = workers("t0_1_1", tmp_path / "coarse" / "t0_1_1")
        assert first.returncode == 0, first.stderr
        assert json.loads((tmp_path / "coarse/t0_1_1/manifest.json").read_text())["buildings"] == 1
        (proc,) = workers.procs
        far = workers("t0_9_9", tmp_path / "coarse" / "t0_9_9")  # no raw data there
        assert far.returncode == 3 and json.loads(far.stdout)["pending"]
        assert workers.procs == [proc] and proc.poll() is None  # the same process, still running
        assert sources.input_key(C.VisualTile(0, 1, 1)) is not None
    finally:
        workers.close()
    assert proc.poll() is not None


def test_drive_replicates_the_hooks_window_and_ahead_cells_and_summarises():
    from turku3d.coarse import drive as D

    assert D.window(125, 125, {"t0_0_0"}) == [
        "t0_-1_-1",
        "t0_-1_0",
        "t0_-1_1",
        "t0_0_-1",
        "t0_0_1",
        "t0_1_-1",
        "t0_1_0",
        "t0_1_1",
    ]
    assert D.ahead(100, 100, 0.5, 0, set()) == set()
    east = D.ahead(100, 100, 20, 0, set())  # coarseTiles.test.ts: 5 x 3 cells out to x 700
    assert len(east) == 15 and "t0_3_1" in east and "t0_4_0" not in east
    tiles = {
        "t0_5_0": {"ring": 40.0, "built": 41.5},
        "t0_6_0": {"ring": 50.0, "built": 48.0},  # built before it entered the ring
        "t0_7_0": {"ring": 60.0, "built": None, "why": "pending-data"},
        "t0_8_0": {
            "ring": 70.0,
            "built": None,
            "why": "t0_8_0: only 0 ground key points (NoCoverage)",
        },
        "t0_1_0": {"ring": 0.0, "built": 30.0},  # the first kilometre
    }
    prefetch = {
        "datasets": {
            "lidar": {"units": 2, "bytes": 3_000_000},
            "wfs": {"units": 1, "bytes": 2_400_000},
        }
    }
    s = D.summary(tiles, 27.0, 2700.0, prefetch)
    assert (s["tiles"], s["built"], s["under_10_s"], s["max_s"]) == (3, 2, 2, 1.5)
    assert s["mb_per_km"] == 2.0 and s["units"] == {"lidar": 2, "wfs": 1}

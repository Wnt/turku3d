"""Coarse raw-data prefetch: cone geometry, unit selection and order, disk lookups, the fetch queue,
the LRU prune, and a service whose builds never wait for a download (fake fetcher, fake builds)."""

import json
import sys
import threading
import time
import urllib.request

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
    tile_blocks,
    unit_paths,
)
from turku3d.fetch.common import Registry

BLOCK = C.LazTile(int(C.ORIGIN_E), int(C.ORIGIN_N))  # engine x 0..500, z 0..500


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
    (root / f"{name}.vrt").write_text("<VRTDataset/>")


def put_wfs(block: C.LazTile):
    adir = C.raw_dir("wfs") / f"coarse-{block.name}"
    adir.mkdir(parents=True, exist_ok=True)
    bbox = list(block.bbox_en.as_tuple())
    for name in WFS_LAYERS:
        slug = name.replace(":", "_")
        (adir / f"{slug}.parquet").write_bytes(b"pq" * 40)
        (adir / f"{slug}.summary.json").write_text(json.dumps({"status": "ok", "bbox_en": bbox}))


def put_osm(block: C.LazTile):
    adir = C.raw_dir("osm") / f"coarse-{block.name}"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / f"coarse-{block.name}-261027.parquet").write_bytes(b"pq" * 40)


# -- geometry -------------------------------------------------------------------------------------


def test_cone_depth_follows_speed():
    assert P.depth(0) == P.depth(P.FOOT_SPEED) == 500
    assert P.depth(P.CAR_SPEED) == P.depth(60) == 1500
    assert P.depth((P.FOOT_SPEED + P.CAR_SPEED) / 2) == pytest.approx(1000)
    assert P.area(0, 0).area == pytest.approx(3.1416 * 500**2, rel=0.01)  # standing: ring only
    car = P.area(0, 0, P.CAR_SPEED, 0)  # east at top speed
    assert car.contains(P.shapely.Point(1490, 0)) and not car.contains(P.shapely.Point(-600, 0))
    assert car.contains(P.shapely.Point(1200, 600)) and not car.contains(P.shapely.Point(1200, 800))
    walk = P.area(0, 0, 0, -P.FOOT_SPEED)  # north on foot: the cone ends with the ring
    assert not walk.contains(P.shapely.Point(0, -520))


def test_plan_selects_blocks_under_the_area_in_fetch_order():
    still = P.plan(250, 250, 0, 0)
    blocks = {u.block for u in still}
    assert len(blocks) == 9  # a 500 m ring from a block centre touches its eight neighbours
    assert [u.dataset for u in still] == ["lidar"] * 9 + ["ortho"] * 9 + ["wfs"] * 9 + ["osm"] * 9
    assert still[0].block == still[9].block == still[27].block == BLOCK  # nearest first in each
    east = P.plan(250, 250, P.CAR_SPEED, 0)
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
        f"osm/{BLOCK.name}",
    ]
    put_lidar(BLOCK)
    put_ortho(BLOCK)
    put_wfs(BLOCK)
    put_osm(BLOCK)
    sources.invalidate()
    assert sources.missing(tile) == []
    assert all(sources.unit_present(Unit(d, BLOCK)) for d in ("lidar", "ortho", "wfs", "osm"))
    assert sources.osm_source(tile).name == f"coarse-{BLOCK.name}-261027.parquet"
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
        assert len(started) == len(set(started)) == 9 + 9 + 8 + 9
        datasets = [k.split("/")[0] for k in started]
        assert datasets == sorted(datasets, key=("lidar", "ortho", "wfs", "osm").index)
        assert f"lidar/{BLOCK.name}" in started[: P.DOWNLOADS]  # nearest first
        assert fake.peak == P.DOWNLOADS
        s = p.status()
        assert s["datasets"]["lidar"]["units"] == 9 and s["datasets"]["lidar"]["bytes"] == 9000
        assert s["cache"]["bytes"] == 35 * 1000
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
        command=_fake_build(order),
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
        rest = [f"{d}/{BLOCK.name}" for d in ("ortho", "wfs", "osm")]
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

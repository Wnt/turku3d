"""Offline tests for turku3d.fetch (no network: local HTTP server + fake WFS/WMS getters)."""

from __future__ import annotations

import http.server
import io
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from turku3d import config
from turku3d.fetch import common, sources

AREA = config.OFFICE_BLOCK


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


# --------------------------------------------------------------------------------------
# Registry + resumable curl download against a local Range-capable server
# --------------------------------------------------------------------------------------


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # minimal single-range support
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(body)
        self.server.hits.append(rng or "full")


@pytest.fixture()
def http_dir(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    handler = lambda *a, **kw: RangeHandler(*a, directory=str(root), **kw)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.hits = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl missing")
def test_curl_download_resume_and_idempotent(tmp_path, http_dir):
    root, base, srv = http_dir
    payload = os.urandom(300_000)
    (root / "a.bin").write_bytes(payload)
    reg = common.Registry(tmp_path / "raw")
    dest = tmp_path / "raw" / "x" / "a.bin"
    dest.parent.mkdir(parents=True)
    # simulate an interrupted download
    dest.with_name("a.bin.part").write_bytes(payload[:100_000])
    head = common.curl_head(f"{base}/a.bin")
    assert head["status"] == 200 and head["content_length"] == len(payload)
    rec, now = common.curl_download(f"{base}/a.bin", dest, reg, expected_bytes=len(payload))
    assert now and dest.read_bytes() == payload
    assert any(h.startswith("bytes=100000-") for h in srv.hits), srv.hits  # resumed, not restarted
    assert rec["sha256"] == common.sha256_bytes(payload)
    assert (tmp_path / "raw" / "SHA256SUMS").read_text() == f"{rec['sha256']}  x/a.bin\n"
    n = len(srv.hits)
    rec2, now2 = common.curl_download(f"{base}/a.bin", dest, reg, expected_bytes=len(payload))
    assert not now2 and rec2["sha256"] == rec["sha256"] and len(srv.hits) == n
    assert reg.is_complete(dest, verify=True) and reg.verify_all() == []
    dest.write_bytes(b"corrupt" + payload[7:])
    assert reg.verify_all() == ["x/a.bin"]


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl missing")
def test_curl_download_size_mismatch(tmp_path, http_dir):
    root, base, _ = http_dir
    (root / "b.bin").write_bytes(b"x" * 1000)
    reg = common.Registry(tmp_path / "raw")
    with pytest.raises(RuntimeError, match="size mismatch"):
        common.curl_download(f"{base}/b.bin", tmp_path / "raw" / "b.bin", reg, expected_bytes=2000)
    assert not (tmp_path / "raw" / "b.bin").exists()


def test_registry_last_record_wins(tmp_path):
    reg = common.Registry(tmp_path)
    p = tmp_path / "f.txt"
    p.write_text("one")
    reg.record(p)
    p.write_text("two!")
    r = reg.record(p)
    assert reg.get(p) == r and reg.is_complete(p, verify=True)
    assert len((tmp_path / "SHA256SUMS").read_text().splitlines()) == 1


# --------------------------------------------------------------------------------------
# sources / laz
# --------------------------------------------------------------------------------------


def test_sources_yaml_is_current():
    path = config.DATA_MANIFESTS / "sources.yaml"
    assert path.read_text() == sources.sources_yaml(), "run `make pipeline-sources`"


def test_laz_index_has_area_tiles():
    from turku3d.fetch import laz

    known = laz.known_tiles()
    for t in AREA.laz_tiles:
        for ds in config.LAZ_DATASETS:
            assert (ds, t.name) in known
    assert AREA.laz_tiles[0].url() == "https://turku.asiointi.fi/3d/pistepilvi/23459500_6704500.laz"


# --------------------------------------------------------------------------------------
# WFS: params, quadtree paging, GML parsing
# --------------------------------------------------------------------------------------

TEKLA_GML = b"""<?xml version="1.0" encoding="utf-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml"
  xmlns:kanta="http://www.kuntatietopalvelu.fi/gml/kantakartta">
 <gml:featureMember>
  <kanta:Liikennevayla>
    <kanta:kplkoodi>62520</kanta:kplkoodi>
    <kanta:sijainnit><kanta:Sijainti><kanta:reunaviiva>
      <gml:LineString><gml:coordinates>23459600.000,6704300.000 23459610.500,6704290.250</gml:coordinates></gml:LineString>
    </kanta:reunaviiva></kanta:Sijainti></kanta:sijainnit>
  </kanta:Liikennevayla>
 </gml:featureMember>
 <gml:featureMember>
  <kanta:Rakennus>
    <kanta:kplkoodi>66000</kanta:kplkoodi>
    <kanta:sijainnit><kanta:Sijainti><kanta:alue>
      <gml:Polygon><gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>23459600,6704200 23459620,6704200 23459620,6704220 23459600,6704220 23459600,6704200</gml:coordinates></gml:LinearRing></gml:outerBoundaryIs>
      <gml:innerBoundaryIs><gml:LinearRing><gml:coordinates>23459605,6704205 23459606,6704205 23459606,6704206 23459605,6704205</gml:coordinates></gml:LinearRing></gml:innerBoundaryIs></gml:Polygon>
    </kanta:alue><kanta:referenssipiste><gml:Point><gml:coordinates>23459610,6704210,12.5</gml:coordinates></gml:Point></kanta:referenssipiste></kanta:Sijainti></kanta:sijainnit>
  </kanta:Rakennus>
 </gml:featureMember>
</wfs:FeatureCollection>"""

CITYGML = b"""<?xml version="1.0" encoding="utf-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
  xmlns:app="http://www.opengis.net/citygml/appearance/2.0" numberOfFeatures="1">
 <gml:featureMember>
  <bldg:Building gml:id="Building_1">
    <gen:stringAttribute name="kerrosluku"><gen:value>3</gen:value></gen:stringAttribute>
    <app:appearance><app:Appearance><app:surfaceDataMember><app:X3DMaterial><app:target>#x</app:target></app:X3DMaterial></app:surfaceDataMember></app:Appearance></app:appearance>
    <bldg:measuredHeight uom="m">12.3</bldg:measuredHeight>
    <bldg:boundedBy><bldg:RoofSurface gml:id="Roof_1"><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember>
      <gml:Polygon gml:id="P1"><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">23459600 6704200 20 23459610 6704200 20 23459610 6704210 22 23459600 6704200 20</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
    </gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:RoofSurface></bldg:boundedBy>
    <bldg:boundedBy><bldg:WallSurface gml:id="Wall_1"><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember>
      <gml:Polygon gml:id="P2"><gml:exterior><gml:LinearRing><gml:posList>23459600 6704200 0 23459610 6704200 0 23459610 6704200 20 23459600 6704200 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
    </gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:WallSurface></bldg:boundedBy>
  </bldg:Building>
 </gml:featureMember>
</wfs:FeatureCollection>"""


def test_wfs_request_params():
    from turku3d.fetch import wfs

    L = {l.name: l for l in sources.WFS_LAYERS}
    p = wfs.request_params(L["kanta:Rakenne"], (1, 2, 3, 4))
    assert p["version"] == "1.0.0" and "<gml:coordinates>1.000,2.000 3.000,4.000" in p["filter"]
    p = wfs.request_params(L["bldg:Building_LOD2"], (1, 2, 3, 4))
    assert p["version"] == "1.1.0" and p["bbox"] == "1.000,2.000,3.000,4.000"
    assert wfs.count_features(TEKLA_GML) == 2


def test_wfs_parse_tekla():
    pytest.importorskip("shapely")
    from turku3d.fetch import wfs

    feats = list(wfs.parse_features(TEKLA_GML, "x"))
    assert len(feats) == 2
    key, gid, attrs, rows = feats[0]
    assert gid == "" and len(key) == 40 and attrs["kplkoodi"] == "62520"
    assert rows[0]["geom_type"] == "LineString" and rows[0]["geom_role"] == "reunaviiva"
    assert rows[0]["geometry"].coords[1] == (23459610.5, 6704290.25)
    _, _, attrs, rows = feats[1]
    assert [r["geom_type"] for r in rows] == ["Polygon", "Point"]
    poly = rows[0]["geometry"]
    assert len(poly.interiors) == 1 and poly.area == pytest.approx(400 - 0.5)
    assert rows[1]["geometry"].has_z and rows[1]["geom_role"] == "referenssipiste"


def test_wfs_parse_citygml():
    pytest.importorskip("shapely")
    from turku3d.fetch import wfs

    (key, gid, attrs, rows), = wfs.parse_features(CITYGML, "bldg:Building_LOD2")
    assert key == gid == "Building_1"
    assert attrs["kerrosluku"] == "3" and attrs["measuredHeight"] == "12.3"
    assert "target" not in attrs  # appearance skipped
    assert [r["surface_type"] for r in rows] == ["RoofSurface", "WallSurface"]
    assert [r["surface_id"] for r in rows] == ["P1", "P2"]
    assert rows[0]["geometry"].has_z and list(rows[0]["geometry"].exterior.coords)[2] == (23459610, 6704210, 22)


def test_wfs_to_local():
    pytest.importorskip("shapely")
    from shapely.geometry import LineString, Point

    from turku3d.fetch import wfs

    out = wfs.to_local([Point(config.OFFICE_E, config.OFFICE_N), LineString([(23459500, 6704500, 5), (23459501, 6704499, 6)])])
    assert out[0].coords[0] == (168.0, 246.0)
    assert list(out[1].coords) == [(0.0, 0.0, 5.0), (1.0, 1.0, 6.0)]


def _fake_wfs_server(cap: int, points):
    """A fake GetFeature: returns every point inside the bbox, truncated at `cap` (like the real server)."""
    calls = []

    def get(url, params, **kw):
        if "filter" in params:
            txt = params["filter"].split("<gml:coordinates>")[1].split("</gml:coordinates>")[0]
            (x0, y0), (x1, y1) = [map(float, p.split(",")) for p in txt.split()]
        else:
            x0, y0, x1, y1 = map(float, params["bbox"].split(","))
        calls.append((x0, y0, x1, y1))
        inside = [(i, p) for i, p in enumerate(points) if x0 <= p[0] <= x1 and y0 <= p[1] <= y1][:cap]
        members = "".join(
            f'<gml:featureMember><k:P xmlns:k="urn:k"><k:id>{i}</k:id><gml:Point><gml:coordinates>{x},{y}</gml:coordinates></gml:Point></k:P></gml:featureMember>'
            for i, (x, y) in inside)  # stable ids -> identical XML across pages
        body = ('<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml">'
                + members + "</wfs:FeatureCollection>").encode()
        return body, {"content-type": "text/xml"}

    return get, calls


def test_wfs_quadtree_paging_and_dedupe(tmp_path, monkeypatch):
    pytest.importorskip("geopandas")
    from turku3d.fetch import wfs

    monkeypatch.setattr(wfs, "WFS_FEATURE_CAP", 10)
    import random

    rnd = random.Random(1)
    pts = [(i, (round(rnd.uniform(0, 100), 3), round(rnd.uniform(0, 100), 3))) for i in range(57)]
    pts.append((999, (50.0, 50.0)))  # on the split lines -> returned by 4 pages, must be deduped
    get, calls = _fake_wfs_server(10, [(x, y) for _, (x, y) in pts])
    layer = sources.WfsLayer("k:P", "1.0.0", "test")
    reg = common.Registry(tmp_path)
    leaves = wfs.fetch_pages(layer, (0, 0, 100, 100), tmp_path / "pages", reg, get=get)
    assert all(wfs.count_features(p.read_bytes()) < 10 for p in leaves)
    n_calls = len(calls)
    assert n_calls > 4
    # idempotent: all pages cached -> no new requests
    leaves2 = wfs.fetch_pages(layer, (0, 0, 100, 100), tmp_path / "pages", reg, get=get)
    assert leaves2 == leaves and len(calls) == n_calls
    raw_rows = sum(len(list(wfs.parse_features(p.read_bytes(), "k:P"))) for p in leaves)
    assert raw_rows > len(pts)  # the point on the split lines really comes back in several pages
    gdf, nfeat = wfs.pages_to_frame(leaves, "k:P")
    assert nfeat == len(gdf) == len(pts)
    assert len(set(gdf.geometry.apply(lambda g: g.coords[0]))) == len(pts)
    assert gdf.geometry_local.iloc[0] is not None


def test_wfs_check_body():
    from turku3d.fetch import wfs

    with pytest.raises(RuntimeError):
        wfs.check_body(b"<ServiceExceptionReport>nope</ServiceExceptionReport>", "x")
    wfs.check_body(TEKLA_GML, "x")


# --------------------------------------------------------------------------------------
# WMS: chunk grid, GeoTIFF writing, empty chunks, idempotency
# --------------------------------------------------------------------------------------


def test_wms_chunk_grid_aligned_and_covering():
    from turku3d.fetch import wms

    bb = AREA.with_margin.to_en().as_tuple()
    ch = wms.chunk_grid(bb, 0.05)
    s = 100.0
    for key, (e0, n0, e1, n1) in ch:
        assert e1 - e0 == pytest.approx(s) and n1 - n0 == pytest.approx(s)
        assert (e0 - config.ORIGIN_E) % s == pytest.approx(0) and (config.ORIGIN_N - n1) % s == pytest.approx(0)
    assert min(c[1][0] for c in ch) <= bb[0] and max(c[1][2] for c in ch) >= bb[2]
    assert min(c[1][1] for c in ch) <= bb[1] and max(c[1][3] for c in ch) >= bb[3]
    assert len(ch) == 20  # 350 m margin box: E 12.5..362.5 -> 4 columns, N offset 75..425 -> 5 rows
    assert len({k for k, _ in ch}) == len(ch)


def test_wms_fetch_layer_fake(data_root, monkeypatch):
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    from PIL import Image

    from turku3d.fetch import wms

    if shutil.which("gdalbuildvrt") is None:
        pytest.skip("gdalbuildvrt missing")
    monkeypatch.setattr(wms, "CHUNK_PX", 64)
    calls = []

    def get(url, params, **kw):
        calls.append(params["BBOX"])
        e0 = float(params["BBOX"].split(",")[0])
        w, h = params["WIDTH"], params["HEIGHT"]
        a = np.zeros((h, w, 4), np.uint8)
        if e0 < config.ORIGIN_E + 100:  # only the westmost chunk column has data; left half opaque
            a[:, : w // 2] = (10, 200, 30, 255)
        buf = io.BytesIO()
        Image.fromarray(a, "RGBA").save(buf, "PNG")
        return buf.getvalue(), {"content-type": "image/png"}

    layer = sources.WmsLayer("Test", "test", 100 / 64, 5.0, "x", "DEFLATE", "")
    s = wms.fetch_layer(layer, AREA, workers=2, log=lambda *_: None, get=get)
    assert s["chunks"] == 20 and s["chunks_ok"] == 5 and s["chunks_empty"] == 15 and not s["errors"]
    tif = sorted((data_root / "raw" / "ortho" / "test" / AREA.name).glob("*.tif"))[0]
    with rasterio.open(tif) as ds:
        assert ds.crs.to_epsg() == 3877 and ds.res == pytest.approx((100 / 64, 100 / 64))
        assert ds.bounds.left == pytest.approx(config.ORIGIN_E)
        m = ds.dataset_mask()
        assert m[:, :32].all() and not m[:, 32:].any()
        assert tuple(ds.read()[:, 0, 0]) == (10, 200, 30)
    with rasterio.open(s["vrt"]) as v:
        assert v.width == 64 and v.height == 5 * 64
    n = len(calls)
    s2 = wms.fetch_layer(layer, AREA, workers=2, log=lambda *_: None, get=get)
    assert len(calls) == n and s2["chunks_ok"] == 5  # nothing re-requested


def test_decode_png_white_as_nodata():
    np = pytest.importorskip("numpy")
    from PIL import Image

    from turku3d.fetch import wms

    a = np.full((4, 4, 3), 255, np.uint8)
    a[0, 0] = (1, 2, 3)
    buf = io.BytesIO()
    Image.fromarray(a, "RGB").save(buf, "PNG")
    rgb, valid = wms.decode_png(buf.getvalue())
    assert valid.sum() == 1 and valid[0, 0]


# --------------------------------------------------------------------------------------
# OSM
# --------------------------------------------------------------------------------------


def test_osm_bbox_contains_area():
    from turku3d.fetch import osm

    lon0, lat0, lon1, lat1 = osm.wgs84_bbox(AREA, extra=0)
    b = AREA.with_margin.to_en()
    for e in (b.emin, b.emax):
        for n in (b.nmin, b.nmax):
            lat, lon = config.en_to_wgs84(e, n)
            assert lon0 <= lon <= lon1 and lat0 <= lat <= lat1
    lat, lon = config.OFFICE_LATLON
    assert lon0 < lon < lon1 and lat0 < lat < lat1


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium missing")
def test_osm_to_parquet_roundtrip(data_root, tmp_path):
    pytest.importorskip("geopandas")
    import subprocess

    from turku3d.fetch import osm

    lat, lon = config.OFFICE_LATLON
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
 <node id="1" version="1" timestamp="2026-01-01T00:00:00Z" lat="{lat}" lon="{lon}"><tag k="barrier" v="kerb"/></node>
 <node id="2" version="1" timestamp="2026-01-01T00:00:00Z" lat="{lat + 0.0001}" lon="{lon}"/>
 <way id="10" version="1" timestamp="2026-01-01T00:00:00Z"><nd ref="1"/><nd ref="2"/><tag k="highway" v="steps"/></way>
</osm>"""
    d = data_root / "raw" / "osm" / AREA.name
    d.mkdir(parents=True)
    src = tmp_path / "t.osm"
    src.write_text(xml)
    pbf = d / f"{AREA.name}-260101.osm.pbf"
    subprocess.run(["osmium", "cat", str(src), "-o", str(pbf), "--overwrite"], check=True)
    out, s = osm.to_parquet(pbf, log=lambda *_: None)
    import geopandas as gpd

    g = gpd.read_parquet(out)
    assert s["counts"]["barrier=kerb"] == 1 and s["counts"]["highway=steps"] == 1
    kerb = g[g.barrier == "kerb"].iloc[0]
    x, z = config.wgs84_to_local(lat, lon)  # ~ (168, 246): the office lat/lon is rounded to ~1 m
    assert kerb.geometry_local.x == pytest.approx(x, abs=0.01) and kerb.geometry_local.y == pytest.approx(z, abs=0.01)
    assert json.loads(kerb.tags) == {"barrier": "kerb"}

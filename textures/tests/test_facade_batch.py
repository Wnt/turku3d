import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from turkutex import building_jobs as jobs
from turkutex.facade_batch import asset_path, metric_size, report, validate_inventory
from turkutex.paths import streetview_dir, textures_data_dir, tools_dir
from turkutex.streetview.facades import Facade, read_footprints, save_facades
from turkutex.streetview.panos import PanoCache
from turkutex.streetview.refs import rectified_points


def inventory(tmp_path):
    fs = [
        Facade(
            "wall_a",
            np.array([0.0, 0.0]),
            np.array([40.0, 0.0]),
            5.0,
            props={
                "building_id": "building",
                "facade_index": 0,
                "street_facing": True,
                "bay_count": 11,
            },
        ),
        Facade(
            "wall_b",
            np.array([40.0, 0.0]),
            np.array([40.0, 10.0]),
            5.0,
            props={"building_id": "building", "facade_index": 1, "street_facing": True},
        ),
    ]
    path = tmp_path / "facades.geojson"
    save_facades(fs, path)
    for f in fs:
        d = tmp_path / "refs" / f.id
        d.mkdir(parents=True)
        vid = f.id + "_view"
        files = {k: vid + "_" + k + ".jpg" for k in ("crop", "outline", "rectified")}
        for name in files.values():
            Image.new("RGB", (32, 16), "#bcbaba").save(d / name)
        (d / (vid + ".json")).write_text(
            json.dumps(
                {
                    "view_id": vid,
                    "files": files,
                    "part": "full",
                    "section_m": [0, f.length],
                    "date": "2026-07",
                    "score": 0.9,
                    "source": "google",
                    "pano_id": "p",
                }
            )
        )
        (d / "refs.json").write_text(json.dumps({"views": [vid], "facade": f.to_feature()}))
    return fs, path, tmp_path / "refs"


def test_generate_jobs_use_metric_wall_and_reference_without_labels(tmp_path):
    _, path, refs = inventory(tmp_path)
    generated = jobs.generate_jobs(path, refs)
    assert [job["id"] for job in generated] == ["wall_a.generate", "wall_b.generate"]
    assert generated[0]["vars"]["width_m"] == "40.000"
    assert "11 bays" in generated[0]["vars"]["bay_instruction"]
    assert len(generated[0]["images"]) == 3  # rectified, outline, extreme-aspect guide
    assert generated[1]["meta"]["kind"] == "generate"


def test_generated_report_requires_opening_and_foreign_object_review(tmp_path):
    fs, path, refs = inventory(tmp_path)
    run, output = tmp_path / "run", tmp_path / "out"
    record = {"id": "wall_a.generate", "ok": True, "key": "k", "meta": {"kind": "generate"}}
    (run / "run_generate").mkdir(parents=True)
    (run / "run_generate/out.jsonl").write_text(json.dumps(record) + "\n")
    sidecar = asset_path(fs[0], output).with_suffix(".json")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps(
            {
                "width_m": fs[0].length,
                "height_m": fs[0].height_m,
                "references": [{"pano_id": "p"}],
                "hashes": {},
                "qa": {
                    "scale_relative_error": 0,
                    "content_aspect_error": 0,
                    "gltf_basisu_valid": True,
                },
            }
        )
    )
    rows = report(path, refs, run, output)["rows"]
    assert "generated image lacks opening and foreign-object QA" in rows[0]["problems"]


def test_one_label_per_building_geometry_overrides_photo_priors(tmp_path):
    fs, path, refs = inventory(tmp_path)
    label = jobs.label_jobs(path, refs)
    assert len(label) == 1 and len(label[0]["images"]) == 2
    assert label[0]["meta"]["facade_ids"] == ["wall_a", "wall_b"]
    output = tmp_path / "labels.jsonl"
    output.write_text(
        json.dumps(
            {
                "id": "building.label",
                "ok": True,
                "meta": label[0]["meta"],
                "answer": {
                    "building_id": "building",
                    "facades": [
                        {
                            "facade_id": f.id,
                            "bays": 99,
                            "wall_height_m": 20,
                            "views": [{"view": f.id + "_view_outline.jpg", "usable": True}],
                        }
                        for f in fs
                    ],
                },
            }
        )
        + "\n"
    )
    improve = jobs.improve_jobs(path, refs, output)
    assert len(improve) == 2
    assert improve[0]["meta"]["height_m"] == 5
    assert improve[0]["meta"]["bay_count"] == 11
    assert "exactly 11 bays" in improve[0]["vars"]["extent"]
    assert (
        improve[0]["meta"]["tile"] == "none"
    )  # Long unique walls are never replaced by a repeated bay.
    assert "WHOLE facade" in improve[0]["vars"]["extent"]
    assert "_rectified.jpg" in improve[0]["images"][0]


def test_unusable_references_not_silently_promoted(tmp_path):
    fs, path, refs = inventory(tmp_path)
    output = tmp_path / "labels.jsonl"
    output.write_text(
        json.dumps(
            {
                "id": "b.label",
                "ok": True,
                "meta": {"facade_ids": ["wall_a"]},
                "answer": {
                    "facades": [
                        {
                            "facade_id": "wall_a",
                            "views": [{"view": "wall_a_view.jpg", "usable": False}],
                        }
                    ]
                },
            }
        )
    )
    assert jobs.improve_jobs(path, refs, output) == []
    # Last record wins; a failed re-run must invalidate an older successful label.
    with output.open("a") as f:
        f.write("\n" + json.dumps({"id": "b.label", "ok": False}) + "\n")
    assert jobs.labels(output) == {}


def test_wait_for_complete_building_reference_attempts(tmp_path):
    _, path, refs = inventory(tmp_path)
    (refs / "wall_b/refs.json").unlink()
    assert jobs.label_jobs(path, refs) == []


def test_missing_results_visible_in_contact_sheet(tmp_path):
    _, path, refs = inventory(tmp_path)
    r = report(path, refs, tmp_path / "run", tmp_path / "out")
    assert r["complete"] == 0 and r["missing"] == 2
    assert all(row["reference_count"] == 1 for row in r["rows"])
    page = tmp_path / "run/contact-sheet.html"
    assert "wall_a" in page.read_text() and "no cleaned texture" in page.read_text()
    assert "data:image/jpeg;base64," in page.read_text()
    stamp = page.stat().st_mtime_ns
    report(path, refs, tmp_path / "run", tmp_path / "out")
    assert page.stat().st_mtime_ns == stamp


def test_failed_aspect_is_not_bound_and_targeted_calls_are_counted(tmp_path):
    fs, path, refs = inventory(tmp_path)
    run, output = tmp_path / "run", tmp_path / "out"
    target = asset_path(fs[0], output).with_suffix(".json")
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "width_m": fs[0].length,
                "height_m": fs[0].height_m,
                "references": [{"pano_id": "p"}],
                "hashes": {},
                "qa": {
                    "scale_relative_error": 0,
                    "content_aspect_error": 0.9,
                    "gltf_basisu_valid": True,
                },
            }
        )
    )
    attempt = {
        "thread_id": "first",
        "meta": {"kind": "improve", "facade_id": fs[0].id},
        "imagegen_calls": [{}],
    }
    for name in ("run_improve", "targeted-repair"):
        directory = run / name
        directory.mkdir(parents=True)
        (directory / "attempts.jsonl").write_text(json.dumps(attempt) + "\n")
    with (run / "targeted-repair/attempts.jsonl").open("a") as stream:
        stream.write(json.dumps({**attempt, "thread_id": "second"}) + "\n")
    result = report(path, refs, run, output)
    assert "generated facade aspect mismatch" in result["rows"][0]["problems"]
    assert "incomplete PBR map set" in result["rows"][0]["problems"]
    assert result["rows"][0]["calls"] == {"label": 0, "improve": 2, "imagegen": 2}
    binding = json.loads((target.parent / "bindings.json").read_text())
    assert binding["facades"][0]["texture"] is None


def test_inventory_rejects_unsafe_duplicate_or_missing_indices(tmp_path):
    fs, path, _ = inventory(tmp_path)
    assert len(validate_inventory(path, tmp_path / "out")) == 2
    fs[1].props["facade_index"] = 0
    save_facades(fs, path)
    with pytest.raises(ValueError, match="Duplicate building"):
        validate_inventory(path, tmp_path)
    fs[0].props["building_id"] = ".."
    with pytest.raises(ValueError, match="Unsafe"):
        asset_path(fs[0], tmp_path)
    fs[0].props["building_id"] = "ok"
    fs[0].props.pop("facade_index")
    with pytest.raises(ValueError, match="facade_index"):
        asset_path(fs[0], tmp_path)


def test_metric_scale_and_rectification_direction():
    width, height = metric_size(38.8, 5.2)
    assert 2000 <= max(width, height) <= 2048
    assert abs(width / 38.8 / (height / 5.2) - 1) < 0.01
    assert width % 4 == height % 4 == 0
    assert metric_size(0.1, 20) == (4, 800)
    corners = np.array([[0, 0, 10], [20, 0, 10], [20, 0, 20], [0, 0, 20]], float)
    points = rectified_points(corners, 200)
    assert points.shape == (100, 200, 3)
    assert points[0, 0, 0] < points[0, -1, 0]
    assert points[0, 0, 2] > points[-1, 0, 2]
    assert points[0, 0, 2] == pytest.approx(19.95)


def test_sandbox_paths_and_read_only_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.delenv("TURKUTEX_STREETVIEW", raising=False)
    shared = tmp_path / "shared/raw/streetview/google/p"
    shared.mkdir(parents=True)
    (shared / "pano_z5.jpg").write_bytes(b"cached")
    assert textures_data_dir() == tmp_path / "work/textures"
    assert tools_dir() == tmp_path / "work/tools"
    cache = PanoCache()
    assert cache.google_image("p") == shared / "pano_z5.jpg"
    assert streetview_dir() == tmp_path / "shared/cache/streetlevel"
    assert not (cache.root / "google/p/pano_z5.jpg").exists()


def test_corrupt_shared_metadata_is_repaired_only_in_sandbox(tmp_path, monkeypatch):
    from turkutex.streetview import sl
    from turkutex.streetview.panos import z17_tile

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.delenv("TURKUTEX_READ_CACHE", raising=False)
    cache = PanoCache(tmp_path / "worker", sleep=0)
    x, y = z17_tile(60.0, 22.0)
    relative = Path("coverage") / f"google_z17_{x}_{y}.json"
    shared = tmp_path / "shared/raw/streetview" / relative
    shared.parent.mkdir(parents=True)
    shared.write_text("[]trailing-corrupt-write")
    calls = []
    monkeypatch.setattr(sl.streetview, "get_coverage_tile", lambda *args: calls.append(args) or [])
    with pytest.warns(RuntimeWarning, match="invalid reference cache"):
        assert cache.google_coverage(60.0, 22.0) == []
    assert shared.read_text() == "[]trailing-corrupt-write"
    assert json.loads((cache.root / relative).read_text()) == []
    assert cache.google_coverage(60.0, 22.0) == []
    assert len(calls) == 1


def test_footprints_preserve_unclosed_last_vertex_at_large_coordinates(tmp_path):
    # Relative np.allclose at E=23 million would incorrectly consider all these vertices equal.
    source = tmp_path / "footprint.gml"
    source.write_text("""<root xmlns:k="http://www.kuntatietopalvelu.fi/gml/kantakartta"
      xmlns:gml="http://www.opengis.net/gml"><k:Rakennus><k:kplkoodi>66000</k:kplkoodi>
      <gml:Polygon><gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>
      23459500,6704500 23459510,6704500 23459510,6704510 23459500,6704510
      </gml:coordinates></gml:LinearRing></gml:outerBoundaryIs></gml:Polygon></k:Rakennus></root>""")
    assert len(read_footprints(source)[0]["ring"]) == 4


def test_refs_resume_when_only_binding_metadata_changes(tmp_path, monkeypatch):
    from turkutex.streetview import refs as module

    f = Facade("f", np.array([0.0, 0.0]), np.array([8.0, 0.0]), 4.0, props={"facade_index": 0})
    params = module.scaled_params(f, module.SelectParams())
    sources = ("google",)
    import hashlib

    key = hashlib.sha256(
        json.dumps(
            [module.REFS_VERSION, f.to_feature(), params.__dict__, sources], sort_keys=True
        ).encode()
    ).hexdigest()
    path = tmp_path / "f"
    path.mkdir()
    (path / "view.json").write_text(json.dumps({"files": {"rectified": "image.jpg"}}))
    (path / "image.jpg").write_bytes(b"image")
    (path / "refs.json").write_text(
        json.dumps(
            {"key": key, "facade": f.to_feature(), "params": params.__dict__, "views": ["view"]}
        )
    )
    f.props["facade_index"] = 5
    monkeypatch.setattr(module, "candidates", lambda *args: pytest.fail("cache miss"))
    assert module.facade_refs(None, f, tmp_path, module.SelectParams(), sources)["views"] == [
        "view"
    ]


def test_changed_geometry_cannot_reuse_old_reference(tmp_path):
    fs, path, refs = inventory(tmp_path)
    assert jobs.reference_matches(fs[0], refs)
    fs[0].height_m = 8
    save_facades(fs, path)
    assert not jobs.reference_matches(fs[0], refs)
    assert jobs.label_jobs(path, refs) == []


def test_provider_outage_keeps_other_candidates_and_logs_no_request_secrets(monkeypatch):
    from types import SimpleNamespace

    from requests.exceptions import SSLError
    from turkutex.streetview import refs as module

    monkeypatch.setattr(module, "coverage_points", lambda *args: [(60.0, 22.0)])
    facade = Facade("wall", np.array([0.0, 0.0]), np.array([8.0, 0.0]), 4.0)
    apple = SimpleNamespace(id="apple", E=4.0, N=-8.0)

    class Cache:
        def google_coverage(self, *args):
            raise SSLError("https://provider.invalid/?secret=never-log-this")

        def lookaround_coverage(self, *args):
            return [apple]

    errors = []
    assert module.candidates(
        Cache(), facade, module.SelectParams(), ("google", "lookaround"), errors
    ) == [apple]
    assert errors == [{"source": "google", "error": "SSLError"}]


def test_mapillary_authorization_header_not_url(tmp_path, monkeypatch):
    import io

    from turkutex.streetview import mapillary

    token = tmp_path / "token"
    token.write_text("test-secret")

    def open_request(req, timeout):
        assert req.get_header("Authorization") == "OAuth test-secret"
        assert "test-secret" not in req.full_url and "access_token" not in req.full_url
        assert timeout == 60
        return io.BytesIO(b'{"data": []}')

    monkeypatch.setattr(mapillary.urllib.request, "urlopen", open_request)
    assert mapillary.request("images", {"fields": "id"}, token) == {"data": []}


def test_mapillary_pagination_does_not_follow_token_bearing_url(tmp_path, monkeypatch):
    from turkutex.streetview import mapillary

    calls = []

    def request(path, params):
        calls.append(dict(params))
        if "after" not in params:
            return {
                "data": [{"id": "unsupported", "camera_type": "perspective"}],
                "paging": {
                    "next": "https://example.invalid?access_token=secret",
                    "cursors": {"after": "cursor"},
                },
            }
        return {"data": []}

    monkeypatch.setattr(mapillary, "request", request)
    cache = PanoCache(tmp_path / "cache")
    assert mapillary.coverage(cache, 60.448, 22.267) == []
    assert len(calls) == 2 and calls[1]["after"] == "cursor"
    assert mapillary.coverage(cache, 60.448, 22.267) == []
    assert len(calls) == 2


def test_metres_up_uv_maps_base_to_bottom_of_texture():
    from turkutex.facade_batch import uv_transform

    facade = Facade("wall", np.array([0.0, 0.0]), np.array([20.0, 0.0]), 10.0)
    transform = uv_transform(facade)

    def uv(u, v):
        return np.array([u, v]) * transform["scale"] + transform["offset"]

    assert np.allclose(uv(0, 0), [0, 1])
    assert np.allclose(uv(20, 10), [1, 0])
    facade.normal_side = "left"
    transform = uv_transform(facade)
    assert np.allclose(uv(0, 0), [1, 1])
    assert np.allclose(uv(20, 10), [0, 0])


def test_improve_keeps_four_views_within_image_tool_limit(tmp_path):
    fs, path, refs = inventory(tmp_path)
    fs[0].height_m = 20.0  # Ordinary 2:1 wall: no geometry guide is needed.
    save_facades(fs, path)
    d = refs / fs[0].id
    view = json.loads((d / "wall_a_view.json").read_text())
    ids = []
    for i in range(4):
        vid = f"wall_a_v{i}"
        files = {k: f"{vid}_{k}.jpg" for k in ("crop", "outline", "rectified")}
        for filename in files.values():
            Image.new("RGB", (16, 16)).save(d / filename)
        (d / f"{vid}.json").write_text(json.dumps({**view, "view_id": vid, "files": files}))
        ids.append(vid)
    (d / "refs.json").write_text(json.dumps({"facade": fs[0].to_feature(), "views": ids}))
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps(
            {
                "id": "building.label",
                "ok": True,
                "meta": {"facade_ids": ["wall_a"]},
                "answer": {
                    "facades": [
                        {
                            "facade_id": "wall_a",
                            "views": [
                                {"view": vid + "_outline.jpg", "usable": True} for vid in ids
                            ],
                        }
                    ]
                },
            }
        )
    )
    result = jobs.improve_jobs(path, refs, labels)
    assert len(result[0]["images"]) == 5
    assert len(result[0]["meta"]["views_used"]) == 4


def test_extreme_facade_uses_exact_layout_without_exceeding_five_inputs(tmp_path):
    from turkutex.post.frame import facade_content_box

    fs, path, refs = inventory(tmp_path)
    guide = jobs.layout_guide(fs[0], refs / fs[0].id)
    with Image.open(guide) as image:
        box = facade_content_box(image)
    assert (box[2] - box[0]) / (box[3] - box[1]) == pytest.approx(8.0, rel=0.01)
    stamp = guide.stat().st_mtime_ns
    assert jobs.layout_guide(fs[0], refs / fs[0].id) == guide
    assert guide.stat().st_mtime_ns == stamp

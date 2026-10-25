"""Unit tests for qa/ (no network, no Codex, no browser). Run: make qa-test"""
import importlib
import json
import math
import sys
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from PIL import Image

QA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA))

from qalib import camera, degrade, registry  # noqa: E402
from qalib.pano import equirect_pixel, perspective  # noqa: E402

POSE = {"E": 23459676.0, "N": 6704242.7, "H": 5.6, "yaw": 324.8, "pitch": 12.0, "roll": 0.0, "fov": 60.0}


@pytest.fixture
def qa_data(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_DATA", str(tmp_path / "qa"))
    return tmp_path / "qa"


# ---------------------------------------------------------------- camera
def test_basis_orthonormal_and_axes():
    for yaw, pitch, roll in [(0, 0, 0), (90, 10, 0), (324.8, -20, 15), (180, 45, -30)]:
        f, r, u = camera.basis(yaw, pitch, roll)
        m = np.stack([f, r, u])
        assert np.allclose(m @ m.T, np.eye(3), atol=1e-12)
        assert np.allclose(np.cross(r, f), u)  # right x forward = up (right-handed ENU)
    f, r, u = camera.basis(90, 0, 0)
    assert np.allclose(f, [1, 0, 0]) and np.allclose(r, [0, -1, 0]) and np.allclose(u, [0, 0, 1])


def test_project_centre_and_signs():
    w, h = 800, 600
    f, _, _ = camera.basis(POSE["yaw"], POSE["pitch"])
    p = np.array([POSE["E"], POSE["N"], POSE["H"]]) + 20 * f
    px, py = camera.project(POSE, w, h, *p)
    assert abs(px - 400) < 1e-6 and abs(py - 300) < 1e-6
    # a point straight above the axis is above the centre; behind the camera -> None
    up = {**POSE, "pitch": 0}
    f0, r0, u0 = camera.basis(up["yaw"], 0)
    q = np.array([up["E"], up["N"], up["H"]]) + 10 * f0 + 1 * u0
    assert camera.project(up, w, h, *q)[1] < 300
    assert camera.project(up, w, h, *(np.array([up["E"], up["N"], up["H"]]) - 5 * f0)) is None
    # positive roll = camera turned clockwise -> a point to the right appears higher in the image
    rolled = {**up, "roll": 10}
    q = np.array([up["E"], up["N"], up["H"]]) + 10 * f0 + 3 * r0
    assert camera.project(rolled, w, h, *q)[1] < 300


def test_fov_conversions():
    assert abs(camera.hfov(camera.vfov(60, 1600, 1200), 1600, 1200) - 60) < 1e-9
    assert abs(camera.vfov(90, 1000, 1000) - 90) < 1e-9


def test_golden_projection_is_current():
    import golden  # qa/tests/golden.py
    assert json.loads(golden.GOLDEN.read_text()) == json.loads(json.dumps(golden.build())), \
        "regenerate with: qa/.venv/bin/python qa/tests/golden.py"


# ---------------------------------------------------------------- pano -> perspective
def test_perspective_hits_marker():
    W, H = 2048, 1024
    eq = np.full((H, W, 3), 40, np.uint8)
    heading, az, el = 64.7, 324.8, 12.0
    u, v = equirect_pixel(heading, W, H, az, el)
    eq[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4] = (255, 0, 0)
    out = np.asarray(perspective(Image.fromarray(eq), heading, {**POSE, "yaw": az, "pitch": el, "fov": 20}, 200, 150))
    ys, xs = np.where((out[..., 0] > 150) & (out[..., 1] < 100))
    assert len(xs) > 0
    assert abs(xs.mean() - 100) < 2 and abs(ys.mean() - 75) < 2


def test_perspective_wraps_seam():
    W, H = 1024, 512
    eq = np.zeros((H, W, 3), np.uint8)
    eq[:, :8] = 255
    eq[:, -8:] = 255  # a vertical white line on the seam (azimuth heading+180)
    out = np.asarray(perspective(Image.fromarray(eq), 0.0, {**POSE, "yaw": 180, "pitch": 0, "fov": 30}, 64, 48))
    assert out[:, 30:34].mean() > 200 and out[:, :10].mean() < 10


# ---------------------------------------------------------------- registry
def _view(i="v1", **kw):
    v = {"id": i, "image": "data/qa/refs/x.jpg", "width": 1280, "height": 960, "pose": dict(POSE), "time": None,
         "source": {"kind": "google_streetview", "pano_id": "abc"}, "capture_date": "2019-06",
         "pose_confidence": {"level": "medium"}}
    v.update(kw)
    return v


def test_registry_upsert_and_validation(tmp_path):
    reg = tmp_path / "views.jsonl"
    registry.upsert(_view("a"), reg)
    registry.upsert(_view("b"), reg)
    registry.upsert(_view("a", width=640), reg)
    rows = registry.load(reg)
    assert [r["id"] for r in rows] == ["b", "a"] and rows[1]["width"] == 640
    with pytest.raises(jsonschema.ValidationError):
        registry.upsert(_view("c", pose={**POSE, "yaw": 400}), reg)
    with pytest.raises(jsonschema.ValidationError):
        registry.upsert(_view("d", source={"kind": "bing"}), reg)
    reg.write_text(reg.read_text() + json.dumps(_view("a")) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        registry.load(reg)


def test_committed_registry_is_valid():
    rows = registry.load()
    assert all(r["width"] > 0 for r in rows)


# ---------------------------------------------------------------- judge (no Codex)
def _strict_ok(s):
    """OpenAI structured-output strict rules: every object closes additionalProperties and requires all keys."""
    if s.get("type") == "object":
        assert s.get("additionalProperties") is False
        assert set(s["required"]) == set(s["properties"])
        for p in s["properties"].values():
            _strict_ok(p)
    if s.get("type") == "array":
        _strict_ok(s["items"])


def test_judge_schema_valid_and_strict():
    s = json.loads((QA / "judge" / "qa.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(s)
    _strict_ok(s)
    good = {"same_place": "yes", "similarity": 7.5, "summary": "x", "ignored_differences": ["cars"],
            "issues": [{"what": "w", "bbox": [0.1, 0.2, 0.3, 0.4], "category": "texture", "severity": 3, "fix_hint": "f"}]}
    jsonschema.validate(good, s)
    bad = json.loads(json.dumps(good))
    bad["issues"][0]["category"] = "colour"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, s)


def test_judge_prompt_and_quota_guard(monkeypatch, tmp_path):
    j = importlib.import_module("judge.judge")
    p = j.build_prompt("captured 2019", with_id=True)
    assert "{{" not in p and "Image 3" in p and "captured 2019" in p and "English" in p
    assert "Image 3" not in j.build_prompt()
    called = []
    monkeypatch.setattr(j.subprocess, "run", lambda *a, **k: called.append(a) or None)
    for pct in (100, None):
        monkeypatch.setattr(j, "usage_percent", lambda pct=pct: pct)
        a = tmp_path / "a.jpg"
        Image.new("RGB", (8, 8)).save(a)
        with pytest.raises(j.QuotaError):
            j.judge(a, a, cap=100)
    assert called == []  # codex never invoked
    d = j.judge(a, a, dry_run=True)
    cmd = d["cmd"]
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-6-astra"] and "read-only" in cmd and "--output-schema" in cmd
    assert cmd.index("-") < cmd.index("-i")  # prompt marker before the greedy -i list
    imgs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"]
    assert Path(imgs[0]).name.startswith("1_reference") and Path(imgs[1]).name.startswith("2_render")


def test_judge_clean_clamps_and_sorts():
    j = importlib.import_module("judge.judge")
    r = j._clean({"issues": [{"bbox": [0.5, 1.2, -0.1, 0.3], "severity": 2}, {"bbox": [0, 0, 1, 1], "severity": 5}]})
    assert r["issues"][0]["severity"] == 5
    assert r["issues"][1]["bbox"] == [0.0, 0.3, 0.5, 1.0]


# ---------------------------------------------------------------- degrade + planted matching
def test_degrade_ops_are_local():
    img = Image.fromarray(np.tile(np.linspace(0, 255, 100, dtype=np.uint8)[None, :, None], (80, 1, 3)) // 2 + 60)
    img = Image.fromarray(np.stack([np.asarray(img)[..., 0], np.asarray(img)[..., 1] // 2, np.asarray(img)[..., 2] // 3], -1))
    out, pl = degrade.recolour(img, [0.5, 0.5, 1.0, 1.0])
    a, b = np.asarray(img).astype(int), np.asarray(out).astype(int)
    assert np.abs(a[:38, :] - b[:38, :]).max() <= 1 and np.abs(a[:, :48] - b[:, :48]).max() <= 1
    assert np.abs(a[45:, 55:] - b[45:, 55:]).mean() > 10
    out, pl = degrade.grey_mask(img, [0, 0, 0.5, 0.5])
    assert (np.asarray(out)[:40, :50] == 128).all() and pl[0]["category"] == "texture"
    assert degrade.iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1 and degrade.overlap_frac([0, 0, 0.5, 1], [0, 0, 1, 1]) == 0.5


def test_match_planted():
    sys.path.insert(0, str(QA / "selftest"))
    planted = importlib.import_module("planted")
    pl = [{"what": "a", "bbox": [0, 0, 0.5, 0.5], "category": "material"},
          {"what": "b", "bbox": [0, 0, 1, 1], "category": "texture"},
          {"what": "c", "bbox": [0.8, 0.8, 1, 1], "category": "texture"}]
    issues = [{"what": "facade wrong colour", "bbox": [0.05, 0.05, 0.45, 0.5], "fix_hint": ""},
              {"what": "everything is blurry", "bbox": [0, 0, 1, 1], "fix_hint": ""}]
    hits = planted.match_planted(pl, issues)
    assert [h["caught_by"] for h in hits] == [0, 1, None]


# ---------------------------------------------------------------- back-mapping + report
def test_backmap_objects_in_bbox(tmp_path):
    from report.backmap import load_ids, objects_in_bbox
    a = np.zeros((100, 100, 3), np.uint8)
    a[:, 50:] = (0, 0, 1)
    a[60:, 50:] = (0, 1, 0)  # id 256
    Image.fromarray(a).save(tmp_path / "id.png")
    ids = load_ids(tmp_path / "id.png")
    table = {"1": {"kind": "building", "id": "LR15"}, "256": {"kind": "prop", "id": "lamp"}}
    objs = objects_in_bbox(ids, table, [0.5, 0, 1, 1])
    assert [o["id"] for o in objs] == ["LR15", "lamp"] and abs(objs[0]["frac"] - 0.6) < 1e-6
    assert objects_in_bbox(ids, table, [0, 0, 0.4, 1]) == []  # sky only


def test_build_report(qa_data):
    from report.report import build_report
    rd = qa_data / "runs" / "r1"
    (rd / "judge").mkdir(parents=True)
    img = rd / "a.png"
    Image.new("RGB", (40, 30), (100, 120, 140)).save(img)
    ida = np.zeros((30, 40, 3), np.uint8)
    ida[:, 20:] = (0, 0, 7)
    Image.fromarray(ida).save(rd / "a.id.png")
    (rd / "a.id.json").write_text(json.dumps({"7": {"kind": "building", "id": "B7", "tile": "t0_0_0"}}))
    run = {"run_id": "r1", "kind": "render", "pairs": [
        {"pair_id": "v1", "view_id": "v1", "label": "render", "ref": str(img), "render": str(img),
         "id_image": str(rd / "a.id.png"), "id_table": str(rd / "a.id.json")},
        {"pair_id": "v2", "view_id": "v2", "label": "render", "ref": str(img), "render": str(img)}]}
    (rd / "run.json").write_text(json.dumps(run))
    res = {"same_place": "yes", "similarity": 6, "summary": "s", "ignored_differences": [],
           "issues": [{"what": "flat wall", "bbox": [0.6, 0.1, 0.9, 0.8], "category": "texture", "severity": 4, "fix_hint": "h"}]}
    (rd / "judge" / "v1.json").write_text(json.dumps({"ok": True, "result": res, "attempts": [{"thread_id": "t"}], "secs": 1,
                                                      "usage_before_pct": 0, "usage_after_pct": 0}))
    out = build_report(rd)
    html = out.read_text()
    assert "flat wall" in html and 'class="box"' in html and "not judged" in html and "<svg" in html
    tickets = [json.loads(x) for x in (rd / "issues.jsonl").read_text().splitlines()]
    assert len(tickets) == 1 and tickets[0]["objects"][0]["id"] == "B7"
    build_report(rd)  # idempotent: history/issues de-duplicated by run id
    assert len((qa_data / "history.jsonl").read_text().splitlines()) == 1
    assert len((qa_data / "issues.jsonl").read_text().splitlines()) == 1


# ---------------------------------------------------------------- 360tour adapter
def test_tour360_adapter(qa_data, tmp_path):
    t = importlib.import_module("views.tour360_adapter")
    root = tmp_path / "360tour"
    d = root / "panorama_ABC_1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": "panorama_ABC_1", "label": "Test pano", "lat": 60.4472217,
                                             "lon": 22.2677783, "gps_alt_m": 70.5, "date": "2016:05:12 20:16:32", "kind": "aerial"}))
    Image.new("RGB", (256, 128), (10, 200, 10)).save(d / "equirect_256.jpg")
    reg = tmp_path / "v.jsonl"
    with pytest.raises(SystemExit, match="heading offset"):
        t.make("ABC", 0, 0, 60, (64, 48), None, None, None, root, reg)
    v = t.make("ABC", -12, -25, 60, (64, 48), "tv", 5.0, None, root, reg)
    assert v["pose"]["yaw"] == 353.0 and v["pose"]["H"] == 70.5 and v["time"] == "2016-05-12T20:16:32+03:00"
    assert abs(camera.hfov(v["pose"]["fov"], 64, 48) - 60) < 1e-3
    assert registry.load(reg)[0]["source"]["kind"] == "360tour"
    assert t.find_panos(tmp_path / "missing") == []


def test_geo_reuses_pipeline_origin():
    from qalib import geo
    assert (geo.ORIGIN_E, geo.ORIGIN_N) == (23459500.0, 6704500.0)
    assert geo.en_to_engine(23459668, 6704254, 7) == (168, 7, 246)
    lat, lon = geo.en_to_wgs84(geo.OFFICE_E, geo.OFFICE_N)
    assert abs(lat - 60.44839) < 2e-4 and abs(lon - 22.26726) < 2e-4
    assert abs(geo.bearing(0, 0, 1, 1) - 45) < 1e-9 and math.isclose(geo.bearing(0, 0, -1, 0), 270)


def test_true_heading_is_converted_to_grid_bearing():
    """Street View headings are true azimuths; at Turku true north has grid bearing +0.64 deg (EPSG:3877)."""
    from qalib import geo

    g = geo.true_to_grid_bearing(geo.OFFICE_E, geo.OFFICE_N, 90.0)
    assert abs(g - 90.637) < 0.01

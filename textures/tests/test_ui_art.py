import json

import pytest
from PIL import Image
from turkutex import ui_art


def test_jobs_cover_every_art_entry_with_one_image(tmp_path):
    path = tmp_path / "jobs.jsonl"
    ui_art.write_jobs(path)
    jobs = [json.loads(line) for line in path.read_text().splitlines()]
    assert [j["id"] for j in jobs] == [a.name for a in ui_art.ART]
    assert all(j["expect_images"] == 1 and "no text" in j["prompt"].lower() for j in jobs)


def test_fit_centre_crops_to_the_target_aspect():
    wide = Image.new("RGB", (400, 100), "red")
    wide.paste(Image.new("RGB", (100, 100), "blue"), (150, 0))
    icon = ui_art.fit(wide, (32, 32))
    assert icon.size == (32, 32) and icon.getpixel((16, 16)) == (0, 0, 255)
    assert ui_art.fit(Image.new("RGB", (1672, 941)), (1280, 720)).size == (1280, 720)


def test_encode_writes_webp_under_the_budget_from_primary_images(tmp_path):
    run = tmp_path / "run"
    records = []
    for art in ui_art.ART:
        png = run / "images" / art.name / "00.png"
        png.parent.mkdir(parents=True)
        Image.linear_gradient("L").resize((300, 200)).convert("RGB").save(png)
        records.append(
            {"id": art.name, "ok": True, "images": [{"path": str(png)}], "primary": str(png)}
        )
    (run / "out.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    sizes = ui_art.encode(run, tmp_path / "out")
    for art in ui_art.ART:
        with Image.open(tmp_path / "out" / f"{art.name}.webp") as image:
            assert image.format == "WEBP" and image.size == ui_art.SIZES[art.kind]
        assert sizes[art.name] <= ui_art.MAX_BYTES


def test_encode_refuses_a_run_with_missing_images(tmp_path):
    (tmp_path / "out.jsonl").write_text(
        json.dumps({"id": "bg_market", "ok": False, "images": []}) + "\n"
    )
    with pytest.raises(SystemExit, match="no image for bg_market"):
        ui_art.encode(tmp_path, tmp_path / "out")

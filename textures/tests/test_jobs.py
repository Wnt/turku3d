import json
from pathlib import Path

from turkutex.jobs import improve_jobs, label_jobs
from turkutex.streetview.facades import Facade, save_facades


def make_refs(tmp: Path):
    f1 = Facade(
        "long",
        __import__("numpy").array([0.0, 0.0]),
        __import__("numpy").array([40.0, 0.0]),
        5.0,
        props={"address": "A 1", "floors": 1, "material": "puu", "year": "1809"},
    )
    f2 = Facade(
        "short",
        __import__("numpy").array([0.0, 50.0]),
        __import__("numpy").array([20.0, 50.0]),
        11.0,
        props={"address": "B 2", "floors": 2, "material": None, "year": "1811"},
    )
    save_facades([f1, f2], tmp / "f.geojson")
    for fid in ("long", "short"):
        d = tmp / "refs" / fid
        d.mkdir(parents=True)
        views = []
        for k, part in enumerate(("full", "partial")):
            vid = f"v{k}_{part[0]}_g_x"
            views.append(vid)
            for suf in (".jpg", "_outline.jpg"):
                (d / f"{vid}{suf}").write_bytes(f"{fid}{vid}{suf}".encode())
            (d / f"{vid}.json").write_text(
                json.dumps(
                    {
                        "view_id": vid,
                        "part": part,
                        "distance_m": 10.0,
                        "view_angle_deg": 5.0,
                        "date": "2026-07",
                        "score": 0.5 - k * 0.1,
                        "files": {"crop": f"{vid}.jpg", "outline": f"{vid}_outline.jpg"},
                    }
                )
            )
        (d / "refs.json").write_text(json.dumps({"views": views}))
    return tmp / "f.geojson", tmp / "refs"


def test_label_and_improve_jobs(tmp_path):
    fac, refs = make_refs(tmp_path)
    lj = label_jobs(fac, refs)
    assert [j["id"] for j in lj] == ["long.label", "short.label"]
    assert all(
        j["schema"].endswith("facade_label.schema.json") and len(j["images"]) == 2 for j in lj
    )
    out = tmp_path / "out.jsonl"
    rows = []
    for fid, bays in (("long", 14), ("short", 7)):
        rows.append(
            {
                "ok": True,
                "meta": {"facade_id": fid},
                "answer": {
                    "views": [
                        {"view": "v0_f_g_x_outline.jpg", "usable": fid == "short", "issue": "none"},
                        {"view": "v1_p_g_x.jpg", "usable": True, "issue": "none"},
                    ],
                    "bays": bays,
                    "wall_height_m": 5.5 if fid == "long" else 11.5,
                },
            }
        )
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ij = {j["meta"]["facade_id"]: j for j in improve_jobs(fac, refs, out)}
    assert ij["long"]["meta"]["mode"] == "strip" and ij["long"]["meta"]["tile"] == "x"
    assert ij["long"]["meta"]["views_used"] == ["v1_p_g_x"]  # unusable full view dropped
    assert ij["short"]["meta"]["mode"] == "whole" and ij["short"]["meta"]["tile"] == "none"
    assert ij["short"]["images"][-1].endswith("v0_f_g_x_outline.jpg")  # best outline appended
    assert "REPEATABLE SECTION" in ij["long"]["vars"]["extent"] and ij["long"]["expect_images"] == 1

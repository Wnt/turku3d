import json

import numpy as np
from PIL import Image
from turkutex import facade_candidates as fc
from turkutex.post.pipeline import sha
from turkutex.streetview.facades import Facade, save_facades

PASS = {
    "facade_id": "wall_a",
    "storeys": 3,
    "expected_storeys": 3,
    "upper_openings": 8,
    "expected_upper_openings": 8,
    "grid_matches": True,
    "orthographic": True,
    "foreign_objects": [],
    "clean": True,
    "notes": "Matches the references.",
}


def run_dir(tmp_path):
    """A synthetic rollout run: one wall facing south at the office, its generate record."""
    facades = tmp_path / "facades.geojson"
    wall = Facade(
        "wall_a",
        np.array([23459670.0, 6704260.0]),
        np.array([23459690.0, 6704260.0]),
        12.0,
        props={"building_id": "b1", "street_facing": True},
    )
    save_facades([wall], facades)
    run = tmp_path / "run"
    (run / "generated").mkdir(parents=True)
    (run / "run_visualqa").mkdir()
    (run / "run_generate").mkdir()
    return run, facades


def generate(run, key, feedback=None):
    record = {
        "id": "wall_a.generate",
        "ok": True,
        "key": key,
        "model": "gpt-6-luna",
        "effort": "low",
        "meta": {
            "kind": "generate",
            "facade_id": "wall_a",
            "width_m": 20.0,
            "height_m": 12.0,
            "views_used": ["v1"],
            "feedback": feedback,
            "prompt_summary": "20.00 x 12.00 m, 1.6667:1. Three storeys.",
        },
        "imagegen_calls": [{"prompt": "An orthographic elevation " + "x" * 3000}],
    }
    with (run / "run_generate/out.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")


def review(run, colour, key, answer, reviewed=None):
    """Write a generated wall and the review of `reviewed` bytes (default: that wall)."""
    image = run / "generated/wall_a.png"
    Image.new("RGB", (50, 30), colour).save(image)
    record = {
        "id": "wall_a.visualqa",
        "ok": True,
        "model": "gpt-6-astra",
        "thread_id": "t-" + key,
        "inputs": [
            {"name": "wall_a.png", "sha256": reviewed or sha(image)},
            {"name": "ref.jpg", "sha256": "r"},
        ],
        "meta": {"kind": "generated_visual_qa", "facade_id": "wall_a", "generate_key": key},
        "answer": answer,
    }
    with (run / "run_visualqa/out.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    return image


def test_every_reviewed_candidate_is_kept_with_its_verdict_and_position(tmp_path):
    run, facades = run_dir(tmp_path)
    dest = tmp_path / "candidates"
    generate(run, "g1")
    first = review(
        run, "#a05030", "g1", {**PASS, "storeys": 4, "notes": "One storey too many."}
    ).read_bytes()
    [kept] = fc.keep(run, facades, "task-1", dest)
    wall = dest / "task-1/wall_a"
    assert (wall / "1.png").read_bytes() == first
    record = json.loads((wall / "1.json").read_text())
    assert record == kept
    assert not record["accepted"]
    assert record["review"]["problems"] == ["storey count mismatch"]
    assert record["review"]["answer"]["notes"] == "One storey too many."
    assert record["generation"]["model"] == "gpt-6-luna"
    assert record["generation"]["prompt_summary"].endswith("Three storeys.")
    assert len(record["generation"]["image_prompt"]) == fc.PROMPT_CHARS
    # The wall faces south: the spawn is 8 m south of its centre, looking north.
    assert record["position"] == {
        "E": 23459680.0,
        "N": 6704260.0,
        "spawn": [23459680.0, 6704252.0, 0.0],
        "tile": "t0_0_0",
    }

    # Rerunning the stage on the same bytes adds nothing.
    assert len(fc.keep(run, facades, "task-1", dest)) == 1
    assert sorted(p.name for p in wall.iterdir()) == ["1.json", "1.png"]

    # The retry overwrites generated/wall_a.png; both candidates stay.
    generate(run, "g2", feedback="One storey too many.")
    second = review(run, "#c0c0b0", "g2", PASS)
    fc.keep(run, facades, "task-1", dest)
    assert (wall / "1.png").read_bytes() == first
    assert (wall / "2.png").read_bytes() == second.read_bytes()
    index = json.loads((dest / "task-1/index.json").read_text())
    assert index["counts"] == {"walls": 1, "candidates": 2, "accepted": 1, "rejected": 1}
    [entry] = index["walls"]
    assert entry["accepted"] and entry["building_id"] == "b1"
    assert [c["accepted"] for c in entry["candidates"]] == [False, True]
    assert entry["candidates"][1]["generation"]["retry_feedback"] == "One storey too many."

    # The acceptance audit can still reject the reviewer's pick.
    rows = [
        {"facade_id": "wall_a", "sha256": sha(second), "accepted": False, "problems": ["aspect"]}
    ]
    assert fc.record_audit(dest / "task-1", rows) == 1
    index = json.loads((dest / "task-1/index.json").read_text())
    assert index["counts"]["accepted"] == 0
    assert index["walls"][0]["candidates"][1]["audit"] == {
        "accepted": False,
        "problems": ["aspect"],
    }


def test_a_review_of_other_bytes_or_a_failed_review_keeps_nothing(tmp_path):
    run, facades = run_dir(tmp_path)
    dest = tmp_path / "candidates"
    generate(run, "g1")
    review(run, "#a05030", "g1", PASS, reviewed="0" * 64)
    assert fc.keep(run, facades, "t", dest) == []
    review(run, "#a05030", "g1", PASS)
    with (run / "run_visualqa/out.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "id": "wall_a.visualqa",
                    "ok": False,
                    "meta": {"kind": "generated_visual_qa", "facade_id": "wall_a"},
                }
            )
            + "\n"
        )
    assert fc.keep(run, facades, "t", dest) == []
    assert not (dest / "t").exists()


def test_cli_keeps_under_the_data_root_for_the_sandbox_task(tmp_path, monkeypatch):
    run, facades = run_dir(tmp_path)
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_SANDBOX", "content-facades-x")
    generate(run, "g1")
    review(run, "#a05030", "g1", PASS)
    assert fc.main(["keep", "--run", str(run), "--facades", str(facades)]) == 0
    index = tmp_path / "data/facade-approvals/candidates/content-facades-x/index.json"
    assert json.loads(index.read_text())["counts"]["accepted"] == 1

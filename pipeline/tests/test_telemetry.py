import json
import time

import pytest
from turku3d.telemetry import priority as p


def write_session(directory, name, samples):
    (directory / name).write_text("".join(json.dumps(s) + "\n" for s in samples) + '{"torn": ')


def sample(t, facades=(), outside=None):
    s = {
        "session": "s1",
        "t": t,
        "E": 23459668.0,
        "N": 6704254.0,
        "H": 2.0,
        "yaw": 90.0,
        "pitch": -5.0,
        "vehicle": None,
        "speed": 1.4,
        "tile": "t0_0_0",
        "facades": [
            {"facade_id": f, "screen_fraction": frac, "distance_m": d, "angle_deg": 10.0}
            for f, frac, d in facades
        ],
    }
    if outside:
        s["outside_tiles"] = outside
    return s


@pytest.fixture
def assembly(tmp_path):
    root = tmp_path / "assembly"
    bound = {
        "GEN_1": "generate",
        "GEN_2": "derived",
        "NB_4": "derived-neighbour",
        "DER_3": "derived",
        "FAST_0": "fast",
        "NB_5": "fast",
    }
    for facade_id, mode in bound.items():
        building, index = facade_id.rsplit("_", 1)
        (root / "facades" / building).mkdir(parents=True, exist_ok=True)
        (root / "facades" / building / f"{index}.json").write_text(
            json.dumps({"source_mode": mode})
        )
    (root / "facades/GEN/style.json").write_text("{}")
    (root / "MANIFEST.json").write_text(json.dumps({"bound": list(bound), "library": []}))
    return root


def test_status_from_the_approval_assembly(assembly):
    assert p.building_status(assembly) == {
        "GEN": "generated",
        "NB": "neighbour",
        "DER": "derived-only",
        "FAST": "fast",
    }
    assert p.building_status(assembly.parent / "missing") == {}


def test_scores_walls_buildings_and_missing_tiles(tmp_path, assembly):
    sessions = tmp_path / "telemetry"
    sessions.mkdir()
    now = time.time()
    write_session(sessions, "session-20261026.jsonl", [sample(now - 86400, [("OLD_0", 0.5, 10)])])
    write_session(
        sessions,
        "session-20261027.jsonl",
        [
            # Close and large: the distance factor is capped at 3; far walls floor at 0.2.
            sample(now - 2, [("GEN_1", 0.1, 10), ("NB_4", 0.2, 600), ("UNB_7", 0.05, 30)]),
            sample(
                now - 1,
                [("GEN_1", 0.2, 20), ("GEN_9", 0.01, 60)],
                {"stand": "t0_2_0", "ahead": ["t0_3_0", "t0_2_-1"]},
            ),
            sample(now, [], {"stand": "t0_2_0", "ahead": ["t0_3_0"]}),
        ],
    )
    names, samples = p.read_sessions(sessions, now - 3600)
    assert names == ["session-20261027.jsonl"] and len(samples) == 3
    result = p.prioritize(names, samples, p.building_status(assembly), "2026-10-27T12:00:00+00:00")
    assert result["sample_count"] == 3
    buildings = {b["building_id"]: b for b in result["buildings"]}
    assert [b["building_id"] for b in result["buildings"]] == ["GEN", "UNB", "NB"]
    gen = buildings["GEN"]
    assert gen["status"] == "generated" and gen["best_wall"] == "GEN_1"
    assert gen["score"] == pytest.approx(0.1 * 3 + 0.2 * 3 + 0.01 * 1)
    assert gen["walls"][0] == {
        "facade_id": "GEN_1",
        "score": 0.9,
        "seen_seconds": 2,
        "mean_distance_m": 15.0,
        "max_screen_fraction": 0.2,
    }
    assert (
        buildings["NB"]["score"] == pytest.approx(0.2 * 0.2)
        and buildings["NB"]["status"] == "neighbour"
    )
    assert buildings["UNB"]["status"] == "unbound" and buildings["UNB"]["score"] == pytest.approx(
        0.1
    )
    assert result["missing_tiles"] == [
        {
            "tile": "t0_2_0",
            "score": 2.0,
            "stood_seconds": 2,
            "looked_seconds": 0,
            "bbox_en": [23460000.0, 6704250.0, 23460250.0, 6704500.0],
        },
        {
            "tile": "t0_3_0",
            "score": 1.0,
            "stood_seconds": 0,
            "looked_seconds": 2,
            "bbox_en": [23460250.0, 6704250.0, 23460500.0, 6704500.0],
        },
        {
            "tile": "t0_2_-1",
            "score": 0.5,
            "stood_seconds": 0,
            "looked_seconds": 1,
            "bbox_en": [23460000.0, 6704500.0, 23460250.0, 6704750.0],
        },
    ]
    assert set(result) == {"generated_at", "sessions", "sample_count", "buildings", "missing_tiles"}


def test_cli_writes_json_and_markdown(tmp_path, assembly):
    sessions = tmp_path / "telemetry"
    sessions.mkdir()
    write_session(
        sessions,
        "session-20261027.jsonl",
        [sample(time.time(), [("GEN_1", 0.1, 10)], {"stand": "t0_-1_0", "ahead": []})],
    )
    out = tmp_path / "out/priority.json"
    assert (
        p.main(["--sessions", str(sessions), "--out", str(out), "--assembly", str(assembly)]) == 0
    )
    data = json.loads(out.read_text())
    assert data["sessions"] == ["session-20261027.jsonl"]
    assert data["buildings"][0]["building_id"] == "GEN"
    md = (tmp_path / "out/priority.md").read_text()
    assert "| 1 | `GEN` | generated | 0.300 | `GEN_1` | 1 | 1 | 10.0 |" in md
    assert "| `t0_-1_0` | 1.0 | 1 | 0 | 23459250, 6704250, 23459500, 6704500 |" in md
    assert p.main(["--sessions", str(tmp_path / "nope"), "--assembly", str(assembly)]) == 2

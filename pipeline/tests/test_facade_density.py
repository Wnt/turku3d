import json

import pytest
from turku3d.package.assemble import facade_density_floor, facade_source_density, halve_densities


def test_optional_facade_density_floor_is_validated(tmp_path):
    wall = tmp_path / "facades/b"
    wall.mkdir(parents=True)
    meta = wall / "0.json"
    meta.write_text(json.dumps({"min_pixels_per_metre": 32}))
    assert facade_density_floor(tmp_path / "facades", "b_0") == 32
    meta.write_text(json.dumps({"min_pixels_per_metre": 2}))
    with pytest.raises(ValueError, match="invalid minimum facade density"):
        facade_density_floor(tmp_path / "facades", "b_0")


def test_density_ladder_steps_every_far_facade_down_before_any_twice():
    ranked = ["far", "mid", "near", "showcase"]  # farthest from spawn first
    floors = {"far": 0, "mid": 0, "near": 0, "showcase": 16}
    plan: dict[str, int] = {}
    assert halve_densities(plan, ranked, floors, 2) == [("far", 64, 16), ("mid", 64, 16)]
    assert halve_densities(plan, ranked, floors, 3) == [
        ("near", 64, 16),
        ("showcase", 64, 16),
        ("far", 16, 8),
    ]
    halve_densities(plan, ranked, floors, 100)
    assert plan == {"far": 1, "mid": 1, "near": 1, "showcase": 16}  # stops at its minimum
    assert halve_densities(plan, ranked, floors, 1) == []
    assert halve_densities(plan, ranked, floors, 1, below_floors=True) == [("showcase", 16, 8)]


def test_source_density_reads_the_sidecar(tmp_path):
    wall = tmp_path / "facades/b"
    wall.mkdir(parents=True)
    (wall / "3.json").write_text(json.dumps({"pixels_per_metre": [31.8, 32.1]}))
    assert facade_source_density(tmp_path / "facades", "b_3") == 31.8

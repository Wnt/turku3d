import json

import numpy as np
import pytest
from turkutex import facade_fast as ff
from turkutex import facade_fast_render as fr
from turkutex.facade_batch import metric_size
from turkutex.streetview.facades import load_facades

E0, N0 = 23459700.0, 6704250.0


def description(**overrides) -> dict:
    base = {
        "facade_id": "b1_0",
        "wall_visible": True,
        "problem": "",
        "material": "plaster",
        "wall_colour": "#d8bf7a",
        "trim_colour": "#ece6da",
        "storeys": 3,
        "window_columns": 4,
        "window_width": 0.45,
        "window_height": 0.55,
        "ground_floor": "shops",
        "ground_openings": 2,
        "roof_line": "cornice",
        "plinth": True,
        "features": ["string_courses"],
    }
    return {**base, **overrides}


def test_validation_rejects_bad_colours_ids_and_values():
    assert fr.validate(description(), "b1_0") == []
    assert fr.validate(description(wall_colour="yellow"), "b1_0")
    assert fr.validate(description(material="marble"), "b1_0")
    assert fr.validate(description(), "b1_1") == ["answer is for b1_0, not b1_1"]
    assert fr.validate(description(features=["arcade", "arcade"]), "b1_0") == ["duplicate features"]
    extra = description()
    extra["colour_name"] = "ochre"
    assert fr.validate(extra, "b1_0")


def test_render_is_deterministic_and_follows_the_layout():
    size = metric_size(11.2, 10.4, 16)
    first = fr.render(description(), 11.2, 10.4, size, "b1_0")
    again = fr.render(description(), 11.2, 10.4, size, "b1_0")
    for kind in ("albedo", "normal", "orm"):
        assert first[kind].shape[:2] == (size[1], size[0])
        assert np.array_equal(first[kind], again[kind])
    plan = first["layout"]
    assert plan["storeys"] == 3 and plan["columns"] == 4 and plan["bay_m"] == pytest.approx(2.8)
    # Glass (low roughness) covers roughly the drawn openings: 8 upper windows plus 2 shops.
    rough = first["orm"][..., 1] / 255
    ww, wh = plan["window_m"]
    expected = 8 * (ww - 0.14) * (wh - 0.14) * 256
    assert 0.5 * expected < (rough < 0.2).sum() < 2.5 * expected + 2 * 4.5 * 3.2 * 256
    # Wall colour dominates the plain wall between windows.
    middle = first["albedo"][size[1] // 2 - 2 : size[1] // 2 + 2, 2:6].reshape(-1, 3).mean(0)
    assert np.abs(middle - np.array([216, 191, 122])).max() < 40
    assert not np.array_equal(
        fr.render(description(), 11.2, 10.4, size, "b1_1")["albedo"], first["albedo"]
    )


def test_misread_counts_are_replaced_by_plausible_ones():
    plan = fr.layout(description(storeys=12, window_columns=30), 9.0, 10.0)
    assert plan["storeys"] == 3 and plan["columns"] == round(9.0 / 2.8)
    blank = fr.layout(description(storeys=1, window_columns=0, ground_floor="plain"), 6.0, 4.0)
    assert blank["storeys"] == 1 and blank["columns"] == 0


def test_register_description_is_valid_and_stable():
    attrs = {"material_class": "wood", "era": "1850-1919", "levels": 2}
    first = fr.register_description("b9_3", "b9", attrs, 12.0, 6.5)
    assert fr.validate(first, "b9_3") == []
    assert first == fr.register_description("b9_3", "b9", attrs, 12.0, 6.5)
    assert first["material"] == "wood" and first["storeys"] == 2 and first["window_columns"] == 5


def wall(building, index, e0, width, height, street=True):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[e0, N0], [e0 + width, N0]]},
        "properties": {
            "building_id": building,
            "facade_index": index,
            "base_height": 2.0,
            "top_height": 2.0 + height,
            "normal": [0.0, 0.0, 1.0],
            "street_facing": street,
            "width_m": width,
            "material_key": "facade_plaster",
        },
    }


def test_descriptions_fall_back_to_the_building_then_the_register(tmp_path):
    inventory = tmp_path / "facades.geojson"
    features = [
        wall("b1", 0, E0, 10.0, 9.0),
        wall("b1", 1, E0 + 20, 5.0, 9.0),
        wall("b2", 0, E0 + 40, 6.0, 9.0),
    ]
    inventory.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    facades = load_facades(inventory)
    run = tmp_path / "run"
    (run / "run_describe").mkdir(parents=True)
    hidden = description(facade_id="b1_1", wall_visible=False, problem="foliage")
    records = [
        {
            "id": "b1_0.fast",
            "ok": True,
            "answer": description(),
            "model": "m",
            "effort": "low",
            "key": "k",
            "meta": {"reference": {"source": "lookaround", "view_id": "v"}},
        },
        {
            "id": "b1_1.fast",
            "ok": True,
            "answer": hidden,
            "model": "m",
            "effort": "low",
            "key": "k2",
            "meta": {"reference": {}},
        },
    ]
    (run / "run_describe/out.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    chosen = ff.descriptions(run, facades, {"b2": {"material_class": "brick", "era": "1960+"}})
    assert chosen["b1_0"]["source"] == "reference"
    assert chosen["b1_1"]["source"] == "building" and chosen["b1_1"]["adopted_from"] == "b1_0"
    assert chosen["b1_1"]["description"]["window_columns"] == 2  # same 2.5 m spacing
    assert "foliage" in chosen["b1_1"]["reason"]
    assert chosen["b2_0"]["source"] == "register"
    assert chosen["b2_0"]["description"]["material"] == "brick"


def test_plan_takes_street_walls_of_the_tiles_without_a_better_map(tmp_path):
    from turkutex.post.pipeline import sha

    assembly = tmp_path / "assembly"
    features = [
        wall("b1", 0, E0, 8.0, 9.0),
        wall("b1", 1, E0 + 10, 8.0, 9.0),
        wall("b1", 2, E0 + 20, 8.0, 9.0),
        wall("b1", 3, E0 + 30, 8.0, 9.0, street=False),
    ]
    inventory = assembly / "APPROVED_INVENTORY.geojson"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    for index, mode in ((1, "derived"), (2, "fast")):
        sidecar = assembly / f"facades/b1/{index}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"source_mode": mode}))
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"inventory_sha256": sha(inventory), "bound": ["b1_1", "b1_2"], "library": []})
    )
    tile = ff.tile_of(load_facades(inventory)[0])
    plan = ff.plan_walls(inventory, assembly, [tile])
    assert [(w["facade_id"], w["current"]) for w in plan["walls"]] == [
        ("b1_0", "none"),
        ("b1_2", "fast"),
    ]
    assert plan["kept"] == {"b1_1": "derived"}


@pytest.mark.skipif(
    __import__("turkutex.post.pipeline", fromlist=["x"]).native_encoder() is None,
    reason="run make textures-ktx",
)
def test_publish_binds_fast_maps_but_never_over_a_generated_or_derived_one(tmp_path):
    from turkutex.post.pipeline import sha

    assembly = tmp_path / "assembly"
    features = [wall("b1", 0, E0, 2.0, 3.0), wall("b1", 1, E0 + 10, 2.0, 3.0)]
    inventory = assembly / "APPROVED_INVENTORY.geojson"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"inventory_sha256": sha(inventory), "bound": [], "library": []})
    )
    run, staging = tmp_path / "run", tmp_path / "staging"
    tile = ff.tile_of(load_facades(inventory)[0])
    common = ["--run", str(run), "--inventory", str(inventory)]
    assert ff.main(["plan", *common, "--assembly", str(assembly), "--tiles", tile]) == 0
    # No describe records: both walls take the register description (no call).
    assert ff.main(["render", *common, "--staging", str(staging), "-j", "2"]) == 0
    sidecar = json.loads((staging / "facades/b1/0.json").read_text())
    assert sidecar["source_mode"] == "fast" and sidecar["references"] == []
    assert sidecar["fast_description"]["source"] == "register"
    assert sidecar["min_pixels_per_metre"] == 0 and sidecar["qa"]["gltf_basisu_valid"]
    # Meanwhile the slow path bound a derived map for b1_1.
    derived = assembly / "facades/b1/1.json"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text(json.dumps({"source_mode": "derived"}))
    manifest = json.loads((assembly / "MANIFEST.json").read_text())
    (assembly / "MANIFEST.json").write_text(json.dumps({**manifest, "bound": ["b1_1"]}))
    assert (
        ff.main(["publish", *common, "--staging", str(staging), "--assembly", str(assembly)]) == 0
    )
    manifest = json.loads((assembly / "MANIFEST.json").read_text())
    assert manifest["bound"] == ["b1_0", "b1_1"]
    assert json.loads(derived.read_text()) == {"source_mode": "derived"}
    published = json.loads((assembly / "facades/b1/0.json").read_text())
    assert published["source_mode"] == "fast"
    for name, digest in published["hashes"].items():
        assert sha(assembly / "facades/b1" / name) == digest
    # A rerun with unchanged descriptions re-encodes nothing.
    assert ff.render_all(run, inventory, staging, None, ff.PPM, 2)["cached"] == 2


def test_inventory_growth_preserves_existing_geometry_bindings_and_hash(tmp_path):
    from turkutex.export import export_lock
    from turkutex.post.pipeline import sha

    assembly = tmp_path / "assembly"
    assembly.mkdir()
    approved = assembly / "APPROVED_INVENTORY.geojson"
    original = wall("b1", 0, E0, 8.0, 9.0)
    approved.write_text(json.dumps({"type": "FeatureCollection", "features": [original]}))
    manifest = assembly / "MANIFEST.json"
    manifest.write_text(
        json.dumps({"inventory_sha256": sha(approved), "bound": ["b1_0"], "library": ["plaster"]})
    )
    source = tmp_path / "source.geojson"
    new = wall("b2", 0, E0 + 30, 6.0, 9.0)
    unrelated = wall("b3", 0, E0 + 60, 6.0, 9.0)
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    wall("b1", 0, E0, 12.0, 9.0),
                    new,
                    unrelated,
                ],
            }
        )
    )
    with export_lock(assembly):
        assert ff.extend_inventory(source, assembly, {"b1_0", "b2_0"}) == 1
        assert ff.extend_inventory(source, assembly, {"b1_0", "b2_0"}) == 0
    assert json.loads(approved.read_text())["features"] == [original, new]
    assert json.loads(manifest.read_text()) == {
        "inventory_sha256": sha(approved),
        "bound": ["b1_0"],
        "library": ["plaster"],
    }


def test_register_tile_inventory_without_staging(tmp_path):
    from turkutex.post.pipeline import sha

    source = tmp_path / "facades.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    wall("b1", 0, E0, 8.0, 9.0),
                    wall("b2", 0, E0 + 300, 8.0, 9.0),
                ],
            }
        )
    )
    assembly = tmp_path / "assembly"
    args = [
        "inventory",
        "--run",
        str(tmp_path / "unused"),
        "--inventory",
        str(source),
        "--assembly",
        str(assembly),
        "--tiles",
        ff.tile_of(load_facades(source)[0]),
    ]
    assert ff.main(args) == 0
    assert ff.main(args) == 0
    approved = assembly / "APPROVED_INVENTORY.geojson"
    assert [f.id for f in load_facades(approved)] == ["b1_0"]
    assert json.loads((assembly / "MANIFEST.json").read_text()) == {
        "inventory_sha256": sha(approved),
        "bound": [],
        "library": [],
    }

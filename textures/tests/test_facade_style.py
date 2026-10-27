import json

import numpy as np
import pytest
from PIL import Image
from turkutex import facade_derive as fd
from turkutex import facade_style as fs
from turkutex.facade_batch import metric_size

PPM = 32
CORE_E, CORE_N = 23459700.0, 6704250.0  # inside config.OFFICE_BLOCK.core


def facade(width_m=8.0, height_m=10.0, bays=4, storeys=3) -> np.ndarray:
    """Plaster wall: cornice, plinth, `storeys` rows of framed dark windows, one per bay."""
    rng = np.random.default_rng(3)
    w, h = round(width_m * PPM), round(height_m * PPM)
    image = np.array([196, 168, 138], float) + rng.normal(0, 3, (h, w, 1))
    cornice, plinth = round(0.6 * PPM), round(0.7 * PPM)
    image[:cornice] = (232, 226, 212)
    image[h - plinth :] = (118, 118, 116)
    storey = (h - cornice - plinth) / storeys
    bay = w / bays
    for s in range(storeys):
        top = round(cornice + s * storey + 0.3 * storey)
        bottom = round(cornice + s * storey + 0.8 * storey)
        for b in range(bays):
            left, right = round((b + 0.3) * bay), round((b + 0.7) * bay)
            image[top - 3 : bottom + 3, left - 3 : right + 3] = (240, 238, 232)  # frame
            image[top:bottom, left:right] = (42, 46, 56)  # glass
    return np.clip(image, 0, 255).astype(np.uint8)


def meta_for(albedo: np.ndarray, building="b1", index=0) -> dict:
    h, w = albedo.shape[:2]
    return {
        "key": "k",
        "building_id": building,
        "facade_index": index,
        "width_m": w / PPM,
        "height_m": h / PPM,
        "pixels_per_metre": [PPM, PPM],
        "files": {k: f"{index}{'' if k == 'albedo' else '.' + k}.ktx2" for k in fs.MAP_KINDS},
        "hashes": {f"{index}{'' if k == 'albedo' else '.' + k}.ktx2": "x" for k in fs.MAP_KINDS},
        "source_mode": "generate",
        "generated_qa": {"visual_qa_answer": {"storeys": 3, "upper_openings": 4}},
    }


def derive(albedo, width_m, height_m, **kw):
    style = fs.style_record(albedo, meta_for(albedo))
    maps = {"albedo": albedo, "normal": albedo, "orm": albedo}
    out, plan = fs.derive_maps(style, maps, width_m, height_m, **kw)
    return style, out, plan, fs.derived_qa(style, albedo, plan, width_m, height_m)


def window_widths(albedo: np.ndarray) -> list[int]:
    from scipy import ndimage

    labels, _ = ndimage.label(fs.glass_mask(fs.lab(albedo)))
    return [box[1].stop - box[1].start for box in ndimage.find_objects(labels)]


def test_style_record_reads_storeys_bays_bands_and_wall_colour():
    albedo = facade()
    style = fs.style_record(albedo, meta_for(albedo), {"material": "muu materiaali"})
    assert style["bands"]["window_rows_read"] == 3
    assert style["window_module"]["openings_per_row"] == 4
    assert style["window_module"]["bay_m"] == pytest.approx(2.0, abs=0.15)
    assert style["bands"]["storey_m"] == pytest.approx((10 - 1.3) / 3, abs=0.2)
    assert style["bands"]["plinth_m"] > 0.6 and style["bands"]["cornice_m"] > 0.5
    assert np.abs(np.array(style["wall"]["srgb"]) - (196, 168, 138)).max() <= 8
    assert style["source"] == "b1_0" and style["attributes"]["material"] == "muu materiaali"
    json.dumps(style)


def test_wider_and_taller_walls_repeat_whole_bays_and_storeys():
    albedo = facade()
    source = window_widths(albedo)
    style, out, plan, qa = derive(albedo, 19.0, 13.0)
    assert qa["pass"], qa
    assert out["albedo"].shape[:2] == metric_size(19.0, 13.0, PPM)[::-1]
    widths = window_widths(out["albedo"])
    # Every window is whole: no cut one at a seam or an edge.
    assert min(widths) >= min(source) - 2 and max(widths) <= max(source) + 2
    rows = fs.runs(fs.glass_mask(fs.lab(out["albedo"])).mean(1) > 0.1)
    assert len(rows) == 4  # 13 m holds one more 2.9 m storey
    # 19 m holds the corners and nine or ten bays, as whole bays in every row.
    assert len(widths) % 4 == 0 and 9 <= len(widths) // 4 <= 10


def test_narrow_wall_is_plain_pier_and_low_wall_is_plain_bands():
    albedo = facade()
    _, out, plan, qa = derive(albedo, 1.0, 10.0)
    assert qa["pass"], qa
    assert {g["mode"] for g in plan["groups"]} == {"plain"}
    assert not fs.glass_mask(fs.lab(out["albedo"])).any()
    _, out, plan, qa = derive(albedo, 8.0, 1.6)
    assert qa["pass"], qa
    assert plan["rows"]["mode"] == "plain"
    assert not fs.glass_mask(fs.lab(out["albedo"])).any()
    # The plinth stays at the foot of the low wall.
    assert out["albedo"][-2].mean() < 130


def test_single_storey_style_keeps_its_window_row():
    albedo = facade(width_m=6.0, height_m=4.0, bays=3, storeys=1)
    _, out, plan, qa = derive(albedo, 9.5, 4.2)
    assert qa["pass"], qa
    assert plan["rows"]["mode"] == "bays"
    assert len(window_widths(out["albedo"])) in (4, 5)


def test_qa_rejects_a_seam_through_a_window():
    albedo = facade()
    style, _, plan, _ = derive(albedo, 8.0, 10.0)
    window = style["plan"]["columns"]["upper"]["openings_px"][1]
    middle = (window[0] + window[1]) // 2
    plan["groups"] = [
        {**g, "indices": list(range(middle, albedo.shape[1])) + list(range(middle)), "seams": []}
        for g in plan["groups"]
    ]
    qa = fs.derived_qa(style, albedo, plan, 8.0, 10.0)
    assert not qa["pass"] and qa["cut_openings"] >= 1


def test_mirrored_filler_walk_flags_backward_steps():
    walk = np.array([5, 6, 7, 6, 5, 6, 7])
    assert fs._seams(walk) == []
    assert fs._mirrored(walk).tolist() == [False, False, True, True, True, False, False]


def test_neighbour_prefers_storeys_then_material_then_era():
    attributes = {
        "own": {"levels": 7, "material_class": "brick", "era": "1960+"},
        "shed": {"levels": 1, "material_class": "brick", "era": "1960+"},
        "block": {"levels": 7, "material_class": "concrete", "era": "1960+"},
    }
    styled = {"shed": {"centre": np.array([10.0, 0])}, "block": {"centre": np.array([90.0, 0])}}
    chosen = fd.assign_neighbour("own", np.zeros(2), attributes, styled)
    assert chosen["style_building"] == "block" and chosen["mismatch"] == 2


def test_building_attributes_read_both_registers(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(
        pa.table(
            {
                "feature_key": ["f1", "f2"],
                "rakennustunnus": ["103X", None],
                "julkisivumateriaali": ["puu", ""],
                "kerrosluku": ["2", None],
                "kottovuosi": ["1865-01-01", None],
                "teksti": ["Linnankatu", None],
                "osoitenumero": ["5", None],
            }
        ),
        tmp_path / "kanta_Rakennus.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "feature_key": ["g9"],
                "PysyvaRakennusTunnus": ["104Y"],
                "Valmistunut": ["21.08.1962"],
                "Kerrosluku": ["7"],
                "Osoitenimi_fi": ["Aurakatu"],
                "Osoitenumero": ["2"],
            }
        ),
        tmp_path / "GIS_Rakennukset.parquet",
    )
    found = fd.building_attributes(tmp_path, ["103X", "gis-104Y", "kanta-f2"])
    assert found["103X"] == {
        "material": "puu",
        "material_class": "wood",
        "year": 1865,
        "era": "1850-1919",
        "levels": 2,
        "address": "Linnankatu 5",
    }
    assert found["gis-104Y"]["year"] == 1962 and found["gis-104Y"]["levels"] == 7
    assert found["gis-104Y"]["material_class"] == "render"
    assert found["kanta-f2"]["levels"] is None and found["kanta-f2"]["era"] == "unknown"


def wall_feature(building, index, e0, width, height, base=2.0):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[e0, CORE_N], [e0 + width, CORE_N]]},
        "properties": {
            "building_id": building,
            "facade_index": index,
            "base_height": base,
            "top_height": base + height,
            "normal": [0.0, 0.0, 1.0],
            "street_facing": True,
            "width_m": width,
            "material_key": "facade_plaster",
        },
    }


def test_selected_building_includes_street_walls_outside_core(tmp_path):
    inventory = tmp_path / "facades.geojson"
    inventory.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    wall_feature("target", 0, CORE_E + 2000, 8, 12),
                    wall_feature("target", 1, CORE_E, 6, 12),
                    wall_feature("other", 0, CORE_E + 2000, 8, 12),
                ],
            }
        )
    )
    assert {wall.id for wall in fd.core_walls(inventory)} == {"target_1"}
    assert {wall.id for wall in fd.core_walls(inventory, "target")} == {"target_0", "target_1"}


@pytest.mark.skipif(
    fd.tools_dir().joinpath("ktx/bin/ktx").is_file() is False
    or __import__("turkutex.post.pipeline", fromlist=["x"]).native_encoder() is None,
    reason="run make textures-ktx",
)
def test_styles_plan_derive_and_publish_bind_derived_walls(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from turkutex.export import export_lock
    from turkutex.post.pipeline import encode_ktx2, sha

    albedo = facade()
    width, height = albedo.shape[1] / PPM, albedo.shape[0] / PPM
    assembly = tmp_path / "assembly"
    inventory = assembly / "APPROVED_INVENTORY.geojson"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    wall_feature("b1", 0, CORE_E, width, height),
                    wall_feature("b1", 1, CORE_E + 20, 13.0, height),
                    wall_feature("b2", 0, CORE_E + 40, 1.2, height),
                ],
            }
        )
    )
    building = assembly / "facades/b1"
    building.mkdir(parents=True)
    meta = meta_for(albedo)
    flat = np.zeros_like(albedo) + np.array([128, 128, 255], np.uint8)
    for kind, image in (("albedo", albedo), ("normal", flat), ("orm", albedo)):
        png = tmp_path / f"{kind}.png"
        Image.fromarray(image).save(png)
        encode_ktx2(png, building / meta["files"][kind], *fd.ENCODINGS[kind])
    meta["hashes"] = {name: sha(building / name) for name in meta["files"].values()}
    meta["width_m"], meta["height_m"] = width, height
    (building / "0.json").write_text(json.dumps(meta))
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"inventory_sha256": sha(inventory), "bound": ["b1_0"], "library": []})
    )
    wfs = tmp_path / "wfs"
    wfs.mkdir()
    pq.write_table(
        pa.table(
            {
                "feature_key": ["f1", "f2"],
                "rakennustunnus": ["b1", "b2"],
                "julkisivumateriaali": ["muu materiaali", "muu materiaali"],
                "kerrosluku": ["3", "3"],
                "kottovuosi": ["1890-01-01", "1895-01-01"],
                "teksti": ["Linnankatu", "Linnankatu"],
                "osoitenumero": ["1", "3"],
            }
        ),
        wfs / "kanta_Rakennus.parquet",
    )
    pq.write_table(
        pa.table({k: pa.array([], pa.string()) for k in fd.GIS_COLUMNS}),
        wfs / "GIS_Rakennukset.parquet",
    )
    staging = tmp_path / "staging"
    common = ["--assembly", str(assembly), "--inventory", str(inventory), "--staging", str(staging)]
    assert fd.main(["styles", *common, "--wfs", str(wfs)]) == 0
    assert fd.main(["plan", *common]) == 0
    plan = json.loads((staging / "plan.json").read_text())
    assert plan["counts"] == {"generate": 1, "derived": 1, "derived-neighbour": 1}
    assert fd.main(["derive", *common]) == 0
    assert fd.main(["contact", *common, "--output", str(tmp_path / "sheet.jpg")]) == 0
    assert fd.main(["publish", *common]) == 0
    manifest = json.loads((assembly / "MANIFEST.json").read_text())
    assert manifest["bound"] == ["b1_0", "b1_1", "b2_0"]
    assert manifest["inventory_sha256"] == sha(inventory)
    derived = json.loads((assembly / "facades/b1/1.json").read_text())
    assert derived["source_mode"] == "derived" and derived["derived_from"] == "b1_0"
    assert derived["references"] == [] and derived["qa"]["derived"]["pass"]
    assert derived["min_pixels_per_metre"] == fd.DENSITY_FLOOR
    assert derived["size"] == list(metric_size(13.0, height, PPM))
    neighbour = json.loads((assembly / "facades/b2/0.json").read_text())
    assert neighbour["source_mode"] == "derived-neighbour"
    assert neighbour["style"]["path"] == "facades/b1/style.json"
    style = assembly / "facades/b1/style.json"
    assert sha(style) == derived["style"]["sha256"]
    for record, folder in ((derived, "b1"), (neighbour, "b2")):
        for name, digest in record["hashes"].items():
            assert sha(assembly / "facades" / folder / name) == digest
    # The source map was not touched and the lock is free again.
    source = json.loads((building / "0.json").read_text())
    assert source["source_mode"] == "generate" and source["min_pixels_per_metre"] == 16
    with export_lock(assembly):
        pass

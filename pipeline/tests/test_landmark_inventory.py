import json
from types import SimpleNamespace
import pytest
from turku3d.landmarks.ranking import rank

@pytest.fixture(autouse=True)
def isolated_parameter_store(tmp_path, monkeypatch):
    from turku3d import config as C

    monkeypatch.setattr(C, "data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(C, "work_root", lambda: tmp_path / "work")

def building(key="103371570S"):
    return SimpleNamespace(facade_key=key, key=key, base=12.0, top=90.0, attributes={})

def test_ranking_promotes_evidence_and_reports_unknowns():
    tags = dict(
        building="cathedral",
        amenity="place_of_worship",
        historic="church",
        wikipedia="fi:Turun tuomiokirkko",
        wikidata="Q160165",
    )
    a = rank(tags)
    b = rank(tags, dict(rky="RKY test", monthly_pageviews=20000, height_m=90, visible_streets=4))
    assert a["tier"] == "landmark" and b["score"] > a["score"]
    assert "rky" in a["missing_evidence"] and "rky" not in b["missing_evidence"]
    assert sum(r["points"] for r in b["reasons"]) == b["score"]
    assert rank({"building": "apartments"})["tier"] == "ordinary"

def test_ranked_buildings_consolidate_way_and_relation_without_double_treatment():
    from turku3d.landmarks.measure import consolidate

    rows = [
        dict(id="way/1", building_id="city-1", name="Castle", tags={"name": "Castle"}, score=100),
        dict(id="relation/2", building_id="city-1", name="relation/2", tags={}, score=90),
    ]
    result = consolidate(rows)
    assert len(result) == 1 and result[0]["id"] == "way/1"
    assert result[0]["aliases"] == ["relation/2"]

def test_footprint_rarity_compares_shapes_with_the_city_population():
    from shapely.geometry import box
    from turku3d.landmarks.ranking import footprint_rarity

    rows = [
        dict(
            footprint_wkt=box(i * 30, 0, i * 30 + 10, 10).wkt, tags={"building": "yes"}, evidence={}
        )
        for i in range(100)
    ]
    rows.append(dict(footprint_wkt=box(0, 0, 80, 10).wkt, tags={"building": "yes"}, evidence={}))
    footprint_rarity(rows)
    assert rows[-1]["score"] > rows[0]["score"]
    assert rows[-1]["evidence"]["footprint_rarity"]["matching_shapes"] == 1

def test_vision_parameters_reject_mixed_region_overlapping_window_rows():
    from turku3d.landmarks.descriptions import ROOT
    from turku3d.landmarks.references import validate

    d = json.loads((ROOT / "103371570S.json").read_text())
    d["windows"]["rows_m"] = [4, 6]
    with pytest.raises(ValueError, match="rows overlap"):
        validate(d, d["building_id"])
    d["windows"]["rows_m"] = [4, 10]
    validate(d, d["building_id"])

def test_landmark_description_invalidates_only_its_own_tiles(tmp_path, monkeypatch):
    from turku3d.landmarks import descriptions

    monkeypatch.setattr(descriptions, "ROOT", tmp_path)
    a = descriptions.fingerprint([["landmark-a", ""]])
    b = descriptions.fingerprint([["landmark-b", ""]])
    (tmp_path / "landmark-a.json").write_text(
        json.dumps(dict(version=1, building_id="landmark-a", colour=[1, 2, 3]))
    )
    assert descriptions.fingerprint([["landmark-a", ""]]) != a
    assert descriptions.fingerprint([["landmark-b", ""]]) == b
    assert not descriptions.manifest_current(
        dict(landmark_buildings=[["landmark-a", ""]], landmark_params=a)
    )
    assert descriptions.manifest_current(
        dict(landmark_buildings=[["landmark-b", ""]], landmark_params=b)
    )

def test_reviewed_parameters_survive_automatic_reference_refresh(tmp_path, monkeypatch):
    from turku3d import config as C
    from turku3d.landmarks import descriptions

    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    monkeypatch.setattr(descriptions, "ROOT", reviewed)
    data = C.data_root() / "landmark-descriptions"
    data.mkdir(parents=True)
    automatic = dict(
        version=1, building_id="new", reference_match="rejected", reference_confidence=0
    )
    (data / "new.json").write_text(json.dumps(automatic))
    assert descriptions.resolve(building("new")) is None
    automatic.update(reference_match="verified", reference_confidence=0.8, colour=[4, 5, 6])
    (data / "new.json").write_text(json.dumps(automatic))
    assert descriptions.resolve(building("new"))["colour"] == [4, 5, 6]
    (reviewed / "new.json").write_text(
        json.dumps(dict(version=1, building_id="new", colour=[1, 2, 3]))
    )
    assert descriptions.resolve(building("new"))["colour"] == [1, 2, 3]
    override = C.work_root() / "landmark-descriptions"
    override.mkdir(parents=True)
    (override / "new.json").write_text(
        json.dumps(dict(version=1, building_id="new", colour=[7, 8, 9]))
    )
    assert descriptions.resolve(building("new"))["colour"] == [7, 8, 9]

def test_asset_batch_continues_after_one_missing_description(tmp_path, monkeypatch):
    import pickle
    import sys
    from turku3d.landmarks import asset

    cache = tmp_path / "cache" / ".inputs" / "t0_0_0"
    cache.mkdir(parents=True)
    (cache / "wfs.test.pkl").write_bytes(
        pickle.dumps({"value": {"buildings": [building("a"), building("b")]}})
    )
    ranked = tmp_path / "ranked.json"
    ranked.write_text(
        json.dumps({"buildings": [dict(id=k, tier="landmark") for k in ("a", "b", "c")]})
    )

    def candidate(b, root, description_root):
        if b.facade_key == "a":
            raise ValueError("No verified description")
        return dict(building_id=b.facade_key, sha256="built")

    monkeypatch.setattr(asset, "build", candidate)
    output = tmp_path / "assets"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "asset",
            "--ranked",
            str(ranked),
            "--cache-root",
            str(tmp_path / "cache"),
            "--out",
            str(output),
        ],
    )
    asset.main()
    results = {r["building_id"]: r for r in json.loads((output / "batch-results.json").read_text())}
    assert results["a"]["state"] == "deferred"
    assert results["b"]["sha256"] == "built"
    assert results["c"]["error"] == "No prepared survey geometry"

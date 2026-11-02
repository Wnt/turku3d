"""Moored ships retain survey dimensions without entering ordinary house facades."""

import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import boat_inputs, boats
from turku3d.coarse.inputs import Building


def scene(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    outline = shapely.Polygon([(228, 23), (232, 20), (262, 20), (266, 23), (262, 26), (232, 26)])
    ship = Building("survey_ship", outline, 0, 4, "facade_plaster")
    ship.facade_key = "stored_ship_facade"
    ship.roof_parts = [
        (outline, [0, 0, 1.5]),
        (shapely.box(244, 21, 254, 25), [0, 0, 5.8]),
        (shapely.box(240, 23, 240.2, 23.2), [0, 0, 12]),
    ]
    land = Building("land_house", shapely.box(200, 60, 220, 70), 1, 6, "facade_plaster")
    osm = tmp_path / "osm.parquet"
    water = tmp_path / "water.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    osm_id="ship",
                    tags=json.dumps({"building": "ship"}),
                    geometry_local=outline.exterior.wkb,
                ),
                dict(
                    osm_id="ship", tags=json.dumps({"building": "ship"}), geometry_local=outline.wkb
                ),
                dict(
                    osm_id="name",
                    tags=json.dumps({"amenity": "restaurant", "name": "River Cafe"}),
                    geometry_local=outline.centroid.wkb,
                ),
            ]
        ),
        osm,
    )
    pq.write_table(
        pa.Table.from_pylist([dict(geometry_local=shapely.box(220, 0, 290, 40).wkb)]), water
    )
    monkeypatch.setattr("turku3d.coarse.inputs.buildings", lambda *args, **kw: [ship, land])
    records = boat_inputs.prepare(C.VisualTile(0, 0, 0), osm, tmp_path / "buildings.parquet", water)
    return outline, ship, land, records


def test_ship_is_separate_from_house_facades_and_retains_measured_cabin_and_mast(
    tmp_path, monkeypatch
):
    _, ship, land, records = scene(tmp_path, monkeypatch)
    assert len(records) == 1
    boat = records[0]
    assert boat["tags"]["name"] == "River Cafe"
    assert boat["deck"] == 1.5 and boat["water"] == 0
    assert len(boat["cabins"]) == 1 and boat["cabins"][0][1] == 5.8
    assert len(boat["masts"]) == 1 and boat["masts"][0][1] == 12
    assert boat["source_facades"] == ["stored_ship_facade"]
    assert boat_inputs.separate([ship, land], records) == [land]


def test_boat_deck_is_complete_across_visual_seam_with_bounded_solid_geometry(
    tmp_path, monkeypatch
):
    outline, _, _, records = scene(tmp_path, monkeypatch)
    all_decks = []
    for tile in (C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)):
        meshes = boats.produce(tile, records)
        assert meshes
        assert sum(len(m.indices) for m in meshes if m.metadata.get("collision", True)) < 2000
        for m in meshes:
            assert np.isfinite(m.positions).all()
            assert m.positions[:, 0].min() >= tile.bbox.xmin
            assert m.positions[:, 0].max() <= tile.bbox.xmax
            if m.material == "bridge_deck":
                assert np.all(m.positions[:, 1] == 1.515)
                all_decks.append(
                    shapely.union_all(shapely.polygons(m.positions[m.indices][:, :, [0, 2]]))
                )
        visuals = boats.visuals(tile, meshes)
        assert all(
            m.extras["layer"] == "boats" and m.extras["material_key"].startswith("boat_")
            for m in visuals
        )
        assert all(m.image and m.uvs is not None for m in visuals)
        assert not any("facade" in m.extras["material_key"] for m in visuals)
        cabin = [m for m in meshes if m.material == "building"]
        assert cabin and min(m.positions[:, 1].min() for m in cabin) >= records[0]["deck"]
    assert shapely.union_all(all_decks).symmetric_difference(outline.buffer(-0.08)).area < 1e-8


def test_water_covered_survey_building_gets_boat_while_land_house_stays_a_house(
    tmp_path, monkeypatch
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    _, ship, land, records = scene(tmp_path, monkeypatch)
    assert records
    path = tmp_path / "empty.parquet"
    pq.write_table(
        pa.Table.from_pylist([dict(tags="{}", geometry_local=shapely.Point(0, 0).wkb)]), path
    )
    records = boat_inputs.prepare(
        C.VisualTile(0, 0, 0), path, tmp_path / "b.parquet", tmp_path / "water.parquet"
    )
    assert len(records) == 1 and records[0]["id"] == "survey:survey_ship"
    assert boat_inputs.separate([ship, land], records) == [land]


def test_floating_piers_and_boat_access_are_not_vessels(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    outline, _, _, _ = scene(tmp_path, monkeypatch)
    path = tmp_path / "piers.parquet"
    pq.write_table(pa.Table.from_pylist([
        dict(osm_id="pier", tags=json.dumps({"man_made": "pier", "floating": "yes"}),
             geometry_local=outline.wkb),
        dict(osm_id="access", tags=json.dumps({"boat": "yes", "man_made": "pier"}),
             geometry_local=outline.wkb),
    ]), path)
    assert boat_inputs.prepare(C.VisualTile(0, 0, 0), path, None, tmp_path / "water.parquet") == []


def test_ship_waterline_shares_river_datum_without_moving_surveyed_deck(tmp_path, monkeypatch):
    _, ship, _, _ = scene(tmp_path, monkeypatch)
    ship.base = 0.37
    records = boat_inputs.prepare(C.VisualTile(0, 0, 0), tmp_path / "osm.parquet",
                                 tmp_path / "buildings.parquet", tmp_path / "water.parquet")
    assert records[0]["water"] == 0
    assert records[0]["deck"] == 1.5


def test_mapped_white_ship_uses_painted_cabins_and_retains_colour_provenance(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    scene(tmp_path, monkeypatch)
    path = tmp_path / "osm.parquet"
    rows = pq.read_table(path).to_pylist()
    for row in rows:
        tags = json.loads(row["tags"])
        if tags.get("building") == "ship":
            tags.update({"building:colour": "white", "building:material": "metal"})
            row["tags"] = json.dumps(tags)
    pq.write_table(pa.Table.from_pylist(rows), path)
    tile = C.VisualTile(0, 0, 0)
    records = boat_inputs.prepare(tile, path, tmp_path / "buildings.parquet", tmp_path / "water.parquet")
    assert records[0]["appearance"] == {"cabin_surface": "white", "source": "osm:building:colour"}
    meshes = boats.produce(tile, records)
    cabin_shells = [m for m in meshes if m.material == "building" and m.metadata.get("collision", True)]
    assert cabin_shells and all(m.metadata["surface"] == "white" for m in cabin_shells)

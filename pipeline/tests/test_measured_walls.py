"""Measured wall colours: export filter, register fact, enrichment priority and the spot-rebuild class."""

import json

import pytest


def line(key, colour, conf, n=3):
    return json.dumps(dict(building=key, measured=dict(wall=dict(colour=colour, conf={"*": conf}, n=n))))


def test_export_keeps_confident_valid_colours_and_the_latest_line(tmp_path):
    from turku3d.coarse import measured_walls as mw

    source = tmp_path / "measured.jsonl"
    source.write_text("\n".join([
        line("A", "#AABBCC", 0.6),
        line("B", "#112233", 0.49),  # below the evidence threshold
        line("C", "teal", 0.9),  # not a measured hex
        line("D", "#445566", 0.7),
        line("D", "#445567", 0.3),  # a newer unconfident measurement withdraws the older one
        line("E", "#e0d4c1", 0.4),
        line("E", "#e0d4c2", 0.8),
        json.dumps(dict(building="F", measured={})),
        line("G", "#4a2525", 0.9),  # L* 20: a wall in shade or a neighbour's crop, not paint
    ]) + "\n")
    dataset = mw.export(source)
    assert dataset["walls"] == {"A": dict(colour="#aabbcc", conf=0.6, n=3), "E": dict(colour="#e0d4c2", conf=0.8, n=3)}
    assert dataset["count"] == 2 and dataset["source"]["lines"] == 9
    assert round(mw.lightness("#776b67"), 1) == 46.2 and mw.lightness("#ffffff") == 100
    path = mw.write(dataset, tmp_path / "derived" / "measured-walls" / "measured-walls.json")
    assert mw.read(path) == {"A": "#aabbcc", "E": "#e0d4c2"}
    with pytest.raises(ValueError):
        mw.write(dataset, tmp_path / "raw" / "measured-walls.json")
    assert mw.source_paths(tmp_path) == [path]
    assert mw.source_paths(tmp_path / "elsewhere") == []


def test_register_joins_measured_colours_of_its_own_rows_only(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse import measured_walls as mw
    from turku3d.coarse.register import read_register

    table = tmp_path / "kanta_Rakennus.parquet"
    pq.write_table(pa.table(dict(rakennustunnus=["A", "B"], julkisivumateriaali=["rappaus", "tiili"],
                                 kerrosluku=["4", "2"], kottovuosi=["1912", "1955"])), table)
    walls = mw.write(dict(schema=mw.SCHEMA, walls={"A": dict(colour="#c8b48c", conf=0.7, n=2),
                                                    "Z": dict(colour="#000000", conf=0.9, n=4)}),
                     tmp_path / "derived" / "measured-walls" / "measured-walls.json")
    registered = read_register([table, walls])
    assert registered["A"]["measured_wall_colour"] == "#c8b48c"
    assert registered["A"]["material_class"] == "render"
    assert "measured_wall_colour" not in registered["B"]
    assert "Z" not in registered  # a building outside this tile's register rows
    assert read_register([table]) == {k: {f: v for f, v in r.items() if f != "measured_wall_colour"}
                                      for k, r in registered.items()}


def test_measured_colour_fills_the_palette_fallback_below_a_mapped_osm_colour():
    import shapely
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.register import enrich
    from turkutex.facade_fast_render import register_description

    def enriched(osm_attrs):
        b = Building("home", shapely.box(0, 0, 10, 20), 0, 12, "facade_plaster")
        b.facade_key = "A"
        b.attributes = dict(material_class="render", era="1850-1919", levels=3)
        enrich(b, {"A": dict(measured_wall_colour="#c8b48c")},
               dict(buildings=[(b.footprint, osm_attrs)]))
        return b.attributes

    plain = enriched({})
    assert plain["wall_colour"] == "#c8b48c" and plain["wall_colour_source"] == "measured"
    assert "measured_wall_colour" not in plain
    assert register_description("A", "A:0", plain, 10, 12)["wall_colour"] == "#c8b48c"
    mapped = enriched(dict(wall_colour="#d8bf7a", wall_colour_name="yellow"))
    assert mapped["wall_colour"] == "#d8bf7a" and "wall_colour_source" not in mapped
    assert "measured_wall_colour" not in mapped


def test_spot_rebuild_class_is_the_exported_tile_list():
    from turku3d.migrations import measured_walls, registry

    assert registry.CHAIN[-1] is measured_walls.MIGRATION
    assert measured_walls.affected(dict(tile="t0_0_0", building_keys=["x"]))
    assert not measured_walls.affected(dict(tile="t0_3_3", building_keys=["x"]))
    assert not measured_walls.affected({})
    assert len(measured_walls.TILES) == 61

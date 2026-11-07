"""Area indexes preserve complete features, source order and changed-source invalidation."""

import geopandas as gpd
import shapely
from turku3d import config as C
from turku3d.spatial import frame, rows


def test_area_selection_keeps_complete_buildings_and_refreshes_changed_source(tmp_path):
    path = tmp_path / "area.parquet"
    shapes = [shapely.box(0, 0, 10, 10), shapely.box(500, 0, 510, 10), shapely.box(700, 0, 710, 10)]
    data = gpd.GeoDataFrame(
        dict(feature_key=["a", "a", "b"], geometry_local=shapes), geometry=shapes, crs="EPSG:3877"
    )
    data["geometry_local"] = gpd.GeoSeries(shapes)
    data.to_parquet(path)
    box = C.VisualTile(0, 0, 0).bbox
    assert frame(path, box).index.tolist() == [0]
    group = frame(path, box, whole_features=True)
    assert group.index.tolist() == [0, 1]
    group.loc[0, "feature_key"] = "mutated"
    assert frame(path, box).feature_key.tolist() == ["a"]
    data.loc[0, "feature_key"] = "new"
    data.to_parquet(path)
    assert frame(path, box).feature_key.tolist() == ["new"]
    selected = list(rows(path, box, columns=("feature_key", "geometry_local", "absent")))
    assert selected[0][0] == 0
    assert set(selected[0][1]) == {"feature_key", "geometry_local"}
    assert selected[0][1]["geometry_local"].equals(shapes[0])


def test_stair_input_reads_only_nearby_complete_ways(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.stairs import prepare

    path = tmp_path / "osm.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    osm_id=i,
                    tags=json.dumps({"highway": "steps"}),
                    geometry_local=shapely.LineString([(x, 10), (x + 10, 10)]).wkb,
                )
                for i, x in enumerate((10, 1000))
            ]
        ),
        path,
    )
    lines, areas = prepare(C.VisualTile(0, 0, 0), path)
    assert len(lines) == 1 and not areas
    assert lines[0][0] == "0"
    assert list(lines[0][1].coords) == [(10, 10), (20, 10)]


def test_indoor_and_upper_level_steps_are_not_built_on_the_ground(tmp_path):
    """Tapiola: deck stairs at level 4;6 crossed Merituulentie as a stair slope; indoor mall flights too; Rotterdam:
    underground station escalators cut pits into the street."""
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.stairs import prepare

    path = tmp_path / "osm.parquet"
    kinds = [
        {"highway": "steps"},
        {"highway": "steps", "level": "0;1"},  # from the ground floor up: stands on the ground
        {"highway": "steps", "level": "4;6", "layer": "1"},
        {"highway": "steps", "indoor": "yes", "level": "0;1"},
        {"highway": "steps", "level": "1;2"},
        {"highway": "steps", "level": "1", "layer": "1", "bridge": "yes"},  # drawn on its deck
        # Rotterdam metro Beurs: escalators between the two underground levels cut stair pits into Coolsingel.
        {"highway": "steps", "conveying": "yes", "level": "-1;-2"},
        {"highway": "steps", "location": "underground"},
        {"highway": "steps", "level": "0;-1"},  # from the street down into the station: keeps its opening
    ]
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(osm_id=i, tags=json.dumps(tags), geometry_local=shapely.LineString([(10, 10 + 5 * i), (20, 10 + 5 * i)]).wkb)
                for i, tags in enumerate(kinds)
            ]
        ),
        path,
    )
    lines, _ = prepare(C.VisualTile(0, 0, 0), path)
    assert sorted(key for key, _, _ in lines) == ["0", "1", "5", "8"]

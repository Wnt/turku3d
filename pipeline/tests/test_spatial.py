"""Area indexes preserve complete features, source order and changed-source invalidation."""

import geopandas as gpd
import shapely
from turku3d import config as C
from turku3d.spatial import frame


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

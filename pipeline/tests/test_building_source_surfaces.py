"""Synthetic city solids exercise source selection without building real tiles."""

import geopandas as gpd
import numpy as np
import shapely
from turku3d import config as C
from turku3d.buildings.source_surfaces import classify_surfaces
from turku3d.coarse.inputs import buildings


def face(x0, x1, height):
    return shapely.Polygon([(x0, 10, height), (x1, 10, height), (x1, 12, height), (x0, 12, height)])


def frame(geometries, kinds, roles=None):
    return gpd.GeoDataFrame(
        {
            "feature_key": ["solid"] * len(kinds),
            "surface_type": kinds,
            "geom_type": ["Polygon"] * len(kinds),
            "geom_role": roles or ["lod1Solid"] * len(kinds),
            "measuredHeight": ["3"] * len(kinds),
            "geometry_local": gpd.GeoSeries(geometries),
        },
        geometry=geometries,
    )


def test_unlabelled_solid_recovers_floor_walls_and_roof_at_large_coordinates():
    wall = shapely.Polygon([(10, 10, 2), (13, 10, 2), (13, 10, 5), (10, 10, 5)])
    f = frame([face(10, 13, 2), wall, face(10, 13, 5)], [""] * 3)
    f.geometry = f.geometry.apply(
        lambda g: shapely.transform(g, lambda xyz: xyz + [23459500, 6704500, 0], include_z=True)
    )
    assert list(classify_surfaces(f).surface_type) == [
        "GroundSurface",
        "WallSurface",
        "RoofSurface",
    ]


def test_explicit_semantics_win_and_incomplete_solids_are_not_invented():
    f = frame([face(10, 13, 2), face(10, 13, 5)], ["GroundSurface", ""])
    assert list(classify_surfaces(f).surface_type) == ["GroundSurface"]
    assert classify_surfaces(frame([face(10, 13, 5)], [""])).empty
    assert classify_surfaces(
        frame([face(10, 13, 2), face(10, 13, 5)], [""] * 2, ["other"] * 2)
    ).empty


def test_disconnected_small_parts_survive_and_keep_exact_tile_ownership(tmp_path):
    # The second, sub-4m2 component belongs to the neighbouring tile; no clipping or duplication.
    f = frame(
        [face(10, 13, 2), face(251, 252, 3), face(10, 13, 5), face(251, 252, 6)],
        ["GroundSurface"] * 2 + ["RoofSurface"] * 2,
    )
    path = tmp_path / "solid.parquet"
    f.to_parquet(path)
    a = buildings(path, C.VisualTile(0, 0, 0))
    b = buildings(path, C.VisualTile(0, 1, 0))
    assert [x.key for x in a + b] == ["solid", "solid-part1"]
    assert a[0].footprint.equals(shapely.force_2d(f.geometry[0]))
    assert b[0].footprint.equals(shapely.force_2d(f.geometry[1]))
    assert sum(x.footprint.area for x in a + b) == 8
    np.testing.assert_allclose([x.base for x in a + b], [2, 3])


def test_full_reader_uses_the_same_unlabelled_solid(tmp_path):
    from turku3d.buildings.citygml import read
    from turku3d.buildings.inputs import Building

    floor, roof = face(10, 13, 2), face(10, 13, 5)
    f = frame([floor, roof], ["", ""])
    f["surface_id"] = ["floor", "roof"]
    f.geometry = f.geometry.apply(
        lambda g: shapely.transform(
            g,
            lambda xyz: np.column_stack(
                (xyz[:, 0] + C.ORIGIN_E, C.ORIGIN_N - xyz[:, 1], xyz[:, 2])
            ),
            include_z=True,
        )
    )
    path = tmp_path / "solid.parquet"
    f.to_parquet(path)
    shells, matches = read(path, [Building("registered", shapely.force_2d(floor))])
    assert matches["registered"]["footprint_iou"] == 1
    assert [s.kind for s in shells["registered"]] == ["GroundSurface", "RoofSurface"]

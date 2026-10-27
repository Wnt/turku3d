"""Small synthetic checks; no city points, street imagery, or pretrained models."""

import numpy as np
from turku3d.lidar.walls import WALL_POINT_DTYPE
from turkutex.facade_fast_render import validate
from turkutex.facade_priors import building_group, fit_head, predict_head, prior
from turkutex.facade_priors_lidar import opening_grid


def test_group_keeps_register_aliases_and_building_parts_together():
    assert building_group("gis-103000000A-part2_7") == building_group("103000000A_3")
    assert building_group("103000001B_7") != building_group("103000000A_3")


def test_register_and_geometry_are_valid_and_keep_single_storey_columns_zero():
    answer = prior("synthetic_0", 12, 12, {"levels": 1, "material_class": "wood"})
    assert validate(answer, "synthetic_0") == []
    assert (answer["storeys"], answer["window_columns"], answer["material"]) == (1, 0, "wood")
    assert prior("synthetic_0", 12, 12, {}, register=False)["storeys"] == 4


def test_ridge_constant_features_remain_finite():
    answer = prior("synthetic_0", 12, 10, {"levels": 3, "material_class": "brick"})
    head = fit_head([[1, 1], [1, 1]], [answer, answer])
    prediction = predict_head(head, [1, 1])
    assert prediction["storeys"] == 3
    assert prediction["window_columns"] == answer["window_columns"]
    assert prediction["material"] == "brick"


def test_exported_forest_traversal_and_single_storey_semantics():
    head = {
        "kind": "forest",
        "fields": {
            "storeys": {"classes": [1, 4], "trees": [[0, 3.5, [[1, 0]], [[0, 1]]]]},
            "window_columns": {"classes": [3, 6], "trees": [[1, 8, [[1, 0]], [[0, 1]]]]},
        },
    }
    assert predict_head(head, [2, 10]) == {"storeys": 1, "window_columns": 0}
    assert predict_head(head, [4, 7]) == {"storeys": 4, "window_columns": 3}


def test_empty_lidar_is_unknown_and_roof_ground_do_not_create_openings():
    points = np.zeros(20, WALL_POINT_DTYPE)
    points["classification"] = 2
    points["h"] = 1
    points["u"] = np.linspace(0, 10, len(points))
    stats, grids = opening_grid(points, 10, 6)
    assert stats["coverage"] == 0
    assert not stats["usable"]
    assert not grids["density_holes"].any()
    assert stats["candidates"]["density"]["storeys"] == 0


def test_lidar_recess_requires_observed_depth():
    points = np.zeros(400, WALL_POINT_DTYPE)
    points["classification"] = 6
    points["u"] = np.tile(np.arange(10) * 0.5 + 0.25, 40)
    points["h"] = np.repeat(np.arange(10) * 0.5 + 0.4, 40)
    points["d"] = 0
    points["d"][(points["u"] > 2) & (points["u"] < 3)] = -0.3
    stats, grids = opening_grid(points, 5, 5)
    assert stats["points"] == 400
    assert grids["recessed"].any()
    assert not grids["recessed"][grids["counts"] == 0].any()

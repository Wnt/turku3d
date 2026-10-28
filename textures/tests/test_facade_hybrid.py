"""Pure synthetic fusion, evidence and independent-label agreement checks."""

from types import SimpleNamespace

import numpy as np
from turkutex.facade_hybrid import (
    bay_spacing,
    field_outputs,
    foreground_evidence,
    merge_fields,
    semantic_foreground_mask,
    surface_discontinuity,
)
from turkutex.facade_hybrid_research import fold, summary
from turkutex.facade_hybrid_truth import agreement
from turkutex.facade_priors import prior
from turkutex.facade_wide_reserve import select as select_wide_reserve


def evidence(**updates):
    return dict(
        facade_share=0.8,
        wall_share=0.6,
        width_m=12,
        minimum_pixels=200,
        opening_boxes=12,
        occlusion_share=0.1,
        **updates,
    )


def test_wide_reserve_selects_unique_visible_buildings_near_the_office():
    def wall(fid, east, width=20, height=8, facing=True):
        return SimpleNamespace(
            id=fid,
            centre=(east, 0),
            length=width,
            height_m=height,
            props={"street_facing": facing},
        )

    walls = [
        wall("excluded_0", 1),
        wall("near_0", 2),
        wall("near_1", 3),
        wall("narrow_0", 4, width=14),
        wall("hidden_0", 5, facing=False),
        wall("far_0", 6),
    ]
    assert [w.id for w in select_wide_reserve(walls, {"excluded"}, (0, 0), 2)] == [
        "near_0",
        "far_0",
    ]


def test_field_abstention_preserves_only_unsupported_register_fields():
    register = prior("building_1", 12, 12, {"levels": 4})
    prediction = dict(storeys=3, window_columns=5, wall_colour="#aabbcc", material="brick")
    e = evidence()
    e["occlusion_share"] = 0.3
    fields = field_outputs(prediction, e)
    result = merge_fields(register, fields)
    assert fields["storeys"]["value"] is None
    assert result["storeys"] == register["storeys"]
    assert result["window_columns"] == register["window_columns"]
    assert result["wall_colour"] == "#aabbcc"
    assert result["material"] == "brick"
    for flag in ("wrong_wall", "multiple_surfaces"):
        rejected = field_outputs(prediction, {**evidence(), flag: True})
        assert all(r["state"] == "abstain" for r in rejected.values())
        assert not merge_fields(register, rejected)["wall_visible"]


def test_no_rows_abstains_without_forcing_false_zero_storeys():
    prediction = dict(storeys=0, window_columns=4, wall_colour="#aaaaaa", material="plaster")
    e = evidence()
    e["opening_boxes"] = 0
    assert field_outputs(prediction, e)["storeys"]["state"] == "abstain"
    assert bay_spacing("wood", "1960+") == 2.6
    assert bay_spacing("stone", "1850-1919") == 2.6
    assert bay_spacing("concrete", "1960+") == 3


def test_vertical_chromatic_boundary_not_plain_shadow():
    rgb = np.full((60, 60, 3), 180, dtype=np.uint8)
    wall = np.ones((60, 60), dtype=bool)
    rgb[:, :30] = 90
    assert not surface_discontinuity(rgb, wall)
    rgb[:, :30] = (210, 80, 60)
    assert surface_discontinuity(rgb, wall)
    assert not surface_discontinuity(rgb, np.zeros_like(wall))


def test_semantic_foreground_excludes_road_sidewalk_and_sky():
    labels = np.array([[2, 6, 9, 4, 12, 17, 20, 80]])
    assert semantic_foreground_mask(labels).tolist() == [
        [False, False, False, True, True, True, True, True]
    ]
    e = foreground_evidence({"occlusion_share": 0.4, "foreground_share": 0.1})
    assert e["occlusion_share"] == 0.1
    assert e["off_wall_share"] == 0.4


def test_independent_agreement_and_building_grouping():
    label = dict(
        usable=True,
        wrong_wall=False,
        multiple_surfaces=False,
        occlusion_share=0.1,
        storeys=4,
        window_columns=5,
        material="plaster",
        wall_colour="#bbbbbb",
    )
    assert agreement(label, dict(label, wall_colour="#bcbcbc"))[0]
    for change in (
        {"storeys": 3},
        {"material": "brick"},
        {"occlusion_share": 0.4},
        {"wall_colour": "#662222"},
        {"usable": False},
    ):
        assert agreement(label, dict(label, **change))[0] is None
    assert fold("gis-123-part2_4") == fold("123_7")


def test_missing_astra_predictions_stay_in_denominator():
    label = dict(storeys=2, window_columns=3, material="brick", wall_colour="#aaaaaa")
    result = summary([dict(label=label, register=label, hybrid=label, astra_low=None)])
    assert result["astra_low"]["storeys"] == {"correct": 0, "total": 1}
    assert result["astra_low"]["colour_median_de76"] is None
    assert result["hybrid"]["colour_median_de76"] == 0


def test_queue_evidence_is_per_field_and_requires_one_hundred_walls():
    from turkutex.facade_hybrid_research import winning_fields

    label = dict(storeys=2, window_columns=3, material="brick", wall_colour="#aaaaaa")
    rows = [
        dict(
            label=label,
            register=dict(label, material="plaster", wall_colour="#ffffff"),
            hybrid=label,
        )
    ] * 100
    assert winning_fields(summary(rows)) == ["material", "wall_colour"]
    assert winning_fields(summary(rows[:99])) == []


def test_wall_extent_joins_coplanar_parts_but_not_offset_terrace_or_courtyard():
    from shapely.geometry import Polygon
    from turkutex.streetview.walls import wall_rectangles

    def wall(left, right, y, base, top):
        return Polygon([(left, y, base), (right, y, base), (right, y, top), (left, y, top)])

    rectangles = wall_rectangles(
        [
            wall(0, 12, 0, 0, 4),
            wall(0, 12, 0, 4, 20),
            wall(0, 12, -2, 0, 4),
            wall(20, 30, 0, 0, 20),
        ]
    )
    assert sorted(
        (round(np.linalg.norm(b - a)), round(z0, 2), round(z1, 2)) for a, b, z0, z1, _ in rectangles
    ) == [
        (10, 0, 20),
        (12, 0, 4),
        (12, 0, 20),
    ]


def test_shallow_survey_step_does_not_cut_a_physical_wall_in_half():
    from shapely.geometry import Polygon
    from turkutex.streetview.walls import wall_rectangles

    surfaces = [
        Polygon([(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 10)]),
        Polygon([(10, 0.25, 0), (20, 0.25, 0), (20, 0.25, 10), (10, 0.25, 10)]),
    ]
    walls = wall_rectangles(surfaces)
    assert len(walls) == 1
    assert abs(np.linalg.norm(walls[0][1] - walls[0][0]) - 20) < 0.1

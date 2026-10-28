"""Pure synthetic fusion, evidence and independent-label agreement checks."""

import numpy as np
from turkutex.facade_hybrid import bay_spacing, field_outputs, merge_fields, surface_discontinuity
from turkutex.facade_hybrid_research import fold, summary
from turkutex.facade_hybrid_truth import agreement, edge_heights
from turkutex.facade_priors import prior


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


def test_wall_eaves_ignore_a_taller_roof_in_the_same_building():
    points = np.array([[0, 0, 10], [10, 0, 10], [0, 8, 24], [10, 8, 24]], float)
    heights = edge_heights(points, np.array([0, 0]), np.array([10, 0]))
    assert heights.tolist() == [10, 10]
    assert not len(edge_heights(points[:1], np.array([0, 0]), np.array([10, 0])))

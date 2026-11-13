from turku3d.world.kinds.building import convert, description


def program(**wall):
    base = {
        "wall_id": "b_w",
        "width_m": 10.0,
        "height_m": 9.0,
        "geometry": {"p0": [0.0, 0.0], "p1": [10.0, 0.0], "normal": [0.0, -1.0], "base_h": 5.0},
        "wall": {"colour": {"value": None}, "material": {"value": "brick"}},
        "windows": {"rectangles_m": []},
        "base": {"openings_m": []},
    }
    return {
        "building_id": "000000000A",
        "representative_wall": "b_w",
        "material": {"value": "render"},
        "needs_review": False,
        "walls": [{**base, **wall}],
    }


def test_measured_rectangles_become_placed_openings_of_shared_types():
    doc = convert.convert(
        program(
            windows={
                "rectangles_m": [
                    [1.0, 4.0, 1.01, 1.6],
                    [4.0, 4.0, 0.99, 1.62],
                    [7.0, 4.0, 1.0, 1.6],
                ],
                "surround_shapes": ["frame", "frame", "pediment"],
                "frame_colour": {"value": "#ffffff"},
                "divisions": {"value": [2, 3]},
            },
            base={"openings_m": [[0.5, 0.2, 3.0, 2.6]]},
            doors={"candidates": [{"rect_m": [5.0, 0.1, 1.6, 2.4], "head_shape": "round-arch"}]},
            trim={
                "string_courses": {"value": [3.4]},
                "cornice": {"value": 0.5},
                "colour": {"value": "#eeeeee"},
            },
        )
    )
    assert description.errors(doc) == []
    wall = doc["walls"][0]
    assert wall["storeys"] == [] and wall["bays"] == []
    assert [o["type"] for o in wall["openings"]] == [
        "window_1",
        "window_1",
        "window_2",
        "shop_1",
        "door_1",
    ]
    assert wall["openings"][0]["u_m"] == 1.505 and wall["openings"][0]["v_m"] == 4.0
    assert doc["opening_types"]["window_2"]["pediment"]["kind"] == "triangular"
    assert (
        doc["opening_types"]["door_1"]["head"] == "round"
        and doc["opening_types"]["door_1"]["leaf"]["leaves"] == 2
    )
    assert [b["kind"] for b in wall["bands"]] == ["string_course", "eaves_cornice"]
    # No measured colour: the material that was read, in one of its usual colours.
    assert doc["materials"]["wall"]["family"] == "brick" and doc["wall_defaults"] == {
        "material": "wall"
    }


def test_a_wall_seen_from_its_p1_end_is_turned_and_an_unread_wall_is_left_to_the_priors():
    turned = program(windows={"rectangles_m": [[1.0, 4.0, 1.0, 1.6]]})
    turned["walls"][0]["geometry"]["normal"] = [0.0, 1.0]
    wall = convert.convert(turned)["walls"][0]
    assert wall["frame"]["p0"] == [10.0, 0.0] and wall["openings"][0]["u_m"] == 8.5
    unread = program()
    unread["walls"][0]["wall"]["material"]["value"] = None
    assert convert.convert(unread) is None


def test_rows_without_rectangles_become_storeys_with_one_opening_per_bay():
    doc = convert.convert(
        program(
            structure={
                "floors": [
                    {
                        "bays": ["shop", "door", "shop"],
                        "role": "base",
                        "head": "flat",
                        "surround": "none",
                    },
                    {
                        "bays": ["window", "blank", "window"],
                        "role": "top",
                        "head": "round",
                        "surround": "frame",
                    },
                ],
                "cornice": "projecting",
                "string_courses": [1],
            }
        )
    )
    assert description.errors(doc) == []
    ground, upper = doc["walls"][0]["storeys"]
    assert [doc["opening_types"][o["type"]]["kind"] for o in ground["openings"]] == [
        "shopfront",
        "door",
        "shopfront",
    ]
    assert (
        len(upper["openings"]) == 2
        and doc["opening_types"][upper["openings"][0]["type"]]["head"] == "round"
    )
    assert [b["kind"] for b in doc["walls"][0]["bands"]] == ["string_course", "eaves_cornice"]

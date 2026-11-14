import numpy as np
import shapely

from turku3d.coarse.inputs import Building
from turku3d.facade_kit import renderer


def house(year=1905, levels=1, eaves=5.5, width=16.0, material="wood"):
    renderer()
    from turkutex.facade_archetypes import sample

    b = Building("h", shapely.box(0, 0, width, 9), 10.0, 10.0 + eaves + 3.5, "facade_plaster")
    b.facade_key = "h"
    b.attributes = {"construction_year": year, "material_class": material, "levels": levels,
                    "levels_source": "register", "use": "1010", "register_id": "h"}
    walls = [dict(wall_id="h:0", outline=True, width_m=width, fragment_width_m=width, uv_offset=0,
                  height_m=eaves, layout_height_m=eaves)]
    program = sample("h", b.attributes, walls)
    v = np.array([[0, 10.0, 0], [width, 10.0, 0], [width, 10.0 + eaves, 0], [0, 10.0 + eaves, 0]], float)
    return b, program, v


def test_old_wooden_house_is_one_storey_on_a_stone_plinth_with_small_windows():
    from turku3d.facade_timber import is_old_timber, timber_wall

    b, program, v = house()
    assert program["archetype"] == "timber_traditional" and is_old_timber(b, program)
    # The sampled grid stretched the register's one storey over the whole eaves wall.
    assert program["walls"][0]["windows"]["size_m"]["value"][1] > 2.5
    w = timber_wall(program["walls"][0], v, b, program)
    plinth = w["material_bands"][0]
    assert plinth["material"] in ("stone", "brick") and 0.6 <= plinth["height_m"] <= 1.35
    assert plinth["colour"].startswith("#") and plinth["relief_m"] > 0
    windows = w["windows"]["rectangles_m"]
    assert windows and w["bays"]["count"] == 0
    assert {round(y, 2) for _, y, _, _ in windows} == {round(plinth["height_m"] + 0.95, 2)}  # one row
    assert all(0.9 <= ww <= 1.15 and 1.5 <= wh <= 1.75 for _, _, ww, wh in windows)
    assert set(w["windows"]["surround_shapes"]) == {"frame"}
    assert w["windows"]["divisions"]["value"] == [2, 3]  # six panes before the 1920s
    assert w["timber_old"]["storeys"] == [3.6] and w["timber_old"]["knee_m"] >= 0.8


def test_register_storeys_are_kept_where_they_fit_and_later_or_undated_timber_is_not_the_class():
    from turku3d.facade_timber import is_old_timber, layout, timber_wall
    from turkutex.facade_archetypes import TIMBER_OLD

    plinth, storeys, knee = layout(8.6, 2, 0.7, TIMBER_OLD)
    assert len(storeys) == 2 and all(2.7 <= h <= 3.6 for _, h in storeys)
    # A register count the shell has no room for counts the rooms in the roof: one storey.
    plinth, storeys, knee = layout(5.2, 2, 0.7, TIMBER_OLD)
    assert len(storeys) == 1
    b, program, v = house(year=1930, levels=2, eaves=8.6)
    w = timber_wall(program["walls"][0], v, b, program)
    rows = sorted({round(y, 2) for _, y, _, _ in w["windows"]["rectangles_m"]})
    assert len(rows) == 2 and w["windows"]["divisions"]["value"] == [2, 1]  # T-windows from 1920
    assert w["trim"]["string_courses"]["value"]  # a belt board at the storey line
    for kwargs in (dict(year=1955), dict(year=None), dict(material="plaster")):
        b, program, _ = house(**kwargs)
        assert not is_old_timber(b, program)
    b, program, _ = house()
    assert not is_old_timber(b, {**program, "description_source": "existing"})


def test_old_wooden_house_paints_a_coloured_projecting_plinth_and_framed_windows():
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_programs import compile_wall
    from turku3d.facade_timber import timber_wall

    b, program, v = house()
    w = timber_wall(program["walls"][0], v, b, program)
    style = compile_wall(w, program["storeys"]["value"])
    style["origin"][1] = b.base
    parts = list(wall_parts(v, w, style))
    plinth = w["material_bands"][0]
    bands = [s for _, _, s in parts if s.get("wall") == plinth["colour"]]
    assert bands and all(s.get("relief_m") == plinth["relief_m"] for s in bands)
    assert all(s["module"] in (42 + 1, 42 + 2) for s in bands)  # brick or stone cells, not boards
    windows = [s for _, _, s in parts if s["module"] < 42 and s["detail"][3] % 4 == 1]
    assert len(windows) == len(w["windows"]["rectangles_m"])
    casings = [s for _, _, s in parts if s.get("relief_m") == 0.055]
    assert len(casings) >= 4 * len(windows) - len(windows)  # four casing strips per window


def test_a_wall_fragment_is_laid_out_over_its_whole_outline_edge():
    """A gable end comes as several quads; one 3.8 m fragment 7.2 m along a 11 m edge must paint in
    the edge's metres (the shader reads u + uv_offset against the wall size: outside it, the whole
    fragment turned into corner-board trim, white, in the first Portsa stills)."""
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_programs import compile_wall
    from turku3d.facade_timber import timber_wall

    b, program, _ = house(width=11.0)
    wall = {**program["walls"][0], "uv_offset": 7.2, "fragment_width_m": 3.8}
    v = np.array([[7.2, 10.0, 0], [11.0, 10.0, 0], [11.0, 15.5, 0], [7.2, 15.5, 0]], float)
    w = timber_wall(wall, v, b, program)
    assert w["width_m"] == 11.0 and w["uv_offset"] == 7.2 and w["measured_size"][0] == 11.0
    whole = timber_wall(program["walls"][0], np.array([[0, 10.0, 0], [11.0, 10.0, 0], [11.0, 15.5, 0],
                                                       [0, 15.5, 0]], float), b, program)
    assert w["windows"]["rectangles_m"] == whole["windows"]["rectangles_m"]  # one rhythm per facade
    style = compile_wall(w, program["storeys"]["value"])
    style["origin"][1] = b.base
    parts = list(wall_parts(v, w, style))
    assert parts and style["size"][0] == 11.0
    for _, uv, s in parts:
        if s.get("detail", [0, 0, 0, 0])[3] > 0:
            continue  # window and door patches carry their own extent
        metres = uv[:, 0] - style["origin"][0]
        assert metres.min() >= 7.2 - 1e-6 and metres.max() <= 11.0 + 1e-6
    assert any(s["module"] in (43, 44) for _, _, s in parts)  # the plinth runs on under this fragment

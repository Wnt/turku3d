import numpy as np
import shapely

from turku3d.coarse.inputs import Building
from turku3d.facade_kit import renderer


def house(year=1905, levels=1, eaves=5.5, width=16.0, material="wood", milieu=None):
    renderer()
    from turkutex.facade_archetypes import sample

    b = Building("h", shapely.box(0, 0, width, 9), 10.0, 10.0 + eaves + 3.5, "facade_plaster")
    b.facade_key = "h"
    b.attributes = {"construction_year": year, "material_class": material, "levels": levels,
                    "levels_source": "register", "use": "1010", "register_id": "h", "milieu": milieu}
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

    plinth, storeys, knee, roof = layout(8.6, 2, 0.7, TIMBER_OLD)
    assert len(storeys) == 2 and all(2.7 <= h <= 3.6 for _, h in storeys) and roof == 0
    # A register count the shell has no room for counts the rooms in the roof: one storey, and the
    # second is a storey in the roof whose windows need a knee wall under the eaves.
    plinth, storeys, knee, roof = layout(5.2, 2, 0.7, TIMBER_OLD)
    assert len(storeys) == 1 and roof == 1 and knee >= 1.0
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


def gable_end(b, program, width=8.0, eaves=5.6, rise=4.0, corner=None):
    """The two shell quads of a symmetric gable end (as the shell cuts it under its sloped top);
    ``corner`` m: its corners' height when they sit under the eaves line."""
    base, corner = b.base, eaves if corner is None else corner
    quads = [np.array([[0, base, 0], [width / 2, base, 0], [width / 2, base + eaves + rise, 0],
                       [0, base + corner, 0]], float),
             np.array([[width / 2, base, 0], [width, base, 0], [width, base + corner, 0],
                       [width / 2, base + eaves + rise, 0]], float)]
    walls = [{**program["walls"][0], "width_m": width, "fragment_width_m": width / 2, "uv_offset": u,
              "height_m": eaves, "layout_height_m": eaves} for u in (0.0, width / 2)]
    return np.concatenate(quads), walls


def test_a_storey_in_the_roof_looks_out_under_the_eaves_and_from_the_gable():
    """Karjakatu 22 (1928, register 2 storeys, 5.6 m eaves): the photo has small windows under the
    eaves over a belt, two windows in the gable and an attic light; the first stills had one low row
    of windows under a blank wall."""
    from turku3d.facade_timber import edge_profiles, timber_wall

    b, program, v = house(year=1928, levels=2, eaves=5.6, width=11.0)
    w = timber_wall(program["walls"][0], v, b, program)
    t = w["timber_old"]
    assert t["roof_storeys"] == 1 and len(t["storeys"]) == 1 and t["knee_m"] >= 1.0
    rows = sorted({y for _, y, _, _ in w["windows"]["rectangles_m"]})
    assert len(rows) == 2 and t["eaves_windows"] >= 3
    eaves = [r for r in w["windows"]["rectangles_m"] if r[1] == rows[1]]
    cornice = w["trim"]["cornice"]["value"]
    assert all(0.45 <= h <= 0.7 and y + h <= 5.6 - cornice for _, y, _, h in eaves)  # under the eaves board
    assert w["trim"]["string_courses"]["value"][-1] < rows[1]  # the belt runs under them
    quads, walls = gable_end(b, program)
    gprogram = {**program, "walls": walls}
    profiles = edge_profiles(quads, gprogram, b.base)
    assert profiles[0] == profiles[1] and len(profiles[0]) == 2
    fragments = [timber_wall(walls[i], quads[i * 4:i * 4 + 4], b, gprogram, profiles[i]) for i in (0, 1)]
    assert fragments[0]["windows"]["rectangles_m"] == fragments[1]["windows"]["rectangles_m"]
    g = fragments[0]["timber_old"]
    assert g["gable_windows"] == 3 and g["eaves_windows"] == 0  # two beside the ridge, an attic light
    gable = [r for r in fragments[0]["windows"]["rectangles_m"] if r[1] > 4.0]
    for x, y, ww, wh in gable:  # every gable window stays under the verges
        assert y + wh + 0.3 <= 5.6 + 4.0 * (1 - abs(x + ww / 2 - 4.0) / 4.0) - 4.0 * ww / 8


def test_a_gable_over_storeys_that_fit_gets_one_attic_window_and_a_low_gable_none():
    from turku3d.facade_timber import edge_profiles, timber_wall

    b, program, _ = house(year=1905, levels=1, eaves=5.5)
    quads, walls = gable_end(b, program, eaves=5.5, rise=3.0)
    gprogram = {**program, "walls": walls}
    profiles = edge_profiles(quads, gprogram, b.base)
    w = timber_wall(walls[0], quads[:4], b, gprogram, profiles[0])
    assert w["timber_old"]["gable_windows"] == 1 and w["timber_old"]["roof_storeys"] == 0
    quads, walls = gable_end(b, program, eaves=5.5, rise=1.0)  # a low-pitched roof: no room
    gprogram = {**program, "walls": walls}
    w = timber_wall(walls[0], quads[:4], b, gprogram, edge_profiles(quads, gprogram, b.base)[0])
    assert w["timber_old"]["gable_windows"] == 0


def test_a_low_gable_over_a_tall_knee_wall_keeps_the_row_under_the_eaves_inside_its_wall():
    """Portsa (t0_-5_1, 103329807K): one 3.6 m storey on a 7.6 m eaves line, a gable rising 1.55 m
    whose corners sit 0.65 m under the eaves. Its first build left the gable end's 2.4 m knee wall
    blank (no roof storey, and no room for an attic light there) while the long walls looked out
    under the eaves."""
    from turku3d.facade_timber import _top, edge_profiles, timber_wall

    b, program, _ = house(year=1906, levels=1, eaves=7.6)
    quads, walls = gable_end(b, program, width=10.5, eaves=7.6, rise=1.55, corner=6.95)
    gprogram = {**program, "walls": walls}
    profile = edge_profiles(quads, gprogram, b.base)[0]
    w = timber_wall(walls[0], quads[:4], b, gprogram, profile)
    t = w["timber_old"]
    assert t["roof_storeys"] == 0 and t["knee_m"] >= 1.6 and t["eaves_windows"] >= 2
    rects = w["windows"]["rectangles_m"]
    head = min(y + h for _, y, _, h in rects)  # the storey's windows
    upper = [r for r in rects if head < r[1] < 7.6]
    assert len(upper) == t["eaves_windows"] and t["eaves_windows"] < len(rects) - len(upper) - t["gable_windows"]
    for x, y, ww, wh in upper:  # the corner bays' row windows would stick out of the wall: left out
        assert y + wh <= min(_top(profile, x), _top(profile, x + ww))


def test_the_plinth_is_the_areas_granite_in_the_town_and_concrete_under_the_1920s_suburbs():
    from turku3d.facade_timber import timber_wall

    def plinth(**kwargs):
        b, program, v = house(**kwargs)
        w = timber_wall(program["walls"][0], v, b, program)
        return w["timber_old"]["plinth_area"], w["material_bands"][0]

    area, band = plinth(year=1905, milieu="Puukaupunkimiljöö")  # Portsa
    assert area == "town" and band["material"] in ("stone", "brick") and band["height_m"] >= 1.0
    area, band = plinth(year=1928, levels=2, eaves=5.6)  # Itäharju, outside every milieu zone
    assert area == "suburb" and band["material"] == "concrete" and band["height_m"] >= 0.85
    area, band = plinth(year=1890)  # an old house outside the town keeps the low plinth
    assert area == "other" and band["material"] in ("stone", "brick") and band["height_m"] <= 1.35
    area, band = plinth(year=1925, milieu="Muuttuva kaupunkiympäristö")  # in the map, not the town
    assert area == "suburb"


def test_window_jambs_close_only_their_upright_edges():
    """The jambs stand between the head and sill casings: their level returns are hidden."""
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_programs import compile_wall
    from turku3d.facade_relief import relief_parts
    from turku3d.facade_timber import timber_wall

    b, program, v = house()
    w = timber_wall(program["walls"][0], v, b, program)
    style = compile_wall(w, program["storeys"]["value"])
    style["origin"][1] = b.base
    parts = list(wall_parts(v, w, style))
    jambs = [(p, uv, s) for p, uv, s in parts if s.get("returns") == "vertical"]
    windows = [s for _, _, s in parts if s["module"] < 42 and s["detail"][3] % 4 == 1]
    assert len(jambs) == 2 * len(windows)
    faces = [list(relief_parts(p, uv, s)) for p, uv, s in jambs]
    assert all(len(f) == 3 for f in faces)  # front and two upright returns, no level ones
    assert all("returns" not in s for f in faces for _, _, s in f)
    heads = [(p, uv, s) for p, uv, s in parts if s.get("relief_m") == 0.055 and "returns" not in s]
    assert all(len(list(relief_parts(p, uv, s))) == 5 for p, uv, s in heads)

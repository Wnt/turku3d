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
    # Portsa (t0_-6_2, 103329775J: register 3 storeys on 6.64 m): the town's 1.2 m plinth left room
    # for one storey where the earlier 0.7 m plinth had two; the register's storey wins.
    plinth, storeys, knee, roof = layout(6.64, 3, 1.2, TIMBER_OLD)
    assert len(storeys) == 2 and roof == 1 and 0.6 <= plinth < 1.0
    plinth, storeys, knee, roof = layout(7.0, 1, 1.2, TIMBER_OLD)  # a one-storey house keeps its plinth
    assert len(storeys) == 1 and roof == 0 and plinth >= 1.2
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
    """Portsa (t0_-5_1, 103329807K): one 3.6 m storey under a 7.6 m layout height, a gable end whose
    corners stand 6.95 m and ridge 9.15 m. Its first build left the gable end's knee wall blank
    while the long walls looked out under the eaves; the row runs under the corners' eaves line."""
    from turku3d.facade_timber import _top, edge_profiles, timber_wall

    b, program, _ = house(year=1906, levels=1, eaves=7.6)
    quads, walls = gable_end(b, program, width=10.5, eaves=7.6, rise=1.55, corner=6.95)
    gprogram = {**program, "walls": walls}
    profile = edge_profiles(quads, gprogram, b.base)[0]
    w = timber_wall(walls[0], quads[:4], b, gprogram, profile)
    t = w["timber_old"]
    assert t["roof_storeys"] == 0 and t["eaves_m"] == 6.95 and t["gable_windows"] == 1
    rects = w["windows"]["rectangles_m"]
    head = min(y + h for _, y, _, h in rects)  # the storey's windows
    upper = [r for r in rects if head < r[1] < 6.95]
    assert len(upper) == t["eaves_windows"] == len(rects) - len(upper) - 1  # one per bay, an attic light
    for x, y, ww, wh in upper:  # under the corners' eaves line, so inside the wall
        assert y + wh <= min(_top(profile, x), _top(profile, x + ww))


def test_the_row_under_the_eaves_follows_the_walls_own_top_under_a_taller_layout_height():
    """Portsa (t0_-5_1, 103321749M): the building's layout height is 9.06 m, its long walls stand
    7.4 m. Measured from 9.06 m the row sat above the wall (invisible, or cut by its top)."""
    from turku3d.facade_timber import timber_wall

    b, program, v = house(year=1906, levels=1, eaves=9.06, width=40.0)
    v[2:, 1] = b.base + 7.4
    w = timber_wall(program["walls"][0], v, b, program)
    t = w["timber_old"]
    assert t["eaves_m"] == 7.4 and t["eaves_windows"] >= 10
    assert all(y + h <= 7.4 - w["trim"]["cornice"]["value"] for _, y, _, h in w["windows"]["rectangles_m"])


def test_the_row_follows_the_blank_band_over_the_heads_and_a_lower_wing_draws_no_cut_window():
    """Portsa (t0_-5_1): 103329809M's long walls stand 6.71 m and 6.64 m; a knee threshold put the
    row on one and not the other. 103321751P's wing stands 5.36 m under the building's 7.69 m layout:
    its upper storey's windows ran through the eaves."""
    from turku3d.facade_timber import timber_wall

    rows = []
    for top in (6.71, 6.64):
        b, program, v = house(year=1906, levels=1, eaves=6.71, width=32.7, milieu="Puukaupunkimiljöö")
        v[2:, 1] = b.base + top
        rows.append(timber_wall(program["walls"][0], v, b, program)["timber_old"]["eaves_windows"])
    assert rows[0] == rows[1] >= 10
    b, program, v = house(year=1906, levels=2, eaves=7.69, width=5.4)
    v[2:, 1] = b.base + 5.36
    w = timber_wall(program["walls"][0], v, b, program)
    assert len(w["timber_old"]["storeys"]) == 2 and w["windows"]["rectangles_m"]
    assert all(y + h <= 5.36 for _, y, _, h in w["windows"]["rectangles_m"])


def test_a_top_sloping_to_one_end_is_no_gable_and_a_low_annex_piece_sets_no_eaves_line():
    """Itaharju (t0_9_-1, 103399308J): a long wall whose single shell quad slopes from 5.23 m to
    7.31 m drew one roof-storey window at its high end (and one past it); its gable end carries a
    2.54 m porch piece on the same edge, which set the eaves line to 2.54 m."""
    from turku3d.facade_timber import timber_wall

    b, program, v = house(year=1927, levels=2, eaves=5.23, width=10.35)
    v[2, 1] = b.base + 7.31
    w = timber_wall(program["walls"][0], v, b, program)
    t = w["timber_old"]
    assert t["gable_windows"] == 0 and t["eaves_m"] == 5.23
    assert all(0 <= x and x + ww <= 10.35 for x, _, ww, _ in w["windows"]["rectangles_m"])
    b, program, v = house(year=1927, levels=2, eaves=5.23, width=10.2)
    profile = [(0.0, 4.0, 5.23, 9.23), (4.0, 8.0, 9.23, 5.23), (8.0, 10.2, 2.54, 2.54)]
    w = timber_wall(program["walls"][0], v, b, program, profile)
    assert w["timber_old"]["eaves_m"] == 5.23 and w["timber_old"]["gable_windows"] == 3


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


def test_the_band_over_the_heads_is_measured_before_the_plinth_lift():
    """old-timber@3 (pp-wood2's evidence tiles: Tiilentekijankatu 9/11, Stalarminkatu 14/25 and four more):
    one-storey houses on 6.4-6.6 m walls had their plinth lifted to 1.5-1.6 m first, which left a
    2.15-2.35 m blank band over the window heads and no row under the eaves. The row is decided on the
    band before the lift; a wall whose band is short before the lift keeps its plain frieze."""
    from turku3d.facade_timber import timber_wall

    for eaves in (6.4, 6.5, 6.6):
        b, program, v = house(year=1906, levels=1, eaves=eaves, milieu="Puukaupunkimiljöö")
        w = timber_wall(program["walls"][0], v, b, program)
        t = w["timber_old"]
        assert t["plinth_m"] >= 1.5 and t["eaves_windows"] >= 5  # the lift stays; the row comes
        heads = max(y + h for _, y, _, h in w["windows"]["rectangles_m"] if y < 4)
        assert eaves - heads < 2.4  # the band after the lift alone would not have earned it
        assert all(y + h <= eaves - w["trim"]["cornice"]["value"] for _, y, _, h in w["windows"]["rectangles_m"])
    b, program, v = house(year=1906, levels=1, eaves=6.0, milieu="Puukaupunkimiljöö")
    assert timber_wall(program["walls"][0], v, b, program)["timber_old"]["eaves_windows"] == 0

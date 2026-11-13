"""The automatic tier: what an unread building is drawn as from register and map facts."""

import collections

from test_building_compile import gabled_shell
from turku3d.world.kinds.building import archetypes, describe, description
from turku3d.world.kinds.building import compile as compile_module


def automatic(register, width=12.0, depth=8.0, eaves=3.2, ridge=2.4, stored=None):
    shell = gabled_shell((0.0, 0.0), (width, 0.0), depth=depth, eaves=eaves, ridge=ridge)
    return describe.effective("test", shell, register, stored)


def triangles(output):
    count = collections.Counter()
    for part in output.parts:
        count[part.name] += len(part.triangles)
    return count


def test_a_brick_house_of_the_nineties_has_tile_eaves_a_porch_and_a_chimney():
    register = {"use": "1000", "year": 1994, "storeys": 1, "material": "tiili", "roof_colour": "#b49f91"}
    doc = automatic(register)
    assert doc["style"]["type"] == "detached_house"
    assert doc["roof"]["material"]["family"] == "concrete_tile"
    assert len(doc["roof"]["chimneys"]) == 1
    assert description.errors({k: v for k, v in doc.items() if k not in ("shell", "register")}) == []
    output = compile_module.compile(doc)
    count = triangles(output)
    assert count["doors"] and count["chimneys"] and count["eaves"] and count["attached"], count


def test_types_follow_function_size_and_year():
    def kind(register, **shape):
        return automatic(register, **shape)["style"]["type"]

    assert kind({"use": "1000", "year": 1905, "material": "puu", "storeys": 1}) == "wooden_house"
    assert kind({"use": "1000", "year": 1952, "material": "puu", "storeys": 2}) == "detached_house"
    assert kind({"use": "1010", "year": 1994, "material": "tiili", "storeys": 1}, width=40.0) == "row_house"
    assert kind({"use": "1010", "year": 1965, "material": "betoni", "storeys": 7}, eaves=21.0) == "slab_block"
    assert kind({"use": "Ei arvoa"}, width=6.0, depth=3.5, eaves=2.4, ridge=0.5) == "garage"
    assert kind({"use": "Ei arvoa"}, width=3.0, depth=2.5, eaves=2.2, ridge=0.4) == "outbuilding"
    assert kind({"use": "1310", "year": 1983, "material": "metallilevy", "storeys": 1}, width=40.0, depth=20.0) == "industrial"
    # An undated house among new houses is a new house, not one of the old wooden quarters.
    assert kind({"use": "1000", "material": "puu", "storeys": 2, "year_near": 2019}) == "detached_house"


def test_a_garage_has_its_door_and_no_windows_and_a_row_has_one_every_three_metres():
    single = compile_module.compile(automatic({"use": "1360", "year": 1994, "storeys": 1}, width=6.0, depth=3.6, eaves=2.5, ridge=0.6))
    count = triangles(single)
    assert count["doors"] and not count["glass"], count
    row = automatic({"use": "Ei arvoa"}, width=18.0, depth=3.5, eaves=2.5, ridge=0.0)
    assert row["style"]["type"] == "garage"
    free = [{"index": 0, "width": 18.0, "rise": 0.0, "apex": 9.0}, {"index": 1, "width": 6.0, "rise": 0.0, "apex": 3.0}]
    roles = archetypes.wall_roles(row, "garage", free, 4.5)
    assert len(roles[0]["garage"]) == 5 and roles[1] == {"blank": True}


def test_a_measured_wall_colour_replaces_the_palette_pick_within_the_familys_lightness():
    register = {"use": "1000", "year": 1994, "storeys": 1, "material": "tiili"}
    picked = automatic(register)["materials"]["wall"]
    measured = automatic(register, stored={"measured": {"wall": {"colour": "#7a4234", "conf": {"*": 0.7}}}})
    assert measured["materials"]["wall"]["family"] == picked["family"] == "brick"
    assert measured["materials"]["wall"]["colour"] == "#7a4234"
    unsure = automatic(register, stored={"measured": {"wall": {"colour": "#7a4234", "conf": {"*": 0.15}}}})
    assert unsure["materials"]["wall"]["colour"] == picked["colour"]
    # Between 0.2 and 0.5 the measurement counts half to all: the colour lies between the pick and the measurement.
    half = automatic(register, stored={"measured": {"wall": {"colour": "#7a4234", "conf": {"*": 0.2}}}})
    lp, lm, lh = (archetypes.lab(c) for c in (picked["colour"], "#7a4234", half["materials"]["wall"]["colour"]))
    assert all(abs(h - (p + m) / 2) < 1.0 for p, m, h in zip(lp, lm, lh))
    # Pixels in deep shade keep their hue; the wall is not drawn darker than brick is.
    dark = automatic(register, stored={"measured": {"wall": {"colour": "#2a1612"}}})
    assert archetypes.lab(dark["materials"]["wall"]["colour"])[0] >= 29.0
    # A described material wins over a measurement.
    stored = {"measured": {"wall": {"colour": "#7a4234"}}, "materials": {"wall": {"family": "plaster", "colour": "#e0e0e0"}}}
    assert automatic(register, stored=stored)["materials"]["wall"]["colour"] == "#e0e0e0"

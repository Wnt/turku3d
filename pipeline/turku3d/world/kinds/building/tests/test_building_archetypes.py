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


def test_a_new_business_block_in_the_stone_town_is_clad_to_fit_and_one_elsewhere_may_be_metal():
    register = {"use": "1050", "year": 2021, "storeys": 8, "material": "muu materiaali", "height_m": 28.0}
    old = automatic({**register, "old_share": 0.65, "milieu": "Liikekeskusta"}, width=40.0, depth=20.0, eaves=26.0, ridge=0.5)
    assert old["style"]["type"] == "commercial"
    assert old["materials"]["wall"]["family"] in ("stone", "ceramic_tile", "plaster")
    # An old street outside the town (an old village) does not make a new business block stone.
    village = automatic({**register, "old_share": 0.65}, width=40.0, depth=20.0, eaves=26.0, ridge=0.5)
    assert not archetypes.old_town(village) and archetypes.old_town(old)
    new_town = automatic({**register, "old_share": 0.1}, width=40.0, depth=20.0, eaves=26.0, ridge=0.5)
    assert new_town["materials"]["wall"]["family"] in ("sheet_metal", "plaster", "concrete")
    assert "#c48a76" in archetypes.OFFICE_SHEET_NEW


def test_a_sixties_house_with_high_eaves_is_one_storey_on_a_basement_and_an_old_house_of_other_material_is_wooden():
    from turku3d.world.kinds.building import priors

    doc = automatic({"use": "1000", "year": 1967, "storeys": 2, "material": "muu materiaali"}, eaves=6.2)
    assert doc["style"]["type"] == "detached_house"
    wall = priors.fitted_wall(doc, priors.resolve_wall(doc, None), 6.2, {"doors": [3.0]})
    kinds = [s["kind"] for s in wall["storeys"]]
    assert kinds[:2] == ["basement", "ground"], kinds
    assert wall["storeys"][0].get("openings"), "the door stays on the ground, in the basement storey"
    old = automatic({"use": "1000", "year": 1925, "storeys": 1, "material": "muu materiaali"})
    assert old["style"]["type"] == "wooden_house"
    assert old["materials"]["wall"]["family"] == "weatherboard"


def test_town_rules_hold_in_the_towns_milieus_only_and_a_customer_door_never_goes_in_a_party_wall():
    # An 1807 house the register calls ancillary is a house in the town, a hall outside it.
    store = {"use": "1360", "year": 1807, "storeys": 2, "material": "muu materiaali", "height_m": 7.9}
    assert automatic({**store, "milieu": "Avokorttelimiljöö"}, width=20.0, depth=8.0)["style"]["type"] == "wooden_house"
    assert automatic(store, width=20.0, depth=8.0)["style"]["type"] == "industrial"
    assert automatic({**store, "milieu": "Teollisuusmenneisyyteen pohjautuva miljöö"}, width=20.0, depth=8.0)["style"]["type"] == "industrial"
    # A restaurant mapped near the party wall of an old wooden house: its door goes in the open street wall.
    house = {"use": "1010", "year": 1787, "storeys": 1, "material": "puu", "premises": [[11.6, 1.0]]}
    walls = [{"index": 0, "width": 12.0, "rise": 0.0, "apex": 6.0, "height": 3.2, "p0": [0.0, 0.0], "p1": [12.0, 0.0],
              "normal": [0.0, -1.0], "party_m": 0.0},
             {"index": 1, "width": 8.0, "rise": 0.0, "apex": 4.0, "height": 3.2, "p0": [12.0, 0.0], "p1": [12.0, 8.0],
              "normal": [1.0, 0.0], "party_m": 8.0}]
    town = automatic({**house, "milieu": "Historiallinen Turku"}, width=12.0, depth=8.0)
    roles = archetypes.wall_roles(town, "wooden_house", walls, 1.6)
    assert roles[0].get("canopy") and roles[0].get("doors") and roles[0]["doors"] == roles[0]["signs"]
    assert not roles[1]
    elsewhere = archetypes.wall_roles(automatic(house, width=12.0, depth=8.0), "wooden_house", walls, 1.6)
    assert not elsewhere[0] and not elsewhere[1]


def test_a_mapped_premise_belongs_to_the_building_that_holds_it_or_the_nearest_on_its_street():
    import numpy as np
    import shapely
    from turku3d.world.kinds import building_shell

    found = [{"outline": shapely.box(0.0, 0.0, 10.0, 10.0), "address": "Linnankatu 29"},
             {"outline": shapely.box(10.0, 0.0, 20.0, 10.0), "address": "Läntinen Rantakatu 27"}]
    points = np.array([[10.4, 5.0], [10.0, 5.0], [5.0, -1.0], [5.0, -3.0]])
    owners = building_shell.premise_owners(points, ["", "Läntinen Rantakatu", "", ""], found)
    # Inside the second only (not both, though 0.4 m from the first); on the party line, the one on its street;
    # mapped at the door outside, the nearest; 3 m out, nobody's.
    assert {k: [list(p) for p in v] for k, v in owners.items()} == {1: [[10.4, 5.0], [10.0, 5.0]], 0: [[5.0, -1.0]]}
    assert building_shell.street_of("Läntinen Rantakatu 27 a") == "Läntinen Rantakatu"


def test_a_block_of_flats_carries_balconies_on_every_tall_wall_facing_the_sun_and_newer_blocks_have_single_lights():
    register = {"use": "1010", "year": 1975, "material": "betoni", "storeys": 6}
    doc = {**automatic(register, width=40.0, eaves=18.0), "id": "building/x"}
    # Two parts of one stepped front facing south-west, the back facing north-east, and a gable end.
    walls = [{"index": 0, "width": 30.0, "rise": 0.0, "apex": 15.0, "height": 18.0, "normal": [-0.5, -0.87]},
             {"index": 1, "width": 30.0, "rise": 0.0, "apex": 15.0, "height": 18.0, "normal": [-0.42, -0.91]},
             {"index": 2, "width": 40.0, "rise": 0.0, "apex": 20.0, "height": 18.0, "normal": [0.5, 0.87]},
             {"index": 3, "width": 12.0, "rise": 0.0, "apex": 6.0, "height": 18.0, "normal": [0.87, -0.5]}]
    for salt in range(40):  # a block whose draw gives it balconies
        doc["id"] = f"building/x{salt}"
        if archetypes.balcony(doc, "slab_block"):
            break
    roles = archetypes.wall_roles(doc, "slab_block", walls, 3.6)
    assert roles[0].get("balconies") and roles[1].get("balconies")
    assert not roles[2].get("balconies") and not roles[3].get("balconies")
    assert archetypes.block("eighties")["window"]["lights"]["pattern"] == "single"
    assert archetypes.block("new")["window"]["lights"]["pattern"] == "single"
    assert archetypes.block("sixties")["window"]["lights"]["pattern"] == "side_vent"

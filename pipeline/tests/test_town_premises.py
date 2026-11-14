"""The town milieu as a register fact and customer premises as shopfronts on their own building."""

import json


def zone_table(tmp_path, zones):
    from turku3d import config as C
    from turku3d.coarse import milieus

    import shapely

    def en(x0, z0, x1, z1):  # engine x/z box -> EPSG:3877 polygon
        return shapely.box(x0 + C.ORIGIN_E, C.ORIGIN_N - z1, x1 + C.ORIGIN_E, C.ORIGIN_N - z0)

    rows = [dict(id=str(k), type=kind, wkt=en(*box).wkt) for k, (kind, box) in enumerate(zones)]
    return milieus.write(dict(schema=milieus.SCHEMA, zones=rows),
                         tmp_path / "derived" / "milieus" / "milieus.json")


def test_milieu_is_a_fact_of_every_building_and_the_town_is_one_data_file(tmp_path):
    import shapely
    from turku3d.coarse import milieus
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.register import enrich

    path = zone_table(tmp_path, [("Liikekeskusta", (0, 0, 100, 100)), ("Puukaupunkimiljöö", (40, 40, 60, 60)),
                                 ("Saapumisen miljöö", (200, 0, 300, 100))])
    zones = milieus.read(path)
    assert milieus.source_paths(tmp_path) == [path] and milieus.source_paths(tmp_path / "x") == []

    def milieu(box):
        b = Building("b", shapely.box(*box), 0, 10, "facade_plaster")
        b.facade_key, b.attributes = "b", {}
        enrich(b, {}, dict(milieus=zones))
        return b.attributes.get("milieu")

    assert milieu((10, 10, 20, 20)) == "Liikekeskusta"
    assert milieu((45, 45, 55, 55)) == "Puukaupunkimiljöö"  # the smallest zone holding it
    assert milieu((220, 20, 230, 30)) == "Saapumisen miljöö"
    assert milieu((500, 500, 510, 510)) is None  # outside every zone: no fact, not the town
    town = json.loads(milieus.TOWN_FILE.read_text())
    assert set(town["town"]) == milieus.town() and not set(town["town"]) & set(town["not_town"])
    assert milieus.in_town(dict(milieu="Liikekeskusta")) and not milieus.in_town(dict(milieu="Saapumisen miljöö"))
    assert not milieus.in_town({})


def test_a_premise_belongs_to_one_building_on_its_own_street_never_across_it():
    import shapely
    from turku3d.coarse import premises

    class B:
        def __init__(self, box):
            self.footprint = shapely.box(*box)

    # Two buildings either side of a 12 m street running along z = 16; a third holding a smaller one.
    north, south = B((0, 0, 20, 10)), B((0, 22, 20, 32))
    big, small = B((40, 0, 70, 20)), B((60, 5, 68, 15))
    street = [(shapely.LineString([(-10, 16), (80, 16)]), "Linnankatu", "residential"),
              (shapely.LineString([(36, -20), (36, 40)]), "Aurakatu", "tertiary")]
    addresses = [(north.footprint, "linnankatu"), (south.footprint, "linnankatu")]
    buildings = [north, south, big, small]
    P = shapely.Point

    def owner(x, z, street_name=""):
        held = premises.owners([(P(x, z), "cafe", street_name)], buildings, street, addresses)
        return next((buildings[j] for j in held), None)

    assert owner(10, 5) is north  # inside
    assert owner(10, 11.2, "linnankatu") is north  # on the wall line, outside the footprint
    assert owner(10, 20.9, "linnankatu") is south
    assert owner(10, 16) is None  # mid-street: nothing within reach
    assert owner(10, 11.2, "aurakatu") is None  # a premise of another street is not this building's
    assert owner(64, 10) is small  # two footprints hold it: the smaller
    assert owner(64, 10, "aurakatu") is big  # ... unless only the larger stands on its street
    # A centreline between the point and a building within reach: never across the street.
    narrow = [(shapely.LineString([(-10, 10.6), (80, 10.6)]), "Linnankatu", "service")]
    assert premises.owners([(P(10, 11.2), "bar", "")], [north], narrow, []) == {}


def test_the_front_is_the_open_wall_nearest_the_premise_never_a_party_wall():
    import shapely
    from turku3d.coarse import premises

    home = shapely.box(0, 0, 12, 10)
    neighbour = shapely.box(12, -2, 30, 12)  # stands against the whole east wall
    # The premise is mapped next to the party wall: its front is the open street wall (z = 0), not x = 12.
    fronts = premises.fronts(home, [shapely.Point(11.0, 3.0)], [neighbour])
    assert [0.0, 0.0, 12.0, 0.0] in [sorted_edge(f) for f in fronts]
    assert all(not (f[0] == f[2] == 12.0) for f in fronts)
    # Without the neighbour the east wall is the nearest open wall and the street wall a corner within 8 m.
    alone = [sorted_edge(f) for f in premises.fronts(home, [shapely.Point(11.0, 3.0)], [])]
    assert [12.0, 0.0, 12.0, 10.0] in alone and [0.0, 0.0, 12.0, 0.0] in alone
    assert [0.0, 0.0, 0.0, 10.0] not in alone  # the far gable is more than a room's depth away
    # A facade traced in short pieces is one wall line; a wall shorter than 4 m never opens.
    bent = shapely.Polygon([(0, 0), (3, 0.1), (6, 0.15), (9, 0.1), (12, 0), (12, 10), (0, 10)])
    assert len(premises.fronts(bent, [shapely.Point(6, 1)], [])) >= 4
    assert premises.fronts(shapely.box(0, 0, 3.5, 3.5), [shapely.Point(1, 1)], []) == []


def sorted_edge(f):
    a, b = sorted([tuple(f[:2]), tuple(f[2:])])
    return [*a, *b]


def test_premises_open_town_buildings_only_and_not_over_a_mapped_shop():
    import shapely
    from turku3d.coarse import premises
    from turku3d.coarse.inputs import Building

    def building(key, box, **attributes):
        b = Building(key, shapely.box(*box), 0, 12, "facade_plaster")
        b.facade_key, b.attributes = key, dict(attributes)
        return b

    town = building("town", (0, 0, 12, 10), milieu="Liikekeskusta")
    harbour = building("harbour", (40, 0, 52, 10), milieu="Saapumisen miljöö")
    shop = building("shop", (80, 0, 92, 10), milieu="Liikekeskusta", ground_floor="shops")
    points = [(shapely.Point(x, 1), "restaurant", "") for x in (6, 46, 86)]
    premises.apply([town, harbour, shop], [], dict(premises=points))
    assert town.attributes["premise_uses"] == ["restaurant"] and town.attributes["premise_fronts"]
    assert "premise_fronts" not in harbour.attributes and "premise_fronts" not in shop.attributes


def test_walls_on_a_front_open_as_shopfronts_below_reviewed_walls():
    import numpy as np
    from turku3d.facade_programs import PREMISES, on_front, premise_shopfronts

    def quad(x0, z0, x1, z1, base=0.0, top=12.0):
        return [[x0, base, z0], [x1, base, z1], [x1, top, z1], [x0, top, z0]]

    vertices = np.asarray(quad(0, 0, 6, 0) + quad(6, 0, 12, 0) + quad(12, 0, 12, 10) + quad(0, 0, 4, 0, 6))
    fronts = [[0.0, 0.0, 12.0, 0.0]]
    assert on_front(vertices[0:4], fronts[0]) and on_front(vertices[4:8], fronts[0])
    assert not on_front(vertices[8:12], fronts[0])

    def wall(**extra):
        return {"bays": dict(count=3), "base": dict(variant=dict(value="windows"), shopfront=dict(value=False)),
                **extra}

    program = dict(walls=[wall(), wall(measured_wall_id="w1"), wall(), wall(bays=dict(count=0))])
    premise_shopfronts(program, vertices, fronts)
    first, reviewed, side, blank = program["walls"]
    assert first["base"]["variant"] == dict(value="shops", source=PREMISES, support=0.6)
    assert first["base"]["shopfront"]["value"] is True and first["premise_front"]
    assert reviewed["base"]["variant"]["value"] == "windows"  # what was read on the wall stays
    assert side["base"]["variant"]["value"] == "windows"  # not on the front
    assert blank["base"]["variant"]["value"] == "windows"  # no bays: stays closed


def test_spot_rebuild_class_is_the_surveyed_tile_list():
    from turku3d.migrations import facade_readings, old_timber, registry, town_premises

    chain = list(registry.CHAIN)
    assert chain.index(town_premises.MIGRATION) > chain.index(facade_readings.MIGRATION)
    assert chain.index(town_premises.MIGRATION) > chain.index(old_timber.MIGRATION)
    assert town_premises.affected(dict(tile="t0_-1_1", building_keys=["x"]))  # Linnankatu 27
    assert not town_premises.affected(dict(tile="t0_40_40"))
    assert not town_premises.affected({})
    assert len(town_premises.TILES) == 31


def test_an_old_wooden_house_opens_its_premise_wall_with_shop_windows():
    import numpy as np
    import shapely

    from turku3d.coarse.inputs import Building
    from turku3d.facade_kit import renderer
    from turku3d.facade_timber import is_old_timber, timber_wall

    renderer()
    from turkutex.facade_archetypes import TIMBER_OLD, sample

    eaves, width = 8.6, 16.0
    b = Building("h", shapely.box(0, 0, width, 9), 10.0, 10.0 + eaves + 3.5, "facade_plaster")
    b.facade_key = "h"
    b.attributes = {"construction_year": 1890, "material_class": "wood", "levels": 2,
                    "levels_source": "register", "use": "1010", "register_id": "h"}
    walls = [dict(wall_id="h:0", outline=True, width_m=width, fragment_width_m=width, uv_offset=0,
                  height_m=eaves, layout_height_m=eaves)]
    program = sample("h", b.attributes, walls)
    v = np.array([[0, 10.0, 0], [width, 10.0, 0], [width, 10.0 + eaves, 0], [0, 10.0 + eaves, 0]], float)
    assert is_old_timber(b, program)
    house = timber_wall(program["walls"][0], v, b, program)
    cafe = timber_wall({**program["walls"][0], "premise_front": True}, v, b, program)
    assert cafe["timber_old"]["shopfront"] and not house["timber_old"]["shopfront"]
    assert cafe["timber_old"]["storeys"] == house["timber_old"]["storeys"]  # the same house
    first = house["timber_old"]["plinth_m"] + house["timber_old"]["storeys"][0]
    shop = [r for r in cafe["windows"]["rectangles_m"] if r[1] < first]
    upper = [r for r in cafe["windows"]["rectangles_m"] if r[1] >= first]
    assert shop and all(TIMBER_OLD["shop_window_m"][0] - 0.01 <= w <= 1.81 and h > 1.9 for _, _, w, h in shop)
    assert all(abs(y - cafe["timber_old"]["plinth_m"] - TIMBER_OLD["shop_sill_m"]) < 0.01 for _, y, _, _ in shop)
    assert upper == [r for r in house["windows"]["rectangles_m"] if r[1] >= first]  # the dwelling above
    assert cafe["doors"]["candidates"] and not house["doors"]["candidates"]  # the premise's own door


def test_public_buildings_keep_their_own_front():
    from turku3d.facade_kit import renderer
    from turku3d.facade_programs import opens_premises

    renderer()
    fronts = dict(premise_fronts=[[0.0, 0.0, 12.0, 0.0]])
    town_house = dict(archetype="jugend")
    assert opens_premises(town_house, dict(fronts, use="1150"), None)  # flats over a cafe
    assert not opens_premises(town_house, dict(fronts, use="1150"), {"ground_floor": "windows"})  # described
    assert not opens_premises(town_house, dict(use="1150"), None)  # no premise
    assert not opens_premises(dict(archetype="civic"), dict(fronts, use="1150"), None)
    for use in ("theatre", "museum", "prison", "place_of_worship", "college", "community_centre"):
        assert not opens_premises(town_house, dict(fronts, osm_use=use), None), use
    assert not opens_premises(town_house, dict(fronts, use="2120"), None)  # the register's civic code
    assert opens_premises(town_house, dict(fronts, osm_use="hotel"), None)  # a hotel's restaurant opens


def test_small_historic_houses_keep_their_windows():
    from turku3d.facade_kit import renderer
    from turku3d.facade_programs import opens_premises

    renderer()
    fronts = dict(premise_fronts=[[0.0, 0.0, 12.0, 0.0]], use="1150")
    house = dict(archetype="empire")
    historic = dict(fronts, milieu="Historiallinen Turku", construction_year=1817)
    assert not opens_premises(house, dict(historic, levels=2), None)  # Linnankatu 2's cafe
    assert not opens_premises(house, dict(historic, levels=1, construction_year="1833"), None)  # a manor
    assert opens_premises(house, dict(historic, levels=3), None)  # a town block over its restaurant
    assert opens_premises(house, dict(historic), None)  # storeys unknown: the premise decides
    assert opens_premises(house, dict(historic, levels=2, construction_year=2003), None)  # glazed pavilion
    assert opens_premises(house, dict(historic, levels=1, construction_year=None), None)  # undated
    assert opens_premises(house, dict(fronts, milieu="Liikekeskusta", levels=2, construction_year=1817), None)


def test_a_premise_inside_a_public_building_opens_nothing():
    import shapely

    from turku3d.coarse.premises import open_premises, public_host

    school = shapely.box(0, 0, 30, 20)  # an old school's outline, its restaurant mapped inside
    found = [(shapely.Point(10, 10), "restaurant", ""), (shapely.Point(40, 10), "cafe", "")]
    assert public_host({"building": "school"}) and public_host({"building": "yes", "amenity": "theatre"})
    assert not public_host({"building": "apartments"}) and not public_host({"amenity": "school"})  # no outline
    assert open_premises(found, [school]) == found[1:]
    assert open_premises(found, []) == found

"""Blocks of flats: sun-facing balcony stacks by period, single lights since 1980 (``turku3d.facade_blocks``)."""

import numpy as np
import shapely

from turku3d import config as C


def block(monkeypatch, year=1965, levels=5, key="103000000A", length=40.0, depth=12.0, attrs=None):
    from turku3d import facade_assets as assets
    from turku3d import facade_programs as fp
    from turku3d.coarse.extrusion import extrude
    from turku3d.coarse.inputs import Building

    monkeypatch.setattr(assets, "building", lambda key: {})
    monkeypatch.setattr(fp, "measured", lambda key: None)
    monkeypatch.setattr(fp, "read_description", lambda key: None)
    # Local x = E, z = -N: the long walls face north (z < 0) and south (z > 0).
    footprint = shapely.box(0, 0, length, depth)
    height = levels * 3.0
    b = Building(key, footprint, 0.0, height, "facade_plaster")
    b.attributes = {"construction_year": year, "levels": levels, "levels_source": "register", "use": "1000",
                    "osm_use": "apartments", "register_id": key, "material_class": "concrete", **(attrs or {})}
    b.roof_parts = [(footprint, np.array([0.0, 0.0, height]))]
    walls = extrude(b)[4]
    return b, walls


def components(meshes):
    return [m for m in meshes if m.extras.get("surface") == "facade-component"]


def program(meshes):
    return meshes[0].extras["facade_program"]


def normal_en(mesh):
    """Outward normal of a component quad in E/N."""
    a, b, c = mesh.positions[mesh.indices[0]]
    n = np.cross(b - a, c - a)  # the rendered (counter-clockwise) front of its first triangle
    return np.array([n[0], -n[2]]) / np.linalg.norm(n[[0, 2]])


def find_key(predicate, prefix="1030"):
    from turku3d.facade_blocks import balcony

    return next(f"{prefix}{i:05d}A" for i in range(10_000) if predicate(balcony(f"{prefix}{i:05d}A", 1965)))


def test_balcony_types_and_shares_follow_the_readings_by_decade():
    from turku3d.facade_blocks import balcony

    keys = [f"10{i:07d}X" for i in range(2000)]
    for year, share, types in ((1950, 0.60, {"cantilever"}), (1965, 0.90, {"recessed", "cantilever"}),
                               (1985, 0.75, {"glazed"}), (2010, 0.90, {"glazed"})):
        parts = [balcony(k, year) for k in keys]
        found = [p for p in parts if p]
        assert abs(len(found) / len(keys) - share) < 0.04
        assert {p["type"] for p in found} == types
    assert balcony("10000001X", 1935) is None  # before 1940: not the rule's
    assert {balcony(k, 1950)["parapet"] for k in keys if balcony(k, 1950)} == {"solid"}
    assert {balcony(k, 2010)["parapet"] for k in keys if balcony(k, 2010)} == {"glass"}
    # The same pick as W2 (crc32 of "building/<key>/balcony"): stable per building.
    assert balcony("103000000A", 1965) == balcony("103000000A", 1965)


def test_balconies_stand_only_on_the_sun_facing_tall_walls_as_cheap_geometry(monkeypatch):
    from turku3d.coarse.stored_facades import wall_meshes

    key = find_key(lambda p: p and p["type"] == "cantilever")
    b, walls = block(monkeypatch, key=key)
    meshes = wall_meshes(b, walls, np.zeros(3))
    prog = program(meshes)
    assert prog["block_of_flats"]["type"] == "cantilever" and prog["block_of_flats"]["walls"] == 1
    parts = components(meshes)
    assert parts and all("component_box" in m.extras for m in parts)  # instanced boxes only
    # Every balcony stands in front of the south wall (N < the footprint's south edge = ORIGIN_N - 12).
    south = C.ORIGIN_N - 12.0
    for m in parts:
        n = C.ORIGIN_N - m.positions[:, 2]
        assert n.max() <= south + 1e-6 and n.min() >= south - 1.45  # 1.4 m sixties cantilevers
        assert m.positions[:, 1].min() >= 3.0 - 0.1  # never on the street storey
    # Four upper storeys, every second bay, a slab + front + two sides per balcony (12 triangles a box).
    stats = prog["block_of_flats"]
    assert stats["balconies"] == 4 * stats["stacks"] and stats["stacks"] >= 4
    assert len(parts) == 4 * stats["balconies"]
    assert sum(len(m.indices) for m in parts) == 12 * len(parts)
    # The archetype's painted rails are gone on every wall (no flat rails, no shader balcony flag).
    assert all(w["balconies"]["value"] == [] for w in prog["walls"])
    assert all(s["detail"][1] == 0 for s in meshes[-1].extras.get("facade_kit_walls", [])
               if "detail" in s)


def test_a_loggia_stack_moves_its_wall_back_and_glazed_panes_face_outward(monkeypatch):
    from turku3d.coarse.stored_facades import wall_meshes

    key = find_key(lambda p: p and p["type"] == "recessed")
    b, walls = block(monkeypatch, key=key)
    meshes = wall_meshes(b, walls, np.zeros(3))
    south = C.ORIGIN_N - 12.0
    parts = components(meshes)
    # The recess: wall surface moved 1.4 m into the block behind each stack.
    wall = next(m for m in meshes if m.extras.get("material_key") == "facade_kit")
    depth = south + 1.4  # N of the recessed face
    assert np.isclose(C.ORIGIN_N - wall.positions[:, 2], depth, atol=1e-3).any()
    assert all(C.ORIGIN_N - m.positions[:, 2].min() <= depth + 1e-3 for m in parts)

    from turku3d.facade_blocks import balcony

    key = next(f"1040{i:05d}A" for i in range(10_000) if balcony(f"1040{i:05d}A", 2010))
    b, walls = block(monkeypatch, year=2010, key=key)
    meshes = wall_meshes(b, walls, np.zeros(3))
    panes = [m for m in components(meshes) if "component_box" not in m.extras]
    assert panes and all(m.extras["material_key"] == "component_glass" for m in panes)
    front = [m for m in panes if abs(normal_en(m)[1]) > 0.9]
    sides = [m for m in panes if abs(normal_en(m)[0]) > 0.9]
    assert front and sides and len(sides) == 2 * len(front)
    boxes = [m for m in components(meshes) if "component_box" in m.extras]
    assert all(normal_en(m)[1] > 0.9 for m in boxes)  # a box's first face is its back, against the wall
    assert all(normal_en(m)[1] < 0 for m in front)  # south wall: panes face south
    for m in sides:  # each side pane faces away from its balcony's middle
        centre = m.positions[:, 0].mean()
        twin = min((s for s in sides if s is not m and np.isclose(s.positions[:, 1].min(), m.positions[:, 1].min())),
                   key=lambda s: abs(s.positions[:, 0].mean() - centre))
        assert np.sign(normal_en(m)[0]) == np.sign(centre - twin.positions[:, 0].mean())


def test_single_light_windows_since_1980_and_who_is_not_the_class(monkeypatch):
    from turku3d.coarse.stored_facades import wall_meshes

    b, walls = block(monkeypatch, year=1992)
    prog = program(wall_meshes(b, walls, np.zeros(3)))
    for w in prog["walls"]:
        if w["bays"]["count"]:
            assert w["windows"]["size_m"]["value"] == [1.4, 1.5]
            assert w["windows"]["divisions"]["value"] == [1, 1]
            assert w["windows"]["transom_fraction"]["value"] == 0
    b, walls = block(monkeypatch, year=1972)
    prog = program(wall_meshes(b, walls, np.zeros(3)))
    assert all("divisions" not in w["windows"] or w["windows"]["divisions"]["source"] != "block-of-flats"
               for w in prog["walls"])
    for attrs in ({"levels": 2}, {"use": "1150"}, {"osm_use": "office"}, {"construction_year": 1935},
                  {"construction_year": None}, {"levels_source": "osm"}):
        b, walls = block(monkeypatch, attrs=attrs)
        assert "block_of_flats" not in program(wall_meshes(b, walls, np.zeros(3)))


def test_a_described_building_keeps_its_description(monkeypatch):
    from turku3d.facade_blocks import is_block

    b, walls = block(monkeypatch)
    sampled = dict(archetype="sixties_slab", attributes=b.attributes)
    assert is_block(b, sampled)
    assert not is_block(b, {**sampled, "description_source": "existing"})
    assert not is_block(b, {**sampled, "archetype": "civic"})


def test_block_balconies_rebuilds_only_tiles_holding_a_block_of_flats():
    from turku3d.migrations import block_balconies, registry, town_premises

    assert registry.CHAIN.index(block_balconies.MIGRATION) > registry.CHAIN.index(town_premises.MIGRATION)
    keys = sorted(block_balconies.keys())
    assert len(keys) > 2000 and len(set(keys)) == len(keys)
    assert block_balconies.affected({"building_keys": ["x", keys[0]]})
    assert not block_balconies.affected({"building_keys": ["x"]}) and not block_balconies.affected({})

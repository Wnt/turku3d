import json
import time
from pathlib import Path

import numpy as np
from turku3d.world.kinds.building import compile as compile_module
from turku3d.world.kinds.building import describe, description, glb

FIXTURES = Path(__file__).parent / "fixtures"


def gabled_shell(p0, p1, depth=9.0, eaves=18.0, ridge=4.0, base=10.0):
    """A box with a gable roof whose front wall runs from p0 to p1 (left to right seen from outside)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    along = (p1 - p0) / np.linalg.norm(p1 - p0)
    inward = np.array(
        [-along[1], along[0]]
    )  # the outside is to the right of p0 -> p1 reversed: front faces -inward
    a, b = p0, p1
    c, d = p1 + inward * depth, p0 + inward * depth
    low, top = base, base + eaves

    def wall(q0, q1, peak=None):
        ring = [[*q0, low], [*q1, low], [*q1, top]]
        if peak is not None:
            ring.append([*((q0 + q1) / 2), top + peak])
        ring.append([*q0, top])
        return {"kind": "wall", "rings": [ring]}

    ra, rb = (a + d) / 2, (b + c) / 2
    surfaces = [
        wall(a, b),
        wall(b, c, ridge),
        wall(c, d),
        wall(d, a, ridge),
        {"kind": "roof", "rings": [[[*a, top], [*b, top], [*rb, top + ridge], [*ra, top + ridge]]]},
        {"kind": "roof", "rings": [[[*c, top], [*d, top], [*ra, top + ridge], [*rb, top + ridge]]]},
        {"kind": "ground", "rings": [[[*a, low], [*d, low], [*c, low], [*b, low]]]},
    ]
    return {
        "source": "test",
        "surfaces": surfaces,
        "terrain": [
            [[*a, low + 0.3], [*b, low + 0.3], [*c, low + 0.3], [*d, low + 0.3], [*a, low + 0.3]]
        ],
    }


def effective(name):
    stored = json.loads((FIXTURES / name).read_text())
    frame = stored["walls"][0]["frame"]
    eaves = 18.0 if "stone" in name else 5.5
    shell = gabled_shell(frame["p0"], frame["p1"], eaves=eaves, ridge=3.0)
    return describe.effective(stored["id"].split("/")[1], shell, stored.get("register", {}), stored)


def test_stone_apartment_compiles_to_closed_looking_parts():
    doc = effective("stone-apartment.json")
    assert description.errors(doc) == []
    # Processor time, so a busy machine does not fail the budget.
    started = time.process_time()
    output = compile_module.compile(doc)
    assert time.process_time() - started < 1.5
    names = {part.name for part in output.parts}
    assert {
        "walls",
        "plinth",
        "glass",
        "frames",
        "trim",
        "roof",
        "eaves",
        "doors",
        "attached",
        "chimneys",
    } <= names
    for part in output.parts:
        assert np.isfinite(part.positions).all() and np.isfinite(part.uvs).all()
        lengths = np.linalg.norm(part.normals, axis=1)
        assert np.allclose(lengths, 1.0, atol=1e-3), part.name
        a, b, c = (part.positions[part.triangles[:, k]] for k in range(3))
        face = np.cross(b - a, c - a)
        area = np.linalg.norm(face, axis=1)
        agree = np.einsum("ij,ij->i", face, part.normals[part.triangles[:, 0]])
        assert (agree[area > 1e-7] > 0).all(), f"{part.name}: a face is wound against its normal"
    # Six bays over four upper storeys, all glazed with the described T-frame: 3 lights each.
    glass = [p for p in output.parts if p.name == "glass" and "GLASS" in p.attributes]
    assert glass and all(p.attributes["GLASS"].shape[1] == 4 for p in glass)
    front = [line for line in output.report if "facade 0" in line or "does not fit" in line]
    assert not [line for line in front if "street" in line]


def test_wooden_house_gets_weatherboard_trim_and_brackets():
    output = compile_module.compile(effective("wooden-house.json"))
    families = {part.material.family for part in output.parts}
    assert {"weatherboard", "painted_wood", "rubble", "sheet_metal", "glass"} <= families
    eaves = [p for p in output.parts if p.name == "eaves"]
    assert (
        sum(len(p.triangles) for p in eaves) > 100
    )  # soffits, fascia boards and the carved brackets


def test_compile_is_deterministic_and_writes_a_glb(tmp_path):
    doc = effective("wooden-house.json")
    first, second = tmp_path / "a.glb", tmp_path / "b.glb"
    stats = glb.write(compile_module.compile(doc), first, doc["id"])
    glb.write(compile_module.compile(doc), second, doc["id"])
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[:4] == b"glTF" and stats["triangles"] > 500


def test_walls_of_set_back_storeys_do_not_turn_the_shell_inside_out():
    from turku3d.world.kinds.building import shell as shell_module

    def wall(a, b, z0, z1):  # outward: seen from outside, a is on the left
        return {"kind": "wall", "rings": [[[*a, z0], [*b, z0], [*b, z1], [*a, z1]]]}

    def ring(x0, y0, x1, y1):  # anticlockwise seen from above, so every wall below faces out
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    outer, inner = ring(0, 0, 20, 12), ring(4, 3, 16, 9)
    surfaces = [{"kind": "ground", "rings": [[[x, y, 0.0] for x, y in outer]]}]
    surfaces += [wall(outer[i], outer[(i + 1) % 4], 0.0, 6.0) for i in range(4)]
    # Twice as many walls stand inside the footprint (a set-back upper part, cut into pieces) as on its edge.
    for z0 in (6.0, 9.0):
        surfaces += [wall(inner[i], inner[(i + 1) % 4], z0, z0 + 3.0) for i in range(4)]
    shell = shell_module.read({"source": "test", "surfaces": surfaces})
    south = max(
        shell.facades,
        key=lambda f: f.outline.area if abs(f.normal[1]) > 0.9 and f.origin[1] < 1 else 0,
    )
    assert south.normal[1] < -0.9  # the street wall on the south edge still faces south


def test_roofing_follows_the_orthophoto_colour_until_somebody_describes_the_roof():
    from turku3d.world.kinds.building import describe

    pitched = {
        "surfaces": [{"kind": "roof", "rings": [[[0, 0, 5], [8, 0, 5], [8, 4, 8], [0, 4, 8]]]}]
    }
    house = describe.automatic("x", pitched, {"year": 1994, "storeys": 1, "roof_colour": "#ba615c"})
    assert house["roof"]["material"]["family"] == "clay_tile"
    red, green, blue = (int(house["roof"]["material"]["colour"][i : i + 2], 16) for i in (1, 3, 5))
    assert red > 1.8 * green and red < 0xBA  # deeper than the sunlit photo, same hue
    old = describe.automatic(
        "x", pitched, {"year": 1905, "storeys": 1, "material": "puu", "roof_colour": "#c69996"}
    )
    assert old["roof"]["material"]["family"] == "sheet_metal"
    grey = describe.automatic("x", pitched, {"year": 1994, "storeys": 1, "roof_colour": "#9c9b98"})
    assert grey["roof"]["material"]["family"] == "felt"
    assert "roof" not in describe.automatic("x", pitched, {"year": 1994})
    assert "roof" not in describe.automatic(
        "x", pitched, {"year": 1994, "roof_colour": "#96b17f"}
    )  # tree crowns
    stored = {"roof": {"material": {"family": "copper", "colour": "#5f8f7a"}}}
    assert (
        describe.effective("x", pitched, {"year": 1994, "roof_colour": "#ba615c"}, stored)["roof"]
        == stored["roof"]
    )
    eaves = describe.effective(
        "x",
        pitched,
        {"year": 1994, "storeys": 1, "roof_colour": "#ba615c"},
        {"roof": {"eaves": {"overhang_m": 0.6}}},
    )
    assert (
        eaves["roof"]["eaves"] == {"overhang_m": 0.6}
        and eaves["roof"]["material"]["family"] == "clay_tile"
    )

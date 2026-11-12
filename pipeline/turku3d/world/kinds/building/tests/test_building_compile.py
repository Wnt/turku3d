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
    started = time.perf_counter()
    output = compile_module.compile(doc)
    assert time.perf_counter() - started < 1.5
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

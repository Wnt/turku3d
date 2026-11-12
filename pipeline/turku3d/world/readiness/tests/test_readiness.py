"""The readiness check on shapes whose answer is known."""

import json
import struct

import numpy as np
import pytest
from turku3d.world.readiness import check, clusters, field, glbfile, measure

OPAQUE = glbfile.surface_of(
    {
        "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.4, 0.3, 1]},
        "extras": {"turku": {"family": "plaster", "params": {"colour": "#806650"}}},
    }
)


def box(size=(4.0, 3.0, 2.0), faces="xXyYzZ", at=(0.0, 0.0, 0.0), divisions=1):
    """An axis-aligned box as separate quads per face (split vertices, as a flat-shaded packer writes them), wound
    counter-clockwise from outside. `faces` chooses which sides exist; `divisions` splits every face evenly."""
    sx, sy, sz = size
    quads = {
        "x": [(0, 0, 0), (0, 0, sz), (0, sy, sz), (0, sy, 0)],
        "X": [(sx, 0, 0), (sx, sy, 0), (sx, sy, sz), (sx, 0, sz)],
        "y": [(0, 0, 0), (sx, 0, 0), (sx, 0, sz), (0, 0, sz)],
        "Y": [(0, sy, 0), (0, sy, sz), (sx, sy, sz), (sx, sy, 0)],
        "z": [(0, 0, 0), (0, sy, 0), (sx, sy, 0), (sx, 0, 0)],
        "Z": [(0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)],
    }
    positions, triangles = [], []
    for name in faces:
        a, b, c, d = (np.array(p, float) for p in quads[name])
        for i in range(divisions):
            for j in range(divisions):
                u0, u1, v0, v1 = (
                    i / divisions,
                    (i + 1) / divisions,
                    j / divisions,
                    (j + 1) / divisions,
                )
                corners = [
                    a + (b - a) * u + (d - a) * v
                    for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
                ]
                base = len(positions)
                positions += corners
                triangles += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return np.array(positions) + np.array(at), np.array(triangles)


def primitive(positions, triangles, material=OPAQUE, part="body", uvs=None):
    return glbfile.Primitive(
        part=part, positions=positions, triangles=triangles, material=material, uvs=uvs
    )


def flagged(report, rule):
    return [flag for flag in report["flags"] if flag.startswith(rule)]


def test_closed_box_is_one_solid_with_an_inside():
    report, _ = check.check_mesh([primitive(*box())], "building")
    assert report["opaque"]["border_m"] == 0 and report["solids"] == {
        "count": 1,
        "closed": 1,
        "closed_area_share": 1.0,
        "inside_out": 0,
        "thinnest_m": pytest.approx(2 * 24 / 52, abs=1e-3),
        "thin": 0,
        "volume_m3": 24.0,
    }
    assert report["field"]["state"] == "sealed" and report["thickness"]["escape_share"] == 0
    assert not flagged(report, "G") and not flagged(report, "F")


def test_box_without_a_bottom_is_open_and_leaks_a_sixth():
    report, _ = check.check_mesh([primitive(*box(faces="xXYzZ"))], "building")
    assert report["opaque"]["open_m"] == pytest.approx(12.0) and report["field"]["state"] == "open"
    assert report["thickness"]["escape_share"] == pytest.approx(1 / 6, abs=0.02)
    assert flagged(report, "G2") and flagged(report, "F1")


def test_t_junction_is_told_from_an_opening():
    """A box whose top is cut in two while the walls below keep one long edge: closed to the eye, cracked to a
    simplifier."""
    positions, triangles = box(faces="xXyzZ")
    top_a, tri_a = box(size=(2.0, 3.0, 2.0), faces="Y")
    top_b, tri_b = box(size=(2.0, 3.0, 2.0), faces="Y", at=(2.0, 0.0, 0.0))
    report, _ = check.check_mesh(
        [
            primitive(
                np.vstack([positions, top_a, top_b]),
                np.vstack([triangles, tri_a + len(positions), tri_b + len(positions) + len(top_a)]),
            )
        ],
        "building",
    )
    assert report["opaque"]["tjunction_m"] == pytest.approx(8.0) and report["opaque"][
        "open_m"
    ] == pytest.approx(0.0, abs=1e-6)
    assert flagged(report, "G3") and not flagged(report, "G2 open")


def test_near_miss_and_inside_out_are_named():
    positions, triangles = box()
    positions[12:16] += [
        0.0007,
        0.0,
        0.0,
    ]  # the top face (the fourth quad) misses the walls by 0.7 mm
    report, _ = check.check_mesh([primitive(positions, triangles)], "building")
    assert report["opaque"]["nearmiss_m"] > 10 and report["opaque"]["open_m"] < 0.1
    flipped, _ = check.check_mesh([primitive(*[box()[0], box()[1][:, ::-1]])], "building")
    assert flipped["solids"]["inside_out"] == 1 and flagged(flipped, "G2")


def test_blended_glass_does_not_close_a_wall_and_thin_parts_are_not_leaks():
    walls = primitive(*box(faces="xXyYz"))
    glass = primitive(
        *box(faces="Z"),
        material=glbfile.surface_of(
            {"alphaMode": "BLEND", "extras": {"turku": {"family": "glass", "params": {}}}}
        ),
    )
    report, _ = check.check_mesh([walls, glass], "building")
    assert report["field"]["state"] == "open" and report["materials"]["surface_share"][
        "blend"
    ] == pytest.approx(12 / 52, abs=1e-3)
    post, _ = check.check_mesh([primitive(*box(size=(0.08, 3.0, 0.08)))], "signpost")
    assert post["field"]["thin_object"] and not post["flags"]
    plate = glbfile.surface_of(
        {"doubleSided": True, "extras": {"turku": {"family": "metal", "params": {}}}}
    )
    sign, _ = check.check_mesh(
        [primitive(*box(size=(0.6, 0.6, 0.0), faces="Z"), material=plate)], "signpost"
    )
    assert flagged(sign, "F5")


def test_ground_layers_and_borders():
    grid, triangles = box(size=(10.0, 0.0, 10.0), faces="Y", divisions=8)
    single, _ = check.check_mesh([primitive(grid, triangles)], "ground")
    assert single["ground"]["stacked_share"] == 0 and not single["flags"]
    paint, paint_triangles = box(size=(4.0, 0.0, 10.0), faces="Y", at=(3.0, 0.01, 0.0))
    stacked, _ = check.check_mesh(
        [primitive(grid, triangles), primitive(paint, paint_triangles, part="markings")], "ground"
    )
    assert stacked["ground"]["stacked_share"] == pytest.approx(0.4, abs=0.05) and flagged(
        stacked, "F4"
    )
    coarse, _ = box(size=(10.0, 0.0, 10.0), faces="Y", divisions=4, at=(10.0, 0.0, 0.0))
    cells = {
        name: (points, (points.min(axis=0), points.max(axis=0)))
        for name, points in (("a", grid), ("b", coarse))
    }
    assert check.border_mismatch(cells) == [
        {"cells": ["a", "b"], "vertices": [9, 5], "unmatched": 4}
    ]
    same, _ = box(size=(10.0, 0.0, 10.0), faces="Y", divisions=8, at=(10.0, 0.0, 0.0))
    assert (
        check.border_mismatch({"a": cells["a"], "b": (same, (same.min(axis=0), same.max(axis=0)))})
        == []
    )


def test_materials_uv_and_repeats():
    positions, triangles = box()
    metric = np.column_stack([positions[:, 0] + positions[:, 2], positions[:, 1]])
    stretched = primitive(positions, triangles, uvs=metric * [3.0, 1.0])
    unknown = glbfile.surface_of(
        {
            "pbrMetallicRoughness": {"baseColorFactor": [0.6, 0.6, 0.6, 1], "roughnessFactor": 0.9},
            "extras": {"turku": {"family": "marzipan", "params": {"layers": {"a": 1}}}},
        }
    )
    report, _ = check.check_mesh(
        [stretched, primitive(*box(at=(9.0, 0.0, 0.0)), material=unknown)], "building"
    )
    assert report["uv"]["stretched_share"] > 0.5 and flagged(report, "M4")
    assert report["materials"]["unknown_families"] == ["marzipan"] and report["materials"][
        "nested_params"
    ] == ["marzipan.layers"]
    assert flagged(report, "M2") and len(flagged(report, "M1")) == 2
    bars = [box(size=(0.05, 1.0, 0.05), at=(0.3 * k, 0.0, 0.0)) for k in range(5)]
    solids = measure.topology(
        np.vstack([p for p, _ in bars]), np.vstack([t + 24 * k for k, (_, t) in enumerate(bars)])
    )["solids"]
    assert measure.repeats(solids) == {
        "shapes": 1,
        "copies": 5,
        "triangles": 60,
        "saved_triangles": 48,
    }


def test_glb_is_read_with_its_material_reference(tmp_path):
    positions, triangles = box()
    blob = positions.astype("<f4").tobytes() + triangles.astype("<u4").tobytes()
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "extras": {"entity": "building/x", "part": "walls"}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [
            {
                "alphaMode": "MASK",
                "doubleSided": True,
                "emissiveFactor": [1, 0.5, 0],
                "extras": {"turku": {"family": "foliage", "params": {"uv": "atlas"}}},
            }
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": triangles.size, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions) * 12},
            {"buffer": 0, "byteOffset": len(positions) * 12, "byteLength": triangles.size * 4},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    path = tmp_path / "box.glb"
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 28 + len(text) + len(blob))
        + struct.pack("<I4s", len(text), b"JSON")
        + text
        + struct.pack("<I4s", len(blob), b"BIN\0")
        + blob
    )
    (only,) = glbfile.read(path)
    assert (
        only.part == "walls"
        and only.triangles.shape == (12, 3)
        and np.allclose(only.positions, positions)
    )
    assert (
        only.material.family,
        only.material.surface,
        only.material.double_sided,
        only.material.emissive,
    ) == ("foliage", "masked", True, (1.0, 0.5, 0.0))


def test_flood_finds_a_gap_wider_than_a_voxel():
    positions, triangles = box(size=(8.0, 8.0, 8.0), divisions=4)
    sealed = field.enclosure(positions, triangles, voxel=0.25)
    assert sealed["state"] == "sealed" and sealed["inside_m3"][0] == pytest.approx(512, rel=0.2)
    holed = np.delete(triangles, [0, 1], axis=0)  # one 2 m panel missing
    assert field.enclosure(positions, holed, voxel=0.25)["state"] == "open"


def test_cluster_hierarchy_needs_few_triangles_far_away_only_when_welded():
    positions, triangles = box(size=(12.0, 9.0, 12.0), divisions=24)
    index, points = measure.weld(positions, measure.SNAP)
    cracked = np.arange(len(positions))[
        triangles
    ]  # every quad on its own vertices: all borders open
    results = clusters.hierarchies(
        {"welded": (points, index[triangles]), "cracked": (positions, cracked)}
    )
    if "skipped" in results["welded"]:
        pytest.skip(results["welded"]["skipped"])
    assert results["welded"]["needed"]["300m"] < 0.05
    assert results["cracked"]["needed"]["300m"] == 1.0

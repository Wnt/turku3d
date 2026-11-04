import math
from collections import Counter

import numpy as np
from turku3d.props import Prop, racks
from turku3d.props.models import model

PATH, STREET = 1, 2 | 1 | 1 << 3  # roads FOOT; CAR | FOOT | class 1


def graph(*ways):
    nodes, edges = [], []
    for flags, a, b in ways:
        edges.append([len(nodes) // 2, len(nodes) // 2 + 1, flags, 0])
        nodes += [*a, *b]
    return {"nodes": nodes, "edges": edges}


def rack(x, z, yaw=0.0, kind="bike_rack"):
    return Prop(f"GIS_Polkupyoraparkki:{x}:{z}", kind, x, 0.0, z, yaw)


def axis_off(yaw, direction):
    """Degrees (0-90) between a posed rack's row (local +X) and a way direction (dx, dz)."""
    row = (math.cos(yaw), -math.sin(yaw))
    dot = abs(row[0] * direction[0] + row[1] * direction[1]) / math.hypot(*direction)
    return math.degrees(math.acos(min(1.0, dot)))


def test_the_model_row_runs_along_local_x_with_each_u_in_yz():
    positions = np.asarray(model("bike_rack", "3").positions)
    assert np.ptp(positions[:, 0]) > 2 * np.ptp(positions[:, 2])  # three stands along X
    feet = positions[positions[:, 1] < 0.01]
    assert set(np.round(np.abs(feet[:, 2]), 1)) <= {0.3}  # both legs of each U at z = +-0.3


def test_a_rack_beside_a_street_runs_along_the_kerb_front_to_the_road():
    r = rack(0.0, 0.0, yaw=0.0)  # row square to the street, the old kerb_parallel heading
    stats = Counter()
    racks.place([r], graph((STREET, (-30, 4.0), (30, 4.0))), stats)
    assert r.orientation == "rack_kerb_row" and axis_off(r.yaw, (1, 0)) < 1
    assert math.cos(r.yaw) > 0.999  # front +Z toward the street at z = 4
    assert stats["rack_kerb_row"] == 1


def test_a_nearer_footpath_sets_the_row_and_the_street_the_front():
    roads = graph((PATH, (-30, -2.0), (30, 8.0)), (STREET, (-30, 6.0), (30, 6.0)))
    r = rack(0.0, 0.0, kind="bike_station")
    racks.place([r], roads)
    assert r.orientation == "rack_path_row" and axis_off(r.yaw, (60, 10)) < 1
    front = (math.sin(r.yaw), math.cos(r.yaw))
    assert front[1] > 0  # the terminal faces the street side (+z)


def test_far_from_any_way_the_register_heading_stays_and_other_kinds_are_untouched():
    r, b = rack(0.0, 0.0, yaw=0.3), Prop("b", "bench", 0.0, 0.0, 0.0, 1.0)
    stats = Counter()
    racks.place([r, b], graph((STREET, (100, 0), (100, 50))), stats)
    assert r.yaw == 0.3 and b.yaw == 1.0 and stats["rack_street_heading"] == 1
    racks.place([r], None)
    assert r.yaw == 0.3


def test_without_ways_a_kerb_parallel_heading_turns_onto_the_kerb():
    r = rack(0.0, 0.0, yaw=0.0)
    r.orientation = "kerb_parallel"  # +Z along a road running along z
    racks.place([r], None)
    assert r.orientation == "rack_kerb_row" and axis_off(r.yaw, (0, 1)) < 1


def test_diagonal_way():
    r = rack(0.0, 0.0, yaw=2.0)
    racks.place([r], graph((STREET, (-10, -12), (20, 18))))
    assert axis_off(r.yaw, (1, 1)) < 1


def batched_tile(tmp_path, props):
    """A finished product whose visual GLB holds the props as one merged batch with object rows."""
    import hashlib
    import json

    from turku3d.migrations import core

    positions, normals, indices, rows = [], [], [], []
    for p in props:
        mesh = model(*p.prototype)
        local = np.asarray(mesh.positions, np.float64)
        c, s = math.cos(p.yaw), math.sin(p.yaw)
        world = np.c_[p.x + c * local[:, 0] + s * local[:, 2], p.y + local[:, 1], p.z - s * local[:, 0] + c * local[:, 2]]
        base = sum(len(v) for v in positions)
        tri = np.asarray(mesh.indices, np.int64).reshape(-1) + base
        rows.append({"object_id": p.object_id, "prop_type": p.kind, "start": sum(len(i) for i in indices),
                     "count": len(tri)})
        positions.append(world)
        normals.append(np.tile([0.0, 1.0, 0.0], (len(local), 1)))
        indices.append(tri)
    pos = np.concatenate(positions).astype("<f4").tobytes()
    nor = np.concatenate(normals).astype("<f4").tobytes()
    idx = np.concatenate(indices).astype("<u4").tobytes()
    binary = pos + nor + idx
    n = len(pos) // 12
    document = {
        "asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "byteStride": 12},
                        {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nor), "byteStride": 12},
                        {"buffer": 0, "byteOffset": 2 * len(pos), "byteLength": len(idx)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
                      {"bufferView": 2, "componentType": 5125, "count": len(idx) // 4, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
        "nodes": [{"mesh": 0, "extras": {"layer": "props", "objects": rows}}],
    }
    vis = core.glb_pack(document, binary)
    digest = hashlib.sha256(vis).hexdigest()
    (tmp_path / f"vis.{digest}.glb").write_bytes(vis)
    manifest = {"tile": "t0_0_0", "visual": {"t0_0_0": {"url": f"vis.{digest}.glb", "sha256": digest, "bytes": len(vis)}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return core.Product(tmp_path, manifest)


def test_migration_turns_drawn_rows_about_their_origin_and_records_the_rule(tmp_path, monkeypatch):
    from turku3d.migrations import core, furniture
    from turku3d.migrations import racks as migration

    source = rack(3.0, -2.0, yaw=0.0)  # the old kerb_parallel pose: row square to the street
    source.variant, source.orientation = "4", "kerb_parallel"
    station = rack(9.0, -2.0, yaw=0.0, kind="bike_station")
    bench = Prop("GIS_Varusteet:b", "bench", -6.0, 0.0, -2.0, 0.0)
    product = batched_tile(tmp_path, [source, station, bench])
    roads = graph((STREET, (13.0, -30), (13.0, 30)))  # along z, east of both
    monkeypatch.setattr(migration, "_tile_inputs", lambda tile: ([source, station, bench], roads))
    migration.transform(product)
    document, binary = core.glb_chunks(product.read(product.manifest["visual"]["t0_0_0"]["url"]))
    pos = furniture.read_accessor(document, binary, product, "", 0).astype(np.float64)
    idx = furniture.read_accessor(document, binary, product, "", 2)[:, 0]
    rows = document["nodes"][0]["extras"]["objects"]
    for p, row in zip((source, station), rows[:2], strict=True):
        assert row["orientation"] == "rack_kerb_row"
        yaw = math.radians(row["yaw_deg"])
        assert axis_off(yaw, (0, 1)) < 0.1 and math.sin(yaw) > 0.999  # along the street, front to it
        world = pos[np.unique(idx[row["start"]: row["start"] + row["count"]])]
        local = np.asarray(model(*p.prototype).positions, np.float64)
        (x, z), residual = furniture.fit_origin(world, yaw, (p.x, p.z), local)
        assert residual < 0.01 and math.hypot(x - p.x, z - p.z) < 0.01  # turned in place
    bench_row = rows[2]
    assert "orientation" not in bench_row
    assert product.manifest["furniture"]["racks"] == {"rack_kerb_row": 2, "rack_turned": 2, "racks": 2}

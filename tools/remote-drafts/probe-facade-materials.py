"""Audit actual generated cladding ranges after first/full OCI tile builds."""

import argparse
import importlib.util
import json
import pickle
import struct
import time
from pathlib import Path


def document(path):
    data = path.read_bytes() if isinstance(path, Path) else path
    assert data[:4] == b"glTF", path
    return json.loads(data[20 : 20 + struct.unpack_from("<I", data, 12)[0]])


def audit(out, expected, tile):
    from turku3d.facade_kit import MATERIALS

    programs = json.loads((out / "facade-programs.json").read_text())
    from turku3d.stage_cache import decompress_payload

    times = json.loads((out / "stage-times.json").read_text())
    layer = (
        out.parent / ".stages" / tile / "building_layer" / (times["building_layer"]["key"] + ".pkl")
    )
    _, files = pickle.loads(decompress_payload(layer.read_bytes()[32:]))
    doc = document(files["buildings.vis.glb"])
    nodes = [n["extras"] for n in doc["nodes"] if n.get("extras", {}).get("surface") == "wall"]
    assert nodes and not any(n.get("material_key") == "facade_stored" for n in nodes)
    result = {}
    for key, family in expected.items():
        own = [p for p in programs if p["building_id"] == key]
        paint = [
            s for n in nodes if n.get("building_id") == key for s in n.get("facade_kit_walls", [])
        ]
        assert own and paint, (key, "missing program or emitted paint")
        materials = {
            MATERIALS[s["module"] // 6 if s["module"] < 42 else s["module"] - 42]
            for s in paint
            if not (s["module"] == 48 and s["wall"] == "#55534e")
        }
        assert materials == {family}, (key, materials)
        walls = [w for p in own for w in p["walls"]]
        assert all(w["wall"]["material"]["value"] == family for w in walls)
        assert all(w["trim"]["material"]["value"] == family for w in walls)
        assert not any(w.get("paint_source") == "stored-facade-map" for w in walls)
        if key == "103295899S":
            assert any(w.get("measured_wall_id") for w in walls)
            assert any(not w.get("measured_wall_id") for w in walls)
            assert any(s["module"] == 44 for s in paint), "No blank stone bands/ornament"
            assert all(s["wall"] != "#dfc477" for s in paint)
        result[key] = dict(
            materials=sorted(materials),
            wall_fragments=len(walls),
            paint_ranges=len(paint),
            colours=sorted({s["wall"] for s in paint}),
            height_range=[
                min(s["origin"][1] for s in paint),
                max(s["origin"][1] + s["size"][1] for s in paint),
            ],
        )
    final = document(out / "vis.glb")
    assert final.get("textures"), "Packaged tile has no textures"
    assert not any(
        m.get("extras", {}).get("material_key") == "facade_stored" for m in final["materials"]
    )
    return result


def main(root):
    spec = importlib.util.spec_from_file_location(
        "build_probe", Path(__file__).with_name("probe-builds.py")
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    started = time.monotonic()
    results = []
    for tile, expected in [
        ("t0_0_1", {"103295899S": "stone", "103295904X": "concrete"}),
        ("t0_0_0", {"103295900T": "stone", "1032959104": "stone"}),
    ]:
        remaining = 510 - (time.monotonic() - started)
        assert remaining > 30, "No remaining build budget"
        row = probe.run(
            root,
            tile_id=tile,
            first_budget=min(45, remaining / 3),
            full_budget=min(180, remaining - 15),
        )
        row["materials"] = audit(Path(row["output"]), expected, tile)
        results.append(row)
        print(json.dumps(row), flush=True)
    print(
        json.dumps(
            dict(result="PASS", seconds=round(time.monotonic() - started, 3), builds=results)
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    main(parser.parse_args().root)

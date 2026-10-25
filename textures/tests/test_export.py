import json

import numpy as np
from turkutex.export import matches
from turkutex.streetview.facades import Facade, load_facades


def test_building_schema_and_safe_geometry_match(tmp_path):
    path = tmp_path / "facades.geojson"
    path.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[10, 20], [14, 20]]},
                        "properties": {
                            "object_id": "kanta1/facade/8",
                            "building_id": "kanta1",
                            "facade_index": 8,
                            "base_height": 3,
                            "top_height": 9,
                            "normal": [0, 0, 1],
                            "material_key": "facade_plaster",
                        },
                    }
                ]
            }
        )
    )
    wall = load_facades(path)[0]
    assert wall.id == "kanta1_8"
    assert wall.height_m == 6 and wall.base_h == 3
    assert wall.normal_side == "right"
    old = Facade("old", np.array([10, 20]), np.array([14, 20]), 6, 3, {"building_id": "kanta1"})
    assert matches(old, wall)
    old.height_m = 4
    assert not matches(old, wall)
    old.height_m = 6
    old.base_h = None
    assert not matches(old, wall)


def _export_partition(args):
    from turkutex.export import main

    main(args)


def test_parallel_merge_preserves_bindings_and_rejects_changed_inventory(tmp_path):
    import concurrent.futures
    import multiprocessing

    import pytest
    from turkutex.export import main
    from turkutex.post.pipeline import sha

    features = []
    for index in range(4):
        features.append(
            {
                "geometry": {"type": "LineString", "coordinates": [[10, 20], [14, 20]]},
                "properties": {
                    "object_id": f"building{index}/facade/0",
                    "building_id": f"building{index}",
                    "facade_index": 0,
                    "base_height": 3,
                    "top_height": 9,
                    "normal": [0, 0, 1],
                },
            }
        )
    inventory = tmp_path / "all.geojson"
    inventory.write_text(json.dumps({"features": features}))
    output = tmp_path / "assembly"
    batches = []
    for index, feature in enumerate(features):
        source = tmp_path / f"partition{index}"
        directory = source / "facades" / f"building{index}"
        directory.mkdir(parents=True)
        files = {"albedo": "0.ktx2", "normal": "0.normal.ktx2", "orm": "0.orm.ktx2"}
        for name in files.values():
            (directory / name).write_bytes(f"fixture-{index}-{name}".encode())
        (directory / "0.json").write_text(
            json.dumps({"files": files, "hashes": {n: sha(directory / n) for n in files.values()}})
        )
        selected = source / "selected.geojson"
        selected.write_text(json.dumps({"features": [feature]}))
        qa = source / "qa.json"
        qa.write_text(
            json.dumps(
                {
                    "inventory_sha256": sha(selected),
                    "rows": [
                        {"facade_id": f"building{index}_0", "complete": True, "street_facing": True}
                    ],
                }
            )
        )
        batches.append(
            [
                str(inventory),
                "--source-inventory",
                str(selected),
                "--source",
                str(source),
                "--qa",
                str(qa),
                "--output",
                str(output),
                "--merge",
            ]
        )
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        list(pool.map(_export_partition, batches))
    manifest = json.loads((output / "MANIFEST.json").read_text())
    assert manifest["bound"] == [f"building{i}_0" for i in range(4)]
    assert len(list((output / "facades").glob("*/*.ktx2"))) == 12
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert main(batches[0]) == 0
    inventory.write_text(json.dumps({"features": features[:1]}))
    with pytest.raises(ValueError, match="inventory changed"):
        main(batches[0])
    assert before == {
        p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()
    }

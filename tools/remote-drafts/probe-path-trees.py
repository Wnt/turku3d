"""Two reported forest tiles: real first/full output, visual envelopes and cache reuse."""

import importlib.util
import json
import os
import pickle
import shutil
import sys
from pathlib import Path


def main(root, assets):
    root.mkdir(parents=True, exist_ok=False)
    os.environ["TURKU3D_WORK"] = str(root)
    import numpy as np
    import shapely
    from turku3d import config as C
    from turku3d.coarse.forests import path_lines, path_surfaces, produce, trunk_base_radius
    from turku3d.coarse.inputs import gather
    from turku3d.coarse.sources import Sources
    from turku3d.col.materials import mat_id
    from turku3d.stage_cache import decompress_payload

    spec = importlib.util.spec_from_file_location("build_probe", Path(__file__).with_name("probe-builds.py"))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    tiles = ("t0_11_11", "t0_11_12")
    for tile in tiles:
        for name in (".inputs", ".stages"):
            source = assets / name / tile
            if source.exists():
                shutil.copytree(source, root / name / tile)
    for pattern in ("shared", ".surface-library*"):
        for source in assets.glob(pattern):
            if source.is_dir():
                shutil.copytree(source, root / source.name)
    report = []
    for tile_id in tiles:
        result = probe.run(root, tile_id=tile_id, first_budget=45, full_budget=120)
        tile = C.VisualTile.parse(tile_id)
        data = gather(tile, root, Sources())
        metrics = json.loads((Path(result["output"]) / "stage-times.json").read_text())

        def stage(name):
            path = root / ".stages" / tile_id / name / (metrics[name]["key"] + ".pkl")
            return pickle.loads(decompress_payload(path.read_bytes()[32:]))

        trees, bushes = stage("vegetation")
        assert trees, "real forest tile produced no trees"
        paths = shapely.union_all(path_lines(data.roads))
        assert not paths.is_empty
        margins = []
        for tree in trees + bushes:
            distance = paths.distance(shapely.Point(tree.x, tree.z))
            trunk = distance - trunk_base_radius(tree.variant) * tree.crown_radius
            crown = distance - tree.crown_radius
            assert trunk >= 2 - 1e-6 and crown >= 1 - 1e-6, (tree.object_id, trunk, crown)
            margins.append((trunk, crown))
        forest_paths = path_surfaces(data)
        assert forest_paths, "reported paths have no visible ground corridor"
        positions, indices, materials, _ = stage("surface_boundaries")
        corridor = shapely.union_all([g for g, _ in forest_paths])
        centers = positions[indices].mean(axis=1)
        inside = shapely.contains_xy(corridor, centers[:, 0], centers[:, 2])
        assert inside.any()
        assert not np.any(materials[inside] == mat_id("grass")), "forest floor still covers path"
        # Reproduce all tree sources from gathered inputs and the cached measured ground.
        from turku3d.coarse.build import draft_trees, grid_height
        grid, *_ = stage("terrain")
        shells = stage("buildings")
        expected, _ = produce(tile, data, lambda x, z: grid_height(tile, grid, x, z),
                             draft_trees(tile, grid, data.trees, data.water, shells))
        assert [t.record() for t in trees] == [t.record() for t in expected]
        structural = {name: metrics[name]["cached"] for name in
                      ("terrain", "bridges", "buildings", "tunnels", "stairs", "kerbs", "fences")}
        result.update(trees=len(trees), bushes=len(bushes), path_triangles=int(inside.sum()),
                      minimum_trunk_edge=min(p[0] for p in margins),
                      minimum_crown_edge=min(p[1] for p in margins), structural_cache_hits=structural,
                      stages={name: dict(cached=value.get("cached"), seconds=value["seconds"])
                              for name, value in metrics.items()})
        report.append(result)
        print(json.dumps(result), flush=True)
    (root / "clearance-result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))

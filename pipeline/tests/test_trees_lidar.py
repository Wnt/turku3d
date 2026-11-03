"""trees-lidar@1: lidar crowns of the drawn trees, written into the vegetation nodes' extras."""

import hashlib
import json

import numpy as np

from turku3d.migrations import core, registry, trees_lidar
from turku3d.vegetation.crowns import SHEAR_MAX, measure

TILE = "t0_0_0"


def crown_cells(x, z, top, radius, base=0.0, offset=(0.0, 0.0)):
    """Highest-return cells (0.5 m) of a dome crown centred at (x + dx, z + dz)."""
    cells = []
    for i in np.arange(-radius, radius + 0.01, 0.5):
        for j in np.arange(-radius, radius + 0.01, 0.5):
            d = np.hypot(i, j)
            if d <= radius:
                cells.append([x + offset[0] + i, base + top - 3 * (d / radius) ** 2, z + offset[1] + j])
    return np.asarray(cells)


def test_measure_sizes_unmeasured_trees_and_leans_offset_crowns():
    canopy = np.concatenate([crown_cells(10, 10, 18, 5), crown_cells(30, 10, 12, 3, offset=(1.5, 0)),
                             crown_cells(60, 10, 0.3, 2)])  # a hedge-high patch is no crown
    trees = [[10, 10, 0, 9, 2.5], [30, 10, 0, 12, 3], [60, 10, 0, 8, 2], [90, 10, 0, 10, 3]]
    big, lean, low, bare = measure(trees, canopy)
    assert 17.5 < big[0] <= 18 and 4.2 < big[1] < 5.1 and big[2:] == [0, 0]
    assert 11 < lean[0] <= 12 and 0 < lean[2] <= SHEAR_MAX and lean[3] == 0
    assert low is None and bare is None


def float_view(blob: bytearray, values) -> tuple[int, int]:
    data = np.asarray(values, dtype="<f4").tobytes()
    offset = len(blob)
    blob.extend(data)
    return offset, len(data)


def vegetation_glb(positions, scales, ids):
    blob = bytearray()
    views, accessors = [], []
    for values, kind in ((positions, "VEC3"), (scales, "VEC3")):
        offset, length = float_view(blob, values)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": length})
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": len(values), "type": kind})
    attributes = {"TRANSLATION": 0, "SCALE": 1}
    nodes = [{"name": "tile", "children": [1, 2]}] + [
        {"name": f"tree_default_fall:{k}", "mesh": 0, "extensions": {"EXT_mesh_gpu_instancing": {"attributes": attributes}},
         "extras": {"layer": "vegetation", "variant": "tree_default_fall", "instance_object_ids": ids}}
        for k in range(2)]
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(blob)}], "bufferViews": views,
                "accessors": accessors, "nodes": nodes, "meshes": [{"primitives": []}]}
    return core.glb_pack(document, bytes(blob))


def product(tmp_path, glb):
    digest = hashlib.sha256(glb).hexdigest()
    (tmp_path / f"vis.{digest}.glb").write_bytes(glb)
    manifest = {"tile": TILE, "visual": {TILE: {"url": f"vis.{digest}.glb", "sha256": digest, "bytes": len(glb),
                                                "h": [-3.0, 15.0]}}}
    return core.Product(tmp_path, manifest)


def test_migration_writes_crowns_into_every_vegetation_node(tmp_path, monkeypatch):
    canopy = np.concatenate([crown_cells(40, 40, 22, 5, base=4), crown_cells(100, 40, 15, 3, base=4)])
    monkeypatch.setattr(trees_lidar, "tile_canopy", lambda tile: canopy)
    glb = vegetation_glb([[40, 4, 40], [100, 4, 40], [200, 4, 200]], [[3, 10, 3], [3, 15, 3], [1, 1, 1]],
                         ["area:1:40:40", "puut:x", "bush:2"])
    p = product(tmp_path, glb)
    trees_lidar.transform(p)
    entry = p.manifest["visual"][TILE]
    assert entry["url"] != f"vis.{hashlib.sha256(glb).hexdigest()}.glb"
    document, binary = core.glb_chunks(p.read(entry["url"]))
    crowns = [n["extras"]["instance_crowns"] for n in document["nodes"][1:]]
    assert crowns[0] == crowns[1]
    area, register, bush = crowns[0]
    assert 21.5 < area[0] <= 22 and 4 < area[1] < 5.1
    assert 14.5 < register[0] <= 15 and bush is None
    assert entry["h"][1] == 4 + area[0]
    # Only the JSON changed: the binary chunk is the base's, and a rerun is byte-identical.
    assert binary == core.glb_chunks(glb)[1]
    (tmp_path / "again").mkdir()
    again = product(tmp_path / "again", glb)
    trees_lidar.transform(again)
    assert again.manifest == p.manifest


def test_tile_without_lidar_or_trees_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(trees_lidar, "tile_canopy", lambda tile: np.empty((0, 3)))
    glb = vegetation_glb([[40, 4, 40]], [[3, 10, 3]], ["puut:x"])
    p = product(tmp_path, glb)
    before = json.dumps(p.manifest, sort_keys=True)
    trees_lidar.transform(p)
    assert json.dumps(p.manifest, sort_keys=True) == before and not p.written


def test_registered_in_the_chain():
    assert trees_lidar.MIGRATION in registry.CHAIN
    assert trees_lidar.MIGRATION.key == "trees-lidar@1"


def test_canopy_needs_every_laz_block_on_disk(tmp_path, monkeypatch):
    from turku3d import config as C
    from turku3d.vegetation.crowns import canopy_cells

    monkeypatch.setattr(C, "raw_dir", lambda kind: tmp_path / kind)
    assert canopy_cells(C.VisualTile.parse(TILE)) is None

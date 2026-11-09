"""trees-lidar@3: lidar crowns of the drawn trees, written into the vegetation nodes' extras."""

import hashlib
import json

import numpy as np

from turku3d.migrations import core, registry, trees_lidar
from turku3d.vegetation.crowns import BASE_CANOPY, SHEAR_MAX, measure

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
    assert 17.5 < big[0] <= 18 and 4.2 < big[1] < 5.1 and big[2:] == [0, 0, 1, 0]
    assert 11 < lean[0] <= 12 and 0 < lean[2] <= SHEAR_MAX and lean[3] == 0
    assert low is None and bare is None


def test_measure_fits_an_elliptic_footprint():
    # A crown squeezed to 0.6 of its width along z (a facade or a row): stretched along x.
    cells = crown_cells(10, 10, 15, 5)
    cells = cells[np.abs(cells[:, 2] - 10) <= 3]
    (crown,) = measure([[10, 10, 0, 15, 4]], cells)
    assert 1.1 < crown[4] <= 1 / np.sqrt(0.75) + 1e-3 and (crown[5] < 0.05 or crown[5] > np.pi - 0.05)
    turned = cells[:, [2, 1, 0]]
    (crown,) = measure([[10, 10, 0, 15, 4]], turned)
    assert abs(crown[5] - np.pi / 2) < 0.05


def leaf_cells(x, z, radius, low, base=0.0):
    """Leaf cells (2 m; x, z, pulses, intensity, low, top) under a crown, ``low(dx, dz)`` m above ``base``."""
    rows = []
    for i in np.arange(-radius, radius + 0.01, 2.0):
        for j in np.arange(-radius, radius + 0.01, 2.0):
            if np.hypot(i, j) <= radius:
                rows.append([x + i, z + j, 10, 100, base + low(i, j), base + 12])
    return np.asarray(rows)


def test_crown_base_is_measured_on_the_open_side_of_a_crown_with_open_ground():
    # A 12 m crown at a stand edge: the stand (canopy over BASE_CANOPY) west of it, a field east. Its foliage reaches
    # 2 m on the field side and 7 m on the stand side (the half facing the field, trunk line included, is measured);
    # a solitary crown is measured all round; inside the stand none.
    edge, solitary, inner = crown_cells(50, 50, 12, 4), crown_cells(150, 50, 12, 4), crown_cells(250, 50, 12, 4)
    stand = np.asarray([[x, 10.0, z] for x in np.arange(30, 47.6, 0.5) for z in np.arange(30, 70.1, 0.5)])
    around = np.asarray([[x, 10.0, z] for x in np.arange(235, 265.1, 0.5) for z in np.arange(35, 65.1, 0.5)
                         if np.hypot(x - 250, z - 50) > 4])
    canopy = np.concatenate([edge, solitary, inner, stand, around])
    side = lambda i, j: 2.0 if i >= 0 else 7.0
    leaves = np.concatenate([leaf_cells(50, 50, 4, side), leaf_cells(150, 50, 4, lambda i, j: 3.5),
                             leaf_cells(250, 50, 4, lambda i, j: 1.0)])
    trees = [[50, 50, 0, 12, 4], [150, 50, 0, 12, 4], [250, 50, 0, 12, 4]]
    at_edge, alone, enclosed = measure(trees, canopy, leaves)
    assert at_edge[6] == round(2.0 / at_edge[0], 3)
    assert alone[6] == round(3.5 / alone[0], 3)
    assert enclosed[6] is None and len(enclosed) == 8 and enclosed[7] is None
    # Without leaf or gap cells the crowns keep the version-1 shape.
    assert [len(c) for c in measure(trees, canopy)] == [6, 6, 6]
    assert BASE_CANOPY < 10


def gap_cells(x, z, radius, ground, vegetation):
    """Gap cells (2 m; x, z, ground and vegetation returns) over a disc."""
    return np.asarray([[x + i, z + j, ground, vegetation] for i in np.arange(-radius, radius + 0.01, 2.0)
                       for j in np.arange(-radius, radius + 0.01, 2.0) if np.hypot(i, j) <= radius])


def test_openness_is_the_ground_share_of_the_returns_under_the_crown():
    # A dense roadside crown lets 1 return in 10 reach the ground, a solitary park crown 6 in 10; the ring of open lawn
    # beyond 0.6 r is not counted. Too few returns measure nothing.
    canopy = np.concatenate([crown_cells(50, 50, 12, 5), crown_cells(150, 50, 12, 5), crown_cells(250, 50, 12, 5)])
    lawn = gap_cells(50, 50, 9, 50, 0)
    lawn = lawn[np.hypot(lawn[:, 0] - 50, lawn[:, 1] - 50) > 4]
    gaps = np.concatenate([gap_cells(50, 50, 3, 2, 18), lawn, gap_cells(150, 50, 3, 12, 8),
                           gap_cells(250, 50, 3, 0, 1)[:1]])
    trees = [[50, 50, 0, 12, 5], [150, 50, 0, 12, 5], [250, 50, 0, 12, 5]]
    dense, open_, sparse = measure(trees, canopy, gaps=gaps)
    assert dense[7] == 0.1 and open_[7] == 0.6 and sparse[7] is None
    assert dense[6] is None and len(dense) == 8


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
    # Leaf cells in the engine frame (the tile's origin is 0 for t0_0_0): the first crown's foliage from 6 m up.
    monkeypatch.setattr(trees_lidar, "tile_leaves", lambda tile: leaf_cells(40, 40, 5, lambda i, j: 2.2, base=4))
    monkeypatch.setattr(trees_lidar, "tile_gaps", lambda tile: gap_cells(40, 40, 4, 3, 17))
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
    assert area[6] == round(2.2 / area[0], 3) and register[6] is None
    assert area[7] == 0.15 and register[7] is None
    assert entry["h"][1] == 4 + area[0]
    # Only the JSON changed: the binary chunk is the base's, and a rerun is byte-identical.
    assert binary == core.glb_chunks(glb)[1]
    (tmp_path / "again").mkdir()
    again = product(tmp_path / "again", glb)
    trees_lidar.transform(again)
    assert again.manifest == p.manifest


def test_tile_without_lidar_or_trees_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(trees_lidar, "tile_canopy", lambda tile: np.empty((0, 3)))
    monkeypatch.setattr(trees_lidar, "tile_leaves", lambda tile: None)
    monkeypatch.setattr(trees_lidar, "tile_gaps", lambda tile: None)
    glb = vegetation_glb([[40, 4, 40]], [[3, 10, 3]], ["puut:x"])
    p = product(tmp_path, glb)
    before = json.dumps(p.manifest, sort_keys=True)
    trees_lidar.transform(p)
    assert json.dumps(p.manifest, sort_keys=True) == before and not p.written


def test_registered_in_the_chain():
    assert trees_lidar.MIGRATION in registry.CHAIN
    assert trees_lidar.MIGRATION.key == "trees-lidar@3"


def test_canopy_needs_every_laz_block_on_disk(tmp_path, monkeypatch):
    from turku3d import config as C
    from turku3d.vegetation.crowns import canopy_cells

    monkeypatch.setattr(C, "raw_dir", lambda kind: tmp_path / kind)
    assert canopy_cells(C.VisualTile.parse(TILE)) is None


def test_block_canopy_keeps_the_highest_class5_return_per_cell_and_caches_it(tmp_path, monkeypatch):
    import laspy

    from turku3d import config as C
    from turku3d.vegetation.crowns import block_canopy

    monkeypatch.setattr(C, "cache_dir", lambda name: tmp_path / "cache" / name)
    monkeypatch.delenv("TURKU3D_SHARED_CACHE", raising=False)
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales, header.offsets = [0.01, 0.01, 0.01], [C.ORIGIN_E, C.ORIGIN_N - 500, 0]
    las = laspy.LasData(header)
    # Engine (x, z) = (E - ORIGIN_E, ORIGIN_N - N): two returns in one 0.5 m cell, a lower class-2 return, another cell.
    e = C.ORIGIN_E + np.array([10.1, 10.3, 10.2, 20.0])
    n = C.ORIGIN_N - np.array([5.1, 5.2, 5.3, 7.0])
    las.x, las.y, las.z = e, n, np.array([12.0, 15.5, 30.0, 9.0])
    las.classification = np.array([5, 5, 2, 5], np.uint8)
    path = tmp_path / "block.laz"
    las.write(path)
    from turku3d.lidar.decoded import source
    source(path)  # Shared survey decode already exists; a new canopy reduction must reuse it.
    def unexpected_decode(*args, **kwargs):
        raise AssertionError("canopy decoded LAZ again")
    monkeypatch.setattr(laspy, "open", unexpected_decode)
    cells = block_canopy(path)
    assert sorted(map(tuple, np.round(cells, 2))) == [(10.3, 15.5, 5.2), (20.0, 9.0, 7.0)]
    cached = list((tmp_path / "cache" / "crown-canopy").glob("*.npz"))
    assert len(cached) == 1
    assert np.array_equal(block_canopy(path), cells)


def test_canopy_highest_matches_source_order_sort_with_ties_and_negative_cells():
    from types import SimpleNamespace
    from turku3d import config as C
    from turku3d.vegetation.crowns import CELL, _highest

    rng = np.random.default_rng(925)
    header = SimpleNamespace(scales=[0.01, 0.01, 0.01], offsets=[C.ORIGIN_E, C.ORIGIN_N, 0])
    for count, span in ((0, 100), (10000, 300), (300, 1000000)):
        xyz = rng.integers(-span, span, (count, 3))
        xyz[:, 2] = rng.integers(-4, 4, count)  # Many equal heights within each occupied cell.
        i = np.floor((xyz[:, 0] * 0.01 + C.ORIGIN_E - C.ORIGIN_E) / CELL).astype(np.int64)
        j = np.floor((C.ORIGIN_N - (xyz[:, 1] * 0.01 + C.ORIGIN_N)) / CELL).astype(np.int64)
        order = np.lexsort((np.arange(count), -xyz[:, 2], j, i))
        first = np.ones(count, bool)
        first[1:] = (i[order][1:] != i[order][:-1]) | (j[order][1:] != j[order][:-1])
        assert np.array_equal(_highest(xyz, header), xyz[np.sort(order[first])])

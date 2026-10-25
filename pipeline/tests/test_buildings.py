"""Small offline geometry fixtures; no city tiles or network in the gate."""

import json

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from turku3d.buildings import roofer
from turku3d.buildings.facades import make_visuals, roof_material
from turku3d.buildings.inputs import Building
from turku3d.buildings.select import choose, residuals
from turku3d.buildings.shells import Surface, make_shell, triangulate
from turku3d.buildings.tiles import collision_tiles
from turku3d.col.materials import mat_id
from turku3d.package.gltf import accessor_array, build_glb, read_glb


def extrusion(poly, height=8.0, base=0.0):
    rings = [np.array(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]]

    def lift(r, y):
        return np.column_stack((r[:, 0], np.full(len(r), y), r[:, 1]))

    surfaces = [
        Surface([lift(r, base) for r in rings], "GroundSurface"),
        Surface([lift(r, height) for r in rings], "RoofSurface"),
    ]
    for ring in rings:
        for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
            surfaces.append(
                Surface(
                    [
                        np.array(
                            [
                                [a[0], base, a[1]],
                                [b[0], base, b[1]],
                                [b[0], height, b[1]],
                                [a[0], height, a[1]],
                            ]
                        )
                    ],
                    "WallSurface",
                )
            )
    return surfaces


@pytest.fixture
def two_buildings():
    # Concave footprint straddles both grid axes. Courtyard proves hole triangulation.
    a = Building("concave", Polygon([(58, 58), (70, 58), (70, 61), (65, 61), (65, 70), (58, 70)]))
    b = Building(
        "courtyard",
        Polygon(
            [(80, 60), (96, 60), (96, 76), (80, 76)], [[(84, 64), (84, 72), (92, 72), (92, 64)]]
        ),
    )
    return [a, b]


def test_closed_concave_and_courtyard(two_buildings):
    import trimesh

    for b in two_buildings:
        s = make_shell(extrusion(b.footprint), b.footprint)
        m = trimesh.Trimesh(s.positions, s.indices, process=False)
        assert m.is_watertight and m.is_winding_consistent
        assert m.volume == pytest.approx(b.footprint.area * 8)
        assert len(s.indices) < 2000
        assert set(s.material) == {mat_id("building"), mat_id("roof")}
        assert s.hausdorff == 0
        roof = [Polygon(t[:, [0, 2]]) for t in s.positions[s.indices[s.material == mat_id("roof")]]]
        assert unary_union(roof).symmetric_difference(b.footprint).area < 1e-7


def test_reject_open_shell_and_wrong_footprint(two_buildings):
    b = two_buildings[0]
    with pytest.raises(ValueError, match="closed"):
        make_shell(extrusion(b.footprint)[:-1], b.footprint)
    with pytest.raises(ValueError, match="Hausdorff"):
        make_shell(extrusion(shapely.affinity.translate(b.footprint, 3)), b.footprint)


def test_snap_and_select_on_final_geometry(two_buildings):
    b = two_buildings[0]
    x, z = np.meshgrid(np.arange(58.1, 70, 0.4), np.arange(58.1, 70, 0.4))
    points = np.column_stack((x.ravel(), np.full(x.size, 8.03), z.ravel()))
    displaced = shapely.affinity.translate(b.footprint, 0.1, 0)
    shell, record = choose(
        b, {"city": extrusion(displaced, 9), "roofer": extrusion(b.footprint)}, points
    )
    assert record["choice"] == "roofer"
    assert record["pass"]
    assert record["candidates"]["roofer"]["median_m"] == pytest.approx(0.03)
    assert record["candidates"]["city"]["roof_vertical"]["median_m"] == pytest.approx(0.97)
    assert shell.hausdorff <= 0.3
    empty = residuals(shell, np.empty((0, 3)), b.footprint)
    assert not empty["pass"] and empty["median_m"] is None
    shell.material[:] = mat_id("building")
    uncovered = residuals(shell, points, b.footprint)
    assert not uncovered["pass"] and uncovered["p75_m"] is None


def test_facade_uvs_metadata_and_stable_order(two_buildings):
    b = two_buildings[0]
    surfaces = extrusion(b.footprint)
    shell = make_shell(surfaces, b.footprint)
    streets = LineString([(50, 50), (100, 50)])
    meshes, features = make_visuals(b, shell, streets, [b.footprint], "roof_metal_gray")
    assert any(f["properties"]["street_facing"] for f in features)
    assert [f["properties"]["facade_index"] for f in features] == list(range(6))
    assert all(f["properties"]["width_m"] > 0 for f in features)
    js, blob = read_glb(build_glb(meshes))
    for node in js["nodes"][1:]:
        extras = node["extras"]
        assert extras["layer"] == "buildings" and extras["building_id"] == b.id
        prim = js["meshes"][node["mesh"]]["primitives"][0]
        uv = accessor_array(js, blob, prim["attributes"]["TEXCOORD_0"])
        assert np.isfinite(uv).all()
        if "facade_index" in extras:
            assert uv[:, 0].min() == pytest.approx(0)
            assert uv[:, 0].max() == pytest.approx(extras["width_m"])
            assert uv[:, 1].max() == pytest.approx(8)
    reordered = make_shell(list(reversed(surfaces)), b.footprint)
    _, again = make_visuals(b, reordered, streets, [b.footprint], "roof_metal_gray")
    assert features == again


def test_collision_tiles_bitwise_seams_and_area(two_buildings):
    shells = [make_shell(extrusion(b.footprint), b.footprint) for b in two_buildings]
    tiles = collision_tiles(shells)
    total = 0
    for arrays in tiles.values():
        p, idx, mat = arrays.values()
        assert p.dtype == np.float64 and idx.dtype == np.uint32 and mat.dtype == np.uint8
        t = p[idx]
        total += np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum()
    original = sum(
        np.linalg.norm(
            np.cross(
                s.positions[s.indices[:, 1]] - s.positions[s.indices[:, 0]],
                s.positions[s.indices[:, 2]] - s.positions[s.indices[:, 0]],
            ),
            axis=1,
        ).sum()
        for s in shells
    )
    assert total == pytest.approx(original)
    left, right = tiles["c_0_0"]["positions"], tiles["c_1_0"]["positions"]
    assert set(map(bytes, left[left[:, 0] == 62.5])) == set(map(bytes, right[right[:, 0] == 62.5]))
    top, bottom = tiles["c_0_0"]["positions"], tiles["c_0_1"]["positions"]
    assert set(map(bytes, top[top[:, 2] == 62.5])) == set(map(bytes, bottom[bottom[:, 2] == 62.5]))


def test_t_junction_repaired():
    poly = shapely.box(0, 0, 10, 10)
    surfaces = extrusion(poly)
    # Insert an eave vertex into just one facade; closure requires splitting the roof edge.
    ring = surfaces[2].rings[0]
    ring = np.insert(ring, 3, (ring[2] + ring[3]) / 2, axis=0)
    surfaces[2].rings[0] = ring
    s = make_shell(surfaces, poly)
    import trimesh

    assert trimesh.Trimesh(s.positions, s.indices, process=False).is_watertight


def test_roofer_transform_and_parts(tmp_path):
    p = tmp_path / "sample.city.jsonl"
    header = {
        "type": "CityJSON",
        "transform": {"scale": [0.001] * 3, "translate": [23459500, 6704500, 0]},
    }
    feature = {
        "vertices": [[0, 0, 1000], [1000, 0, 1000], [1000, -1000, 1000]],
        "CityObjects": {
            "part": {
                "parents": ["building"],
                "geometry": [
                    {
                        "type": "Solid",
                        "lod": "2.2",
                        "boundaries": [[[[0, 1, 2]]]],
                        "semantics": {"surfaces": [{"type": "RoofSurface"}], "values": [[0]]},
                    }
                ],
            }
        },
    }
    p.write_text(json.dumps(header) + "\n" + json.dumps(feature) + "\n")
    s = roofer.read([p])["building"][0]
    assert s.kind == "RoofSurface"
    np.testing.assert_equal(s.rings[0], [[0, 1, 0], [1, 1, 0], [1, 1, 1]])


@pytest.mark.parametrize(
    "rgb,key",
    [
        ([160, 60, 50], "roof_tile_red"),
        ([50, 90, 70], "roof_metal_green"),
        ([40] * 3, "roof_metal_dark"),
        ([170] * 3, "roof_metal_gray"),
    ],
)
def test_roof_color(rgb, key):
    assert roof_material(rgb) == key


def test_terrain_following_base_is_triangulated_without_flattening():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    surfaces = extrusion(poly)
    for surface in surfaces:
        for ring in surface.rings:
            for vertex in ring:
                if vertex[1] == 0:
                    vertex[1] = 0.01 * vertex[0] * vertex[2]
    shell = make_shell(surfaces, poly)
    assert [10, 1, 10] in shell.positions.tolist()
    assert shell.hausdorff == 0


def test_touching_ring_is_earcut_as_valid_regions():
    # A courtyard touching the exterior at a vertex is legal as a collection of
    # polygon regions, although the original single polygon is not valid.
    ring = np.array([[0, 5, 0], [10, 5, 0], [10, 5, 10], [0, 5, 10]])
    hole = np.array([[0, 5, 5], [3, 5, 3], [3, 5, 7]])
    p, t = triangulate(Surface([ring, hole], "RoofSurface"))
    actual = unary_union([Polygon(v[:, [0, 2]]) for v in p[t]])
    assert actual.area == pytest.approx(94)


def test_vertex_touching_solids_are_not_a_manifold():
    a = shapely.box(0, 0, 5, 5)
    b = shapely.box(5, 5, 10, 10)
    with pytest.raises(ValueError, match="manifold"):
        make_shell(extrusion(a) + extrusion(b), unary_union([a, b]))


def test_near_collinear_footprint_does_not_create_overlapping_faces():
    # Millimetre source quantisation and footprint snapping can leave real thin
    # triangles. Their vertices must not be treated as exact T junctions.
    poly = Polygon([(0, 0), (3, 0.000001), (25, 0), (25, 10), (0, 10)])
    shell = make_shell(extrusion(poly), poly)
    assert shell.hausdorff == pytest.approx(0, abs=1e-12)
    assert len(shell.indices) > 0


def test_roofer_stages_one_read_only_source(tmp_path, monkeypatch):
    sources = []
    for i in range(2):
        p = tmp_path / f"{i}.laz"
        p.write_bytes(b"fixture")
        sources.append(p)
    footprints = tmp_path / "footprints.gpkg"
    footprints.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(roofer.subprocess, "run", lambda command, **kw: calls.append(command))
    monkeypatch.setattr(roofer.subprocess, "check_output", lambda *args, **kw: "roofer 1.0.0")
    output = tmp_path / "reconstruction"
    roofer.run(sources, footprints, output)
    assert calls[0][-3:] == [str(output / "pointclouds"), str(footprints), str(output)]
    assert all((output / "pointclouds" / p.name).is_symlink() for p in sources)
    assert all(p.read_bytes() == b"fixture" for p in sources)
    assert json.loads((output / "execution.json").read_text())["version"] == "roofer 1.0.0"


def test_publish_layer_contract_and_remove_only_stale_owned_files(two_buildings, tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from turku3d import config as C
    from turku3d.buildings.build import publish

    ortho = tmp_path / "ortho.tif"
    rgb = np.zeros((3, 50, 50), np.uint8)
    rgb[:] = np.array([160, 60, 50], np.uint8)[:, None, None]
    with rasterio.open(
        ortho,
        "w",
        driver="GTiff",
        width=50,
        height=50,
        count=3,
        dtype="uint8",
        crs=C.CRS_HORIZONTAL,
        transform=from_origin(C.ORIGIN_E + 50, C.ORIGIN_N - 50, 1, 1),
    ) as dst:
        dst.write(rgb)
    selected, records = {}, []
    for b in two_buildings:
        p = b.footprint.representative_point()
        shell, record = choose(b, {"roofer": extrusion(b.footprint)}, np.array([[p.x, 8, p.y]]))
        selected[b.id] = shell
        records.append(record)
    out = tmp_path / "buildings"
    report = publish(two_buildings, selected, records, out, LineString(), [ortho], [], {})
    assert report["pass"] and report["shell_count"] == 2
    assert all(r["roof"]["material_key"] == "roof_tile_red" for r in records)
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["code_version"] and manifest["layer"] == "buildings"
    assert all((out / name).exists() for name in manifest["tiles_written"])
    assert not list(out.glob("*.col")) and not (out / "tileset.json").exists()
    facades = json.loads((out / "facades.geojson").read_text())
    assert facades["schema_version"] == 1
    assert len(facades["features"]) == 14
    # A previous owned tile should disappear, an unrelated file should survive.
    (out / "obsolete.col.npz").write_bytes(b"obsolete")
    (out / "notes.txt").write_text("keep")
    manifest["tiles_written"].append("obsolete.col.npz")
    (out / "MANIFEST.json").write_text(json.dumps(manifest))
    publish(two_buildings, selected, records, out, LineString(), [ortho], [], {})
    assert not (out / "obsolete.col.npz").exists()
    assert (out / "notes.txt").read_text() == "keep"
    from turku3d.buildings.audit import audit

    result = audit(out)
    assert result["pass"], result["errors"]
    assert result["shells"] == 2
    assert result["seam_pairs"] > 0
    path = next(out.glob("*.col.npz"))
    with np.load(path) as tile:
        arrays = {key: tile[key].copy() for key in tile.files}
    for key in ("indices", "material", "building_index"):
        arrays[key] = arrays[key][1:]
    np.savez_compressed(path, **arrays)
    broken = audit(out)
    assert not broken["pass"]
    assert any("not closed" in error for error in broken["errors"])


def test_courtyard_facades_are_occluded_by_the_same_building(two_buildings):
    building = two_buildings[1]
    shell = make_shell(extrusion(building.footprint), building.footprint)
    _, facades = make_visuals(
        building, shell, LineString([(70, 55), (110, 55)]), [building.footprint], "roof_metal_gray"
    )
    assert any(f["properties"]["street_facing"] for f in facades[:4])
    assert not any(f["properties"]["street_facing"] for f in facades[4:])

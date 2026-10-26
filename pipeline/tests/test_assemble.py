import hashlib
import io
import json
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from turku3d import config as C
from turku3d.col.format import read_col
from turku3d.layers import (
    PRIMITIVE_DTYPE,
    CollisionLayer,
    write_collision,
    write_manifest,
    write_visual,
)
from turku3d.package.assemble import assemble as assemble_office
from turku3d.package.assemble import facade_priorities
from turku3d.package.gltf import GlbMesh, read_glb
from turku3d.package.manifest import verify_tree
from turku3d.package.seams import verify_seams
from turku3d.parked.__main__ import publish as publish_parked
from turku3d.parked.models import MODELS


def assemble(*args, **kwargs):
    return assemble_office(*args, area="work-3x3", **kwargs)


def test_facade_budget_priority_uses_spawn_distance_and_tile(tmp_path):
    inventory = tmp_path / "facades.geojson"
    features = []
    for index, e, n in [
        (0, C.ORIGIN_E + 20, C.ORIGIN_N - 20),
        (1, C.ORIGIN_E + 200, C.ORIGIN_N - 200),
        (2, C.ORIGIN_E + 270, C.ORIGIN_N - 20),
    ]:
        features.append(
            dict(
                properties=dict(building_id="b", facade_index=index),
                geometry=dict(type="LineString", coordinates=[[e, n], [e + 1, n]]),
            )
        )
    inventory.write_text(json.dumps(dict(type="FeatureCollection", features=features)))
    priorities = facade_priorities(inventory, ["b_0", "b_1", "b_2"], {"position": [20, 0, 20]})
    assert priorities["t0_0_0"] == ["b_1", "b_0"]
    assert priorities["t0_1_0"] == ["b_2"]


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    # Unit tests must never discover real terrain or sandbox facade assets.
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))


def fixture(root, ids=("t0_0_0",), *, image=False):
    ground = root / "ground"
    files = []
    for tid in ids:
        tile = C.VisualTile.parse(tid)
        for c in tile.collision_tiles():
            x, _, z = c.origin
            s = C.COLLISION_TILE_SIZE
            pos = np.array(
                [
                    [x, 2.12345678, z],
                    [x + s, 2.12345678, z],
                    [x, 2.12345678, z + s],
                    [x + s, 2.12345678, z + s],
                ]
            )
            idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
            name = c.id + ".col.npz"
            write_collision(ground / name, CollisionLayer(pos, idx, np.array([1, 2], np.uint8)))
            files.append(name)
        x, _, z = tile.origin
        s = tile.size
        pos = np.array(
            [
                [x, 2.12345678, z],
                [x + s, 2.12345678, z],
                [x, 2.12345678, z + s],
                [x + s, 2.12345678, z + s],
            ]
        )
        extras = dict(
            layer="ground", object_id="ground_" + tid, material_key="road", texture_uv="normalized"
        )
        mesh = GlbMesh(
            tid,
            pos - np.array(tile.origin),
            idx,
            uvs=np.array([[0, 0], [1, 0], [0, 1], [1, 1]], np.float32),
            extras=extras,
        )
        if image:
            stream = io.BytesIO()
            Image.new("RGB", (8, 8), (128, 150, 80)).save(stream, format="PNG")
            mesh.image = stream.getvalue()
        name = tid + ".vis.glb"
        files.append(name)
        write_visual(ground / name, [mesh], root_translation=tile.origin)
    write_manifest(ground, inputs={"synthetic": "v1"}, params={}, code="test", tiles=files)


def test_assembled_ground_binding_survives_visual_packaging(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers)
    ground = tmp_path / "textures/ground"
    tile = ground / "t0_0_0"
    tile.mkdir(parents=True)
    library = ground.parent / "library"
    library.mkdir()
    for name in ("albedo", "normal", "orm"):
        (library / f"{name}.array.ktx2").write_bytes(name.encode())
    maps = {}
    for name in ("ortho", "splat0", "splat1"):
        data = name.encode()
        (tile / f"{name}.ktx2").write_bytes(data)
        maps[name] = {"uri": f"{name}.ktx2", "sha256": hashlib.sha256(data).hexdigest()}
    source = layers / "ground/c_0_0.col.npz"
    spec = {
        "ground_inputs": [
            {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        ],
        "textures": maps,
    }
    (tile / "material.json").write_text(json.dumps(spec))

    assemble(["t0_0_0"], layers=layers, out=out, ground=ground)
    manifest = json.loads((out / "manifest.json").read_text())
    entry = manifest["visual"]["t0_0_0"]
    assert entry["ground_texture_bytes"] == sum(
        (tile / f"{name}.ktx2").stat().st_size for name in maps
    )
    glb, _ = read_glb((out / entry["url"]).read_bytes())
    binding = glb["scenes"][0]["extras"]["ground_binding"]
    assert (out / binding["spec"]).is_file()
    assert all(
        (out / binding["library"] / f"{name}.array.ktx2").is_file()
        for name in ("albedo", "normal", "orm")
    )


def test_incremental_tiles_keep_drivable_parked_cars(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=("t0_0_0", "t0_1_0"))
    width, height, length = MODELS["sedan"]
    cars = [
        dict(
            object_id=f"car-{i}",
            model="sedan",
            color="blue",
            x=x,
            y=2.12345678,
            z=100,
            yaw=0,
            width=width,
            height=height,
            length=length,
            clear_front_m=30.0,
            clear_rear_m=30.0,
        )
        for i, x in enumerate((80, 280))
    ]
    parked = layers / "traffic_static"
    parked.mkdir(parents=True)
    (parked / "placements.json").write_text(json.dumps({"cars": cars}))
    publish_parked(cars, parked, {"fixture": "test"}, 16)
    assemble(["t0_0_0"], layers=layers, out=out)
    first = json.loads((out / "manifest.json").read_text())
    assert [car["object_id"] for car in first["vehicles"]] == ["car-0"]
    assert first["collision"]["c_1_1"]["prims"] == 0
    assemble(["t0_1_0"], layers=layers, out=out)
    second = json.loads((out / "manifest.json").read_text())
    assert {car["object_id"] for car in second["vehicles"]} == {"car-0", "car-1"}
    assert not verify_tree(out)


def test_assemble_roundtrip_textures_parents_cache_and_invalidation(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, image=True)
    library = tmp_path / "library"
    (library / "road").mkdir(parents=True)
    Image.new("RGB", (8, 8), (128, 128, 255)).save(library / "road/normal.png")
    Image.new("RGB", (8, 8), (255, 180, 0)).save(library / "road/orm.png")
    Image.new("RGB", (8, 8), (100, 130, 90)).save(library / "road/albedo.png")
    buildings = layers / "buildings"
    roof = GlbMesh(
        "roof",
        np.array([[-10.0, 10, 0], [10.0, 10, 0], [-10.0, 10, 10]]),
        np.array([[0, 2, 1]], np.uint32),
        uvs=np.zeros((3, 2), np.float32),
        extras=dict(layer="buildings", object_id="roof", material_key="road", surface="roof"),
    )
    wall = GlbMesh(
        "facade",
        np.array([[-10.0, 0, 0], [10.0, 0, 0], [-10.0, 10, 0]]),
        np.array([[0, 1, 2]], np.uint32),
        uvs=np.zeros((3, 2), np.float32),
        extras=dict(
            layer="buildings",
            object_id="facade",
            material_key="road",
            building_id="b1",
            facade_index=0,
        ),
    )
    write_visual(buildings / "t0_0_0.vis.glb", [roof, wall])
    write_manifest(buildings, inputs={}, params={}, code="test", tiles=["t0_0_0.vis.glb"])
    # Optional layer contributes triangles and a whole primitive crossing a tile edge.
    p = np.zeros(1, PRIMITIVE_DTYPE)
    p["kind"] = "box"
    p["center"] = [62.5, 3, 20]
    p["half_extents"] = [1, 1, 1]
    p["material"] = 19
    p["yaw"] = 0.4
    structure = layers / "structures"
    write_collision(
        structure / "c_0_0.col.npz",
        CollisionLayer(
            np.array([[20.0, 2.12345678, 20], [21.0, 2.12345678, 20], [20.0, 2.12345678, 21]]),
            np.array([[0, 2, 1]], np.uint32),
            np.array([19], np.uint8),
            p,
        ),
    )
    write_manifest(structure, inputs={}, params={}, code="test", tiles=["c_0_0.col.npz"])
    result = assemble(["t0_0_0"], layers=layers, out=out, library=library)
    assert set(result["changed"]) == {"t0_0_0", "t1_0_0", "t2_0_0"}
    assert verify_tree(out) == verify_seams(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert len(m["collision"]) == 16
    for cid, entry in m["collision"].items():
        tile = read_col(out / entry["url"])
        assert max(abs(tile.world_positions()[:, 1] - 2.12345678)) < 0.002
        assert sorted(tile.materials.tolist()) == ([1, 2, 19] if cid == "c_0_0" else [1, 2])
        assert entry["bytes"] <= 1_000_000
        if cid == "c_0_0":
            assert len(tile.primitives) == 1
            assert np.allclose(tile.primitives[0].center, [62.5, 3, 20])
    glb, blob = read_glb((out / m["visual"]["t0_0_0"]["url"]).read_bytes())
    assert "EXT_meshopt_compression" in glb["extensionsRequired"]
    assert "KHR_texture_basisu" in glb["extensionsRequired"]
    assert len(glb["images"]) == 3  # Missing unique facade uses the library fallback.
    assert m["visual"]["t1_0_0"]["tris"] == 3  # Ground plus roof, no facade wall.
    assert m["visual"]["t2_0_0"]["tris"] == 3
    for img in glb["images"]:
        assert img["mimeType"] == "image/ktx2"
        assert img["uri"].startswith("../shared/")
    assert all(
        n["extras"]["kind"] == "building"
        for n in glb["nodes"]
        if n.get("extras", {}).get("layer") == "buildings"
    )
    mat = glb["materials"][0]
    assert mat["pbrMetallicRoughness"].get("metallicFactor", 1) == 1
    assert mat["pbrMetallicRoughness"].get("roughnessFactor", 1) == 1

    def compression(texture_info):
        tex = glb["textures"][texture_info["index"]]
        image = glb["images"][tex["extensions"]["KHR_texture_basisu"]["source"]]
        if "uri" in image:
            data = (out / "visual/0" / image["uri"]).read_bytes()
            return struct.unpack_from("<I", data, 44)[0]
        bv = glb["bufferViews"][image["bufferView"]]
        return struct.unpack_from("<I", blob, bv.get("byteOffset", 0) + 44)[0]

    assert compression(mat["pbrMetallicRoughness"]["baseColorTexture"]) == 1  # ETC1S/BasisLZ
    assert compression(mat["normalTexture"]) == 2  # UASTC/Zstandard
    assert m["visual"]["t0_0_0"]["brotli_bytes"] <= 8_000_000
    assert m["visual"]["t0_0_0"]["max_error_m"] == 0
    ts = json.loads((out / "tileset.json").read_text())
    box = ts["root"]["boundingVolume"]["box"]
    assert box[0] - box[3] <= -10  # Whole-object bounds extend outside the nominal tile.
    assert ts["root"]["children"][0]["extras"]["id"] == "t2_0_0"
    assert ts["root"]["children"][0]["children"][0]["children"][0]["extras"]["id"] == "t0_0_0"
    before = {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    assert assemble(["t0_0_0"], layers=layers, out=out, library=library)["changed"] == []
    assert before == {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    # Corrupt output invalidates a cache hit; repair must pass hashes again.
    (out / m["visual"]["t0_0_0"]["url"]).write_bytes(b"broken")
    assert assemble(["t0_0_0"], layers=layers, out=out, library=library)["changed"]
    assert verify_tree(out) == []
    shared = out / m["visual"]["t0_0_0"]["shared_images"][0]["url"]
    shared.write_bytes(b"broken")
    assert verify_tree(out)
    assert assemble(["t0_0_0"], layers=layers, out=out, library=library)["changed"]
    assert verify_tree(out) == []
    # Raw staging cannot authorize unique textures, even with a matching filename.
    facades = tmp_path / "assembly/facades"
    (facades / "b1").mkdir(parents=True)
    with pytest.raises(ValueError, match="filtered texture assembly export"):
        assemble(["t0_0_0"], layers=layers, out=out, library=library, facades=facades)
    inventory = buildings / "facades.geojson"
    feature = dict(
        properties=dict(building_id="b1", facade_index=0, width_m=20, base_height=0, top_height=10)
    )
    inventory.write_text(json.dumps(dict(type="FeatureCollection", features=[feature])))
    export = dict(
        inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest(), bound=["b1_0"]
    )
    (facades.parent / "MANIFEST.json").write_text(json.dumps(export))
    files, hashes = {}, {}
    for slot, info in [
        ("albedo", mat["pbrMetallicRoughness"]["baseColorTexture"]),
        ("normal", mat["normalTexture"]),
        ("orm", mat["occlusionTexture"]),
    ]:
        tex = glb["textures"][info["index"]]
        img = glb["images"][tex["extensions"]["KHR_texture_basisu"]["source"]]
        if "uri" in img:
            data = (out / "visual/0" / img["uri"]).read_bytes()
        else:
            view = glb["bufferViews"][img["bufferView"]]
            data = blob[view.get("byteOffset", 0) : view.get("byteOffset", 0) + view["byteLength"]]
        name = "0.ktx2" if slot == "albedo" else f"0.{slot}.ktx2"
        (facades / "b1" / name).write_bytes(data)
        files[slot], hashes[name] = name, hashlib.sha256(data).hexdigest()
    sidecar = dict(
        building_id="b1",
        facade_index=0,
        files=files,
        hashes=hashes,
        width_m=20,
        height_m=10,
        qa=dict(gltf_basisu_valid=True),
        uv=dict(units="metres", scale=[-0.05, -0.1], offset=[1, 1]),
    )
    (facades / "b1/0.json").write_text(json.dumps(sidecar))
    kwargs = dict(layers=layers, out=out, library=library, facades=facades)
    assert assemble(["t0_0_0"], **kwargs)["changed"]
    js, encoded = read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
    node = next(n for n in js["nodes"] if n.get("extras", {}).get("object_id") == "facade")
    material = js["materials"][js["meshes"][node["mesh"]]["primitives"][0]["material"]]
    for info in (
        material["pbrMetallicRoughness"]["baseColorTexture"],
        material["normalTexture"],
        material["occlusionTexture"],
        material["pbrMetallicRoughness"]["metallicRoughnessTexture"],
    ):
        assert info["extensions"]["KHR_texture_transform"] == {
            "scale": [-0.05, -0.1],
            "offset": [1, 1],
        }
        tex = js["textures"][info["index"]]
        assert js["samplers"][tex["sampler"]]["wrapS"] == 33071
    assert {im.get("name") for im in js["images"]} >= set(files.values())
    normal_tex = js["textures"][material["normalTexture"]["index"]]
    normal_image = js["images"][normal_tex["extensions"]["KHR_texture_basisu"]["source"]]
    if "uri" in normal_image:
        normal_data = (out / "visual/0" / normal_image["uri"]).read_bytes()
    else:
        normal_view = js["bufferViews"][normal_image["bufferView"]]
        normal_data = encoded[normal_view["byteOffset"] :]
    assert struct.unpack_from("<I", normal_data, 44)[0] == 2
    assert assemble(["t0_0_0"], **kwargs)["changed"] == []
    # A layer republish can change the inventory bytes without changing this facade.
    republished = dict(
        type="FeatureCollection",
        features=[
            feature,
            dict(
                properties=dict(
                    building_id="other", facade_index=0, width_m=2, base_height=0, top_height=2
                )
            ),
        ],
    )
    inventory.write_text(json.dumps(republished))
    assert assemble(["t0_0_0"], **kwargs)["changed"] == []
    js, _ = read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
    assert any(im.get("name") == "0.ktx2" for im in js["images"])
    normal_path = facades / "b1" / files["normal"]
    original = normal_path.read_bytes()
    normal_path.write_bytes(b"changed after QA")
    with pytest.raises(ValueError, match="Facade map changed after export"):
        assemble(["t0_0_0"], **kwargs)
    normal_path.write_bytes(original)
    # An unlisted index or changed authoritative geometry must revert to the library.
    for change in ("unlisted", "geometry"):
        export["bound"] = [] if change == "unlisted" else ["b1_0"]
        (facades.parent / "MANIFEST.json").write_text(json.dumps(export))
        if change == "geometry":
            feature["properties"]["top_height"] = 11
            inventory.write_text(json.dumps(republished))
        assemble(["t0_0_0"], **kwargs)
        js, _ = read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
        assert not any(im.get("name") == "0.ktx2" for im in js["images"])


def test_region_batch_cli_and_manifest(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=("t0_0_0", "t0_1_0"))
    cmd = [
        sys.executable,
        "-m",
        "turku3d.package.assemble",
        "--layers",
        str(layers),
        "--out",
        str(out),
        "--area",
        "work-3x3",
        "--tile",
        "t0_0_0",
        "t0_1_0",
    ]
    result = json.loads(subprocess.check_output(cmd, text=True))
    assert result["manifest"] == str(out / "manifest.json")
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert {k for k, v in manifest["visual"].items() if v["level"] == 0} == {"t0_0_0", "t0_1_0"}
    assert len(manifest["collision"]) == 32
    assert {Path(v["url"]).parent.name for v in manifest["collision"].values()} == {
        "t0_0_0",
        "t0_1_0",
    }
    assert verify_tree(out) == verify_seams(out) == []
    cached = json.loads(subprocess.check_output(cmd, text=True))
    assert cached["changed"] == [] and cached["manifest"] == result["manifest"]
    assert cached["facades_bound"] == result["facades_bound"]
    # Consume the published manifest exactly as the region runner does: independent jobs
    # only after assembly succeeds. Format checks keep this small fixture fast.
    directories = sorted({(out / v["url"]).parent for v in manifest["collision"].values()})

    def validate(directory):
        report = tmp_path / f"validate-{directory.name}.json"
        subprocess.run(
            [
                "node",
                str(C.REPO_ROOT / "tools/validator/src/cli.ts"),
                "--checks",
                "format",
                "--out",
                str(report),
                str(directory),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(report.read_text())["pass"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(validate, directories))


def test_building_library_fallbacks_and_metre_uvs(tmp_path):
    library = tmp_path / "work/textures/assembly/library"
    for key in ("render_smooth", "roof_standing_seam_metal"):
        directory = library / key
        directory.mkdir(parents=True)
        (directory / "material.json").write_text(json.dumps({"uv_scale": [0.5, 0.25]}))
        for slot, color in (
            ("albedo", (100, 130, 90)),
            ("normal", (128, 128, 255)),
            ("orm", (255, 180, 0)),
        ):
            Image.new("RGB", (8, 8), color).save(directory / f"{slot}.png")
    keys = [
        "facade_brick",
        "facade_wood",
        "facade_concrete",
        "facade_plaster",
        "roof_tile_red",
        "roof_metal_green",
        "roof_metal_dark",
        "roof_metal_gray",
    ]
    meshes = [
        GlbMesh(
            key,
            np.array([[0.0, 0, 0], [2, 0, 0], [0, 0, 4]]),
            np.array([[0, 2, 1]], np.uint32),
            uvs=np.array([[0, 0], [2, 0], [0, 4]], np.float32),
            extras=dict(layer="buildings", object_id=key, material_key=key, surface="roof"),
        )
        for key in keys
    ]
    directory = tmp_path / "layers/buildings"
    write_visual(directory / "t0_0_0.vis.glb", meshes)
    write_manifest(directory, inputs={}, params={}, code="test", tiles=["t0_0_0.vis.glb"])
    out = tmp_path / "dist"
    assemble(["t0_0_0"], layers=directory.parent, out=out)
    js, _ = read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
    assert len(js["materials"]) == 2
    batches = [
        node["extras"] for node in js["nodes"] if node.get("extras", {}).get("layer") == "buildings"
    ]
    assert len(batches) == 2
    assert {item["object_id"] for batch in batches for item in batch["objects"]} == set(keys)
    for mat in js["materials"]:
        key = mat["extras"]["material_key"]
        assert key in {"render_smooth", "roof_standing_seam_metal"}
        assert mat["extras"]["library_material_key"] == key
        for info in (
            mat["pbrMetallicRoughness"]["baseColorTexture"],
            mat["normalTexture"],
            mat["occlusionTexture"],
            mat["pbrMetallicRoughness"]["metallicRoughnessTexture"],
        ):
            assert info["extensions"]["KHR_texture_transform"] == {"scale": [0.5, 0.25]}
            tex = js["textures"][info["index"]]
            assert js["samplers"][tex["sampler"]]["wrapS"] == 10497


def test_seam_rejected_before_publishing(tmp_path):
    layers = tmp_path / "layers"
    fixture(layers)
    from turku3d.layers import read_collision, read_manifest

    file = layers / "ground/c_1_0.col.npz"
    layer = read_collision(file)
    layer.positions[0, 1] += 0.01
    write_collision(file, layer)
    m = read_manifest(layers / "ground", verify=False)
    write_manifest(layers / "ground", inputs={}, params={}, code="test", tiles=list(m["tiles"]))
    with pytest.raises(ValueError, match="seam mismatch"):
        assemble(["t0_0_0"], layers=layers, out=tmp_path / "out")
    assert not (tmp_path / "out/manifest.json").exists()


def test_missing_layers_have_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="Missing required office-block layers"):
        assemble_office(["t0_0_0"], layers=tmp_path / "missing", out=tmp_path / "out")


def test_incremental_neighbours_and_removed_collision_are_updated(tmp_path):
    from turku3d.col.format import FLAG_AOI_BORDER
    from turku3d.layers import read_manifest

    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=("t0_0_0", "t0_1_0"))
    assemble(["t0_0_0"], layers=layers, out=out)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["collision"]["c_3_1"]["flags"] & FLAG_AOI_BORDER
    assemble(["t0_1_0"], layers=layers, out=out)
    manifest = json.loads((out / "manifest.json").read_text())
    assert not manifest["collision"]["c_3_1"]["flags"] & FLAG_AOI_BORDER
    assert len(manifest["collision"]) == 32
    m = read_manifest(layers / "ground")
    del m["tiles"]["c_4_1.col.npz"]
    write_manifest(layers / "ground", inputs={}, params={}, code="test", tiles=list(m["tiles"]))
    assemble(["t0_1_0"], layers=layers, out=out)
    manifest = json.loads((out / "manifest.json").read_text())
    assert "c_4_1" not in manifest["collision"]
    assert not (out / "collision/t0_1_0/c_4_1.col").exists()
    assert manifest["collision"]["c_3_1"]["flags"] & FLAG_AOI_BORDER
    assert verify_tree(out) == verify_seams(out) == []


def test_structures_only_can_ship_without_empty_parent_content(tmp_path):
    directory = tmp_path / "layers/structures"
    mesh = GlbMesh(
        "wall",
        np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        np.array([[0, 1, 2]], np.uint32),
        extras=dict(layer="structures", object_id="wall", material_key="concrete"),
    )
    write_visual(directory / "t0_0_0.vis.glb", [mesh])
    write_manifest(directory, inputs={}, params={}, code="test", tiles=["t0_0_0.vis.glb"])
    out = tmp_path / "out"
    assemble(["t0_0_0"], layers=directory.parent, out=out)
    m = json.loads((out / "manifest.json").read_text())
    assert set(m["visual"]) == {"t0_0_0"}
    assert m["collision"] == {}
    assert assemble(["t0_0_0"], layers=directory.parent, out=out)["changed"] == []


def test_coarse_dtm_uses_complete_cached_coverage(tmp_path):
    import laspy
    import rasterio
    from rasterio.shutil import copy as copy_raster
    from rasterio.transform import from_origin
    from turku3d.package import coarse
    from turku3d.package.gltf import accessor_array

    tile = C.VisualTile(1, 0, 0)
    lazdir = C.raw_dir("laz") / "maanpinta"
    lazdir.mkdir(parents=True)
    las = laspy.create(point_format=0, file_version="1.2")
    las.header.offsets = [C.ORIGIN_E, C.ORIGIN_N, 0]
    las.x = np.array([C.ORIGIN_E, C.ORIGIN_E + 500, C.ORIGIN_E, C.ORIGIN_E + 500])
    las.y = np.array([C.ORIGIN_N, C.ORIGIN_N, C.ORIGIN_N - 500, C.ORIGIN_N - 500])
    las.z = np.array([2.0, 3.0, 4.0, 5.0])
    las.write(lazdir / "23459500_6704500.laz")
    ortho = C.raw_dir("ortho") / "ortho2022to"
    ortho.mkdir(parents=True)
    with rasterio.open(
        ortho / "tiny.tif",
        "w",
        driver="GTiff",
        width=16,
        height=16,
        count=3,
        dtype="uint8",
        crs="EPSG:3877",
        transform=from_origin(C.ORIGIN_E, C.ORIGIN_N, 62.5, 62.5),
    ) as ds:
        ds.write(np.full((3, 16, 16), 100, np.uint8))
    copy_raster(ortho / "tiny.tif", ortho / "tiny.vrt", driver="VRT")
    sources = coarse.discover(tile)
    assert sources and len(sources["laz"]) == 1
    assert ortho / "tiny.tif" in sources["files"]
    assert coarse.discover(C.VisualTile(2, 0, 0)) is None  # Three required LAZ tiles absent.
    output = coarse.build(tile, sources, tmp_path / "dtm.glb", {})
    js, blob = read_glb(output.read_bytes())
    primitive = js["meshes"][0]["primitives"][0]
    pos = accessor_array(js, blob, primitive["attributes"]["POSITION"])
    assert pos[:, 0].min() == pos[:, 2].min() == 0
    assert pos[:, 0].max() == pos[:, 2].max() == 500
    assert pos[:, 1].min() == 2 and pos[:, 1].max() == 5
    assert js["images"][0]["mimeType"] == "image/png"

    # Complete the 1 km core and prove every quadrant gets a persistent 500 m child.
    for i, j in ((1, 0), (0, 1), (1, 1)):
        las.x = C.ORIGIN_E + np.array([i * 500, (i + 1) * 500, i * 500, (i + 1) * 500])
        las.y = C.ORIGIN_N - np.array([j * 500, j * 500, (j + 1) * 500, (j + 1) * 500])
        las.write(lazdir / f"{int(C.ORIGIN_E + i * 500)}_{int(C.ORIGIN_N - j * 500)}.laz")
    layers = tmp_path / "layers"
    fixture(layers)
    out = tmp_path / "dist"
    assemble(["t0_0_0"], layers=layers, out=out)
    manifest = json.loads((out / "manifest.json").read_text())
    expected = {"t1_0_0", "t1_0_1", "t1_1_0", "t1_1_1"}
    assert {k for k, v in manifest["visual"].items() if v["level"] == 1} == expected
    assert all(v["coarse_dtm"] for v in manifest["visual"].values() if v["level"] > 0)
    tileset = json.loads((out / "tileset.json").read_text())
    assert {c["extras"]["id"] for c in tileset["root"]["children"][0]["children"]} == expected


def test_batch_preserves_object_ranges_and_uses_one_material(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers)
    buildings = layers / "buildings"
    meshes = [
        GlbMesh(
            f"wall-{i}",
            np.array([[i, 0, 10], [i + 1, 0, 10], [i, 3, 10]], dtype=float),
            np.array([[0, 1, 2]], np.uint32),
            uvs=np.zeros((3, 2), np.float32),
            extras=dict(
                layer="buildings", object_id=f"wall-{i}", material_key="facade_wood", facade_index=i
            ),
        )
        for i in range(100)
    ]
    write_visual(buildings / "t0_0_0.vis.glb", meshes)
    write_manifest(buildings, inputs={}, params={}, code="test", tiles=["t0_0_0.vis.glb"])
    assemble(["t0_0_0"], layers=layers, out=out)
    doc, _ = read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
    batches = [n for n in doc["nodes"] if n.get("extras", {}).get("layer") == "buildings"]
    assert len(batches) == 1
    objects = batches[0]["extras"]["objects"]
    assert {o["object_id"] for o in objects} == {f"wall-{i}" for i in range(100)}
    assert [(o["start"], o["count"]) for o in objects] == [(i * 3, 3) for i in range(100)]
    assert len(doc["materials"]) == 2  # ground + the shared facade material


def test_office_spawn_uses_ground_outside_footprints_and_public_parking(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point, box, mapping
    from turku3d.package import spawn

    root = tmp_path / "layers"
    buildings = root / "buildings"
    buildings.mkdir(parents=True)
    x, z = C.OFFICE_E - C.ORIGIN_E, C.ORIGIN_N - C.OFFICE_N
    footprint = box(x - 4, z - 4, x + 4, z + 4)
    (buildings / "footprints.geojson").write_text(
        json.dumps(
            dict(
                turku3d_coords="engine-xz",
                features=[dict(geometry=mapping(footprint), properties=dict(building_id="office"))],
            )
        )
    )
    (buildings / "facades.geojson").write_text(
        json.dumps(
            dict(
                features=[
                    dict(
                        geometry=dict(
                            coordinates=[
                                [C.ORIGIN_E + x - 4, C.ORIGIN_N - z - 4],
                                [C.ORIGIN_E + x + 4, C.ORIGIN_N - z - 4],
                            ]
                        ),
                        properties=dict(
                            building_id="office",
                            object_id="office/facade/0",
                            street_facing=True,
                            normal=[0, 0, 1],
                        ),
                    )
                ]
            )
        )
    )
    positions, indices = [], []
    for a in range(-10, 30):
        for b in range(-10, 20):
            n = len(positions)
            positions.extend([[x + a, 3, z + b], [x + a + 1, 3, z + b], [x + a, 3, z + b + 1]])
            indices.append([n, n + 2, n + 1])
    layer = CollisionLayer(
        np.array(positions, np.float64),
        np.array(indices, np.uint32),
        np.full(len(indices), 2, np.uint8),  # sidewalk only: no curbside spot, use parking
    )
    parking = box(x + 12, z + 4, x + 28, z + 15)
    frame = gpd.GeoDataFrame(
        dict(
            amenity=["parking"], tags=['{"access":"yes"}'], osm_id=[123], geometry_local=[parking]
        ),
        geometry=[parking],
        crs="EPSG:3877",
    )
    monkeypatch.setattr(spawn, "osm_sources", lambda: [tmp_path / "parking.parquet"])
    monkeypatch.setattr(gpd, "read_parquet", lambda _: frame)
    result = spawn.derive_spawn(root, {"ground": {"c_2_3": layer}})
    assert result["position"][1] == 3
    assert result["position"][2] > z + 4
    assert not footprint.covers(Point(np.array(result["position"])[[0, 2]]))
    assert result["headingDeg"] == 0  # facing the south facade from the street
    assert result["vehicle"]["osm_id"] == 123
    assert parking.contains(Point(np.array(result["vehicle"]["position"])[[0, 2]]))


def test_office_spawn_parks_car_curbside_next_to_player(tmp_path, monkeypatch):
    from shapely.geometry import Point, box, mapping
    from turku3d.package import spawn

    root = tmp_path / "layers"
    buildings = root / "buildings"
    buildings.mkdir(parents=True)
    x, z = C.OFFICE_E - C.ORIGIN_E, C.ORIGIN_N - C.OFFICE_N
    footprint = box(x - 4, z - 4, x + 4, z + 4)
    (buildings / "footprints.geojson").write_text(
        json.dumps(
            dict(
                turku3d_coords="engine-xz",
                features=[dict(geometry=mapping(footprint), properties=dict(building_id="office"))],
            )
        )
    )
    (buildings / "facades.geojson").write_text(
        json.dumps(
            dict(
                features=[
                    dict(
                        geometry=dict(
                            coordinates=[
                                [C.ORIGIN_E + x - 4, C.ORIGIN_N - z - 4],
                                [C.ORIGIN_E + x + 4, C.ORIGIN_N - z - 4],
                            ]
                        ),
                        properties=dict(
                            building_id="office",
                            object_id="office/facade/0",
                            street_facing=True,
                            normal=[0, 0, 1],
                        ),
                    )
                ]
            )
        )
    )
    positions, indices, materials = [], [], []
    for a in range(-10, 30):
        for b in range(-10, 20):
            n = len(positions)
            positions.extend(
                [
                    [x + a, 3, z + b],
                    [x + a + 1, 3, z + b],
                    [x + a, 3, z + b + 1],
                    [x + a + 1, 3, z + b + 1],
                ]
            )
            indices.extend([[n, n + 2, n + 1], [n + 1, n + 2, n + 3]])
            materials.extend([1 if 9 <= b <= 14 else 2] * 2)  # road strip south of the sidewalk
    layer = CollisionLayer(
        np.array(positions, np.float64), np.array(indices, np.uint32), np.array(materials, np.uint8)
    )
    monkeypatch.setattr(spawn, "osm_sources", lambda: [])
    result = spawn.derive_spawn(root, {"ground": {"c_2_3": layer}})
    car = np.array(result["vehicle"]["position"])[[0, 2]]
    feet = np.array(result["position"])[[0, 2]]
    assert result["vehicle"]["source"] == "curbside road next to the start"
    assert 2.0 <= np.linalg.norm(car - feet) <= 25.0
    assert z + 9 <= car[1] <= z + 15
    assert not footprint.intersects(Point(car).buffer(2.5))

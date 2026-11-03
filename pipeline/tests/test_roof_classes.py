"""roof-classes: roof ortho crops go up to power-of-two squares; models, placements and block LODs follow."""

import hashlib
import struct

from turku3d.coarse import roof_classes
from turku3d.migrations import core


def ktx2(width, height, body=b""):
    return b"\xabKTX 20\xbb\r\n\x1a\n" + struct.pack("<IIIII", 0, 1, width, height, 0) + body


def sha(data):
    return hashlib.sha256(data).hexdigest()


def roof_model(crop_uri):
    document = {"asset": {"version": "2.0"},
                "images": [{"uri": crop_uri, "mimeType": "image/ktx2"}],
                "textures": [{"extensions": {"KHR_texture_basisu": {"source": 0}}}],
                "materials": [{"extras": {"material_key": "roof_ortho"}, "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                              {"extras": {"material_key": "facade_kit"}}],
                "meshes": [{"name": "roof"}]}
    return core.glb_pack(document, b"\1\2\3\4")


def product(tmp_path, crops):
    """A tile with one placement model per crop (and one placement each, two blocks, one block LOD)."""
    tile, shared = tmp_path / "t0_0_0", tmp_path / "shared"
    tile.mkdir(parents=True); shared.mkdir(parents=True)
    models, placements = [], []
    for i, (width, height) in enumerate(crops):
        crop = ktx2(width, height, bytes([i]))
        (shared / f"{sha(crop)}.ktx2").write_bytes(crop)
        model = roof_model(f"../shared/{sha(crop)}.ktx2")
        (shared / f"{sha(model)}.glb").write_bytes(model)
        models.append({"id": sha(model), "uri": f"../shared/{sha(model)}.glb", "bytes": len(model)})
        placements.append({"id": f"b{i}", "model": sha(model), "block": "k0" if i % 2 else "k1"})
    manifest = {"tile": "t0_0_0", "buildingPlacements": {"version": 1, "models": models, "placements": placements,
                                                          "blocks": [{"id": "k0", "lod": models[0]["id"]}]}}
    return core.Product(tile, manifest)


def fake_resample(data, side):
    return ktx2(side, side, data[-1:])


def test_size_classes_never_lose_texels():
    assert [roof_classes.size_class(w, h) for w, h in [(8, 4), (28, 20), (76, 72), (100, 100), (128, 108), (180, 184), (512, 512), (2, 2)]] \
        == [8, 32, 128, 128, 128, 256, 512, 8]
    for w in range(4, 600, 4):
        for h in (4, w, 2 * w):
            side = roof_classes.size_class(w, h)
            assert side >= w and side >= h and side & (side - 1) == 0 and side < 2 * max(w, h, 8)


def test_models_get_square_crops_and_references_follow(tmp_path):
    p = product(tmp_path, [(28, 20), (512, 512), (180, 184)])
    old = [m["id"] for m in p.manifest["buildingPlacements"]["models"]]
    evidence = roof_classes.classify_models(p, fake_resample)
    bp = p.manifest["buildingPlacements"]
    assert sorted(e["to"] for e in evidence.values()) == [32, 256]
    assert all(e["to"] >= max(e["from"]) for e in evidence.values())
    by_old = dict(zip(old, bp["models"]))
    unchanged = by_old[old[1]]
    assert unchanged["id"] == old[1]  # already a 512 square: untouched
    for i in (0, 2):
        model = by_old[old[i]]
        assert model["id"] != old[i] and model["uri"] == f"../shared/{model['id']}.glb"
        document, binary = core.glb_chunks(p.read(model["uri"]))
        assert binary == b"\1\2\3\4"
        uri = document["images"][0]["uri"]
        assert uri.startswith("../shared/") and roof_classes.ktx2_size(p.read(uri)) == ((32, 32) if i == 0 else (256, 256))
        assert model["bytes"] == len(p.read(model["uri"]))
    ids = {m["id"] for m in bp["models"]}
    assert all(placement["model"] in ids for placement in bp["placements"])
    assert bp["blocks"][0]["lod"] == by_old[old[0]]["id"]


def test_classification_is_deterministic_and_idempotent(tmp_path):
    a, b = product(tmp_path / "a", [(76, 72), (8, 4)]), product(tmp_path / "b", [(76, 72), (8, 4)])
    roof_classes.classify_models(a, fake_resample)
    roof_classes.classify_models(b, fake_resample)
    assert a.manifest == b.manifest and a.written == b.written
    written = dict(a.written)
    assert roof_classes.classify_models(a, fake_resample) == {}  # square crops: nothing left to do
    assert a.written == written

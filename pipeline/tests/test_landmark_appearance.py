import json
import struct
from types import SimpleNamespace

import numpy as np
from turku3d.landmarks.appearance import roof_mesh, wall_meshes
from turku3d.landmarks.ranking import rank
from turku3d.package.gltf import build_glb


def building(key="103371570S"):
    return SimpleNamespace(facade_key=key, key=key, base=12.0, top=90.0, attributes={})


def test_unidentified_building_preserves_generic_facade_path():
    assert wall_meshes(building("ordinary"), np.zeros((4, 3)), np.zeros(3)) is None


def test_cathedral_glb_has_physical_maps_and_height_aligned_uv():
    b = building()
    wall = np.array([[0, 12, 0], [15, 12, 0], [15, 75, 0], [0, 75, 0]], float)
    meshes = wall_meshes(b, wall, np.zeros(3))
    m = meshes[0]
    assert m.extras["landmark_tower"]
    assert np.allclose(m.uvs, [[0, 1], [1, 1], [1, 0], [0, 0]])
    data = build_glb(meshes)
    size = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + size])
    mat = doc["materials"][0]
    assert len(doc["images"]) == 3
    assert mat["normalTexture"]["index"] != mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    assert mat["occlusionTexture"] == mat["pbrMetallicRoughness"]["metallicRoughnessTexture"]
    assert np.isfinite(m.positions).all()


def test_roof_triangle_projection_has_no_degenerate_spire_uv():
    b = building()
    p = np.array([[0, 65, 0], [4, 65, 0], [2, 90, 2]], float)
    mesh = roof_mesh(b, p, np.array([[0, 1, 2]]), np.zeros(3))
    assert np.linalg.det(np.stack([mesh.uvs[1] - mesh.uvs[0], mesh.uvs[2] - mesh.uvs[0]])) > 1


def test_ranking_promotes_evidence_and_reports_unknowns():
    tags = dict(
        building="cathedral",
        amenity="place_of_worship",
        historic="church",
        wikipedia="fi:Turun tuomiokirkko",
        wikidata="Q160165",
    )
    a = rank(tags)
    b = rank(tags, dict(rky="RKY test", monthly_pageviews=20000, height_m=90, visible_streets=4))
    assert a["tier"] == "landmark" and b["score"] > a["score"]
    assert "rky" in a["missing_evidence"] and "rky" not in b["missing_evidence"]
    assert sum(r["points"] for r in b["reasons"]) == b["score"]
    assert rank({"building": "apartments"})["tier"] == "ordinary"

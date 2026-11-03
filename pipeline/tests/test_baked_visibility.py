"""Baked lighting must survive raw-model extraction without changing authored pigment."""

import numpy as np
import pytest

from turku3d.package.gltf import GlbMesh, accessor_array, build_glb, read_glb
from turku3d.wall_surfaces import layer_meshes


def triangle(**kwargs):
    return GlbMesh(
        "wall", np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
        np.array([[0, 1, 2]], np.uint32), **kwargs,
    )


def test_visibility_survives_model_extraction_separately_from_pigment(tmp_path):
    pigment = np.array([[.1, .2, .3], [.3, .4, .5], [.5, .6, .7]], np.float32)
    visibility = np.array([[.2, .7, 1], [.5, .8, 1], [1, 1, 0]], np.float32)
    original = triangle(colors=pigment, baked_visibility=visibility)
    path = tmp_path / "architecture.glb"
    path.write_bytes(build_glb([original], root_translation=(250, 0, -250)))
    extracted, = layer_meshes([path])
    np.testing.assert_array_equal(extracted.colors, pigment)
    np.testing.assert_allclose(extracted.baked_visibility, visibility, atol=.5 / 255)
    # Moving a complete model into its own translation-only frame does not rebake lighting.
    extracted.positions = extracted.positions - [0.5, 0, 0]
    js, binary = read_glb(build_glb([extracted], root_translation=(250.5, 0, -250)))
    attrs = js["meshes"][0]["primitives"][0]["attributes"]
    np.testing.assert_array_equal(accessor_array(js, binary, attrs["COLOR_0"]), pigment)
    # UNORM8 RGBA on the wire: a re-emitted bake is bit-identical, alpha unused.
    shipped = js["accessors"][attrs["COLOR_1"]]
    assert (shipped["componentType"], shipped["type"], shipped["normalized"]) == (5121, "VEC4", True)
    np.testing.assert_allclose(accessor_array(js, binary, attrs["COLOR_1"])[:, :3], visibility, atol=.5 / 255)
    assert js["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == list(original.color)


def test_unbaked_mesh_omits_visibility(tmp_path):
    data = build_glb([triangle()])
    js, _ = read_glb(data)
    assert "COLOR_1" not in js["meshes"][0]["primitives"][0]["attributes"]
    path = tmp_path / "unbaked.glb"
    path.write_bytes(data)
    extracted, = layer_meshes([path])
    assert extracted.baked_visibility is None


@pytest.mark.parametrize("values", [
    np.ones((2, 3)), np.ones((3, 4)), np.ones(9),
    np.full((3, 3), np.nan), np.full((3, 3), np.inf),
    np.full((3, 3), -.01), np.full((3, 3), 1.01),
])
def test_invalid_visibility_is_rejected(values):
    with pytest.raises(ValueError, match="baked_visibility"):
        build_glb([triangle(baked_visibility=values)])

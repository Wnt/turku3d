"""Synthetic visual/collider agreement fixtures for the collision audit."""

import numpy as np
from turku3d.collision_audit import audit
from turku3d.layers import (
    PRIMITIVE_DTYPE,
    CollisionLayer,
    write_collision,
    write_manifest,
    write_visual,
)
from turku3d.package.gltf import GlbMesh


def test_primitive_audit_detects_unmatched_and_oversized(tmp_path):
    root = tmp_path / "layers" / "props"
    root.mkdir(parents=True)
    cube = np.array(
        [
            [-0.5, 0, -0.5],
            [0.5, 0, -0.5],
            [0.5, 1, -0.5],
            [-0.5, 1, -0.5],
            [-0.5, 0, 0.5],
            [0.5, 0, 0.5],
            [0.5, 1, 0.5],
            [-0.5, 1, 0.5],
        ],
        dtype=np.float64,
    )
    write_visual(
        root / "t0_0_0.vis.glb",
        [
            GlbMesh(
                "test",
                cube,
                np.array([[0, 1, 2], [0, 2, 3]], np.uint32),
                extras={"layer": "props", "object_id": "test", "material_key": "test"},
            )
        ],
    )
    parts = np.zeros(3, PRIMITIVE_DTYPE)
    parts["kind"] = "box"
    parts["material"] = 28
    parts["center"] = [[0, 0.5, 0], [3, 0.5, 0], [0, 0.5, 3]]
    parts["half_extents"] = [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [1, 0.5, 0.5]]
    write_collision(
        root / "c_0_0.col.npz",
        CollisionLayer(
            np.empty((0, 3), np.float64), np.empty((0, 3), np.uint32), np.empty(0, np.uint8), parts
        ),
    )
    write_manifest(
        root,
        inputs={},
        params={"seams": "objects"},
        code="fixture",
        tiles=["t0_0_0.vis.glb", "c_0_0.col.npz"],
    )
    issues = audit(root.parent, (0, 0), radius=5)
    assert [i["primitive"] for i in issues] == [1, 2]


def test_walkable_pavement_cannot_hide_a_high_step(tmp_path):
    root = tmp_path / "layers" / "ground"
    root.mkdir(parents=True)
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0.1]], np.float64)
    indices = np.array([[0, 1, 2]], np.uint32)
    write_visual(
        root / "t0_0_0.vis.glb",
        [
            GlbMesh(
                "ground",
                positions,
                indices,
                extras={"layer": "ground", "object_id": "ground", "material_key": "sidewalk"},
            )
        ],
    )
    write_collision(
        root / "c_0_0.col.npz", CollisionLayer(positions, indices, np.array([2], np.uint8))
    )
    write_manifest(
        root,
        inputs={},
        params={"seams": "continuous"},
        code="fixture",
        tiles=["t0_0_0.vis.glb", "c_0_0.col.npz"],
    )
    issues = audit(root.parent, (0, 0), radius=5)
    assert [i["kind"] for i in issues] == ["blocking_walkable_surface"]

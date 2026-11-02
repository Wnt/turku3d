import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image

from turku3d.facade_assets import best_description
from turku3d.landmarks.appearance import wall_meshes
from turku3d.landmarks.descriptions import resolve
from turku3d.landmarks.elevations import coordinates, texture


def test_reference_consensus_beats_wide_occluded_view():
    def row(key, material, width, source="reference"):
        return dict(facade_id=key, width_m=width, fast_description=dict(
            source=source, description=dict(material=material)))
    rows = [row("a", "brick", 3), row("b", "brick", 5), row("c", "stone", 38),
            row("d", "stone", 50, "building")]
    assert best_description(rows)["facade_id"] == "b"
    assert best_description([rows[0], rows[2]])["facade_id"] == "c"


def test_fragments_share_measured_elevation_and_do_not_repeat_shopfronts():
    b = SimpleNamespace(key="hall", facade_key="103304670P", base=4.039, attributes={})
    d = resolve(b)
    row = d["elevations"][0]
    origin, axis = np.array(row["origin_xz"]), np.array(row["axis_xz"])
    def quad(lo, hi):
        xz = origin + np.array([lo, hi, hi, lo])[:, None] * axis
        return np.c_[xz[:, 0], b.base + np.array([0, 0, 6, 6]), xz[:, 1]]
    left, right = quad(0, 8), quad(8, row["width_m"])
    np.testing.assert_allclose(coordinates(left, b.base, row)[[1, 2]],
                               coordinates(right, b.base, row)[[0, 3]])
    meshes = wall_meshes(b, np.concatenate([left, right]), np.zeros(3), d)
    assert sum(len(m.indices) for m in meshes) == 4
    assert len(meshes) == 1
    albedo, _, _ = texture(json.dumps(d), json.dumps(row))
    image = np.asarray(Image.open(io.BytesIO(albedo)))
    # Brick between openings remains red; the three tall panes remain neutral.
    def patch(x, y):
        px = round(x / row["width_m"] * image.shape[1])
        py = round((1 - y / row["height_m"]) * image.shape[0])
        return image[py-2:py+3, px-2:px+3].mean((0, 1))
    brick = patch(5.6, 4.5)
    assert brick[0] - brick[1] > 30
    for x in (6.55, 8.4, 10.2):
        glass = patch(x, 4.5)
        assert abs(glass[0] - glass[1]) < 15

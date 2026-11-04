"""Survey shell roofs take the orthophoto mean under them; nodata keeps the ROOF pigment."""

import numpy as np

from turku3d.coarse import roof_pigment


def test_roof_mean_comes_from_the_photo_under_it(monkeypatch):
    image = np.zeros((roof_pigment.PIXELS, roof_pigment.PIXELS, 3), np.uint8)
    image[:, : roof_pigment.PIXELS // 2] = (200, 100, 50)  # west half of t0_0_0 is a red roof
    monkeypatch.setattr(roof_pigment, "_leaf", lambda path, tile: image)
    pigments = roof_pigment.RoofPigments({"t0_0_0": "mosaic.vrt"})
    west = pigments.mean(np.array([[10, 20, 10], [60, 20, 10], [10, 20, 60]]), np.array([[0, 1, 2]]))
    expected = roof_pigment._linear(np.array([200.0, 100.0, 50.0]))
    np.testing.assert_allclose(west, expected, atol=1e-4)
    # The east half is black nodata, and a roof outside any mosaic has no reading: both keep ROOF.
    assert pigments.mean(np.array([[200, 20, 10], [240, 20, 10], [200, 20, 60]]), np.array([[0, 1, 2]])) is None
    assert pigments.mean(np.array([[600, 20, 10], [640, 20, 10], [600, 20, 60]]), np.array([[0, 1, 2]])) is None


def test_ortho_inputs_skip_tiles_without_imagery():
    class Sources:
        def ortho_source(self, tile):
            if tile.i:
                raise RuntimeError("no coverage")
            return "/data/ortho.vrt"

    from turku3d import config as C
    assert roof_pigment.ortho_inputs(Sources(), [C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)]) == {"t0_0_0": "/data/ortho.vrt"}

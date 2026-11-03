import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.inputs import Building


def hipped(x0=10.0, z0=10.0, w=20.0, d=10.0, eave=12.0, ridge=16.0):
    """Four hipped facets (about 38 deg) over a w x d footprint."""
    a, b, c, e = [x0, eave, z0], [x0 + w, eave, z0], [x0 + w, eave, z0 + d], [x0, eave, z0 + d]
    r0, r1 = [x0 + d / 2, ridge, z0 + d / 2], [x0 + w - d / 2, ridge, z0 + d / 2]
    roof = np.array([a, b, c, e, r0, r1], float)
    indices = np.array([[0, 4, 5], [0, 5, 1], [1, 5, 2], [2, 5, 4], [2, 4, 3], [3, 4, 0]], np.uint32)
    return roof, indices


def building(**attrs):
    b = Building("lib", shapely.box(10, 10, 30, 20), 0, 16, "facade_brick")
    b.attributes = {**dict(construction_year=1903, material_class="brick", use="2120"), **attrs}
    return b


def test_historic_civic_pitched_roof_gets_seams_down_each_facet_in_its_own_colour():
    import io

    from PIL import Image
    from turku3d.coarse.metal_roofs import SEAM_M, mesh

    tile = C.VisualTile(0, 0, 0)
    roof, indices = hipped()
    photo = np.full((64, 64, 3), [150, 82, 70], np.uint8)
    m = mesh(tile, building(), roof, indices, np.zeros(3), photo)
    assert m.extras["material_key"] == "roof_metal" and m.extras["roof_colour"] == "#965246"
    albedo = np.asarray(Image.open(io.BytesIO(m.image)), float)
    assert np.abs(albedo.mean(axis=(0, 1)) - [150, 82, 70]).max() < 25
    # Seams are texture columns: the eave direction maps to u only, the fall line to v only.
    tri = roof[indices.reshape(-1)].reshape(-1, 3, 3)
    uv = m.uvs.reshape(-1, 3, 2)
    for t, coords in zip(tri, uv, strict=True):
        edges, deltas = (t[1:] - t[0]).T, coords[1:] - coords[0]
        n = np.cross(t[1] - t[0], t[2] - t[0])
        along = np.cross([0, 1, 0], n)
        along /= np.linalg.norm(along)
        fall = np.cross(n / np.linalg.norm(n), along)
        for direction, axis in ((along, 1), (fall, 0)):
            weights = np.linalg.lstsq(edges, direction, rcond=None)[0]
            assert abs(weights @ deltas[:, axis]) < 1e-9
    assert 0.4 <= SEAM_M <= 0.7


def test_flat_tiled_and_ordinary_roofs_keep_their_photo():
    from turku3d.coarse.metal_roofs import mesh

    tile = C.VisualTile(0, 0, 0)
    roof, indices = hipped()
    photo = np.full((64, 64, 3), [150, 82, 70], np.uint8)
    flat, _ = hipped(ridge=12.2)
    assert mesh(tile, building(), flat, indices, np.zeros(3), photo) is None
    assert mesh(tile, building(roof_material="tile"), roof, indices, np.zeros(3), photo) is None
    housing = building()
    housing.attributes.update(use="1000")
    assert mesh(tile, housing, roof, indices, np.zeros(3), photo) is None
    castle = building(construction_year=1280)
    castle.attributes.pop("use")
    assert mesh(tile, castle, roof, indices, np.zeros(3), photo) is not None

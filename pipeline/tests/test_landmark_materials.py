import json

import numpy as np


def test_mortar_coverage_preserves_physical_area_across_texture_densities():
    from turku3d.landmarks.procedural import joint_coverage

    period, joint, length = 0.09, 0.012, 9.0
    for count in (64, 360, 2048):
        footprint = length / count
        centres = (np.arange(count) + 0.5) * footprint
        coverage = joint_coverage(centres, period, joint, footprint)
        assert np.isclose(coverage.mean(), joint / period, atol=1e-10)
        assert np.all((coverage >= 0) & (coverage <= 1))


def test_a_metal_roof_with_zero_seam_width_is_a_standing_seam_roof_not_black():
    """103371533K (t0_-3_-2) describes its metal roof with "seam_m": 0. As a panel width that
    divided by zero: the NaN surface packed a black albedo and a byte-identical black normal map,
    which the packager could not encode."""
    import io

    from PIL import Image
    from turku3d.landmarks.procedural import material_maps

    def pixels(png):
        return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), float)

    roof = dict(material="metal", colour=[127, 89, 76])
    zero = json.dumps(dict(roof=dict(roof, seam_m=0)))
    default = json.dumps(dict(roof=roof))
    albedo, normal, orm = (pixels(p) for p in material_maps(zero, "roof"))
    assert material_maps(zero, "roof") == material_maps(default, "roof")
    assert material_maps(zero, "roof")[0] != material_maps(zero, "roof")[1]
    assert np.allclose(albedo.reshape(-1, 3).mean(0), [127, 89, 76], atol=12)
    assert normal[..., 2].min() > 200 and orm[..., 0].min() >= 0.65 * 255 - 1

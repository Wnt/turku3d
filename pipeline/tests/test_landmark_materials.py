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


"""Facade v4 CPU sampler follows the public node shader's pigment and band rules."""

import numpy as np

from gmaps_facade_v4 import evaluate, linear, sampler, srgb, warp


def attributes(count=3):
    values = {
        '_KIT_LAYOUT': [0, 2, 3], '_KIT_WALL': [.5, .4, .3],
        '_KIT_TRIM': [.8, .7, .6], '_KIT_BASE': [.3, .35, .4],
        '_KIT_EXTENT': [0, 0, 4, 6], '_KIT_FLOORS': [3, 3, 0],
        '_KIT_OPENING': [.4, .5, .2, .1], '_KIT_BANDS': [0, 0, 0, 0],
        '_KIT_DETAIL': [0, 0, 0, 0],
    }
    return {name: np.tile(row, (count, 1)).astype(float) for name, row in values.items()}


def test_plain_base_wall_and_cornice_pigments():
    attrs = attributes()
    attrs['_KIT_BANDS'][2, 1] = .3
    pixels = np.full((2, 2, 4), [200, 180, 160, 0], np.uint8)
    values = evaluate(np.array([[1, 1], [1, 4], [1, 5.9]]), attrs, pixels)
    albedo = linear(np.array([200, 180, 160]) / 255)
    assert np.allclose(values[0], srgb(albedo * attrs['_KIT_BASE'][0]) * 255)
    assert np.allclose(values[1], srgb(albedo * attrs['_KIT_WALL'][1]) * 255)
    assert np.allclose(values[2], srgb(attrs['_KIT_TRIM'][2]) * 255)


def test_warp_and_missing_shader_attributes():
    assert np.allclose(warp(np.array([0., .25, .5, .75, 1.]),
                            .25, .75, .1, .9), [0, .1, .5, .9, 1])
    assert sampler(None, {'kit': {'version': 4}, 'kit_maps_complete': True,
                          'attributes': {'_KIT_LAYOUT': 'layout'}}, None) is None


def test_synthetic_v4_webgpu_parity():
    # Synthetic atlas and attributes measured from facadeKitMaterial in WebGPU.
    y, x = np.indices((2048, 1024))
    pixels = np.stack(((x * 17 + y * 3) % 256, (x * 7 + y * 11) % 256,
                       (x * 13 + y * 5) % 256, np.bitwise_xor(x, y) % 256), -1).astype(np.uint8)
    attrs = {name: np.asarray(value, float) for name, value in {
        '_KIT_LAYOUT': [[1, 2.3, 3.1]], '_KIT_WALL': [[.2, .4, .6]],
        '_KIT_TRIM': [[.7, .5, .3]], '_KIT_BASE': [[.32, .28, .21]],
        '_KIT_EXTENT': [[.13, .07, 4.3, 6.2]], '_KIT_FLOORS': [[3.5, 2.7, 1]],
        '_KIT_OPENING': [[.52, .46, .24, .13]], '_KIT_BANDS': [[.37, .24, .09, .12]],
        '_KIT_DETAIL': [[0, 0, 0, 6]],
    }.items()}
    cpu = linear(evaluate(np.asarray([[.37, .29]]), attrs, pixels)[0] / 255)
    gpu = [.24072527885437012, .3674481213092804, .42820513248443604]
    assert np.allclose(cpu, gpu, atol=3e-4)

import numpy as np
import pytest
from PIL import Image
from turkutex.post.frame import facade_content_box


def test_paired_presentation_margins_preserve_every_content_pixel():
    pixels = np.full((100, 160, 3), 254, dtype=np.uint8)
    pixels[30:70] = np.random.default_rng(12).integers(30, 220, (40, 160, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    box = facade_content_box(image)
    assert box == (0, 30, 160, 70)
    np.testing.assert_array_equal(np.asarray(image.crop(box)), pixels[30:70])


def test_light_cornice_and_nonwhite_sky_are_not_presentation_frames():
    pixels = np.full((100, 160, 3), 120, dtype=np.uint8)
    pixels[:30] = 254
    assert facade_content_box(Image.fromarray(pixels)) == (0, 0, 160, 100)
    pixels[-30:] = [240, 245, 255]
    assert facade_content_box(Image.fromarray(pixels)) == (0, 0, 160, 100)


def test_blank_generation_is_rejected():
    with pytest.raises(ValueError, match="blank white canvas"):
        facade_content_box(Image.new("RGB", (160, 100), "white"))

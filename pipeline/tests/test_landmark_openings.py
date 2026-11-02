from PIL import Image, ImageDraw

from turku3d.landmarks.openings import panelled_door


def test_glazed_door_keeps_upper_glass_and_lower_recess_within_the_opening():
    colour = Image.new("RGB", (100, 120), (91, 80, 62))
    height = Image.new("F", colour.size, -.06)
    panelled_door(ImageDraw.Draw(colour), ImageDraw.Draw(height),
                  (10, 10, 90, 110), {}, 40, 40)
    assert colour.getpixel((18, 20)) == (38, 45, 41)
    assert height.getpixel((18, 20)) < -.075
    assert colour.getpixel((20, 90)) == (71, 60, 42)
    assert height.getpixel((20, 90)) < -.065
    assert colour.getpixel((5, 20)) == (91, 80, 62)

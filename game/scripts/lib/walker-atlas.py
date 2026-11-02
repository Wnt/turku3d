"""Pack the pinned CC0 source albedos into the crowd's shared, padded colour atlas."""
import json
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

spec = json.loads(Path(sys.argv[1]).read_text())
atlas = Image.new('RGBA', (spec['columns'] * 512, spec['rows'] * 512), (255, 255, 255, 255))
for entry in spec['entries']:
    im = Image.open(entry['file']).convert('RGBA') if entry['file'] else Image.new('RGBA', (1, 1), 'white')
    trim = Image.open(entry['mask']).convert('L').resize(im.size, Image.Resampling.LANCZOS) if entry.get('mask') else Image.new('L', im.size, 0)
    if entry['name'] == 'Cloth_tshirt':
        # Remove the pinned CC0 source's chest emblem; sample unprinted cotton from its back panel.
        # This edits only our source-derived atlas, never the reference panoramas.
        w, h = im.size
        logo = Image.new('L', im.size, 0)
        ImageDraw.Draw(logo).ellipse((w * .503, h * .225, w * .655, h * .60), fill=255)
        logo = logo.filter(ImageFilter.GaussianBlur(3))
        plain = ImageChops.offset(im, round(w * .382), 0)
        im = Image.composite(plain, im, logo)
        trim = Image.composite(Image.new('L', im.size, 0), trim, logo)
    # Opaque surfaces use alpha for the independently tinted trim; hair keeps its real cutout opacity.
    if not (entry['name'].startswith('Hair_') or entry['name'] == 'Eyebrow'):
        # Keep RGB recoverable through browser image decoders that premultiply alpha.
        # Opaque trim occupies alpha128..255; hair/brows retain genuine opacity0..255.
        im.putalpha(trim.point(lambda value: 128 + round(value * 127 / 255)))
    # Alpha is data on opaque cloth, so do not premultiply it into RGB while resizing.
    rgb = im.convert('RGB').resize((504, 504), Image.Resampling.LANCZOS)
    alpha = im.getchannel('A').resize((504, 504), Image.Resampling.LANCZOS)
    im = rgb.convert('RGBA')
    im.putalpha(alpha)
    tile = Image.new('RGBA', (512, 512))
    tile.paste(im, (4, 4))
    tile.paste(im.crop((0, 0, 1, 504)).resize((4, 504)), (0, 4))
    tile.paste(im.crop((503, 0, 504, 504)).resize((4, 504)), (508, 4))
    tile.paste(tile.crop((0, 4, 512, 5)).resize((512, 4)), (0, 0))
    tile.paste(tile.crop((0, 507, 512, 508)).resize((512, 4)), (0, 508))
    atlas.paste(tile, ((entry['tile'] % spec['columns']) * 512, (entry['tile'] // spec['columns']) * 512))
atlas.save(sys.argv[2], optimize=True)

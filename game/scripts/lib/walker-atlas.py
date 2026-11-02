"""Pack the pinned CC0 source albedos into the crowd's shared, padded colour atlas."""
import json
import sys
from pathlib import Path
from PIL import Image

spec = json.loads(Path(sys.argv[1]).read_text())
atlas = Image.new('RGBA', (spec['columns'] * 512, spec['rows'] * 512), (255, 255, 255, 255))
for entry in spec['entries']:
    im = Image.open(entry['file']).convert('RGBA') if entry['file'] else Image.new('RGBA', (1, 1), 'white')
    im = im.resize((504, 504), Image.Resampling.LANCZOS)
    tile = Image.new('RGBA', (512, 512))
    tile.paste(im, (4, 4))
    tile.paste(im.crop((0, 0, 1, 504)).resize((4, 504)), (0, 4))
    tile.paste(im.crop((503, 0, 504, 504)).resize((4, 504)), (508, 4))
    tile.paste(tile.crop((0, 4, 512, 5)).resize((512, 4)), (0, 0))
    tile.paste(tile.crop((0, 507, 512, 508)).resize((512, 4)), (0, 508))
    atlas.paste(tile, ((entry['tile'] % spec['columns']) * 512, (entry['tile'] // spec['columns']) * 512))
atlas.save(sys.argv[2], optimize=True)

"""Read description JSON only and rerender its procedural map; never open reference imagery."""
import argparse
import json
import os
from pathlib import Path
from PIL import Image
from turkutex.facade_fast_render import render

ap=argparse.ArgumentParser()
ap.add_argument('--root',type=Path,help='Optional existing fast-pass directory containing plan/descriptions JSON')
ap.add_argument('--facade',default='103295893K_36')
a=ap.parse_args()
if a.root:
    description=json.loads((a.root/'descriptions.json').read_text())[a.facade]['description']
    wall=next(w for w in json.loads((a.root/'plan.json').read_text())['walls'] if w['facade_id']==a.facade)
else:
    saved=json.loads(Path('tools/video/office-facade-description.json').read_text())
    description,wall=saved['description'],saved['wall']
out=Path(os.environ['TURKU3D_WORK'])/'video/assets'
(out/'parameters.json').write_text(json.dumps({'description':description,'wall':wall},indent=2))
maps=render(description,wall['width_m'],wall['height_m'],(1200,760),a.facade)
Image.fromarray(maps['albedo']).save(out/'facade.png')

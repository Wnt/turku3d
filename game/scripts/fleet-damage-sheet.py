#!/usr/bin/env python3
"""Tiles fleet-damage-sheet.mjs shots into contact sheets: OUT_DIR/sheet-<shot>.jpg, one tile per model with its id
and what stays on the body (from report.json).

  python3 game/scripts/fleet-damage-sheet.py OUT_DIR [COLUMNS]
"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1]); columns = int(sys.argv[2]) if len(sys.argv) > 2 else 8
report = json.loads((out / 'report.json').read_text())
models = [m for m, r in report['models'].items() if 'shots' in r]
tile = (480, 300)
try:
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 15)
except OSError:
    font = ImageFont.load_default()
for shot in ('dent50', 'wreck-front', 'wreck-rear'):
    rows = (len(models) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * tile[0], rows * tile[1]), (24, 28, 32))
    for i, model in enumerate(models):
        r = report['models'][model]
        img = Image.open(out / f'{model}-{shot}.png').convert('RGB')
        img.thumbnail((tile[0], tile[1] - 38))
        x, y = (i % columns) * tile[0], (i // columns) * tile[1]
        sheet.paste(img, (x + (tile[0] - img.width) // 2, y))
        stays = [name for name, p in (r.get('parts') or {}).items() if not p['kept']]
        glass = r.get('glass') or {}
        draw = ImageDraw.Draw(sheet)
        draw.text((x + 6, y + tile[1] - 37), model, fill=(235, 240, 244), font=font)
        note = ('on body: ' + ', '.join(stays) if stays else 'all six parts') + f" · {len(glass.get('windows', []))} windows"
        draw.text((x + 6, y + tile[1] - 19), note[:64], fill=(170, 185, 195), font=font)
    sheet.save(out / f'sheet-{shot}.jpg', quality=88)
    print(out / f'sheet-{shot}.jpg')

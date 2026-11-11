#!/usr/bin/env python3
"""Labelled contact sheet of the scout stills: trailer-contact.py SCOUT_DIR [COLUMNS]."""
import sys, glob, os
from PIL import Image, ImageDraw, ImageFont
d = sys.argv[1]; cols = int(sys.argv[2]) if len(sys.argv) > 2 else 6
files = sorted(f for f in glob.glob(os.path.join(d, '*.jpg')) if not os.path.basename(f).startswith('contact'))
w, h, pad = 480, 270, 26
rows = (len(files) + cols - 1) // cols
sheet = Image.new('RGB', (cols * w, rows * (h + pad)), (16, 16, 16))
draw = ImageDraw.Draw(sheet)
try: font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 17)
except Exception: font = ImageFont.load_default()
for i, f in enumerate(files):
    x, y = (i % cols) * w, (i // cols) * (h + pad)
    sheet.paste(Image.open(f).convert('RGB').resize((w, h), Image.LANCZOS), (x, y + pad))
    draw.text((x + 6, y + 4), os.path.basename(f)[:-4], fill=(255, 255, 255), font=font)
sheet.save(os.path.join(d, 'contact.jpg'), quality=88)
print(len(files), 'stills ->', os.path.join(d, 'contact.jpg'))

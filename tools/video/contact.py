"""Small labelled camera-probe contact sheets for shot selection."""
import json
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
root=Path(os.environ['TURKU3D_WORK'])/'video'
shots=json.loads(Path('tools/video/shots.json').read_text())
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',20)
for start in range(0,len(shots),9):
    page=Image.new('RGB',(1440,3*294),'#0b1421')
    d=ImageDraw.Draw(page)
    for n,shot in enumerate(shots[start:start+9]):
        file=root/'shots'/f"{shot['id']}-probe-1.png"
        if not file.exists():continue
        image=Image.open(file).convert('RGB').resize((480,270))
        x=n%3*480;y=n//3*294
        page.paste(image,(x,y));d.text((x+5,y+270),shot['id'],font=font,fill='white')
    page.save(root/'review'/f'probes-{start//9+1}.jpg',quality=92)

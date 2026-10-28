"""Footage-led v2 edit: unique moving shots, small data overlays, ducked project music."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import textwrap
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
ROOT = Path(os.environ['TURKU3D_WORK']) / 'video'
OUT = Path.home() / 'turku3d/media/pipeline-video'
WHITE, TEAL, MUTED = '#f3f6f8', '#75e7cf', '#c5d3dd'
FONTS = Path('/System/Library/Fonts/Supplemental')


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def font(size, bold=False):
    return ImageFont.truetype(str(FONTS / ('Arial Bold.ttf' if bold else 'Arial.ttf')), size)


def text(draw, xy, value, size=32, fill=WHITE, bold=False):
    draw.text(xy, value, font=font(size, bold), fill=fill)


def stamp(t):
    ms = round(t*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def timeline():
    OUT.mkdir(parents=True, exist_ok=True)
    for folder in ['edit', 'review', 'audio']:
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    story = json.loads(Path('tools/video/story.json').read_text())
    index = json.loads((ROOT / 'audio/index.json').read_text())
    cursor = 0
    chunks, cues, result = [], [], []
    for scene in story:
        samples = [np.zeros(4800)]
        seq = [r for r in index if r['scene'] == scene['id']]
        for i, row in enumerate(seq):
            rate, data = wavfile.read(row['path'])
            assert rate == 24000
            data = data.astype(np.float64) / np.iinfo(data.dtype).max if data.dtype.kind == 'i' else data
            a = cursor + sum(map(len, samples))/24000
            b = a + len(data)/24000
            display = row['sentence']
            for spoken, caption in [('Turku three D', 'Turku3D'), ('Three dot J S', 'three.js'),
                                    ('Web G P U', 'WebGPU'), ('twenty twenty two', '2022'),
                                    ('three point four seconds', '3.4 seconds'),
                                    ('one and a half kilometres', '1.5 kilometres'),
                                    ('two hundred and fifty metres', '250 metres'),
                                    ('Creative Commons Attribution four point zero', 'CC BY 4.0')]:
                display = display.replace(spoken, caption)
            words = display.split()
            parts = max(1, math.ceil(len(display)/76))
            cuts = [round(len(words)*i/parts) for i in range(parts+1)]
            for j in range(parts):
                cue = ' '.join(words[cuts[j]:cuts[j+1]])
                start = a + (b-a)*cuts[j]/len(words)
                end = a + (b-a)*cuts[j+1]/len(words)
                cues.append(f'{len(cues)+1}\n{stamp(start)} --> {stamp(end)}\n{textwrap.fill(cue,43)}\n')
            samples.append(data)
            if i < len(seq)-1:
                samples.append(np.zeros(2400))
        samples.append(np.zeros(4800))
        data = np.concatenate(samples)
        # Equal shot lengths in integer frames, with no repeated shot or last-frame hold.
        frames = math.ceil(len(data)/800/len(scene['shots']))*len(scene['shots'])
        data = np.pad(data, (0, frames*800-len(data)))
        duration = frames/FPS
        result.append({**scene, 'start':cursor, 'duration':duration, 'frames':frames})
        chunks.append(data)
        cursor += duration
    ids = [shot for scene in result for shot in scene['shots']]
    assert len(ids) == len(set(ids)), 'No shot may be reused'
    wavfile.write(ROOT/'audio/narration-raw.wav', 24000, np.concatenate(chunks).astype('float32'))
    (ROOT/'timeline.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    (OUT/'turku3d-pipeline-v2.srt').write_text('\n'.join(cues))
    (OUT/'script-v2.md').write_text('# Turku3D — a city built from data\n\n'
        'Voice: local Kokoro bf_emma, speed 1.03. Music: original Harbour Glass, rendered by the game MusicSystem.\n'
        'All footage: own sandbox, fixed-step WebGPU, 1920×1080 at 30 fps. Car shots use the production Rapier worker.\n\n'
        + ''.join(f"## {stamp(s['start']).split(',')[0]} — {s['title']}\n\n{s['text']}\n\n"
          f"Shots: {', '.join(s['shots'])}. Evidence: {', '.join(s['sources'])}.\n\n" for s in result))
    chapters = [('hook','From data to a browser world'),('lidar','Measure the city'),('ground','Build the detailed core'),
                ('draft','Build beyond the core'),('tools','Development and the next step'),('credits','Sources and credits')]
    (OUT/'chapters-v2.txt').write_text('\n'.join(
        f"{int(next(s['start'] for s in result if s['id']==key))//60:02}:{int(next(s['start'] for s in result if s['id']==key))%60:02} {label}"
        for key,label in chapters)+'\n')
    print(f'Timeline: {cursor:.2f}s / {len(ids)} unique moving shots', flush=True)
    return result


def overlay(scene, kind):
    im = Image.new('RGBA', (W,H))
    d = ImageDraw.Draw(im)
    if kind == 'title':
        width = min(1800, max(740, int(d.textlength(scene['title'],font=font(48,True))+92)))
        d.rounded_rectangle((48,48,width,187),18,fill=(6,16,25,216))
        d.rectangle((48,66,54,166), fill=TEAL)
        text(d,(80,67),scene['label'],23,TEAL,True)
        text(d,(78,108),scene['title'],48,WHITE,True)
    elif kind == 'credit':
        d.rectangle((0,1028,W,H),fill=(6,16,25,185))
        text(d,(50,1042),'© City of Turku · CC BY 4.0   |   © OpenStreetMap contributors · ODbL',23)
        text(d,(1715,1042),'TURKU3D',23,TEAL,True)
    elif kind == 'data':
        asset = scene.get('asset')
        if asset:
            x,y,w,h = 55,235,650,495
            d.rounded_rectangle((x,y,x+w,y+h),18,fill=(6,16,25,235))
            if asset == 'parameters':
                desc=json.loads((ROOT/'assets/parameters.json').read_text())['description']
                d.rounded_rectangle((x,y,x+1080,y+495),18,fill=(6,16,25,235))
                text(d,(x+26,y+24),'LOCAL DESCRIPTION  →  PROCEDURAL ALBEDO',25,TEAL,True)
                lines=['{',f'  material: {desc["material"]}',f'  wall_colour: {desc["wall_colour"]}',
                       f'  ground_floor: {desc["ground_floor"]}','}']
                for i,line in enumerate(lines):text(d,(x+27,y+100+i*48),line,26)
                source=Image.open(ROOT/'assets/facade.png').convert('RGBA')
                source.thumbnail((480,320),Image.Resampling.LANCZOS)
                im.alpha_composite(source,(x+565,y+92+(320-source.height)//2))
                d=ImageDraw.Draw(im)
                text(d,(x+26,y+436),'Actual local render / layout fitted to wall dimensions / no reference pixels',25,TEAL)
            else:
                source=Image.open(ROOT/'assets'/f'{asset}.png').convert('RGBA')
                if asset in ('lidar','lod2'):
                    rgb=np.asarray(source.convert('RGB')).astype(int)
                    mask=np.max(np.abs(rgb-np.array([12,23,37])),axis=2)>40
                    yy,xx=np.where(mask)
                    source=source.crop((max(0,int(xx.min())-30),max(0,int(yy.min())-30),
                                        min(source.width,int(xx.max())+31),min(source.height,int(yy.max())+31)))
                source.thumbnail((600,365),Image.Resampling.LANCZOS)
                im.alpha_composite(source,(x+(w-source.width)//2,y+60+(365-source.height)//2))
                d=ImageDraw.Draw(im)
                names={'lidar':'ACTUAL LIDAR / HEIGHT-COLOURED RETURNS','relief':'GROUND RETURNS / MEASURED CROSS SECTION',
                       'ortho':'SOURCE ORTHOPHOTO / OFFICE BLOCK','lod2':'CITY LOD2 / SOURCE SURFACES','osm':'OSM / STREETS, BARRIERS & TREES'}
                text(d,(x+23,y+22),names[asset],23,TEAL,True)
                label={'lidar':'Same office neighbourhood', 'relief':'Candidates still require validation',
                       'ortho':'Same location / independent camera framing','lod2':'Roofs: ochre · other surfaces: blue',
                       'osm':'Local extract / not a rendered road surface'}[asset]
                text(d,(x+23,y+447),label,24)
                if asset == 'lod2':
                    reg=json.loads((ROOT/'assets/register.json').read_text())
                    d.rounded_rectangle((55,749,705,850),14,fill=(6,16,25,235))
                    text(d,(78,765),'BUILDING REGISTER / OFFICE',23,TEAL,True)
                    text(d,(78,803),reg['PysyvaRakennusTunnus']+' · '+reg['Osoitenimi_fi']+' '+reg['Osoitenumero'],24)
        elif scene['id'] in ['facades','package','prefetch','timings','swap','tools','credits']:
            lines={
                'facades':['IMAGE MODEL  →  INDEPENDENT REVIEW  →  RETRY'],
                'package':['VISUAL TILE: 250 m     WIRE BUDGET: 8 MB'],
                'prefetch':['500 m ring  +  forward cone up to 1.5 km','Request geometry shown schematically; this is a camera capture'],
                'timings':['Westbound: 3.4 s mean build','Southeast: missing downloads delayed tiles · recorded 27 Oct 2026'],
                'swap':['CURRENT WORLD  →  LOAD REPLACEMENT  →  SWAP'],
                'tools':['SANDBOX  →  FAST GATE  →  MAIN  →  BUILD & PUBLISH'],
                'credits':['City data © City of Turku · CC BY 4.0','Map data © OpenStreetMap contributors · ODbL',
                           'Local voice: Kokoro bf_emma · Music: Turku3D / Harbour Glass']}
            lines=lines[scene['id']]
            top=830-len(lines)*18
            d.rounded_rectangle((55,top,1505,top+55+len(lines)*41),16,fill=(6,16,25,220))
            for i,line in enumerate(lines):text(d,(80,top+24+i*41),line,29,TEAL if i==0 else WHITE,i==0)
            if scene['id']=='prefetch':
                d.rounded_rectangle((1370,240,1865,705),18,fill=(6,16,25,232))
                text(d,(1395,262),'REQUEST SHAPE / SCHEMATIC',23,TEAL,True)
                for x in range(1395,1850,45):d.line((x,315,x,650),fill='#294452',width=1)
                for y in range(325,660,45):d.line((1395,y,1840,y),fill='#294452',width=1)
                cx,cy=1570,535
                d.polygon([(cx,cy),(1663,330),(1805,475)],fill=(85,210,177,75),outline=TEAL,width=3)
                d.ellipse((cx-88,cy-88,cx+88,cy+88),outline=TEAL,width=4)
                d.polygon([(cx+13,cy-16),(cx-12,cy-2),(cx+2,cy+12)],fill=WHITE)
                text(d,(1395,650),'500 m ring · cone grows with speed',23)

    target=ROOT/'edit'/f"{scene['id']}-{kind}.png"
    im.save(target)
    return target


def render_scene(scene):
    inputs=[]
    filters=[]
    count=len(scene['shots'])
    for i,shot in enumerate(scene['shots']):
        source=ROOT/'shots'/f'{shot}.mp4'
        duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(source)]))
        inputs += ['-i',str(source)]
        frames=scene['frames']//count
        filters.append(f'[{i}:v]setpts={frames/FPS/duration}*PTS,fps=30,trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]')
    filters.append(''.join(f'[v{i}]' for i in range(count))+f'concat=n={count}:v=1:a=0,eq=contrast=1.045:saturation=1.055:brightness=-0.008[base]')
    for kind in ['title','credit','data']:
        inputs += ['-loop','1','-i',str(overlay(scene,kind))]
    filters += [f'[{count}:v]format=rgba,fade=t=out:st=3.6:d=0.45:alpha=1[title]',
                f'[base][title]overlay=0:0:shortest=1[a]',
                f'[a][{count+1}:v]overlay=0:0:shortest=1[b]',
                f'[b][{count+2}:v]overlay=0:0:shortest=1:enable=\'between(t,1.3,{scene["duration"]-0.5})\'[v]']
    run(['ffmpeg','-v','error','-y',*inputs,'-filter_complex',';'.join(filters),'-map','[v]',
         '-frames:v',str(scene['frames']),'-an','-c:v','libx264','-profile:v','high','-crf','18',
         '-preset','fast','-pix_fmt','yuv420p','-threads','3',str(ROOT/'edit'/f"{scene['id']}.mp4")])
    print('Rendered',scene['id'],flush=True)


def normalize(source, target, level=-14, peak=-1.5):
    filt=f'loudnorm=I={level}:TP={peak}:LRA=11'
    measured=run(['ffmpeg','-hide_banner','-i',str(source),'-af',filt+':print_format=json','-f','null','-'],capture_output=True,text=True).stderr
    m=json.loads(re.search(r'\{\s*"input_i".*?\}',measured,re.S).group(0))
    filt+=f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    run(['ffmpeg','-v','error','-y','-i',str(source),'-af',filt,'-ar','48000',str(target)])
    return m


def finish(story):
    duration=sum(s['duration'] for s in story)
    listing=ROOT/'edit/concat.txt'
    listing.write_text(''.join(f"file '{ROOT/'edit'/s['id']}.mp4'\n" for s in story))
    normalize(ROOT/'audio/narration-raw.wav',ROOT/'audio/narration.wav')
    normalize(ROOT/'audio/music.wav',ROOT/'audio/music-quiet.wav',-29,-10)
    graph=f'[0:a]asplit=2[voice][key];[1:a]atrim=duration={duration},afade=t=in:d=1.5,afade=t=out:st={duration-3}:d=3[bed];[bed][key]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=500[duck];[voice][duck]amix=inputs=2:normalize=0[mix]'
    run(['ffmpeg','-v','error','-y','-i',str(ROOT/'audio/narration.wav'),'-i',str(ROOT/'audio/music-quiet.wav'),
         '-filter_complex',graph,'-map','[mix]','-ar','48000',str(ROOT/'audio/mix.wav')])
    metrics=normalize(ROOT/'audio/mix.wav',ROOT/'audio/final.wav')
    (ROOT/'review/mix-normalization.json').write_text(json.dumps(metrics,indent=2)+'\n')
    final=OUT/'turku3d-pipeline-v2.mp4'
    run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(ROOT/'audio/final.wav'),
         '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-shortest',str(final)])
    run(['ffmpeg','-v','error','-y','-i',str(final),'-vf','scale=1280:720','-c:v','libx264','-preset','slow',
         '-b:v','630k','-maxrate','690k','-bufsize','1260k','-threads','3','-c:a','aac','-b:a','64k',
         '-movflags','+faststart',str(OUT/'turku3d-pipeline-v2-preview-720p.mp4')])
    assert (OUT/'turku3d-pipeline-v2-preview-720p.mp4').stat().st_size < 25_000_000
    run(['ffmpeg','-v','error','-y','-ss','2','-i',str(ROOT/'shots/river_open.mp4'),'-frames:v','1',str(ROOT/'edit/thumbnail-frame.png')])
    im=Image.open(ROOT/'edit/thumbnail-frame.png').convert('RGBA');d=ImageDraw.Draw(im)
    d.rounded_rectangle((55,66,990,456),25,fill=(5,15,27,222))
    text(d,(90,96),'TURKU3D',52,TEAL,True)
    text(d,(85,186),'FROM REAL DATA',78,WHITE,True)
    text(d,(85,291),'TO A WORLD',90,WHITE,True)
    im.convert('RGB').save(OUT/'thumbnail-v2.png')
    print(final,flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['timeline','render','finish']);ap.add_argument('--scene');args=ap.parse_args()
    story=timeline()
    if args.mode=='render':
        for scene in story:
            if not args.scene or scene['id'] in args.scene.split(','):render_scene(scene)
        if not args.scene:finish(story)
    if args.mode=='finish':finish(story)

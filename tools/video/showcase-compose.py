"""Footage-led showcase edit: unique moving shots, small data overlays, ducked project music."""
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
    story = json.loads(Path('tools/video/showcase-story.json').read_text())
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
            cuts=[0]
            weak={'a','an','the','and','from','to','of','as','their','its','through','at','in','for','with','Auto'}
            for j in range(1,parts):
                target=len(display)*j/parts
                candidates=range(cuts[-1]+1,len(words)-(parts-j)+1)
                def score(k):
                    balance=abs(len(' '.join(words[:k]))-target)
                    penalty=28 if words[k-1] in weak else 0
                    penalty+=25 if words[k] in {'style','J','S'} else 0
                    return balance+penalty-(8 if words[k-1].endswith((',',';')) else 0)
                cuts.append(min(candidates,key=score))
            cuts.append(len(words))
            for j in range(parts):
                cue = ' '.join(words[cuts[j]:cuts[j+1]])
                # Align caption boundaries to the model's synthesized token durations.
                # Caption spelling can differ (e.g. "three point four" → "3.4").
                import difflib
                norm=lambda value:re.sub(r'[^a-z0-9]','',value.lower())
                source=[norm(t['text']) for t in row['tokens']]
                target=[norm(t) for t in words]
                anchors={}
                for match in difflib.SequenceMatcher(None,target,source,autojunk=False).get_matching_blocks():
                    for k in range(match.size):anchors[match.a+k]=row['tokens'][match.b+k]
                for lo,hi in zip([-1,*sorted(anchors)],[*sorted(anchors),len(words)]):
                    if hi-lo<=1:continue
                    left=anchors[lo]['end'] if lo>=0 else 0
                    right=anchors[hi]['start'] if hi<len(words) else b-a
                    for k in range(lo+1,hi):
                        anchors[k]={'start':left+(right-left)*(k-lo-1)/(hi-lo-1),
                                    'end':left+(right-left)*(k-lo)/(hi-lo-1)}
                start = a + anchors[cuts[j]]['start']
                end = min(b,a+anchors[cuts[j+1]-1]['end'])
                cues.append(f'{len(cues)+1}\n{stamp(start)} --> {stamp(end)}\n{textwrap.fill(cue,43)}\n')
            samples.append(data)
            if i < len(seq)-1:
                samples.append(np.zeros(round(24000 * max(0.1, (scene.get('minimum_seconds',0) - sum(r['seconds'] for r in seq) - 1.4) / max(1,len(seq)-1)))))
        samples.append(np.zeros(48000 if scene['id']=='credits' else 4800))
        data = np.concatenate(samples)
        # Equal shot lengths in integer frames, with no repeated shot or last-frame hold.
        frames = math.ceil(max(len(data)/800,scene.get('minimum_seconds',0)*FPS)/len(scene['shots']))*len(scene['shots'])
        data = np.pad(data, (0, frames*800-len(data)))
        duration = frames/FPS
        result.append({**scene, 'start':cursor, 'duration':duration, 'frames':frames})
        chunks.append(data)
        cursor += duration
    ids = [shot for scene in result for shot in scene['shots']]
    assert len(ids) == len(set(ids)), 'No shot may be reused'
    wavfile.write(ROOT/'audio/narration-raw.wav', 24000, np.concatenate(chunks).astype('float32'))
    (ROOT/'timeline.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    (OUT/'turku3d-showcase-v3.srt').write_text('\n'.join(cues))
    (OUT/'script-v3.md').write_text('# Turku3D — a city built from data\n\n'
        'Voice: local Kokoro bf_emma, speed 1.03. Music: original Harbour Glass, rendered by the game MusicSystem.\n'
        'All footage: own sandbox WebGPU client, 1920×1080 at 30 fps. Generator assets served through a sandbox relay from deployed OCI.\n\n'
        + ''.join(f"## {stamp(s['start']).split(',')[0]} — {s['title']}\n\n{s['text']}\n\n"
          f"Shots: {', '.join(s['shots'])}. Evidence: {', '.join(s['sources'])}.\n\n" for s in result))
    chapters = [(s['id'], s['title']) for s in result if s['id'] not in ('game','credits')]
    (OUT/'chapters-v3.txt').write_text('\n'.join(
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
        lines = {
            'generation':['ISOLATED OCI BUILD / SAME DEPLOYED GENERATOR', 'Shared stage/input caches · time-lapse · real elapsed time in inset'],
            'sources':['LIDAR + ORTHOPHOTO + WFS + OSM', 'Ground · water · roads · structures · trees · facade kit'],
            'stream':['GLB + COLLISION CELLS → TILE PRODUCER → FORWARDER → BROWSER', '4-core OCI Arm64 · three.js WebGPU · Rapier worker'],
            'numbers':['BULK RUN: 1,128 TILES / 36 MIN / 64 CORES', 'Cache snapshot: 30 October 2026 · coverage is incomplete', 'Flight-loop trace: frame p90 16.74 ms / worst 366.67 ms · captures excluded'],
            'rounds':['LOCAL MULTIPLAYER DEMONSTRATION · LAN TARGET: 10 PLAYERS', 'Round clock advanced for results · sunset uses developer time control'],
            'credits':['City data © City of Turku · CC BY 4.0', 'Map data © OpenStreetMap contributors · ODbL',
                       'Voice: local Kokoro bf_emma · Music: Turku3D / Harbour Glass'],
        }.get(scene['id'], [])
        if lines:
            top=850-len(lines)*16
            d.rounded_rectangle((55,top,1660,top+45+len(lines)*40),16,fill=(6,16,25,220))
            for i,line in enumerate(lines):text(d,(80,top+20+i*40),line,27,TEAL if i==0 else WHITE,i==0)

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
        start=scene.get('shot_trims',[0]*count)[i]
        duration-=start
        inputs += ['-ss',str(start),'-i',str(source)]
        frames=round(scene['shot_seconds'][i]*FPS) if scene.get('shot_seconds') else scene['frames']//count
        speed=1 if scene.get('shot_seconds') and duration>=frames/FPS else frames/FPS/duration
        filters.append(f'[{i}:v]setpts={speed}*PTS,fps=30,trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]')
    filters.append(''.join(f'[v{i}]' for i in range(count))+f'concat=n={count}:v=1:a=0,eq=contrast=1.08:saturation=1.10:brightness=-0.012:gamma=0.97[base]')
    for kind in ['title','credit','data']:
        inputs += ['-loop','1','-i',str(overlay(scene,kind))]
    panel_end=min(7.0,scene['duration']-0.5) if scene.get('asset')=='parameters' else scene['duration']-0.5
    filters += [f'[{count}:v]format=rgba,fade=t=out:st=3.6:d=0.45:alpha=1[title]',
                f'[base][title]overlay=0:0:shortest=1[a]',
                f'[a][{count+1}:v]overlay=0:0:shortest=1[b]',
                f'[b][{count+2}:v]overlay=0:0:shortest=1:enable=\'between(t,1.3,{panel_end})\'[v]']
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
    normalize(ROOT/'audio/music.wav',ROOT/'audio/music-quiet.wav',-29,-3)
    graph=f'[0:a]asplit=2[voice][key];[1:a]atrim=duration={duration},afade=t=in:d=1.5,afade=t=out:st={duration-3}:d=3[bed];[bed][key]sidechaincompress=threshold=0.12:ratio=3:attack=20:release=400[duck];[voice][duck]amix=inputs=2:normalize=0[mix]'
    run(['ffmpeg','-v','error','-y','-i',str(ROOT/'audio/narration.wav'),'-i',str(ROOT/'audio/music-quiet.wav'),
         '-filter_complex',graph,'-map','[mix]','-ar','48000',str(ROOT/'audio/mix.wav')])
    metrics=normalize(ROOT/'audio/mix.wav',ROOT/'audio/final.wav')
    (ROOT/'review/mix-normalization.json').write_text(json.dumps(metrics,indent=2)+'\n')
    final=OUT/'turku3d-showcase-v3.mp4'
    run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(ROOT/'audio/final.wav'),
         '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-shortest',str(final)])
    run(['ffmpeg','-v','error','-y','-i',str(final),'-vf','scale=1280:720','-c:v','libx264','-preset','fast',
         '-b:v','1100k','-maxrate','1200k','-bufsize','2200k','-threads','3','-c:a','aac','-b:a','64k',
         '-movflags','+faststart',str(OUT/'turku3d-showcase-v3-preview-720p.mp4')])
    assert (OUT/'turku3d-showcase-v3-preview-720p.mp4').stat().st_size < 25_000_000
    thumbnail()
    print(final,flush=True)


def thumbnail():
    run(['ffmpeg','-v','error','-y','-ss','6.2','-i',str(ROOT/'shots/closing_pullback.mp4'),'-frames:v','1',str(ROOT/'edit/thumbnail-frame.png')])
    im=Image.open(ROOT/'edit/thumbnail-frame.png').convert('RGBA');d=ImageDraw.Draw(im)
    d.rounded_rectangle((55,66,990,456),25,fill=(5,15,27,222))
    text(d,(90,96),'TURKU3D',52,TEAL,True)
    text(d,(85,186),'THE CITY BUILDS',78,WHITE,True)
    text(d,(85,291),'AS YOU MOVE',90,WHITE,True)
    d.rectangle((0,1028,W,H),fill=(6,16,25,220))
    text(d,(50,1042),'Game frame · © City of Turku · CC BY 4.0 · © OpenStreetMap contributors · ODbL',23)
    im.convert('RGB').save(OUT/'thumbnail-v3.png')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['timeline','render','finish','thumbnail']);ap.add_argument('--scene');args=ap.parse_args()
    story=timeline()
    if args.mode=='render':
        for scene in story:
            if not args.scene or scene['id'] in args.scene.split(','):render_scene(scene)
        if not args.scene:finish(story)
    if args.mode=='finish':finish(story)
    if args.mode=='thumbnail':thumbnail()

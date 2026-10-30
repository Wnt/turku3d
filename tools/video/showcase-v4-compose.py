"""V4 edit: archived v3 highlights, newly captured source comparisons, local narration.

All media is local. The fixed edit list never modifies a previous master.
"""
import importlib.util
import difflib
import re
import json
import math
import os
from pathlib import Path
import subprocess
import textwrap
import numpy as np
from scipy.io import wavfile
from PIL import Image, ImageDraw

def module(name, file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
base=module('showcase','showcase-compose.py')
source=module('source_edit','compose.py')
ROOT,OUT,FPS=base.ROOT,base.OUT,30
STORY=Path(__file__).with_name('showcase-v4-story.json')

def timeline():
    story=json.loads(STORY.read_text());index=json.loads((ROOT/'audio/index.json').read_text())
    chunks=[];cues=[];cursor=0
    for scene in story:
        duration=scene['minimum_seconds'];rows=[r for r in index if r['scene']==scene['id']]
        speech=sum(r['seconds'] for r in rows)
        assert speech+0.25<=duration, f"{scene['id']}: voice {speech:.2f}s exceeds {duration}s"
        gap=min(.65,max(.12,(duration-speech-.4)/max(1,len(rows)-1)))
        samples=[np.zeros(2400)];local=.1
        for i,row in enumerate(rows):
            rate,data=wavfile.read(row['path']);assert rate==24000
            if data.dtype.kind=='i':data=data.astype(float)/np.iinfo(data.dtype).max
            words=row['sentence'].replace('Turku three D','Turku3D').replace('Web G P U','WebGPU').replace('Three dot J S','three.js').split()
            # Match displayed words to Kokoro token timings, interpolating spelling contractions.
            norm=lambda v:re.sub(r'[^a-z0-9]','',v.lower())
            tokens=row['tokens'];anchors={}
            for match in difflib.SequenceMatcher(None,[norm(w) for w in words],[norm(t['text']) for t in tokens],autojunk=False).get_matching_blocks():
                for k in range(match.size):anchors[match.a+k]=tokens[match.b+k]
            for lo,hi in zip([-1,*sorted(anchors)],[*sorted(anchors),len(words)]):
                if hi-lo<=1:continue
                left=anchors[lo]['end'] if lo>=0 else 0
                right=anchors[hi]['start'] if hi<len(words) else row['seconds']
                for k in range(lo+1,hi):anchors[k]={'start':left+(right-left)*(k-lo-1)/(hi-lo-1),'end':left+(right-left)*(k-lo)/(hi-lo-1)}
            count=math.ceil(len(' '.join(words))/76)
            cuts=[0]
            numeric={'one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','fifteen','twenty','thirty','sixty','hundred','thousand','and'}
            weak={'a','the','of','in','to','from','with','and','window'}
            for k in range(1,count):
                target=len(' '.join(words))*k/count
                def penalty(n):
                    prev=norm(words[n-1]);next_=norm(words[n])
                    return abs(len(' '.join(words[:n]))-target)+(100 if prev in numeric and next_ in numeric else 0)+(30 if prev in weak or next_=='rhythm' else 0)
                cuts.append(min(range(cuts[-1]+1,len(words)-(count-k)+1),key=penalty))
            cuts.append(len(words))
            for lo,hi in zip(cuts,cuts[1:]):
                a=cursor+local+anchors[lo]['start'];b=cursor+local+min(row['seconds'],anchors[hi-1]['end'])
                cues.append(f"{len(cues)+1}\n{base.stamp(a)} --> {base.stamp(b)}\n{textwrap.fill(' '.join(words[lo:hi]),43)}\n")
            samples.append(data);local+=len(data)/rate
            if i<len(rows)-1:samples.append(np.zeros(round(gap*rate)));local+=gap
        samples=np.concatenate(samples);frames=round(duration*FPS)
        assert len(samples)<=frames*800
        chunks.append(np.pad(samples,(0,frames*800-len(samples))))
        scene.update(start=cursor,duration=duration,frames=frames);cursor+=duration
    wavfile.write(ROOT/'audio/narration-raw.wav',24000,np.concatenate(chunks).astype('float32'))
    (ROOT/'timeline.json').write_text(json.dumps(story,indent=2)+'\n')
    (OUT/'turku3d-showcase-v4.srt').write_text('\n'.join(cues))
    (OUT/'script-v4.md').write_text('# Turku3D showcase v4 — measurements become a game\n\nLocal Kokoro bf_emma; original game Harbour Glass music, ducked. v3 gameplay and generation excerpts retain their capture provenance. Pipeline game shots are refreshed against deployed assets.\n\n'+''.join(f"## {base.stamp(s['start'])} — {s['title']}\n\n{s['text']}\n\nSources: {', '.join(s['sources'])}.\n\n" for s in story))
    chapters=[(0,'A city that builds as you move'),(24,'Basic first, textured next'),(40,'Measure the city'),(60,'Roofs, maps and generated facades'),(95,'Measured progress and credits')]
    (OUT/'chapters-v4.txt').write_text('\n'.join(f'{t//60:02}:{t%60:02} {name}' for t,name in chapters)+'\n')
    print(f'Timeline {cursor:.2f}s',flush=True);return story

def extract(story):
    master=OUT/'turku3d-showcase-v3.mp4'
    for scene in story:
        if not scene.get('archive'):continue
        clips=scene['archive'];inputs=[];filters=[]
        for i,(a,b) in enumerate(clips):
            inputs+=['-ss',str(a),'-t',str(b-a),'-i',str(master)]
            filters.append(f'[{i}:v]setpts=PTS-STARTPTS[v{i}]')
        filters.append(''.join(f'[v{i}]' for i in range(len(clips)))+f'concat=n={len(clips)}:v=1:a=0[v]')
        base.run(['ffmpeg','-v','error','-y',*inputs,'-filter_complex',';'.join(filters),'-map','[v]','-an','-c:v','libx264','-crf','17','-preset','fast','-threads','3',str(ROOT/'shots'/f"archive_{scene['id']}.mp4")])
        print('Extracted',scene['id'],flush=True)

def overlay(scene,kind):
    if kind=='data' and scene.get('asset'):
        return source.overlay(scene,kind)
    if kind=='data' and scene['id']=='passes':
        im=Image.new('RGBA',(1920,1080));d=ImageDraw.Draw(im)
        d.rounded_rectangle((70,760,1420,1000),20,fill=(6,16,25,230))
        base.text(d,(105,785),'01  GEOMETRY + COLLISION',42,base.TEAL,True)
        base.text(d,(105,855),'02  TEXTURES + FACADE DETAIL',42,base.WHITE,True)
        base.text(d,(105,930),'Content hashes reuse unchanged stages',26)
        p=ROOT/'edit/passes-data.png';im.save(p);return p
    return base.overlay(scene,kind)

def render(story,selection=None):
    original=base.overlay;base.overlay=overlay
    # Dispatch normal overlay calls through the unmodified helper, avoiding recursion.
    global normal_overlay
    normal_overlay=original
    for scene in story:
        if selection and scene['id'] not in selection:continue
        if scene['id']=='ortho':
            render_matched(scene);continue
        if scene.get('archive'):
            # Baked v3 graphics/grade stay intact; do not stack new titles on them.
            src=ROOT/'shots'/f"archive_{scene['id']}.mp4"
            seconds=sum(b-a for a,b in scene['archive'])
            if scene.get('retime'):
                filters=[]
                for i,(start,end,length) in enumerate(scene['retime']):
                    filters.append(f'[0:v]trim=start={start}:end={end},setpts={length/(end-start)}*(PTS-STARTPTS),fps=30[v{i}]')
                filters.append(''.join(f'[v{i}]' for i in range(len(scene['retime'])))+f"concat=n={len(scene['retime'])}:v=1:a=0[v]")
                temp=ROOT/'edit/generation-retimed.mp4'
                base.run(['ffmpeg','-v','error','-y','-i',str(src),'-filter_complex',';'.join(filters),'-map','[v]','-frames:v',str(scene['frames']),'-an','-c:v','libx264','-crf','18','-preset','fast','-threads','3',str(temp)])
                src=temp;seconds=scene['duration']
            base.run(['ffmpeg','-v','error','-y','-i',str(src),'-vf',f"setpts={scene['duration']/seconds}*PTS,fps=30",'-frames:v',str(scene['frames']),'-an','-c:v','libx264','-crf','18','-preset','fast','-threads','3',str(ROOT/'edit'/f"{scene['id']}.mp4")])
        else:
            base.render_scene(scene)
            if scene['id']=='lidar':
                target=ROOT/'edit/lidar.mp4';temp=ROOT/'edit/lidar-animated.mp4'
                base.run(['ffmpeg','-v','error','-y','-i',str(target),'-i',str(ROOT/'assets/lidar-orbit.mp4'),'-filter_complex','[0:v][1:v]overlay=55:235:shortest=1[v]','-map','[v]','-an','-c:v','libx264','-crf','18','-preset','fast','-threads','3',str(temp)])
                temp.replace(target)
        if scene['id'] in ('generation','numbers','rounds'):
            clarify_archive(scene)
        print('Rendered',scene['id'],flush=True)
    base.overlay=original

# Keep original overlay reachable while the shared renderer calls our dispatcher.
normal_overlay=base.overlay
_overlay_impl=overlay
def overlay(scene,kind):
    if kind=='data' and (scene.get('asset') or scene['id']=='passes'):return _overlay_impl(scene,kind)
    return normal_overlay(scene,kind)

def render_matched(scene):
    # Both panels use the exact captured perspective; the source is projected at H=0.
    src=ROOT/'shots/ortho_top.mp4';mapped=ROOT/'assets/ortho-camera.mp4'
    header=Image.new('RGBA',(1920,1080));d=ImageDraw.Draw(header)
    d.rectangle((0,0,1920,1080),fill='#0c1725')
    base.text(d,(55,65),'THE SAME CAMERA. THE SAME OFFICE BLOCK.',48,base.WHITE,True)
    base.text(d,(55,155),'2022 TRUE ORTHOPHOTO',30,base.TEAL,True)
    base.text(d,(1000,155),'CURRENT GAME',30,base.TEAL,True)
    base.text(d,(55,800),'Aerial source projected onto H=0 · terrain and roof heights differ in game',27)
    base.text(d,(55,870),'City imagery helps classify ground and colour roofs; street surfaces use material maps.',27)
    base.text(d,(55,1040),'© City of Turku · CC BY 4.0   |   © OpenStreetMap contributors · ODbL',23)
    p=ROOT/'edit/matched.png';header.save(p)
    base.run(['ffmpeg','-v','error','-y','-framerate','30','-loop','1','-i',str(p),'-i',str(mapped),'-i',str(src),'-filter_complex',
      '[1:v]scale=900:506[a];[2:v]scale=900:506[b];[0:v][a]overlay=30:245:shortest=1[c];[c][b]overlay=990:245:shortest=1[v]',
      '-map','[v]','-frames:v',str(scene['frames']),'-an','-c:v','libx264','-crf','18','-preset','fast','-threads','3',str(ROOT/'edit/ortho.mp4')])

def clarify_archive(scene):
    im=Image.new('RGBA',(1920,1080));d=ImageDraw.Draw(im)
    if scene['id']=='generation':
        d.rounded_rectangle((55,818,1660,943),16,fill=(6,16,25,255))
        base.text(d,(80,838),'RECORDED OCI BUILD · 30 OCT 2026 · SHARED CACHES',27,base.TEAL,True)
        base.text(d,(80,878),'Isolated output · time-lapse · actual elapsed time in inset',27)
    elif scene['id']=='rounds':
        d.rounded_rectangle((55,818,1660,943),16,fill=(6,16,25,255))
        base.text(d,(80,838),'LOCAL MULTIPLAYER · THREE INDEPENDENT CLIENTS',27,base.TEAL,True)
        base.text(d,(80,878),'LAN party target: 10 players',27)
    else:
        d.rounded_rectangle((55,800,1660,1000),16,fill=(6,16,25,255))
        base.text(d,(80,820),'BULK RUN: 1,128 TILES / 36 MIN / 64 CORES',30,base.TEAL,True)
        base.text(d,(80,866),'Snapshot: 30 Oct 2026, 11:30 UTC · backdrop: Samppalinna outdoor pool',27)
        base.text(d,(80,910),'Recorded frame trace: p90 16.74 ms / worst 366.67 ms',27)
        base.text(d,(80,952),'Capture frames excluded · fixed-step video is not an FPS benchmark',24)
    panel=ROOT/'edit'/f"{scene['id']}-disclosure.png";im.save(panel)
    target=ROOT/'edit'/f"{scene['id']}.mp4";temp=ROOT/'edit'/f"{scene['id']}-disclosed.mp4"
    base.run(['ffmpeg','-v','error','-y','-i',str(target),'-loop','1','-i',str(panel),'-filter_complex','[0:v][1:v]overlay=0:0:shortest=1[v]','-map','[v]','-an','-c:v','libx264','-crf','18','-preset','fast','-threads','3',str(temp)])
    temp.replace(target)

def finish(story):
    for scene in story:
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=r_frame_rate,avg_frame_rate,time_base,nb_frames','-of','json',str(ROOT/'edit'/f"{scene['id']}.mp4")]))['streams'][0]
        assert info['r_frame_rate']==info['avg_frame_rate']=='30/1', f"{scene['id']}: non-30fps input"
        assert info['time_base']=='1/15360' and int(info['nb_frames'])==scene['frames'], f"{scene['id']}: incompatible concat timestamps"
    def thumbnail():
        frame=ROOT/'edit/thumbnail-v4-frame.png'
        base.run(['ffmpeg','-v','error','-y','-ss','2','-i',str(ROOT/'shots/cathedral.mp4'),'-frames:v','1',str(frame)])
        im=Image.open(frame).convert('RGBA');d=ImageDraw.Draw(im)
        d.rounded_rectangle((45,430,755,990),24,fill=(5,15,27,235))
        base.text(d,(85,465),'TURKU3D',50,base.TEAL,True)
        for y,line in [(560,'THE CITY BUILDS'),(675,'AS YOU MOVE')]:base.text(d,(80,y),line,70,base.WHITE,True)
        base.text(d,(85,840),'REAL DATA → PLAYABLE WORLD',30,base.TEAL,True)
        d.rectangle((0,1028,1920,1080),fill=(6,16,25,245))
        base.text(d,(50,1042),'Game frame · © City of Turku · CC BY 4.0 · © OpenStreetMap contributors · ODbL',23)
        im.convert('RGB').save(OUT/'thumbnail-v4.png')
    base.finish(story, edition='v4', thumbnail_callback=thumbnail)

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['timeline','extract','render','finish']);ap.add_argument('--scene');a=ap.parse_args()
    story=timeline()
    if a.mode=='extract':extract(story)
    if a.mode=='render':render(story,a.scene.split(',') if a.scene else None)
    if a.mode=='finish':finish(story)

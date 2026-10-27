"""Code-generated 1080p edit. Streams frames to ffmpeg; no frame dump or stock imagery."""
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

W,H,FPS=1920,1080,30
ROOT=Path(os.environ['TURKU3D_WORK'])/'video'
OUT=Path.home()/'turku3d/media/pipeline-video'
BG='#0b1421'; PANEL='#142438'; WHITE='#eff5f7'; MUTED='#a4b8ca'; TEAL='#65e0c5'; GOLD='#f4ba70'
FONTS=Path('/System/Library/Fonts/Supplemental')

def font(size,bold=False,mono=False):
    return ImageFont.truetype(str(FONTS/('Andale Mono.ttf' if mono else 'Arial Bold.ttf' if bold else 'Arial.ttf')),size)

def txt(draw,xy,text,size=40,fill=WHITE,bold=False,mono=False):
    draw.text(xy,text,font=font(size,bold,mono),fill=fill)

def para(draw,xy,text,size=36,width=42,fill=MUTED):
    for i,line in enumerate(textwrap.wrap(text,width)):
        txt(draw,(xy[0],xy[1]+i*int(size*1.4)),line,size,fill)

def panel(draw,box):
    draw.rounded_rectangle(box,24,fill=PANEL,outline='#27415a',width=2)

def arrow(draw,x,y,length=100,color=TEAL):
    draw.line((x,y,x+length,y),fill=color,width=5)
    draw.polygon([(x+length,y),(x+length-18,y-10),(x+length-18,y+10)],fill=color)

assets={}
def asset(name):
    if name not in assets:
        assets[name]=Image.open(ROOT/'assets'/f'{name}.png').convert('RGB')
    return assets[name]

def fit(im,box,source,zoom=1,cover=False):
    x,y,w,h=box
    scale=(max if cover else min)(w/source.width,h/source.height)*zoom
    resized=source.resize((round(source.width*scale),round(source.height*scale)),Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(w,h),BG)
    if cover:
        ox=(resized.width-w)//2;oy=(resized.height-h)//2
        resized=resized.crop((ox,oy,ox+w,oy+h))
    canvas.paste(resized,((w-resized.width)//2,(h-resized.height)//2))
    im.paste(canvas,(x,y))

def base(scene,u,bg=None):
    im=bg.copy() if bg else Image.new('RGB',(W,H),BG)
    if bg:
        shade=Image.new('RGBA',(W,H));d=ImageDraw.Draw(shade)
        for y in range(270):d.line((0,y,W,y),fill=(5,13,23,round(210*(1-y/270))))
        for y in range(900,H):d.line((0,y,W,y),fill=(5,13,23,round(210*(y-900)/180)))
        im=Image.alpha_composite(im.convert('RGBA'),shade).convert('RGB')
    d=ImageDraw.Draw(im)
    txt(d,(80,54),scene['label'],26,TEAL,True)
    title_size=min(64,int(1740/max(1,len(scene['title']))*1.8))
    txt(d,(76,103),scene['title'],title_size,WHITE,True)
    d.line((80,199,1840,199),fill='#355269',width=2)
    credit='© City of Turku · CC BY 4.0    |    © OpenStreetMap contributors · ODbL'
    txt(d,(80,1009),credit,24,WHITE if bg else MUTED)
    txt(d,(1690,1009),'TURKU3D',24,TEAL,True)
    d.rectangle((0,1074,int(W*u),1079),fill=TEAL)
    return im

def diagram(scene,u,bg=None):
    im=base(scene,u,None if scene['kind']=='compare' or scene['id']=='draft' else bg);d=ImageDraw.Draw(im);kind=scene['kind']
    if kind=='game':
        if scene['id']=='hook':
            panel(d,(80,790,750,947));txt(d,(110,820),'Real data → playable world',38,WHITE,True)
            txt(d,(110,879),'Captured in Chrome / WebGPU',28,TEAL)
        elif scene['id']=='draft':
            fit(im,(80,270,1270,640),bg,1.08,cover=True);d=ImageDraw.Draw(im)
            panel(d,(1380,270,1840,910))
            txt(d,(1420,315),'LOCAL DRAFT',34,GOLD,True)
            para(d,(1420,405),'250 m tile made from cached inputs',31,19,WHITE)
            para(d,(1420,585),'Coarse relief + orthophoto + extruded buildings',29,19,MUTED)
            txt(d,(80,932),'Isolated tile capture / neighbouring tiles not loaded',25,MUTED)
        return im
    if kind=='source':
        name=scene['asset']
        if name in ('lidar','lod2'):
            source=asset(name)
            crop=(400,300,source.width-360,source.height-250) if name=='lidar' else (310,145,source.width-310,source.height-145)
            fit(im,(80,230,1760,730),source.crop(crop),1+.025*u)
            d=ImageDraw.Draw(im)
            if name=='lidar':
                txt(d,(120,880),'Measured point cloud / height-coloured returns',30,TEAL)
                if u>.5:txt(d,(120,925),'Ground, roof and canopy separate in 3D',26,WHITE)
            else:
                txt(d,(120,880),'City LOD2 / source roof and wall surfaces',30,TEAL)
                if u>.5:txt(d,(120,925),'Roofs (ochre) · other surfaces (blue)',26,WHITE)
        elif name=='register':
            fit(im,(80,250,820,690),asset('ortho'));d=ImageDraw.Draw(im)
            d.ellipse((475,598,515,638),outline=GOLD,width=6)
            txt(d,(380,660),'OFFICE BLOCK',27,GOLD,True)
            panel(d,(950,250,1840,940));data=json.loads((ROOT/'assets/register.json').read_text())
            rows=[('Address',data['Osoitenimi_fi']+' '+data['Osoitenumero']),('Permanent building ID',data['PysyvaRakennusTunnus']),('Completion (register)',data['Valmistunut']),('Storeys (register)',data['Kerrosluku'])]
            for i,(k,v) in enumerate(rows):txt(d,(990,290+i*154),k,26,TEAL);txt(d,(990,335+i*154),v,39,WHITE,True)
        elif name=='parameters':
            panel(d,(80,250,880,937));p=json.loads((ROOT/'assets/parameters.json').read_text());desc=p['description']
            txt(d,(118,280),'ACTUAL DESCRIPTION / EXCERPT',25,TEAL,True)
            lines=['{',f'  "material": "{desc["material"]}",',f'  "wall_colour": "{desc["wall_colour"]}",',f'  "ground_floor": "{desc["ground_floor"]}"', '}']
            for i,line in enumerate(lines):txt(d,(120,355+i*58),line,31,WHITE,mono=True)
            txt(d,(118,836),'Läntinen Rantakatu 15',30,GOLD,True)
            txt(d,(118,890),'Layout fitted to wall dimensions',25,MUTED)
            fit(im,(980,300,850,540),asset('facade'),1);d=ImageDraw.Draw(im)
            txt(d,(1010,864),'PROCEDURAL ALBEDO',30,TEAL,True)
            txt(d,(1010,910),'Rendered from JSON / no photo pixels',27,MUTED)
            arrow(d,900,565,55)
        elif name=='relief':
            fit(im,(80,230,1760,580),asset(name),1.01)
            d=ImageDraw.Draw(im);panel(d,(80,827,1840,954))
            d.line((135,918,310,918,310,872,490,872),fill=TEAL,width=7)
            txt(d,(545,850),'CURB / SCHEMATIC STEP',26,TEAL,True)
            txt(d,(545,893),'Cross-section candidates still need mapped edges and validation.',27,WHITE)
        elif name not in ('lidar','lod2'):
            fit(im,(80,230,1760,610 if name=='osm' else 730),asset(name),1+.025*u);d=ImageDraw.Draw(im)
            if name=='osm':
                for i,(c,t) in enumerate([(WHITE,'Streets & paths'),(GOLD,'Barriers'),(TEAL,'Trees & nature')]):
                    d.rectangle((180+i*520,897,208+i*520,925),fill=c);txt(d,(225+i*520,890),t,30,c)
            if name=='lidar':txt(d,(120,880),'Office block / real city point cloud',30,TEAL)
            if name=='lod2':txt(d,(120,880),'Source LOD2 surfaces / office block',30,TEAL)
        return im
    if kind=='compare':
        fit(im,(80,270,850,650),asset('ortho'),cover=True)
        if bg:fit(im,(990,270,850,650),bg,1,cover=True)
        d=ImageDraw.Draw(im);txt(d,(80,225),'2022 TRUE ORTHOPHOTO',28,TEAL,True);txt(d,(990,225),'GAME / SAME OFFICE BLOCK',28,GOLD,True)
        for x,y in [(513,623),(1416,595)]:
            d.ellipse((x-24,y-24,x+24,y+24),outline=GOLD,width=6)
            d.ellipse((x-5,y-5,x+5,y+5),fill=GOLD)
        txt(d,(430,665),'OFFICE',25,GOLD,True)
        txt(d,(1350,637),'OFFICE',25,GOLD,True)
        arrow(d,938,600,40);txt(d,(80,934),'Läntinen Rantakatu 15 / source and render at the same location',26,MUTED)
        return im
    if kind=='stack':
        for x,title,lines,color in [(80,'DETAILED CORE',['Measured ground + structures','Reconstructed building shells','Reviewed facade maps'],TEAL),(1000,'DRAFT WORLD',['Fast cached source inputs','Simplified geometry','Shared procedural facade kit'],GOLD)]:
            panel(d,(x,280,x+840,780));txt(d,(x+45,325),title,38,color,True)
            for i,t in enumerate(lines):txt(d,(x+45,430+i*95),t,34)
        txt(d,(250,870),'three.js r186    /    WebGPU    /    Rapier 0.21 worker',40,WHITE,True)
    elif kind=='facade_loop':
        for i,(title,sub) in enumerate([('GENERATE','Image model → wall map'),('REVIEW','Independent visual check'),('BIND','Approved map → wall')]):
            if u < i*.26:continue
            x=80+i*610;panel(d,(x,330,x+540,650));txt(d,(x+38,370),f'0{i+1}',31,TEAL)
            txt(d,(x+38,435),title,42,WHITE,True);txt(d,(x+38,550),sub,28,MUTED)
            if i<2 and u >= (i+1)*.26:arrow(d,x+550,490,48)
        if u >= .78:
            d.line((960,675,960,780,350,780,350,675),fill=GOLD,width=5)
            txt(d,(460,800),'Failed check → feedback → retry',35,GOLD,True)
            txt(d,(80,910),'Building style + derived walls extend the treatment',30,MUTED)
    elif kind=='placement':
        fit(im,(80,270,830,610),asset('osm'),1.02+.02*u,cover=True);d=ImageDraw.Draw(im)
        txt(d,(100,900),'MAP FEATURES / REAL LOCAL EXTRACT',25,TEAL,True)
        panel(d,(990,270,1840,890))
        txt(d,(1040,315),'PLACEMENT RULES',35,GOLD,True)
        for i,(head,body) in enumerate([('STREET OBJECTS','Check surface and clearance'),('PARKED VEHICLES','Use valid road-side positions'),('TREES','Instance meshes; reduce distant detail')]):
            y=400+i*150
            d.ellipse((1043,y+5,1070,y+32),fill=TEAL)
            txt(d,(1100,y),head,30,WHITE,True)
            txt(d,(1100,y+46),body,25,MUTED)
        txt(d,(990,915),'Placement schematic / source geometry at left',27,MUTED)
    elif kind=='features':
        for i,(name,sub,color) in enumerate([('BRIDGE','Deck + clearance',TEAL),('BARRIER','Line follows terrain',GOLD),('FOREST','Exclude roads and water',TEAL)]):
            x=80+i*610;panel(d,(x,300,x+540,850))
            txt(d,(x+40,345),name,37,color,True)
            if i==0:
                d.rectangle((x+60,560,x+480,590),fill=color)
                d.rectangle((x+70,590,x+120,760),fill='#6d8594');d.rectangle((x+420,590,x+470,760),fill='#6d8594')
                d.line((x+145,690,x+395,690),fill='#527f91',width=7)
                d.line((x+290,600,x+290,680),fill=GOLD,width=5)
                d.polygon([(x+290,595),(x+277,612),(x+303,612)],fill=GOLD)
                d.polygon([(x+290,685),(x+277,668),(x+303,668)],fill=GOLD)
                txt(d,(x+300,620),'clearance',25,GOLD)
            elif i==1:
                d.line((x+60,730,x+250,710,x+480,760),fill='#678778',width=15)
                for bx,by in [(120,723),(230,714),(340,731),(450,753)]:
                    d.line((x+bx,by,x+bx,by-150),fill=color,width=9)
                d.line((x+120,573,x+230,564,x+340,581,x+450,603),fill=color,width=7)
            else:
                for tx,ty in [(120,650),(250,580),(390,675)]:
                    d.rectangle((x+tx-8,ty,x+tx+8,ty+70),fill='#785b46')
                    d.ellipse((x+tx-60,ty-100,x+tx+60,ty+45),fill='#547b62')
                d.line((x+60,755,x+480,755),fill=GOLD,width=14)
            txt(d,(x+40,790),sub,25,MUTED)
        txt(d,(80,925),'Procedural examples / shapes are schematic',27,MUTED)
    elif kind=='package':
        layers=['GROUND','STRUCTURES','BUILDINGS','DETAILS']
        for i,t in enumerate(layers):
            if u>=i*.16:
                panel(d,(80,270+i*148,650,389+i*148));txt(d,(115,307+i*148),t,34,WHITE,True)
        if u>=.65:arrow(d,690,575,170)
        panel(d,(930,280,1840,885));txt(d,(985,325),'250 m',110,TEAL,True);txt(d,(985,465),'visual tile footprint',35)
        if u>=.55:
            txt(d,(985,570),'8 MB',92,GOLD,True);txt(d,(985,685),'maximum visual wire size',35)
        if u>=.8:txt(d,(985,790),'GLB + KTX2  /  collision cells',31,MUTED)
    elif kind=='prefetch':
        # Scaled schematic, not a live map or a timed build demonstration.
        cx,cy=690,675;scale=.32;radius=500*scale
        for x in range(80,1250,80):d.line((x,250,x,950),fill='#20344b',width=1)
        for y in range(250,951,80):d.line((80,y,1250,y),fill='#20344b',width=1)
        reach=(500+1000*u)*scale
        d.pieslice((cx-reach,cy-reach,cx+reach,cy+reach),250,290,fill='#365d57',outline=TEAL,width=3)
        d.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill='#24415a',outline=GOLD,width=4)
        d.polygon([(cx,cy-23),(cx-17,cy+20),(cx+17,cy+20)],fill=WHITE)
        txt(d,(cx+radius+15,cy+15),'500 m ring',30,GOLD,True)
        panel(d,(1310,290,1840,900));txt(d,(1345,340),'LOOK AHEAD',35,TEAL,True)
        txt(d,(1345,450),'40° cone',47,WHITE,True);para(d,(1345,555),'500 m on foot',32,22)
        para(d,(1345,655),'Up to 1.5 km at 27 m/s',36,20,TEAL)
        txt(d,(80,935),'Illustration of the request policy / distances from the producer',25,MUTED)
    elif kind=='timings':
        panel(d,(80,290,850,900));txt(d,(125,340),'WESTBOUND SERVICE RUN',32,TEAL,True);txt(d,(125,435),'3.4 s',118,WHITE,True)
        txt(d,(125,600),'mean service build time',36,MUTED);txt(d,(125,745),'54 / 54 built after the first km',30)
        panel(d,(930,290,1840,900));txt(d,(975,340),'SOUTHEAST SERVICE RUN',32,GOLD,True);txt(d,(975,445),'Downloads fell behind',41,WHITE,True)
        para(d,(975,555),'24 of 55 tiles built during the drive after the first kilometre.',36,34)
        para(d,(975,750),'Recorded at 27 m/s under heavy host load. Not a guarantee.',30,45,GOLD)
        txt(d,(80,942),'Source: pipeline/turku3d/coarse/README.md · measured 2026-10-27',25,MUTED)
    elif kind=='swap':
        for i,(title,color) in enumerate([('RESIDENT WORLD',TEAL),('LOAD REPLACEMENT',GOLD),('SWAP WHEN READY',TEAL)]):
            x=80+i*610;panel(d,(x,350,x+540,740));txt(d,(x+30,395),title,29,color,True)
            for k in range(4):
                height=[90,130,70,110][k];d.rectangle((x+45+k*115,650-height,x+135+k*115,650),fill=color if i!=1 or u>k/5 else '#365065')
            if i<2:arrow(d,x+550,545,48)
        txt(d,(220,845),'Current geometry stays visible while the next version loads',38,WHITE,True)
        txt(d,(80,935),'State diagram / not a real-time loading recording',27,MUTED)
    elif kind=='tools':
        labels=['SANDBOX','FAST GATE','LAND','BUILD + PUBLISH','ADVISORY CHECKS']
        for i,label in enumerate(labels):
            if u<i*.17:continue
            x=80+i*355;panel(d,(x,370,x+320,680));txt(d,(x+30,410),f'0{i+1}',30,TEAL)
            for j,line in enumerate(textwrap.wrap(label,14)):txt(d,(x+30,493+j*45),line,30,WHITE,True)
            if i<4 and u>=(i+1)*.17:arrow(d,x+325,540,25)
        if u>=.72:txt(d,(160,790),'Isolated changes → quick feedback → asynchronous integration',38)
        if u>=.9:txt(d,(160,860),'Deeper checks report failures after publication',32,GOLD)
    elif kind=='credits':
        txt(d,(80,280),'CITY DATA',28,TEAL,True);txt(d,(80,335),'© City of Turku · CC BY 4.0',48,WHITE,True)
        txt(d,(80,450),'MAP DATA',28,TEAL,True);txt(d,(80,505),'© OpenStreetMap contributors · ODbL',48,WHITE,True)
        txt(d,(80,635),'LOCAL PRODUCTION',28,TEAL,True)
        txt(d,(80,690),'Kokoro narration · Python graphics · FFmpeg edit',39)
        txt(d,(80,805),'Game footage: isolated Chrome / WebGPU capture',34,MUTED)
        txt(d,(80,865),'Street-level photos remain local reference only.',34,GOLD)
    return im

def run(cmd,**kwargs):
    return subprocess.run(cmd,check=True,**kwargs)

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def timeline():
    story=json.loads(Path('tools/video/story.json').read_text());index=json.loads((ROOT/'audio/index.json').read_text())
    OUT.mkdir(parents=True,exist_ok=True);(ROOT/'edit').mkdir(exist_ok=True)
    chunks=[];srt=[];cursor=0;shotlist=[]
    for scene in story:
        seq=[r for r in index if r['scene']==scene['id']];samples=[np.zeros(7200)]
        for i,row in enumerate(seq):
            rate,data=wavfile.read(row['path']);assert rate==24000
            data=data.astype('float64')/2147483648 if data.dtype==np.int32 else data.astype('float64')
            start=cursor+sum(len(c) for c in samples)/24000
            samples.append(data);end=start+len(data)/24000
            display=row['sentence']
            for spoken,caption in [('Turku three D','Turku3D'),('Three dot J S','three.js'),
                ('Web G P U','WebGPU'),('twenty twenty two','2022'),
                ('three point four seconds','3.4 seconds'),('one and a half kilometres','1.5 kilometres'),
                ('two hundred and fifty metres','250 metres'),
                ('Creative Commons Attribution four point zero','CC BY 4.0')]:
                display=display.replace(spoken,caption)
            words=display.split()
            n_parts=max(1,math.ceil(len(display)/78))
            if n_parts==1:parts=[display]
            else:
                # Balance cue lengths instead of leaving a two-word orphan at the end.
                parts=[];remaining=words
                for part_i in range(n_parts):
                    if part_i==n_parts-1:parts.append(' '.join(remaining));break
                    target=len(' '.join(remaining))/(n_parts-part_i)
                    chosen=1
                    while chosen<len(remaining)-1 and len(' '.join(remaining[:chosen+1]))<=target:chosen+=1
                    parts.append(' '.join(remaining[:chosen]));remaining=remaining[chosen:]
            used=0
            for part_i,part in enumerate(parts):
                n=len(part.split());a=start+(end-start)*used/len(words);b=start+(end-start)*(used+n)/len(words);used+=n
                # Whisper alignment showed the long vegetation phrase spoken later than a word-count split.
                if display.startswith('Instancing reuses meshes') and len(parts)==2:
                    if part_i==0:b=min(end,b+1.16)
                    else:a=min(end,a+1.16)
                if display.startswith('The goal is a Grand Theft') and len(parts)==2:
                    if part_i==0:b=max(start,b-1.15)
                    else:a=max(start,a-.45)
                srt.append(f"{len(srt)+1}\n{stamp(a)} --> {stamp(b)}\n{textwrap.fill(part,width=43)}\n")
            if i<len(seq)-1:samples.append(np.zeros(4320))
        samples.append(np.zeros(7200));data=np.concatenate(samples)
        frames=math.ceil(len(data)/24000*FPS);target=frames*800
        data=np.pad(data,(0,target-len(data)))
        duration=frames/FPS;scene={**scene,'start':cursor,'duration':duration,'frames':frames}
        chunks.append(data);shotlist.append(scene);cursor+=duration
    wavfile.write(ROOT/'audio/narration-raw.wav',24000,np.concatenate(chunks).astype('float32'))
    (OUT/'turku3d-pipeline.srt').write_text('\n'.join(srt))
    (ROOT/'timeline.json').write_text(json.dumps(shotlist,indent=2,ensure_ascii=False))
    (OUT/'script.md').write_text('# Turku3D — from data to a playable city\n\n'+''.join(f"## {stamp(s['start']).split(',')[0]} — {s['title']}\n\n{s['text']}\n\nEvidence: {', '.join(s['sources'])}\n\n" for s in shotlist))
    chapters=[('hook','From data to a playable city'),('lidar','The source data'),('ground','Building the detailed blocks'),('draft','On-the-fly generation'),('tools','The development loop'),('credits','Sources and credits')]
    (OUT/'chapters.txt').write_text('\n'.join(f"{int(next(s['start'] for s in shotlist if s['id']==id))//60:02}:{int(next(s['start'] for s in shotlist if s['id']==id))%60:02} {title}" for id,title in chapters)+'\n')
    print(f'Timeline: {cursor:.2f} s',flush=True)
    return shotlist

def render_scene(scene):
    target=ROOT/'edit'/f"{scene['id']}.mp4"
    encoder=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{W}x{H}','-framerate',str(FPS),'-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','18','-profile:v','high','-pix_fmt','yuv420p','-threads','3',str(target)],stdin=subprocess.PIPE)
    decoder=None
    if scene.get('shot'):
        path=ROOT/'shots'/f"{scene['shot']}.mp4"
        duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(path)]))
        decoder=subprocess.Popen(['ffmpeg','-v','error','-i',str(path),'-vf',f"setpts={scene['duration']/duration}*PTS,fps=30,tpad=stop_mode=clone:stop_duration=1",'-f','rawvideo','-pix_fmt','rgb24','-threads','2','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    first=None
    try:
        for f in range(scene['frames']):
            bg=None
            if decoder:
                raw=decoder.stdout.read(W*H*3)
                if len(raw)!=W*H*3:raise RuntimeError('Short decoded frame')
                bg=Image.frombytes('RGB',(W,H),raw)
            # Comparison layout uses the footage as a panel, with an opaque card background.
            if scene['kind']=='compare':
                frame=diagram(scene,f/scene['frames'],bg)
            else:frame=diagram(scene,f/scene['frames'],bg)
            if first is None:
                first=frame;frame.save(ROOT/'edit'/f"{scene['id']}-card.jpg",quality=92)
            encoder.stdin.write(frame.tobytes())
    finally:
        if decoder:decoder.stdout.close();decoder.terminate();decoder.wait()
        encoder.stdin.close()
    if encoder.wait():raise RuntimeError('Encoder failed')
    print('Rendered',scene['id'],flush=True)

def finish(story):
    listing=ROOT/'edit/concat.txt';listing.write_text(''.join(f"file '{ROOT/'edit'/s['id']}.mp4'\n" for s in story))
    raw=ROOT/'audio/narration-raw.wav'
    measure=subprocess.run(['ffmpeg','-hide_banner','-i',str(raw),'-af','loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json','-f','null','-'],capture_output=True,text=True,check=True).stderr
    match=re.search(r'\{\s*"input_i".*?\}',measure,re.S)
    if not match:raise RuntimeError('FFmpeg loudnorm JSON missing')
    m=json.loads(match.group(0));(ROOT/'review/loudnorm-measure.json').write_text(json.dumps(m,indent=2))
    filt=f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=summary"
    run(['ffmpeg','-v','error','-y','-i',str(raw),'-af',filt,'-ar','48000',str(ROOT/'audio/narration.wav')])
    run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(ROOT/'audio/narration.wav'),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-shortest',str(OUT/'turku3d-pipeline.mp4')])
    im=Image.open(ROOT/'shots/aerial-probe.png').convert('RGB');overlay=Image.new('RGBA',(W,H),(4,13,25,90));im=Image.alpha_composite(im.convert('RGBA'),overlay)
    d=ImageDraw.Draw(im);d.rectangle((0,0,900,H),fill=(5,15,27,220));txt(d,(90,100),'TURKU3D',64,TEAL,True)
    txt(d,(85,300),'BUILDING',112,WHITE,True);txt(d,(85,430),'A CITY',112,WHITE,True);txt(d,(90,635),'FROM REAL DATA',49,GOLD,True)
    txt(d,(90,940),'In your browser',42,WHITE);im.convert('RGB').save(OUT/'thumbnail.png')
    print('Final',OUT/'turku3d-pipeline.mp4',flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['timeline','cards','render','finish']);ap.add_argument('--scene');a=ap.parse_args()
    story=timeline()
    if a.mode=='cards':
        for s in story:
            bg=Image.open(ROOT/'shots'/f"{s['shot']}-probe.png").convert('RGB') if s.get('shot') else None
            diagram(s,.5,bg).save(ROOT/'edit'/f"{s['id']}-card.jpg",quality=92)
    if a.mode=='render':
        for s in story:
            if not a.scene or s['id'] in a.scene.split(','):render_scene(s)
        if not a.scene:finish(story)
    if a.mode=='finish':finish(story)

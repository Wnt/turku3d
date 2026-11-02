#!/usr/bin/env python3
"""Compose matched 60 fps halves with vector-like typography and frame-exact transitions."""
import argparse,json,subprocess
from pathlib import Path
import math,wave
from datetime import datetime
import numpy as np
from PIL import Image,ImageDraw,ImageFont
P=argparse.ArgumentParser();P.add_argument('--out',required=True);P.add_argument('--shots',default=str(Path(__file__).with_name('comparison-shots.json')));P.add_argument('--preview',action='store_true');P.add_argument('--still',metavar='SHOT_ID|title|end',help='Compose one full-resolution cached midpoint or card without rendering video');a=P.parse_args()
out=Path(a.out);c=json.loads(Path(a.shots).read_text());edit=out/'edit';edit.mkdir(exist_ok=True);fps=c['fps'];transition=c['transition'];W,H=1920,1080
stamp=datetime.now();date_long=stamp.strftime('%d %B %Y')
font='/System/Library/Fonts/Supplemental/Arial.ttf';bold='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
def run(args):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)],check=True)
def encode():return ['-an','-c:v','libx264','-crf','14','-preset','fast','-threads','3','-pix_fmt','yuv420p','-r',fps,'-video_track_timescale',15360]
def text(d,xy,s,size=30,fill='#f4f5f4',heavy=False):d.text(xy,s,font=ImageFont.truetype(bold if heavy else font,size),fill=fill)
def overlay(shot):
 im=Image.new('RGBA',(W,H));d=ImageDraw.Draw(im)
 d.rectangle((958,0,961,H),fill=(235,241,239,225))
 hour=f"{int(shot['hour']):02d}:{round(shot['hour']%1*60):02d}"
 for x,label,weather in [(36,'PRODUCTION','Clear'),(996,'NEW BUILD',shot['weather'].capitalize())]:
  d.rounded_rectangle((x,32,x+310,137),12,fill=(9,20,27,205))
  text(d,(x+19,44),label,30,heavy=True)
  text(d,(x+19,88),f'{weather}  ·  {hour}',24,fill='#d5e1df')
 # A gradual scrim preserves road/actor detail instead of covering the whole foreground.
 for y in range(940,H):d.line((0,y,W,y),fill=(7,17,24,round(205*((y-940)/(H-940))**1.4)))
 d.rectangle((40,1001,46,1045),fill='#87cebd')
 text(d,(65,993),shot['title'],40,heavy=True)
 number=next(i+1 for i,s in enumerate(c['shots']) if s['id']==shot['id'])
 text(d,(W-380,1007),f'{number:02d} / {len(c["shots"]):02d}   ·   MATCHED CAMERA',21,fill='#d5e1df')
 p=edit/(shot['id']+'-overlay.png');im.save(p);return p
def card(end=False,still=False):
 im=Image.new('RGBA',(W,H));d=ImageDraw.Draw(im)
 if end:
  d.rectangle((100,302,106,742),fill='#87cebd')
  text(d,(144,300),'THE CITY KEEPS EVOLVING',34,fill='#87cebd',heavy=True)
  text(d,(137,370),'Same city. A new level of detail.',78,heavy=True)
  text(d,(145,510),'15 matched journeys · frame-exact capture · 60 fps',34)
  text(d,(145,575),'Local comparison · '+date_long,28,fill='#bdcfce')
  text(d,(145,680),'City of Turku · CC BY 4.0    /    © OpenStreetMap contributors · ODbL',24,fill='#bdcfce')
  text(d,(145,725),'Weather shown on the new build; production uses its available clear setting.',23,fill='#bdcfce')
 else:
  # Preserve both captured halves: introduce the city and comparison at the actual framing.
  im=Image.new('RGBA',(W,H),(7,17,24,135));d=ImageDraw.Draw(im)
  d.line((960,0,960,H),fill=(212,232,225,100),width=2)
  text(d,(96,64),'TURKU3D  /  GEN 2',30,fill='#a1dfcc',heavy=True)
  text(d,(96,200),'PRODUCTION',30,fill='#d5e1df',heavy=True)
  text(d,(1020,200),'NEW BUILD',30,fill='#d5e1df',heavy=True)
  text(d,(90,402),'Turku.\nBefore & after.',100,heavy=True)
  d.rectangle((96,660,150,665),fill='#a1dfcc')
  text(d,(96,708),'15 places. One shared camera.',34)
 p=edit/('end.png' if end else 'title.png');im.save(p)
 dest=edit/(('end' if end else 'title')+('-still.png' if still else '.mp4'));duration=c['endSeconds'] if end else c['titleSeconds']
 frame_args=['-frames:v',1] if still else ['-frames:v',round(duration*fps),*encode()]
 if end:
  run(['-loop','1','-framerate',fps,'-i',out/'clips'/'aura-dawn-right-sample.jpg','-loop','1','-framerate',fps,'-i',p,'-filter_complex','[0:v]scale=1920:2160,crop=1920:1080,gblur=sigma=24,eq=brightness=-0.24:saturation=0.55[bg];[bg][1:v]overlay,format=yuv420p[v]','-map','[v]',*frame_args,dest])
 else:
  run(['-loop','1','-framerate',fps,'-i',out/'clips'/'aura-dawn-left-sample.jpg','-loop','1','-framerate',fps,'-i',out/'clips'/'aura-dawn-right-sample.jpg','-loop','1','-framerate',fps,'-i',p,'-filter_complex','[0:v][1:v]hstack,gblur=sigma=2,eq=saturation=0.9[bg];[bg][2:v]overlay,format=yuv420p[v]','-map','[v]',*frame_args,dest])
 return dest,duration
if a.still in ('title','end'):
 card(a.still=='end',still=True);raise SystemExit()
segments=[]
for shot in c['shots']:
 if a.still and shot['id']!=a.still:continue
 p=overlay(shot);dest=edit/(shot['id']+'.mp4');segments.append((dest,c['duration']))
 if a.still:
  run(['-i',out/'clips'/(shot['id']+'-left-sample.jpg'),'-i',out/'clips'/(shot['id']+'-right-sample.jpg'),'-i',p,'-filter_complex','[0:v][1:v]hstack,eq=contrast=1.015:saturation=1.025[scene];[scene][2:v]overlay=format=auto,format=yuv420p[v]','-map','[v]','-frames:v',1,edit/(shot['id']+'-still.png')]);continue
 if a.preview:
  left=out/'preview'/(shot['id']+'-left-1.jpg');right=out/'preview'/(shot['id']+'-right-1.jpg')
  run(['-i',left,'-i',right,'-i',p,'-filter_complex','[0:v]scale=960:1080[l];[1:v]scale=960:1080[r];[l][r]hstack[s];[s][2:v]overlay','-frames:v',1,edit/(shot['id']+'-preview.jpg')]);continue
 run(['-i',out/'clips'/(shot['id']+'-left.mp4'),'-i',out/'clips'/(shot['id']+'-right.mp4'),'-i',p,'-filter_complex','[0:v][1:v]hstack,eq=contrast=1.015:saturation=1.025[scene];[scene][2:v]overlay=format=auto,format=yuv420p[v]','-map','[v]','-t',c['duration'],*encode(),dest])
 print('Composed',shot['id'],flush=True)
if a.still:
 if not segments:raise SystemExit(f'Unknown shot: {a.still}')
 raise SystemExit()
if a.preview:
 thumbs=[]
 for shot in c['shots']:
  im=Image.open(edit/(shot['id']+'-preview.jpg'));im.thumbnail((640,360));thumbs.append(im)
 sheet=Image.new('RGB',(1920,360*math.ceil(len(thumbs)/3)))
 for i,im in enumerate(thumbs):sheet.paste(im,((i%3)*640,(i//3)*360))
 sheet.save(out/'preview-contact.jpg',quality=93);raise SystemExit()
segments=[card(),*segments,card(True)]
parts=[]
for i,(clip,duration) in enumerate(segments):
 start=transition if i else 0;end=duration-transition if i<len(segments)-1 else duration
 body=edit/f'body-{i:02}.mp4';run(['-ss',start,'-i',clip,'-t',end-start,*encode(),body]);parts.append(body)
 if i<len(segments)-1:
  nxt=segments[i+1][0];dest=edit/f'transition-{i:02}.mp4';kind=['fade','smoothleft','fade','dissolve'][i%4]
  run(['-ss',duration-transition,'-t',transition,'-i',clip,'-t',transition,'-i',nxt,'-filter_complex',f'[0:v]settb=1/{fps},setpts=PTS-STARTPTS[a];[1:v]settb=1/{fps},setpts=PTS-STARTPTS[b];[a][b]xfade=transition={kind}:duration={transition}:offset=0,format=yuv420p[v]','-map','[v]','-t',transition,*encode(),dest]);parts.append(dest)
listing=edit/'concat.txt';listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in parts))
# Original, locally synthesized ambient score: no recordings, downloads, or licensed samples.
duration=sum(d for _,d in segments)-(len(segments)-1)*transition;sr=48000;audio=np.zeros((round(duration*sr),2),dtype=np.float32)
for beat in range(math.ceil(duration*2)):
 t0=beat*.5;chord=[[45,52,57,60],[41,48,53,57],[48,55,60,64],[43,50,55,59]][(beat//8)%4]
 for j,n in enumerate(chord):
  if beat%2 and j!=2:continue
  length=min(2.8,duration-t0);t=np.arange(round(length*sr))/sr;freq=440*2**((n+12*(j==2)-69)/12)
  env=(1-np.exp(-t*6))*np.exp(-t*.9)*np.minimum(1,(length-t)*5)
  sound=(np.sin(2*np.pi*freq*t)+.18*np.sin(2*np.pi*freq*2*t))*.024*env
  first=round(t0*sr);last=min(len(audio),first+len(sound));audio[first:last,0]+=sound[:last-first]*(.8 if j%2 else 1);audio[first:last,1]+=sound[:last-first]*(1 if j%2 else .8)
fade=np.minimum(1,np.arange(len(audio))/sr/2)*np.minimum(1,(len(audio)-np.arange(len(audio)))/sr/3);audio*=fade[:,None]
wav=edit/'original-score.wav'
with wave.open(str(wav),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
run(['-f','concat','-safe',0,'-i',listing,'-i',wav,'-c:v','copy','-c:a','aac','-b:a','256k','-movflags','+faststart','-t',duration,out/'turku3d-before-after.mp4'])
print('Final',out/'turku3d-before-after.mp4',duration,'seconds',flush=True)

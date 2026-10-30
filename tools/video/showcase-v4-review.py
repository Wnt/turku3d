"""Review the exact delivered v4 cut and its retained capture/source provenance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from PIL import Image,ImageDraw,ImageFont
ap=argparse.ArgumentParser();ap.add_argument('--quick',action='store_true');a=ap.parse_args()
root=Path(os.environ['TURKU3D_WORK'])/'video';review=root/'review';review.mkdir(parents=True,exist_ok=True)
out=Path.home()/'turku3d/media/pipeline-video';evidence=out/'review-v4-evidence'
final=out/'turku3d-showcase-v4.mp4'
def read(name):
    path=root/name
    if not path.exists():path=evidence/name
    return json.loads(path.read_text())
def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
info=probe(final);video=next(s for s in info['streams'] if s['codec_type']=='video');sound=next(s for s in info['streams'] if s['codec_type']=='audio')
assert (video['width'],video['height'],video['r_frame_rate'],video['codec_name'],video['profile'])==(1920,1080,'30/1','h264','High')
assert (sound['codec_name'],sound['sample_rate'])==('aac','48000')
duration=float(info['format']['duration']);assert 110<=duration<=125
assert video['avg_frame_rate']=='30/1' and int(video['nb_frames'])==round(duration*30), 'Mixed frame-rate export'
story=read('timeline.json');assert abs(sum(s['duration'] for s in story)-duration)<1/30
pipeline=[s for s in story if s['id'] in ('lidar','relief','ortho','lod2','osm','parameters','passes')]
assert 40<=sum(s['duration'] for s in pipeline)<=50
preview=out/'turku3d-showcase-v4-preview-720p.mp4';pv=probe(preview);pvs=next(s for s in pv['streams'] if s['codec_type']=='video')
assert (pvs['width'],pvs['height'])==(1280,720)
assert preview.stat().st_size<25_000_000 and abs(float(pv['format']['duration'])-duration)<1/30
srt=(out/'turku3d-showcase-v4.srt').read_text()
def secs(t):
    h,m,s,ms=map(int,re.split('[:,]',t));return h*3600+m*60+s+ms/1000
cues=[tuple(map(secs,p)) for p in re.findall(r'(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)',srt)]
assert cues and all(0<=x<y<=duration for x,y in cues)
assert all(cues[i][1]<=cues[i+1][0]+.01 for i in range(len(cues)-1))
# Chapter starts are accurate timeline boundaries and leave at least ten seconds.
chapters=(out/'chapters-v4.txt').read_text().splitlines()
starts=[sum(int(v)*m for v,m in zip(line.split()[0].split(':'),[60,1])) for line in chapters]
assert starts[0]==0 and all(b-a>=10 for a,b in zip(starts,starts[1:]+[duration]))
assert all(any(abs(t-scene['start'])<1/30 for scene in story) for t in starts)
archives=[];captures=[]
for scene in story:
    if scene.get('archive'):
        archives.extend(scene['archive']);continue
    for shot in scene['shots']:
        meta=read(f'shots/{shot}.json');assert meta['info']['info']['backend']=='WebGPU' and not meta['errors']
        poses=[m['pose'] for m in meta['motion']]
        distance=sum(sum((b[k]-a[k])**2 for k in ('E','N','H'))**.5 for a,b in zip(poses,poses[1:]))
        assert distance>1
        captures.append({'id':shot,'camera_travel_m':distance})
archives.sort();assert all(archives[i][1]<=archives[i+1][0] for i in range(len(archives)-1)), 'Reused archive frames'
ortho=read('shots/ortho_top.json');assert len(ortho['motion'])>=180 and all('camera' in s for s in ortho['motion'][:180])
summary={'sha256':hashlib.file_digest(final.open('rb'),'sha256').hexdigest(),'duration':duration,'size_bytes':final.stat().st_size,'preview_size_bytes':preview.stat().st_size,'pipeline_seconds':sum(s['duration'] for s in pipeline),'subtitles':len(cues),'new_capture_shots':captures,'archive_ranges':archives}
(review/'technical-v4.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2),flush=True)
if a.quick:raise SystemExit(0)
# Contact sheets carry exact sample timestamps; no unlabelled blank padding is evidence.
frames=review/'frames';frames.mkdir(exist_ok=True)
subprocess.run(['ffmpeg','-v','error','-y','-i',str(final),'-vf','fps=1/2,scale=480:270',str(frames/'%03d.jpg')],check=True)
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',20)
images=sorted(frames.glob('*.jpg'))
for begin in range(0,len(images),20):
    sheet=Image.new('RGB',(1920,5*300),'#07101b');d=ImageDraw.Draw(sheet)
    for n,path in enumerate(images[begin:begin+20]):
        x=n%4*480;y=n//4*300;sheet.paste(Image.open(path),(x,y));t=(begin+n)*2
        d.text((x+10,y+274),f'{t//60:02}:{t%60:02}',font=font,fill='white')
    sheet.save(review/f'v4-contact-{begin//20+1:02}.jpg')
for name,args in [('ebur128',['-af','ebur128=peak=true']),('blackdetect',['-vf','blackdetect=d=0.05:pix_th=0.05','-an']),('freezedetect',['-vf','freezedetect=n=-50dB:d=3','-an']),('silencedetect',['-af','silencedetect=noise=-45dB:d=0.7','-vn'])]:
    result=subprocess.run(['ffmpeg','-hide_banner','-i',str(final),*args,'-f','null','-'],capture_output=True,text=True,check=True)
    (review/f'{name}-v4.txt').write_text(result.stderr)
    if name=='blackdetect':assert 'black_start:' not in result.stderr, 'Black segment in delivered cut'
    if name=='freezedetect':assert 'freeze_start:' not in result.stderr, 'Three-second freeze in delivered cut'
print('PASS: format, timeline, preview, captions, archive ranges, current WebGPU footage and matched camera provenance',flush=True)

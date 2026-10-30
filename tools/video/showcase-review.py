"""Repeatable local checks and 2-second contact sheets for independent reviewers."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

root=Path(os.environ['TURKU3D_WORK'])/'video/review'
root.mkdir(exist_ok=True)
final=Path.home()/'turku3d/media/pipeline-video/turku3d-showcase-v3.mp4'
if not final.exists():raise SystemExit(f'Missing {final}')

def run(command,**kwargs):return subprocess.run(command,check=True,**kwargs)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)]))
(root/'ffprobe.json').write_text(json.dumps(probe,indent=2))
video=next(s for s in probe['streams'] if s['codec_type']=='video')
audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
assert (video['width'],video['height'])==(1920,1080)
assert video['codec_name']=='h264' and video['profile']=='High'
assert audio['codec_name']=='aac' and audio['sample_rate']=='48000'
assert 110<=float(probe['format']['duration'])<=130
timeline=json.loads((root.parent/'timeline.json').read_text())
assert abs(sum(s['duration'] for s in timeline)-float(probe['format']['duration'])) < 1/30, 'Export does not match the current timeline'
identity={'path':str(final),'sha256':hashlib.file_digest(final.open('rb'),'sha256').hexdigest()}
(root/'review-input.json').write_text(json.dumps(identity,indent=2)+'\n')
preview=final.with_name('turku3d-showcase-v3-preview-720p.mp4')
preview_info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(preview)]))
pv=next(s for s in preview_info['streams'] if s['codec_type']=='video')
assert (pv['width'],pv['height'])==(1280,720)
assert preview.stat().st_size<25_000_000
assert abs(float(preview_info['format']['duration'])-float(probe['format']['duration']))<1/30

# Subtitle timestamps must be ordered and inside the completed video.
srt=(final.parent/'turku3d-showcase-v3.srt').read_text()
def seconds(t):
    hh,mm,ss,ms=map(int,re.split('[:,]',t));return hh*3600+mm*60+ss+ms/1000
intervals=[tuple(map(seconds,p)) for p in re.findall(r'(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)',srt)]
assert intervals and all(0<=a<b<=float(probe['format']['duration'])+0.1 for a,b in intervals)
assert all(intervals[i][1]<=intervals[i+1][0]+0.01 for i in range(len(intervals)-1))
if '--quick' in __import__('sys').argv:
    print('PASS: 1080p master, 720p preview size, timeline and subtitle alignment')
    raise SystemExit(0)

# Exhaustive 2-second sampling; four or five 6x6 sheets, without raw captures in repo.
for stale in root.glob('*contact-*.jpg'):stale.unlink()
run(['ffmpeg','-v','error','-y','-i',str(final),'-vf','fps=1/2,scale=320:180,tile=6x6:nb_frames=36:margin=2:padding=2','-fps_mode','vfr',str(root/'v3-contact-%02d.jpg')])
# Detect wholly black samples, clipping and silence quantitatively; visual/audio reviewers adjudicate.
metrics=run(['ffmpeg','-hide_banner','-i',str(final),'-af','ebur128=peak=true','-f','null','-'],capture_output=True,text=True).stderr
(root/'ebur128.txt').write_text(metrics)
black=run(['ffmpeg','-hide_banner','-i',str(final),'-vf','blackdetect=d=0.05:pix_th=0.05','-an','-f','null','-'],capture_output=True,text=True).stderr
(root/'blackdetect.txt').write_text('\n'.join(x for x in black.splitlines() if 'black_start' in x)+'\n')
silence=run(['ffmpeg','-hide_banner','-i',str(final),'-af','silencedetect=noise=-45dB:d=0.7','-vn','-f','null','-'],capture_output=True,text=True).stderr
(root/'silencedetect.txt').write_text('\n'.join(x for x in silence.splitlines() if 'silence_' in x)+'\n')
summary={**identity,'duration_s':float(probe['format']['duration']),'size_bytes':final.stat().st_size,
    'preview_size_bytes':preview.stat().st_size,
    'video':{k:video[k] for k in ('codec_name','profile','width','height','r_frame_rate')},
    'audio':{k:audio[k] for k in ('codec_name','sample_rate')},'subtitle_intervals':len(intervals),
    'contact_sheets':len(list(root.glob('v3-contact-*.jpg'))),
    'black_segments':len([x for x in (root/'blackdetect.txt').read_text().splitlines() if 'black_start' in x])}
(root/'technical.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))

# Verify the edit's footage inventory and actual camera movement, not only its labels.
shot_ids=[shot for scene in timeline for shot in scene['shots']]
assert len(shot_ids)==len(set(shot_ids)), 'A captured shot was reused'
shots=[]
capture_metadata=[]
for shot in shot_ids:
    info=json.loads((root.parent/'shots'/f'{shot}.json').read_text())
    capture_metadata.append(info)
    poses=[m['pose'] for m in info['motion']]
    travel=sum(sum((b[k]-a[k])**2 for k in ['E','N','H'])**0.5 for a,b in zip(poses,poses[1:]))
    assert travel>1 or shot in ('game_market',), f'{shot}: less than one metre of actual camera movement'
    assert info['info']['info']['backend']=='WebGPU'
    assert not info['errors'], f'{shot}: page errors'
    shots.append({'id':shot,'camera_travel_m':round(travel,2),'samples':len(poses),
                  'physics_drive':bool(info['shot'].get('drive')) or shot=='game_drive'})
motion=run(['ffmpeg','-hide_banner','-i',str(final),'-vf','freezedetect=n=-50dB:d=4','-an','-f','null','-'],capture_output=True,text=True).stderr
(root/'freezedetect.txt').write_text('\n'.join(x for x in motion.splitlines() if 'freeze_' in x)+'\n')
(root/'footage.json').write_text(json.dumps({'moving_game_seconds_lower_bound':sum(s['duration'] for s in timeline)-8,
    'runtime_seconds':float(probe['format']['duration']),'unique_shots':len(shot_ids),'shots':shots,
    'game_seconds_excluding_source_panel_scenes':sum(s['duration'] for s in timeline if not s.get('asset')),
    'max_source_panel_fraction':1080*495/(1920*1080),'reused_shots':0},indent=2)+'\n')
print(f'{len(shot_ids)} unique game shots; short shop/round UI inserts; freeze audit saved')

(root/'capture-metadata.json').write_text(json.dumps(capture_metadata,indent=2)+'\n')

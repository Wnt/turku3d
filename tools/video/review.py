"""Repeatable local checks and 2-second contact sheets for independent reviewers."""
import json
import os
import re
import subprocess
from pathlib import Path
from PIL import Image

root=Path(os.environ['TURKU3D_WORK'])/'video/review'
root.mkdir(exist_ok=True)
final=Path.home()/'turku3d/media/pipeline-video/turku3d-pipeline.mp4'
if not final.exists():raise SystemExit(f'Missing {final}')

def run(command,**kwargs):return subprocess.run(command,check=True,**kwargs)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)]))
(root/'ffprobe.json').write_text(json.dumps(probe,indent=2))
video=next(s for s in probe['streams'] if s['codec_type']=='video')
audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
assert (video['width'],video['height'])==(1920,1080)
assert video['codec_name']=='h264' and video['profile']=='High'
assert audio['codec_name']=='aac' and audio['sample_rate']=='48000'
assert 180<=float(probe['format']['duration'])<=300

# Exhaustive 2-second sampling; four or five 6x6 sheets, without raw captures in repo.
run(['ffmpeg','-v','error','-y','-i',str(final),'-vf','fps=1/2,scale=320:180,tile=6x6:nb_frames=36:margin=2:padding=2','-fps_mode','vfr',str(root/'contact-%02d.jpg')])
# Detect wholly black samples, clipping and silence quantitatively; visual/audio reviewers adjudicate.
metrics=run(['ffmpeg','-hide_banner','-i',str(final),'-af','ebur128=peak=true','-f','null','-'],capture_output=True,text=True).stderr
(root/'ebur128.txt').write_text(metrics)
black=run(['ffmpeg','-hide_banner','-i',str(final),'-vf','blackdetect=d=0.05:pix_th=0.05','-an','-f','null','-'],capture_output=True,text=True).stderr
(root/'blackdetect.txt').write_text('\n'.join(x for x in black.splitlines() if 'black_start' in x)+'\n')
silence=run(['ffmpeg','-hide_banner','-i',str(final),'-af','silencedetect=noise=-45dB:d=0.7','-vn','-f','null','-'],capture_output=True,text=True).stderr
(root/'silencedetect.txt').write_text('\n'.join(x for x in silence.splitlines() if 'silence_' in x)+'\n')
# Subtitle timestamps must be ordered and inside the completed video.
srt=(final.parent/'turku3d-pipeline.srt').read_text()
def seconds(t):
    hh,mm,ss,ms=map(int,re.split('[:,]',t));return hh*3600+mm*60+ss+ms/1000
intervals=[tuple(map(seconds,p)) for p in re.findall(r'(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)',srt)]
assert intervals and all(0<=a<b<=float(probe['format']['duration'])+0.1 for a,b in intervals)
assert all(intervals[i][1]<=intervals[i+1][0]+0.01 for i in range(len(intervals)-1))
summary={'duration_s':float(probe['format']['duration']),'size_bytes':final.stat().st_size,
    'video':{k:video[k] for k in ('codec_name','profile','width','height','r_frame_rate')},
    'audio':{k:audio[k] for k in ('codec_name','sample_rate')},'subtitle_intervals':len(intervals),
    'contact_sheets':len(list(root.glob('contact-*.jpg'))),
    'black_segments':len([x for x in (root/'blackdetect.txt').read_text().splitlines() if 'black_start' in x])}
(root/'technical.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))

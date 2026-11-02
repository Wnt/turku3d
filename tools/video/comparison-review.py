#!/usr/bin/env python3
import argparse,json,subprocess,math
from pathlib import Path
from PIL import Image,ImageDraw
p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out);video=out/'turku3d-before-after.mp4';review=out/'review';review.mkdir(exist_ok=True)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate,nb_read_frames,duration','-of','json',str(video)]))['streams'][0]
assert (probe['width'],probe['height'],probe['avg_frame_rate'])==(1920,1080,'60/1'),probe
assert 58<=float(probe['duration'])<=62,probe
assert int(probe['nb_read_frames'])==round(float(probe['duration'])*60),probe
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(video),'-vf','fps=1/2,scale=480:270',str(review/'frame-%03d.jpg')],check=True)
frames=sorted(review.glob('frame-*.jpg'));sheet=Image.new('RGB',(480*5,300*math.ceil(len(frames)/5)),(13,23,29));d=ImageDraw.Draw(sheet)
for i,f in enumerate(frames):
 x,y=i%5*480,i//5*300;sheet.paste(Image.open(f),(x,y));d.text((x+10,y+276),f'{i*2:02d} seconds',fill='white')
sheet.save(out/'contact-sheet.jpg',quality=92)
log=subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-vf','blackdetect=d=0.1:pix_th=0.05:pic_th=0.98','-an','-f','null','-'],capture_output=True,text=True,check=True).stderr
black=[line for line in log.splitlines() if 'black_start:' in line]
result={'video':str(video),'probe':probe,'black_intervals':black,'contact_sheet':str(out/'contact-sheet.jpg'),'manual_review_required':True}
(out/'review.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if black:raise SystemExit('Review black intervals before delivery')

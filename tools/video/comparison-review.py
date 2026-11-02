#!/usr/bin/env python3
import argparse,json,subprocess,math
from pathlib import Path
from PIL import Image,ImageDraw
p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--captures-only',action='store_true');a=p.parse_args();out=Path(a.out);video=out/'turku3d-before-after.mp4';review=out/'review';review.mkdir(exist_ok=True)
receipts=[]
npc_audit=[]
for left in sorted((out/'clips').glob('*-left.json')):
 l=json.loads(left.read_text());r=json.loads(Path(str(left).replace('-left.json','-right.json')).read_text())
 assert l['frames']==r['frames']==240,(left,'frame count')
 assert l['width']==r['width']==960 and l['height']==r['height']==1080
 assert l['shot']==r['shot'],(left,'mismatched path')
 right_frames={x['frame']:x for x in r['motion']}
 assert len(right_frames)==240,(left,'missing frame coverage')
 assert all(x['pose']==right_frames[x['frame']]['pose'] for x in l['motion']),(left,'mismatched camera')
 assert all(x['coverage']['ready'] and x['coverage']['scenery']['ready'] for x in r['motion']),(left,'incomplete world')
 assert all(x.get('population',{}).get('ready') for x in r['motion']),(left,'missing NPC readiness')
 assert all(abs(x['simSeconds']-(x['frame']+60)/r['fps'])<1e-9 for x in r['motion']),(left,'non-exact simulation clock')
 maxima={'traffic':0.,'people':0.}; moving={'traffic':set(),'people':set()}
 for a,b in zip(r['motion'],r['motion'][1:]):
  for kind,limit in [('traffic',.5),('people',.075)]:
   previous={v['id']:v for v in a['population'][kind]}
   for v in b['population'][kind]:
    if v['id'] not in previous:continue
    step=math.dist([v[k] for k in ['x','y','z']],[previous[v['id']][k] for k in ['x','y','z']])
    maxima[kind]=max(maxima[kind],step)
    assert step<=limit,(left,'actor teleport',kind,v['id'],b['frame'],step)
    if step>1e-5:moving[kind].add(v['id'])
 npc_audit.append({'shot':r['shot']['id'],'max_frame_step_m':maxima,'moving_actors':{k:len(v) for k,v in moving.items()}})
 assert all(not t['stale'] and t['pass']=='full' and t['revision']==r['sourceRevision'] for t in r['manifests']),(left,'stale new tiles')
 assert r['scenery']['progress']['ready'],(left,'incomplete far source')
 assert not r['errors'],(left,'page errors',r['errors'])
 receipts.append(left.name)
assert len(receipts)==15,receipts
(out/'npc-motion-audit.json').write_text(json.dumps(npc_audit,indent=2))
if a.captures_only:
 print(json.dumps({'paired_clips':len(receipts),'new_frames_with_complete_coverage':len(receipts)*240,'fresh_manifests':True}));raise SystemExit(0)
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

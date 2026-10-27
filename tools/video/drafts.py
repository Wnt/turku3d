"""Request two real draft builds from the isolated service and write a QA tileset."""
import json
import os
import time
import urllib.request
from pathlib import Path

work=Path(os.environ['TURKU3D_WORK']);root=work/'coarse'
port=int(os.environ['TURKU3D_PORT_BASE'])+5
ids=['t0_2_0','t0_1_-1']
for tile in ids:
    deadline=time.monotonic()+180
    while True:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/coarse/{tile}/manifest.json') as res:
                answer=json.load(res)
                if res.status==200:break
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'{tile}: {e.read().decode()}') from e
        if time.monotonic()>deadline:raise TimeoutError(f'{tile}: {answer}')
        time.sleep(2)
    print(tile,answer.get('seconds'),flush=True)
children=[]
for tile in ids:
    _,i,j=tile.split('_');i=int(i);j=int(j)
    children.append({'boundingVolume':{'box':[i*250+125,-j*250-125,40,125,0,0,0,125,0,0,0,60]},
        'geometricError':0,'content':{'uri':f'{tile}/vis.glb'},'extras':{'id':tile},'refine':'REPLACE'})
(root/'tileset.json').write_text(json.dumps({'asset':{'version':'1.0','gltfUpAxis':'y'},'geometricError':1024,
    'root':{'boundingVolume':{'box':[500,0,40,500,0,0,0,500,0,0,0,100]},
            'geometricError':512,'refine':'REPLACE','children':children}},indent=2)+'\n')
print(root/'tileset.json')

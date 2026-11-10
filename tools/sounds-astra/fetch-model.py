"""Download one pinned, public Apache-2.0 ACE-Step checkpoint without credentials."""
from pathlib import Path
import json,urllib.request,hashlib,concurrent.futures,time
work=Path.home()/'turku3d/loops/sounds-astra'
meta=json.loads((Path(__file__).parent/'model-source.json').read_text())
assert meta['cardData']['license']=='apache-2.0'
revision=meta['sha'];root=work/'work/ace-weights'
def fetch(row):
    name=row['rfilename']
    if name=='.gitattributes':return
    dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists() and dest.stat().st_size==row['size']:
        print('Present',name,flush=True);return
    url=f'https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B/resolve/{revision}/{name}'
    part=dest.with_suffix(dest.suffix+'.partial');h=hashlib.sha256();total=0;last=time.monotonic()
    with urllib.request.urlopen(url,timeout=90) as response,part.open('wb') as target:
        while chunk:=response.read(4*1024*1024):
            target.write(chunk);h.update(chunk);total+=len(chunk)
            if time.monotonic()-last>30:print(name,round(total/1e6),'MB',flush=True);last=time.monotonic()
    assert total==row['size'],(name,total,row['size'])
    if 'lfs' in row:assert h.hexdigest()==row['lfs']['sha256'],name
    part.replace(dest);print('Verified',name,round(total/1e6),'MB',flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(fetch,meta['siblings']))
print('Downloaded pinned model:',revision,flush=True)

"""Compare direct stage code identities across the latest ten OCI generator deployments."""
import base64
import json
from pathlib import Path
import shlex

from facade_publish import remote

HOME = '/home/ubuntu/turku3d-drafts'


def main():
    script = Path('tools/remote-drafts/stage-identities.py').read_bytes()
    path = '/tmp/arrival-warm-stage-identities.py'
    remote('python3 -c ' + shlex.quote('import base64,pathlib; pathlib.Path(' + repr(path)
           + ').write_bytes(base64.b64decode(' + repr(base64.b64encode(script).decode()) + '))'))
    driver = '''import hashlib,json,os,pathlib,subprocess
home=pathlib.Path(HOME)
roots=sorted((home/'sources').iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)
selected=[]
for root in roots:
 h=hashlib.sha256()
 for directory in ('pipeline','textures','tools/remote-drafts','game/public/basis'):
  for p in sorted((root/directory).rglob('*')):
   if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
    h.update(p.relative_to(root).as_posix().encode());h.update(b'\\0');h.update(p.read_bytes())
 if selected and h.hexdigest()==selected[-1][1]: continue
 selected.append((root,h.hexdigest()))
 if len(selected)==11: break
rows=[]
for root,digest in reversed(selected):
 env={**os.environ,'TURKU3D_DRAFT_SOURCE':str(root)}
 p=subprocess.run([str(root/'tools/remote-drafts/run.sh'),'python',SCRIPT],env=env,capture_output=True,text=True)
 if p.returncode: raise RuntimeError(root.name+': '+p.stderr[-3000:])
 after=subprocess.run([str(root/'tools/remote-drafts/run.sh'),'python',SCRIPT,'--narrow-samplers'],env=env,capture_output=True,text=True,check=True)
 rows.append(dict(commit=root.name,keys=json.loads(p.stdout),narrow_keys=json.loads(after.stdout)))
print(json.dumps(rows))
'''.replace('HOME', repr(HOME)).replace('SCRIPT', repr(path))
    import subprocess
    try:
        rows = json.loads(remote('python3 -c ' + shlex.quote(driver)))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr[-4000:]) from error
    changes = [dict(commit=b['commit'], invalidated=sorted(k for k, v in b['keys'].items()
               if a['keys'].get(k) != v), after=sorted(k for k,v in b['narrow_keys'].items() if a['narrow_keys'].get(k)!=v)) for a,b in zip(rows,rows[1:])]
    result = dict(definition='Latest content-distinct uploaded OCI generator sources; direct code keys, unchanged raw inputs. Upstream output changes may additionally invalidate downstream stages.',
                  snapshots=rows, changes=changes)
    import os
    output=Path(os.environ['TURKU3D_WORK'])/'reports/stage-invalidations.json'
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(changes,indent=2))


if __name__=='__main__':
    main()

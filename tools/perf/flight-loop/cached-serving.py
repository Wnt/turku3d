"""Measure deployed cached delivery under a concurrent flight corridor; never clear/build tiles."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/dev"))
from facade_publish import HOST, SSH

PROBE = r'''
import concurrent.futures,json,pathlib,time,urllib.request
home=pathlib.Path.home()/'turku3d-drafts'
freeze=json.loads((home/'LAN_FREEZE.json').read_text())
code=json.loads(pathlib.Path('/srv/turku3d/work/coarse/.scheduler.json').read_text())['code']
assert code==freeze['code'], (code,freeze['code'])
tiles=[f't0_0_{i}' for i in range(-7,2)]
def request(tile):
 start=time.monotonic()
 with urllib.request.urlopen('http://127.0.0.1:7764/coarse/'+tile+'/collision.json?client=flight-probe',timeout=5) as r:
  value=json.load(r)
 assert value.get('pass_kind')=='full' and len(value['collision'])==16, value
 assert value['generation']==code
 return dict(tile=tile,ms=round((time.monotonic()-start)*1000,3))
rounds=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
 for _ in range(3):
  rounds.append(list(pool.map(request,tiles)))
  time.sleep(1.1)
print(json.dumps(dict(code=code,rounds=rounds)),flush=True)
assert max(r['ms'] for r in rounds[0])<800, 'cold cached delivery exceeds one flight crossing'
assert max(r['ms'] for rs in rounds[1:] for r in rs)<200, 'warm cached delivery is blocked'
'''

result = subprocess.run([*SSH, HOST, "python3 -"], input=PROBE, text=True,
                        capture_output=True, timeout=25)
folder = Path(os.environ["TURKU3D_WORK"]) / "reports"
folder.mkdir(parents=True, exist_ok=True)
(folder / "cached-serving.json").write_text(result.stdout)
print(result.stdout, end="")
print(result.stderr, end="", file=sys.stderr)
raise SystemExit(result.returncode)

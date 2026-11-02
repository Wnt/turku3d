#!/usr/bin/env python3
"""Mac: pin a committed integration archive without replacing the staged sources/tools.

Never starts a tile build. The coordinator selects the final revision with this command.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/dev'))
from shared_tile_identity import generation
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('ref',nargs='?',default='local/loop-integration')
p.add_argument('--host',default='root@185.20.139.132')
p.add_argument('--root',default='/root/turku3d-bulk-gen2')
a=p.parse_args()
commit=subprocess.check_output(['git','rev-parse',a.ref+'^{commit}'],text=True).strip()
meta=dict(commit=commit,revision=commit,generation=generation(commit))
target=a.root+'/farm/revisions/'+commit
q=shlex.quote
# Pinning while a producer/prefetch process is alive could mix code revisions.
check='''import json,os,sys
from pathlib import Path
root=Path(sys.argv[1])
for p in (root/'state').glob('*.pid'):
 try:
  pid=int(p.read_text());cmd=Path(f'/proc/{pid}/cmdline').read_bytes()
 except (ValueError,OSError):continue
 if str(root).encode() in cmd and any(s in cmd for s in (b'prefetch.py',b'bulk.py')):
  raise SystemExit('Wait for active prefetch/build before pinning: '+p.name)
'''
subprocess.run(['ssh',a.host,'python3 -c '+q(check)+' '+q(a.root)],check=True)
subprocess.run(['ssh',a.host,'mkdir -p '+q(target+'/repo')],check=True)
archive=subprocess.Popen(['git','archive',commit],stdout=subprocess.PIPE)
result=subprocess.run(['ssh',a.host,'tar -x -C '+q(target+'/repo')],stdin=archive.stdout)
archive.stdout.close()
if archive.wait() or result.returncode:raise RuntimeError('archive transfer failed')
with tempfile.NamedTemporaryFile('w') as f:
 json.dump(meta,f);f.flush()
 subprocess.run(['scp','-q',f.name,a.host+':'+target+'/revision.json'],check=True)
activate='''import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]);commit=sys.argv[2]
repo=root/'repo'
if repo.is_dir() and not repo.is_symlink():
 initial=(root/'state/COMMIT').read_text().strip()
 retained=root/('initial-repo-'+initial)
 repo.rename(retained)
tmp=root/'.repo-next';tmp.unlink(missing_ok=True)
tmp.symlink_to(root/'farm/revisions'/commit/'repo');tmp.replace(repo)
(root/'state/revision.json').write_bytes((root/'farm/revisions'/commit/'revision.json').read_bytes())
(root/'state/COMMIT').write_text(commit+chr(10))
'''
subprocess.run(['ssh',a.host,'python3 -c '+q(activate)+' '+q(a.root)+' '+q(commit)],check=True)
print(json.dumps(meta))

"""Run isolated OCI warming evidence against frozen published source or a scheduler overlay."""
import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import signal
import time
import uuid

from facade_publish import HOST, SSH, remote
from deploy_drafts import HOME


def stop_owned(root):
    script = """import os,pathlib,signal
root=pathlib.Path(ROOT)
try:
 pid=int((root/'coarse/.probe.pid').read_text())
 argv=pathlib.Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0')
 if len(argv)>1 and argv[1].decode()==str(root/'probe-warm-cache.py'):
  os.kill(pid,signal.SIGTERM)
except (OSError,ValueError): pass
""".replace('ROOT', repr(root))
    try:
        subprocess.run([*SSH, HOST, 'python3 -c ' + shlex.quote(script)],
                       timeout=2, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit')
    parser.add_argument('--before', action='store_true')
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}' if args.commit else remote(f'readlink -f {HOME}/repo')
    root = '/srv/turku3d/work/arrival-warm-cache-' + uuid.uuid4().hex[:12]
    def interrupted(signum, _frame):
        stop_owned(root)
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    remote('mkdir -p ' + shlex.quote(root))
    for name in ['tools/remote-drafts/probe-warm-cache.py', *(['pipeline/turku3d/coarse/idle.py'] if args.before else [])]:
        data = base64.b64encode(Path(name).read_bytes()).decode()
        remote('python3 -c ' + shlex.quote('import base64,pathlib; pathlib.Path(' + repr(root+'/'+Path(name).name)
               + ').write_bytes(base64.b64decode(' + repr(data) + '))'))
    request = Path(os.environ['TURKU3D_WORK']).parent / 'request.json'
    remaining = 300
    if request.exists():
        remaining = min(300, int(json.loads(request.read_text())['created'] + 600 - time.time()) - 5)
    if remaining < 20:
        raise SystemExit('Insufficient remaining async budget for an OCI build probe')
    command = ['nice','-n','19','timeout','--kill-after=3',str(remaining), source+'/tools/remote-drafts/run.sh', 'python',
               root+'/probe-warm-cache.py', '--root',root+'/coarse', '--seconds', str(min(240, remaining-5))]
    if args.before:
        command += ['--overlay',root+'/idle.py']
    try:
        output = remote('TURKU3D_DRAFT_SOURCE='+shlex.quote(source)+' '+shlex.join(command))
    except subprocess.CalledProcessError as error:
        print(error.stdout)
        raise SystemExit(error.stderr[-5000:]) from error
    finally:
        stop_owned(root)
    report=Path(os.environ['TURKU3D_WORK'])/'reports'/('warm-before-merge.log' if args.before else 'warm-deployed.log')
    report.write_text(output+'\n')
    print(output)


if __name__=='__main__':
    main()

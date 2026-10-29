"""Run isolated OCI warming evidence against frozen published source or a scheduler overlay."""
import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from facade_publish import remote
from deploy_drafts import HOME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit')
    parser.add_argument('--before', action='store_true')
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}' if args.commit else remote(f'readlink -f {HOME}/repo')
    root = '/srv/turku3d/work/arrival-warm-cache-' + uuid.uuid4().hex[:12]
    remote('mkdir -p ' + shlex.quote(root))
    for name in ['tools/remote-drafts/probe-warm-cache.py', *(['pipeline/turku3d/coarse/idle.py'] if args.before else [])]:
        data = base64.b64encode(Path(name).read_bytes()).decode()
        remote('python3 -c ' + shlex.quote('import base64,pathlib; pathlib.Path(' + repr(root+'/'+Path(name).name)
               + ').write_bytes(base64.b64decode(' + repr(data) + '))'))
    command = ['timeout','--kill-after=3','300', source+'/tools/remote-drafts/run.sh', 'python',
               root+'/probe-warm-cache.py', '--root',root+'/coarse']
    if args.before:
        command += ['--overlay',root+'/idle.py']
    try:
        output = remote('TURKU3D_DRAFT_SOURCE='+shlex.quote(source)+' '+shlex.join(command))
    except subprocess.CalledProcessError as error:
        print(error.stdout)
        raise SystemExit(error.stderr[-5000:]) from error
    report=Path(os.environ['TURKU3D_WORK'])/'reports'/('warm-before-merge.log' if args.before else 'warm-deployed.log')
    report.write_text(output+'\n')
    print(output)


if __name__=='__main__':
    main()

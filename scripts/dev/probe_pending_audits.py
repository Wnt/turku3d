"""Retry retained immutable probe audits on deployed source, without rebuilding tiles."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--expect', type=int, required=True)
    args = parser.parse_args()
    root = Path(args.root)
    if root.parent != Path('/srv/turku3d/work') or not root.name.startswith('tile-arrival-parity-'):
        parser.error('--root must identify retained probe evidence, never the production cache')
    source = f'{HOME}/sources/{args.commit}'
    request = Path(os.environ['TURKU3D_WORK']).parent / 'request.json'
    remaining = 400
    if request.exists():
        remaining = min(remaining, int(json.loads(request.read_text())['created'] + 600 - time.time()) - 10)
    if remaining < 20:
        raise SystemExit('Insufficient remaining async budget for retained audits')
    script = f'''import json,pathlib
from turku3d.coarse import audit
root=pathlib.Path({str(root)!r})
assert (root/'.visual-audits').is_dir()
audit.drain(root/'.visual-audits')
results=[json.loads(p.read_text()) for p in (root/'.visual-audits').glob('*.result.json')]
report=dict(root=str(root),health=audit.health(root),results=results)
print(json.dumps(report),flush=True)
assert report['health']['pending']==0,report['health']
assert len(results)>={args.expect} and all(r['state']=='passed' for r in results),report
'''
    command = ['timeout', '--kill-after=3', str(remaining), source + '/tools/remote-drafts/run.sh',
               'python', '-c', script]
    try:
        output = remote('TURKU3D_DRAFT_SOURCE=' + shlex.quote(source) + ' ' + shlex.join(command))
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-5000:]) from error
    report = Path(os.environ['TURKU3D_WORK']) / 'reports/audit-recovery.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(output + '\n')
    print(output, flush=True)


if __name__ == '__main__':
    main()

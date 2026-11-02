#!/usr/bin/env python3
"""Run on the Mac. Read OCI at idle IO priority; direct rsync bytes to UpCloud.
Only the explicitly provisioned SSH key is needed on OCI. Logs stay on the Mac.
"""
import argparse
import base64
import concurrent.futures
import json
from pathlib import Path
import shlex
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--host', default='root@185.20.139.132')
p.add_argument('--root', default='/root/turku3d-bulk-gen2')
p.add_argument('--state', type=Path, required=True)
a = p.parse_args()
a.state.mkdir(parents=True, exist_ok=True)
# Carry the Mac's verified host key via KnownHostsCommand, without writing an OCI file.
hostname = a.host.split('@')[-1]
known = subprocess.check_output(['ssh-keygen', '-F', hostname], text=True)
known = '\n'.join(line for line in known.splitlines() if not line.startswith('#'))
if not known:
    raise RuntimeError('Connect to the host from this Mac first to verify its SSH host key')
ssh = ['ssh', '-i', str(Path.home()/'.ssh/oci_cuttlefish'), 'ubuntu@82.70.50.61']
jobs = {
    'raw': ('/srv/turku3d/data/raw/', 'data/raw/'),
    'textures': ('/srv/turku3d/data/textures/', 'data/textures/'),
    'facade-approvals': ('/srv/turku3d/data/facade-approvals/', 'data/facade-approvals/'),
    'facade-assets': ('/srv/turku3d/data/facade-assets/', 'data/facade-assets/'),
    'facade-characterization': ('/srv/turku3d/data/facade-characterization/', 'data/facade-characterization/'),
    'facade-params': ('/srv/turku3d/data/facade-params/', 'data/facade-params/'),
    'facade-rules-models': ('/srv/turku3d/work/facade-rules-models/', 'supplements/facade-rules-models/'),
}
subprocess.run(['ssh', a.host, 'mkdir -p ' + ' '.join(shlex.quote(a.root+'/'+d) for _, d in jobs.values())], check=True)
def copy(item):
    name, (source, destination) = item
    key_command = shlex.join(['/usr/bin/python3', '-c', 'import base64;print(base64.b64decode(' + repr(base64.b64encode(known.encode()).decode()) + ').decode())'])
    transport = shlex.join(['ssh', '-c', 'aes128-gcm@openssh.com', '-i', '/home/ubuntu/.ssh/bulk_upcloud', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'KnownHostsCommand='+key_command])
    command = 'exec nice -n 19 ionice -c 3 rsync -aL --partial --stats -e ' + shlex.quote(transport) + ' ' + shlex.quote(source) + ' ' + shlex.quote(a.host+':'+a.root+'/'+destination)
    started = time.time()
    with (a.state/(name+'.log')).open('a') as log:
        result = subprocess.run([*ssh, 'bash -c '+shlex.quote(command)], stdout=log, stderr=subprocess.STDOUT)
    row = dict(source=source, destination=destination, code=result.returncode, seconds=round(time.time()-started,1))
    (a.state/(name+'.json')).write_text(json.dumps(row)+'\n')
    print(json.dumps(row), flush=True)
    return result.returncode
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    codes = list(pool.map(copy, jobs.items()))
raise SystemExit(int(any(codes)))

"""Rebuild the reported NaN tile and matching service failures on published OCI source."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

SCAN = r"""
import datetime,json,re
from pathlib import Path
cutoff=datetime.datetime(2026,10,29,21,tzinfo=datetime.timezone.utc).timestamp()
recent=False
failures=[]
for line in Path('/home/ubuntu/turku3d-drafts/reports/service.log').open():
    if line.startswith('{"job":'):
        row=json.loads(line)
        recent=row.get('at',0)>=cutoff
    if recent and 'failure detail:' in line and 'cannot convert float NaN to integer' in line:
        failures.append(line.strip())
tiles=sorted(set(re.findall(r'coarse (t0_-?\d+_-?\d+)', '\n'.join(failures))))
print(json.dumps(dict(since_utc='2026-10-29T21:00:00Z',failures=failures,tiles=tiles)))
"""

AUDIT = r"""
import json
from pathlib import Path
import numpy as np
from turku3d.col.format import read_col
out=Path(OUTPUT)
manifest=json.loads((out/'manifest.json').read_text())
vertices=triangles=0
for entry in manifest['collision'].values():
    col=read_col((out/entry['url']).read_bytes())
    assert np.isfinite(col.positions).all()
    assert all(np.isfinite([*p.center,*p.rotation,*p.params]).all() for p in col.primitives)
    vertices+=len(col.positions)
    triangles+=len(col.indices)
assert vertices and triangles
print(json.dumps(dict(collision_tiles=len(manifest['collision']),vertices=vertices,
                      triangles=triangles,nonfinite_values=0)))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    scan = json.loads(remote("python3 -c " + shlex.quote(SCAN)))
    print(json.dumps(dict(service_scan=scan)), flush=True)
    source = f"{HOME}/sources/{args.commit}"
    for tile in sorted({"t0_-5_6", *scan["tiles"]}):
        output = subprocess.check_output(
            [sys.executable, "scripts/dev/probe_drafts.py", "--commit", args.commit,
             "--tile", tile, "--first-budget", "75", "--full-budget", "180"],
            text=True,
        )
        row = json.loads(output)
        print(json.dumps(dict(build=row)), flush=True)
        command = shlex.join([
            f"{source}/tools/remote-drafts/run.sh", "python", "-c",
            AUDIT.replace("OUTPUT", repr(row["output"])),
        ])
        audit = json.loads(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command))
        print(json.dumps(dict(tile=tile, collision_audit=audit)), flush=True)


if __name__ == "__main__":
    main()

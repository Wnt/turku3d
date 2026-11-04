#!/usr/bin/env python3
"""Verify the staged train4 inputs on turku3d-bulk3 (run on the bulk host).

* Source version and survey blocks: every file present is checked against the base source version's
  catalog (size + SHA-256, source-version.json). The set of present catalog files must equal the set
  present on osgallery-dev (presence.txt, listed there without hashing).
* All 1282 catalogued pistepilvi blocks must be present, in the version dir or in the supply dir.
* Products, env, revisions and metadata: sha256 lists made on osgallery-dev (sums-osg.txt) must
  match the same lists made here.

  verify_stage.py STAGE_DIR VERSION_DIR [SUPPLY_DIR]
  (STAGE_DIR holds presence.txt and sums-osg.txt; writes verify.json)
"""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LAZ = 'raw/laz/pistepilvi/'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def check(item):
    path, want = item
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return str(path), 'missing'
    if size != want['bytes']:
        return str(path), f'size {size} != {want["bytes"]}'
    got = sha256(path)
    return str(path), None if got == want['sha256'] else f'sha256 {got}'


def main():
    global VERSION, SUPPLY
    stage, VERSION = Path(sys.argv[1]), Path(sys.argv[2])
    SUPPLY = Path(sys.argv[3] if len(sys.argv) > 3 else '/bulk/turku3d/lidarpc/raw/pistepilvi')
    catalog = json.loads((VERSION / 'source-version.json').read_text())['files']
    osg_present = set(stage.joinpath('presence.txt').read_text().split('\n')) - {''}
    here_present = {p for p in catalog if (VERSION / p).is_file()}
    jobs = [(VERSION / p, catalog[p]) for p in sorted(here_present)]
    blocks = {Path(p).stem: v for p, v in catalog.items() if p.startswith(LAZ)}
    supply = sorted(SUPPLY.glob('*.laz'))
    unknown_supply = [s.name for s in supply if s.stem not in blocks]
    jobs += [(s, blocks[s.stem]) for s in supply if s.stem in blocks]
    with ThreadPoolExecutor(max_workers=os.cpu_count() * 2) as pool:
        bad = {p: e for p, e in pool.map(check, jobs) if e}
    covered = {b for b in blocks if (VERSION / LAZ / f'{b}.laz').is_file() or (SUPPLY / f'{b}.laz').is_file()}
    sums = subprocess.run(['sha256sum', '--quiet', '-c', str(stage / 'sums-osg.txt')], cwd='/',
                          capture_output=True, text=True)
    sums_n = sum(1 for _ in open(stage / 'sums-osg.txt'))
    result = dict(
        catalog_files=len(catalog),
        version_present_here=len(here_present), version_present_osg=len(osg_present),
        presence_equal=here_present == osg_present,
        only_here=sorted(here_present - osg_present)[:20], only_osg=sorted(osg_present - here_present)[:20],
        supply_blocks=len(supply), unknown_supply=unknown_supply,
        city_blocks=len(blocks), city_blocks_covered=len(covered),
        hashed_files=len(jobs), hashed_bytes=sum(v['bytes'] for _, v in jobs), hash_errors=bad,
        sums_files=sums_n, sums_ok=sums.returncode == 0, sums_failures=sums.stdout.splitlines()[:20],
    )
    result['ok'] = (result['presence_equal'] and not bad and not unknown_supply
                    and len(covered) == len(blocks) and result['sums_ok'])
    (stage / 'verify.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('only_here', 'only_osg')}, indent=1))


if __name__ == '__main__':
    main()

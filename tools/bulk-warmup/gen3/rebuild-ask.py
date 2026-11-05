#!/usr/bin/env python3
"""Bulk host: ask the host farm (127.0.0.1:7950) again for every spot rebuild the pass has parked and that is not
built yet (window-run.sh's watchdog runs it every 60 s). The farm builds each rebuild once (single flight), so a repeat
request is free; it keeps the rebuild workers busy after a farm restart instead of waiting for the pass's own retry.
  rebuild-ask.py FARM_ROOT GENERATION      prints how many were asked
"""
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

root, gen = Path(sys.argv[1]), sys.argv[2]
want = {}
for line in (root / 'migrations' / gen / 'pass.jsonl').read_text().splitlines():
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get('state') == 'missing-rebuild' and r.get('rebuild_revision'):
        want[(r['rebuild_revision'], r['tile'])] = r.get('rebuild_generation', '')
    elif r.get('state') in ('migrated', 'kept'):
        want = {k: v for k, v in want.items() if k[1] != r['tile']}
todo = [(rev, tile) for (rev, tile), g in want.items() if not (root / 'products' / g / tile / 'ready.json').exists()]


def ask(item):
    try:
        urlopen(f'http://127.0.0.1:7950/v/{item[0]}/coarse/{item[1]}/manifest.json', timeout=10).read()
    except OSError:
        pass


with ThreadPoolExecutor(8) as pool:
    list(pool.map(ask, todo))
print(len(todo))

#!/usr/bin/env python3
"""Before the city worker pool: verify proof texture encodes and warm every ground map."""
import json
from pathlib import Path
import sys
import time
from turku3d import profiling
from turku3d.coarse import surfaces
from turku3d.coarse.build import default_library
from shared_tiles import Farm, atomic_json

root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
started=time.monotonic();profile=profiling.snapshot()
# These serial proofs populate cross-tile compiled textures, facade encodes and
# shared vehicle assets. Refuse a new cold generation until it has real proofs.
for tile in ('t0_0_0','t0_1_0','t0_-1_0'):
    ready=json.loads((root/tile/'ready.json').read_text())
    if ready['generation']!=root.name:raise ValueError('warm proof generation mismatch')
    Farm.validate(root,tile,json.loads((root/tile/'manifest.json').read_text()))
library=surfaces.library(root,default_library(),range(len(surfaces.KEYS)))
report=dict(library=str(library),channels=len(surfaces.KEYS),
            seconds=round(time.monotonic()-started,3),**profiling.elapsed(profile))
atomic_json(root/'warm.json',report)
print(json.dumps(report),flush=True)

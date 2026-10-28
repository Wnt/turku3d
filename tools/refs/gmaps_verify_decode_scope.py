"""Verify a scoped decode retains every source from an original unscoped decode."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ.get('GMAPS_TOOLS', str(Path.home()/'turku3d/private/gmaps-extract')))
from gmaps_decode_scope import response_selection

parser = argparse.ArgumentParser()
parser.add_argument('--capture', type=Path, required=True)
parser.add_argument('--cache', type=Path, required=True)
args = parser.parse_args()
original = json.loads((args.capture/'decoded/manifest.json').read_text())
if original.get('response_selection') or not original['meshes']:
    raise ValueError('Verification requires a nonempty original unscoped decode')
selected = response_selection(args.capture, args.cache)
kept = {row['sha256'] for row in selected}
missing = [row['node'] for row in original['meshes'] if row['source_sha256'] not in kept]
if missing:
    raise ValueError(f'Spatial selection dropped original decoded sources: {missing}')
print(json.dumps(dict(original_meshes_retained=len(original['meshes']), selected_responses=len(selected))))

#!/usr/bin/env python3
"""Traffic-specific blind A/B using the shared Astra judge runner (the city judge ignores cars)."""
import argparse
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import time

spec = importlib.util.spec_from_file_location('judge', Path.home() / 'turku3d/tools/visual-judge/judge.py')
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)

parser = argparse.ArgumentParser()
parser.add_argument('--a', required=True)
parser.add_argument('--b', required=True)
parser.add_argument('--ref', action='append', default=[])
parser.add_argument('--pose', default='')
parser.add_argument('--brief', default='')
args = parser.parse_args()
swap = random.choice([False, True])
with tempfile.TemporaryDirectory() as directory:
    paths = [args.b, args.a] if swap else [args.a, args.b]
    images = [judge.small(p, Path(directory)) for p in paths + args.ref]
    prompt = f'''You are the blind traffic-behaviour judge for Turku3D, a Finnish city driving game.
Images 1 and 2 show alternative game traffic from an identical fixed camera. If a contact sheet, panels are
consecutive time samples in reading order. Judge ONLY traffic: believable lanes, turns and queue placement,
vehicle spacing, stop positions, priority/crossing behaviour if actually evidenced, and how the traffic reads
to a player. Ignore buildings, lighting, vegetation and vehicle mesh quality. Prefer realistic functioning
traffic over a static traffic jam. Do not infer movement or legal compliance from a single still.
Images 3 onward are real Finnish references or GTA traffic technique references. GTA is a technique reference
only: assets, road rules and traffic mix should be Finnish. {args.brief}
Return JSON only: {{"per_category":{{"lane_discipline":"1|2|same", "spacing":"1|2|same",
"intersection_readability":"1|2|same", "motion":"1|2|same"}}, "overall":"1|2|same",
"why":"one sentence", "regressions":[], "evidence_limits":[]}}.'''
    raw = judge.parse_json(judge.run_codex(prompt, images, directory))

def back(value):
    if str(value) not in ('1', '2'):
        return 'same'
    return ('B' if str(value) == '1' else 'A') if swap else ('A' if str(value) == '1' else 'B')

result = {**raw, 'per_category': {k: back(v) for k, v in raw.get('per_category', {}).items()},
          'overall': back(raw.get('overall'))}
judge.log({'t': time.time(), 'mode': 'traffic-ab', 'pose': args.pose, 'a': args.a, 'b': args.b,
           'refs': args.ref, 'result': result})
print(json.dumps(result, indent=2))

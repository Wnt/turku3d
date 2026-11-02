#!/usr/bin/env python3
"""Blind vehicle-specific prompt through the shared gpt-6-astra judge engine.

The city judge deliberately ignores vehicles. This adapter judges the racer and leaves
all reference imagery outside the repository. Engine, model, logging and cache are shared.
"""
import argparse, importlib.util, json, random, tempfile
from pathlib import Path

engine_path = Path.home() / 'turku3d/tools/visual-judge/judge.py'
spec = importlib.util.spec_from_file_location('shared_judge', engine_path)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
p = argparse.ArgumentParser()
p.add_argument('--game', required=True)
p.add_argument('--before')
p.add_argument('--ref', action='append', default=[])
p.add_argument('--out', required=True)
p.add_argument('--brief', default='')
a=p.parse_args()
with tempfile.TemporaryDirectory() as d:
    inputs = [a.game] if not a.before else [a.before, a.game]
    swapped = bool(a.before) and random.choice([True, False])
    if swapped: inputs.reverse()
    images = [engine.small(x,Path(d)) for x in inputs + a.ref]
    prompt = '''You are a strict senior vehicle art director judging an original fictional Finnish unlimited-class off-road racer in a browser game. Judge THE VEHICLE, not the city or background. Target: a convincing spectacular 1000hp-class desert pre-runner / rock racer with tube-frame chassis, long-travel wishbones, real coil springs and separate bypass shocks, huge beadlock tyres, exposed V8 induction, protected cockpit and light bar. Original fictional livery, plausible engineering, material separation, weathering and detail matter. Do not require real badges, specific sponsor marks, copying the reference design, the same view or real photographic background. Evaluate what is visible. Answer JSON only. '''
    prompt += a.brief + '\n'
    if a.before:
        prompt += '''Images 1 and 2 are blind candidate game versions at an identical pose; images 3+ are real off-road reference photos. Choose the better racer. Return {"winner":"1"|"2"|"tie","scores":{"1":0-10,"2":0-10},"reasons":[...],"regressions":[...],"next":[3 concrete highest-impact improvements]}.'''
    else:
        prompt += '''Image 1 is the game vehicle; images 2+ are engineering/material references. Return {"scores":{"silhouette":0-10,"engineering":0-10,"materials":0-10,"detail":0-10,"overall":0-10},"convincing_high_end_racer":true|false,"next":[5 concrete highest-impact improvements]}.'''
    result=engine.parse_json(engine.run_codex(prompt,images,d))
    if a.before:
        w=result['winner'];result['winner']='tie' if w=='tie' else ('after' if (w=='1')==swapped else 'before')
    result['model']=engine.MODEL
    Path(a.out).write_text(json.dumps(result,indent=2)+'\n')
    engine.log({'mode':'vehicle-ab' if a.before else 'vehicle-score','game':a.game,'before':a.before,'refs':a.ref,'result':result})
    print(json.dumps(result,indent=2))

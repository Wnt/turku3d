"""Run the native-coordinate proof before a budgeted two-browser quarter sweep.

Provider bytes and findings are sandbox products. Installed private decoder/scorer
modules are inputs; this runner never modifies their source or round-2 evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from collections import Counter

REPO = Path(__file__).resolve().parents[2]
TOOLS = Path(os.environ.get('GMAPS_TOOLS', Path.home() / 'turku3d/private/gmaps-extract'))
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(REPO / 'scripts/dev'))
from storage import atomic_json
import browser_budget

ROOT = Path(os.environ['TURKU3D_WORK']) / 'gmaps-round3'
ROOT.mkdir(parents=True, exist_ok=True)
PLAN = json.loads((Path.home() / 'turku3d/private/gmaps-data/round2/plan.json').read_text())


def config(row, lane):
    from pyproj import Transformer
    e0, n0, e1, n1 = row['extent_en']
    lon, lat = Transformer.from_crs(3877, 4326, always_xy=True).transform((e0+e1)/2, (n0+n1)/2)
    out = ROOT / 'captures' / row['coverage_id']
    return dict(**{k: row[k] for k in ('tile', 'coverage_id', 'extent_en')},
                lat=lat, lon=lon, output=str(out), profile=f'round3-{lane}',
                headless=True, resume=(out / 'capture.json').exists())


def capture(rows, lane, seconds):
    invocation = ROOT / f'invocation-{lane}.json'
    atomic_json(invocation, dict(batch=[config(row, lane) for row in rows],
                                 deadline_ms=int((time.time()+seconds)*1000)))
    env = dict(os.environ, GMAPS_TOOLS=str(TOOLS), GMAPS_PYTHON=sys.executable, REPO=str(REPO), TURKU3D_JOBS='1')
    log = (ROOT / f'browser-{lane}.log').open('a')
    return subprocess.Popen([sys.executable, str(REPO/'scripts/dev/host_slots.py'), 'browser',
                             'node', str(REPO/'tools/refs/gmaps_capture.mjs'), str(invocation)],
                            env=env, stdout=log, stderr=subprocess.STDOUT)


def score_one(row):
    from detail import metadata, audit
    import mesh
    from mesh import decode_capture
    import loop
    from storage import ROOT as PRIVATE_ROOT, configured_limit, evict
    # The scorer may compact only this sandbox's disposable products.
    loop.evict = lambda **kwargs: evict(root=ROOT, **kwargs)
    mesh.evict = loop.evict
    from storage import usage
    mesh.usage = lambda: usage(ROOT)
    score = loop.score
    import round2_oci as oci
    oci.DEST = ROOT / 'oci'
    oci.DEST.mkdir(exist_ok=True)
    out = ROOT / 'captures' / row['coverage_id']
    record = json.loads((out/'capture.json').read_text())
    report = metadata(out)
    if record.get('inventory_state') != 'eligible' or not report['complete']:
        raise ValueError('Scoring requires verified eligible reference')
    own = oci.await_current(row['tile'])
    atomic_json(out/'oci-snapshot.json', own)
    if own['status'] != 'frozen':
        raise ValueError(f'Current OCI unavailable: {own}')
    decoded = decode_capture(out, PRIVATE_ROOT/'tools/libcrn.dylib', limit=configured_limit())
    if decoded['errors'] or not decoded['meshes']:
        raise ValueError('Incomplete reference decode')
    audit(out)
    directory, report = score(row['tile'], Path(own['path']).parent, capture=out)
    return dict(status='scored', report=str(directory/'report.json'), own_sha256=own['sha256'],
                confirmed=sum(c['status']=='confirmed' for c in report['candidates']))


def findings(states):
    themes = [
        ('Textures', {'ground colour', 'wall colour'}),
        ('Bridges', {'bridge height', 'missing bridge deck'}),
        ('Ground, wall and object geometry', {'ground height', 'extra structure', 'missing building', 'footprint offset'}),
        ('Roofs and heights (last priority)', {'roof type', 'roof height', 'roof colour'}),
    ]
    candidates=[]
    for key, state in states.items():
        if state['status']=='scored':
            candidates.extend(dict(c, coverage_id=key) for c in json.loads(Path(state['report']).read_text())['candidates'])
    scored=sum(s['status']=='scored' for s in states.values())
    lines=['# Round 3 incremental generator findings', '',
           f'{scored}/40 scored; nominal footprint {scored*0.015625:.6f} km². Missing node footprints are masked from calibration and scores.', '']
    counts={}
    for name, kinds in themes:
        selected=[c for c in candidates if c['problem']['kind'] in kinds]
        counts[name]=dict(Counter(c['status'] for c in selected))
        lines.extend([f'## {name}', '', f'Counts: {counts[name]}', ''])
        for c in sorted(selected, key=lambda c:c['status']!='confirmed')[:12]:
            lines.append(f"- {c['status']}: {c['coverage_id']} {c['problem']}")
        lines.append('')
    lines.extend(['Small props, wall geometry, bridge undersides and tunnel interiors remain unmeasured where the scorer lacks independent semantic evidence. Appearance alone does not prove material type.', '', '## Inventory', ''])
    lines.extend(f'- {key}: {value["status"]}' for key,value in states.items())
    (ROOT/'findings.md').write_text('\n'.join(lines)+'\n')
    atomic_json(ROOT/'findings.json', dict(scored=scored, themes=counts, candidates=candidates))
    return scored


def run(mode):
    states_path=ROOT/'coverage.json'
    states=json.loads(states_path.read_text())['quarters'] if states_path.exists() else {
        row['coverage_id']:dict(status='planned', tile=row['tile'], extent_en=row['extent_en']) for row in PLAN['quarters']}
    atomic_json(ROOT/'plan.json', PLAN)
    if mode=='sweep':
        proof=json.loads((ROOT/'proof.json').read_text())
        if proof['status']!='scored' or proof['max_target_error_m']>=5:
            raise ValueError('One-quarter proof must be scored before sweep')
    rows=[r for r in PLAN['quarters'] if states[r['coverage_id']]['status']!='scored']
    if mode=='proof': rows=rows[:1]
    remaining=browser_budget.read(os.environ['TURKU3D_SANDBOX'])['remaining_minutes']*60
    lanes=1 if mode=='proof' else 2
    started=time.monotonic()
    browsers=[capture(rows[lane::lanes], lane, remaining/lanes-10) for lane in range(lanes)]
    jobs={}
    announced=0
    with ProcessPoolExecutor(max_workers=2) as pool:
        while True:
            for row in rows:
                key=row['coverage_id']
                file=ROOT/'captures'/key/'capture.json'
                if not file.exists(): continue
                record=json.loads(file.read_text())
                if states[key]['status'] in ('scored', 'score failed'): continue
                states[key].update(status=record.get('inventory_state','planned'),
                                   progress=record.get('detail_progress'),
                                   error=record.get('error'), stop_reason=record.get('stop_reason'))
                if record.get('finished') and record.get('inventory_state')=='eligible' and key not in jobs:
                    jobs[key]=pool.submit(score_one, row)
                    states[key]['status']='scoring'
            for key, job in list(jobs.items()):
                if not job.done():
                    states[key]['status']='scoring'
                    continue
                if states[key]['status'] in ('scored','score failed'): continue
                try: states[key].update(job.result())
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    states[key].update(status='score failed', error=str(exc))
            atomic_json(states_path, dict(seed=PLAN['seed'], quarters=states))
            scored=findings(states)
            if scored//10>announced:
                announced=scored//10
                subprocess.run([str(REPO/'scripts/dev/codex-task'),'msg',os.environ['TURKU3D_SANDBOX'],
                                '--kind','note','--subject',f'{scored} round-3 quarters scored',
                                f'Incremental confirmed theme counts and evidence: {ROOT / "findings.md"}'])
            if all(p.poll() is not None for p in browsers) and all(j.done() for j in jobs.values()): break
            time.sleep(5)
    if mode=='proof':
        key=rows[0]['coverage_id']
        record=json.loads((ROOT/'captures'/key/'capture.json').read_text())
        errors=[v['timing']['target_error_m'] for v in record['detail_visits']]
        proof=dict(**states[key], coverage_id=key, max_target_error_m=max(errors,default=999),
                   capture_seconds=sum(s['seconds'] for s in record['session_statistics']),
                   end_to_end_seconds=time.monotonic()-started)
        atomic_json(ROOT/'proof.json',proof)
        print(json.dumps(proof),flush=True)
        if proof['status']!='scored': raise SystemExit('Proof failed; sweep prohibited')
    print(f'Findings: {ROOT / "findings.md"}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['proof','sweep'])
    run(parser.parse_args().mode)

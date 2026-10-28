"""Capture advertised quarter trees in one native page; score independently."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import gmaps_round3 as base

ROOT = base.ROOT / 'synthetic'
base.ROOT = ROOT


def run():
    proof = json.loads((ROOT / 'proof.json').read_text())
    if proof['status'] != 'scored':
        raise ValueError('Synthetic proof must be eligible and scored before expansion')
    coverage = ROOT / 'coverage.json'
    states = json.loads(coverage.read_text())['quarters'] if coverage.exists() else {
        row['coverage_id']: dict(status='planned', tile=row['tile'], extent_en=row['extent_en'])
        for row in base.PLAN['quarters']}
    for row in base.PLAN['quarters']:
        states.setdefault(row['coverage_id'], dict(status='planned', tile=row['tile'], extent_en=row['extent_en']))
    first = base.PLAN['quarters'][0]['coverage_id']
    states[first].update(proof)
    base.atomic_json(ROOT / 'plan.json', base.PLAN)
    rows = [row for row in base.PLAN['quarters'] if states[row['coverage_id']]['status'] not in ('scored','abstained')]
    capture_rows = [row for row in rows if not (ROOT/'captures'/row['coverage_id']/'capture.json').exists()
        or json.loads((ROOT/'captures'/row['coverage_id']/'capture.json').read_text()).get('inventory_state') != 'eligible']
    invocation = ROOT / 'sweep-invocation.json'
    remaining = base.browser_budget.read(os.environ['TURKU3D_SANDBOX'])['remaining_minutes'] * 60
    base.atomic_json(invocation, dict(session='synthetic-sweep',
        quarters=[base.config(row, 'synthetic-sweep') for row in capture_rows],
        deadline_ms=int((time.time() + remaining - 10) * 1000)))
    env = dict(os.environ, REPO=str(base.REPO), GMAPS_TOOLS=str(base.TOOLS),
        GMAPS_PYTHON=sys.executable, TURKU3D_JOBS='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    log = (ROOT / 'browser-sweep.log').open('a')
    browser = subprocess.Popen([sys.executable, str(base.REPO/'scripts/dev/host_slots.py'),
        'browser', 'node', str(base.REPO/'tools/refs/gmaps_synthetic.mjs'), str(invocation)],
        env=env, stdout=log, stderr=subprocess.STDOUT) if capture_rows else None
    jobs = {}
    attempted = {}
    announced = json.loads((ROOT/'milestone.json').read_text()).get('scored',0)//10 if (ROOT/'milestone.json').exists() else 0
    with ProcessPoolExecutor(max_workers=4) as pool:
        while True:
            for row in rows:
                key = row['coverage_id']
                record_path = ROOT / 'captures' / key / 'capture.json'
                if not record_path.exists() or states[key]['status'] in ('scored','abstained'):
                    continue
                record = json.loads(record_path.read_text())
                if key in jobs:
                    continue
                states[key].update(status=record['inventory_state'], progress=record.get('detail_progress'),
                    stop_reason=record.get('stop_reason'))
                if len(jobs) < 4 and record.get('finished') and record['inventory_state'] == 'eligible' and time.time()-attempted.get(key,0) > 15:
                    jobs[key] = pool.submit(base.score_one, row, ROOT)
                    attempted[key] = time.time()
                    states[key]['status'] = 'scoring'
            for key, job in list(jobs.items()):
                if not job.done():
                    continue
                try:
                    states[key].update(job.result())
                    states[key].pop('error', None)
                except Exception as error:
                    states[key].update(status='awaiting score retry', error=str(error))
                    print(json.dumps(dict(quarter=key, error=str(error))), flush=True)
                del jobs[key]
            base.atomic_json(coverage, dict(seed=base.PLAN['seed'], quarters=states))
            scored = base.findings(states)
            if scored//10 > announced:
                announced = scored//10
                base.atomic_json(ROOT / 'milestone.json', dict(scored=scored, at=time.time()))
                subprocess.run([str(base.REPO/'scripts/dev/codex-task'), 'msg', 'orchestrator',
                    '--kind', 'note', '--subject', f'{scored} synthetic quarters scored',
                    f'Confirmed theme counts: { {name: counts.get("confirmed", 0) for name, counts in json.loads((ROOT / "findings.json").read_text())["themes"].items()} }. Findings and score reports: {ROOT / "findings.md"}'])
            if (browser is None or browser.poll() is not None) and not jobs:
                eligible = [key for key,state in states.items() if state['status'] not in ('scored','abstained')
                    and (ROOT/'captures'/key/'capture.json').exists()
                    and json.loads((ROOT/'captures'/key/'capture.json').read_text()).get('inventory_state') == 'eligible']
                if not eligible:
                    break
            time.sleep(2)
    print(json.dumps(dict(scored=sum(s['status']=='scored' for s in states.values()),
        findings=str(ROOT/'findings.md'))), flush=True)


def prove():
    invocation = ROOT / 'proof-invocation.json'
    session = f'synthetic-proof-{int(time.time())}'
    row = base.PLAN['quarters'][0]
    remaining = base.browser_budget.read(os.environ['TURKU3D_SANDBOX'])['remaining_minutes'] * 60
    base.atomic_json(invocation, dict(session=session, quarters=[base.config(row, session)],
        deadline_ms=int((time.time()+remaining-10)*1000)))
    env = dict(os.environ, REPO=str(base.REPO), GMAPS_TOOLS=str(base.TOOLS),
        GMAPS_PYTHON=sys.executable, TURKU3D_JOBS='1')
    started = time.monotonic()
    subprocess.run([sys.executable, str(base.REPO/'scripts/dev/host_slots.py'), 'browser',
        'node', str(base.REPO/'tools/refs/gmaps_synthetic.mjs'), str(invocation)], env=env, check=True)
    record = json.loads((ROOT/'captures'/row['coverage_id']/'capture.json').read_text())
    if record['inventory_state'] != 'eligible':
        raise ValueError('Synthetic capture proof is incomplete; sweep prohibited')
    capture_wall = time.monotonic()-started
    result = base.score_one(row, ROOT)
    result.update(coverage_id=row['coverage_id'], capture_wall_seconds=capture_wall,
        from_browser_launch_through_score_seconds=time.monotonic()-started,
        capture_seconds=record['session_statistics'][0]['seconds'],
        native_initialization_seconds=record['native_initialization_seconds'])
    base.atomic_json(ROOT/'proof.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['proof', 'sweep'], nargs='?', default='sweep')
    if parser.parse_args().mode == 'proof':
        prove()
    else:
        run()

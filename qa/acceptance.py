#!/usr/bin/env python3
"""Check W1-11 evidence without mistaking a ground-only stub for an assembled AOI."""
import argparse
import json
from pathlib import Path
from qalib.paths import resolve
from qalib import registry


def check(run, views, calibration):
    errors=[]
    if len(views)<20: errors.append('fewer than 20 reference views')
    required={'google_streetview','apple_lookaround','mapillary','360tour','gmaps_photosphere'}
    missing=required-{v['source']['kind'] for v in views}
    if missing: errors.append('missing sources: '+', '.join(sorted(missing)))
    if not run.get('complete'): errors.append('run did not render and judge every view successfully')
    if set(run.get('expected_views',[])) != {v['id'] for v in views}:errors.append('registry differs from run view set')
    if not run.get('manifest_sha256'):errors.append('assembled package manifest not recorded')
    entries=[]
    for p in run['pairs']:
        if p.get('id_table'):
            entries.extend(json.loads(resolve(p['id_table']).read_text()).values())
    if not any(e.get('kind')=='building' for e in entries):
        errors.append('current package exposes no building asset IDs; assembled building layer is required')
    if calibration.get('n')!=3 or calibration.get('comparison_key')!=run.get('comparison_key'):
        errors.append('three-repeat calibration of this comparison setup is missing')
    return errors


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--calibration',type=Path,required=True)
    a=ap.parse_args()
    run=json.loads((a.run/'run.json').read_text())
    views=registry.load(a.run/'views.jsonl')
    calibration=json.loads(a.calibration.read_text())
    errors=check(run,views,calibration)
    print(json.dumps({'ok':not errors,'views':len(views),'blockers':errors},indent=2))
    return 1 if errors else 0


if __name__=='__main__':raise SystemExit(main())

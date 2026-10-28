"""Shared quarter configuration, immutable OCI scoring and incremental findings.

Provider bytes and findings are sandbox products. Installed private decoder/scorer
modules are inputs; this runner never modifies their source or round-2 evidence.
"""
import json
import os
from pathlib import Path
import sys
import time
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


def score_one(row, product_root=None, frozen_own=None):
    product_root = Path(product_root) if product_root else ROOT
    from detail import metadata, audit
    import mesh
    from mesh import decode_capture
    import loop
    from storage import ROOT as PRIVATE_ROOT, configured_limit, evict
    # The scorer may compact only this sandbox's disposable products.
    loop.evict = lambda **kwargs: evict(root=product_root, **kwargs)
    mesh.evict = loop.evict
    from storage import usage
    mesh.usage = lambda: usage(product_root)
    score = loop.score
    out = product_root / 'captures' / row['coverage_id']
    record = json.loads((out/'capture.json').read_text())
    report = metadata(out)
    if record.get('inventory_state') != 'eligible' or not report['complete']:
        raise ValueError('Scoring requires verified eligible reference')
    from gmaps_oci import freeze_current
    own = frozen_own or freeze_current(row['tile'], product_root / 'oci')
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



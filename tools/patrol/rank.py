#!/usr/bin/env python3
"""Rank judged patrol findings by player impact (tools/patrol/README.md).

  python3 tools/patrol/rank.py RUN_DIR [--top 10]

Inputs: RUN_DIR/candidates.json (analyze.py) and RUN_DIR/judge/*.json (the vision judge's verdicts: a list of
{n, real, severity 1-5, confidence 0-1, title, issue, cause, owner} per candidate n, plus judge-only findings with
n = null and their own spot/view/image). Candidates sharing an `issue` key are one finding; its frequency is the
number of distinct spots/views it was seen in.

impact = severity * confidence * visibility * street * centre * sqrt(frequency)
  visibility: detector-specific screen share or block count mapped to 0.4..1
  street:     1 at street level, 0.6 for the raised view
  centre:     spots.json weight (1 = Kauppatori/riverside/Aurakatu)
Writes RUN_DIR/ranked.json and RUN_DIR/report.md.
"""
import json, math, sys
from pathlib import Path

RUN = Path(sys.argv[1]); TOP = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 10
clist = json.load(open(RUN / 'candidates.json'))
cands = {c['n']: c for c in clist}
by_image = {c['image']: c for c in clist if c.get('image')}
verdicts = []
for p in sorted((RUN / 'judge').glob('*.json')):
    verdicts += json.load(open(p))


def visibility(c):
    m = c.get('metrics') or {}
    if 'area_share' in m: s = m['area_share']
    elif 'share' in m: s = m['share']
    elif 'blocks' in m and c['detector'] in ('shimmer', 'wander'): s = m['blocks'] * 576 / (960 * 540)
    elif 'blocks' in m: s = m['blocks'] / 336
    elif 'area_m2' in m: s = min(1, m['area_m2'] / 400)
    elif 'area_px' in m: s = m['area_px'] / (960 * 540)
    else: s = 0.05
    return 0.4 + 0.6 * min(1.0, math.sqrt(s / 0.1))


groups = {}
stale = 0
for v in verdicts:
    if v.get('n') is not None and v.get('image') and v['image'] not in by_image and v['image'].startswith('crops/'):
        stale += 1; continue  # its candidate no longer exists after re-analysis
    if not v.get('real'): continue
    # verdicts match their candidate by image path (stable across re-analysis), else by n; judge-only findings
    # carry their own fields
    c = by_image.get(v.get('image')) if v.get('n') is not None else None
    c = c or (cands.get(v.get('n')) if v.get('n') is not None else None) or v
    key = v.get('issue') or f"n{v.get('n')}"
    g = groups.setdefault(key, {'issue': key, 'members': [], 'seen': set()})
    g['members'].append((v, c)); g['seen'].add((c.get('spot'), c.get('view')))
ranked = []
for key, g in groups.items():
    best = None
    for v, c in g['members']:
        score = (v.get('severity', 1) * v.get('confidence', 0.5) * visibility(c) * (1.0 if c.get('street', True) else 0.6)
                 * c.get('centre', 0.7))
        if best is None or score > best[0]: best = (score, v, c)
    score, v, c = best
    freq = len(g['seen'])
    ranked.append({'issue': key, 'impact': round(score * math.sqrt(freq), 3), 'frequency': freq, 'title': v.get('title'),
                   'severity': v.get('severity'), 'confidence': v.get('confidence'), 'description': v.get('description', ''),
                   'cause': v.get('cause') or c.get('cause'), 'owner': v.get('owner') or c.get('owner'),
                   'image': c.get('image'), 'spot': c.get('spot'), 'view': c.get('view'), 'detector': c.get('detector', 'judge'),
                   'E': c.get('E'), 'N': c.get('N'), 'x': c.get('x'), 'z': c.get('z'), 'cam': c.get('cam'),
                   'seen_at': sorted(f'{a}/{b}' for a, b in g['seen'] if a)})
ranked.sort(key=lambda r: -r['impact'])
json.dump(ranked, open(RUN / 'ranked.json', 'w'), indent=1)
live = [v for v in verdicts if v.get('n') is not None and not (v.get('image', '').startswith('crops/') and v['image'] not in by_image)]
judged = len(live)
fp = len([v for v in live if not v.get('real')])
lines = [f'# Patrol {RUN.name}: top {TOP}', '',
         f'{len(cands)} detector candidates, {judged} judged, {fp} rejected by the judge '
         f'({fp / max(1, judged):.0%} detector false positives); {len(ranked)} distinct real issues.', '']
for i, r in enumerate(ranked[:TOP], 1):
    lines += [f"## {i}. {r['title']}  (impact {r['impact']}, severity {r['severity']}, confidence {r['confidence']}, seen {r['frequency']}x)",
              f"- where: {r['spot']} / {r['view']}; E {r['E']} N {r['N']} (engine x {r['x']} z {r['z']}); `?cam={r['cam']}`",
              f"- image: `{r['image']}`", f"- detector: {r['detector']}; seen at: {', '.join(r['seen_at'])}",
              f"- suspected cause: {r['cause']}", f"- owner: {r['owner']}", f"- {r['description']}", '']
open(RUN / 'report.md', 'w').write('\n'.join(lines))
print('\n'.join(lines[:4 + 7 * TOP]))

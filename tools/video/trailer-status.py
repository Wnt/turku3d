#!/usr/bin/env python3
"""Rewrite the shot table of DP-STATUS.md from the clip sidecars (python3 tools/video/trailer-status.py [NOTES.json]).

NOTES.json maps a shot id to its state text. Everything between the table markers is replaced."""
import json, os, re, sys, time

ROOT = os.path.expanduser('~/turku3d/video/2026-11-11-trailer')
SHOTS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trailer-shots.json')))['shots']
# Shots that exist only in the recapture list (2026-11-11 afternoon) follow the storyboard ones.
SHOTS += [s for s in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trailer-recapture.json')))['shots'] if s['id'] not in {x['id'] for x in SHOTS}]
ORDER = [s['id'] for s in SHOTS]
VARIANT = {s['id']: s.get('variant') for s in SHOTS}
notes = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}


def newest(shot, prefix):
    folder = os.path.join(ROOT, 'clips', shot)
    best = None
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        m = re.fullmatch(prefix + r'(\d+)\.json', name)
        if m and os.path.exists(os.path.join(folder, name[:-5] + '.mp4')) and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), name)
    if not best:
        return None
    side = json.load(open(os.path.join(folder, best[1])))
    return best[1][:-5], side


def cell(found):
    if not found:
        return 'none'
    name, s = found
    c = s.get('cadence', {})
    text = f"`{name}.mp4` {s.get('size')} {s.get('preset')}"
    if s.get('tier') == 'final' or name.startswith('s'):
        clean = c.get('exactDuplicateCaptures') == 0 and c.get('freezes') == 0 and str(c.get('stream', {}).get('nb_read_frames')) == str(c.get('expectedFrames'))
        again = (s.get('readiness') or {}).get('framesResettled') if isinstance(s.get('readiness'), dict) else None
        verdict = 'cadence clean' if clean else 'CADENCE FAULT'
        if s.get('tier') == 'final':
            verdict += ', strict pass' if s.get('pass') else f', {again} frames re-settled before capture'
        text += f", {verdict}, grab {s.get('grab')}"
    text += f", {c.get('stream', {}).get('nb_read_frames')}/{c.get('expectedFrames')} frames, {c.get('exactDuplicateCaptures')} dup, {c.get('freezes')} freezes"
    m = s.get('motion')
    if m:
        text += f", motion {'pass' if m.get('pass') else 'FAIL'} ({m.get('subject')}: rendered {(m.get('rendered') or {}).get('rms')} px rms, pixels median {(m.get('pixels') or {}).get('median')} px)"
    elif s.get('shot', {}).get('cars'):
        text += ', no motion check (replayed on the recorded time stamps)'
    if s.get('minCameraClearance') is not None:
        text += f", camera {s['minCameraClearance']} m above ground"
    if s.get('tier') == 'final':
        text += f", view {s.get('sceneryRadius')} m, traffic {s.get('overrides', {}).get('traffic')}/{s.get('shot', {}).get('traffic')}, walkers {s.get('overrides', {}).get('walkers')}"
    return text


lines = [f'<!-- table:start -->', f'Updated {time.strftime("%H:%M:%S")}. Clips are `clips/<shot>/<name>.mp4` with a `.json` sidecar and `-contact.jpg`.', '',
         '| Shot | Newest draft (720p high) | Newest safety take (1080p high, CRF 12) | Newest final (1080p ultra) | State |', '|---|---|---|---|---|']
for shot in ORDER:
    lines.append(f"| {shot} | {cell(newest(shot, 'd'))} | {cell(newest(shot, 's'))} | {cell(newest(shot, 'v'))} | {notes.get(shot, '') or (('variant: ' + VARIANT[shot]) if VARIANT.get(shot) else '')} |")
lines.append('<!-- table:end -->')
path = os.path.join(ROOT, 'DP-STATUS.md')
text = open(path).read()
block = '\n'.join(lines)
if '<!-- table:start -->' in text:
    text = re.sub(r'<!-- table:start -->.*?<!-- table:end -->', lambda _: block, text, flags=re.S)
else:
    head, _, rest = text.partition('\n## ')
    text = head + '\n## Shots (storyboard v7)\n\n' + block + '\n\n## ' + rest
open(path, 'w').write(text)
print(block)

#!/usr/bin/env python3
"""Score settled loop captures and rebuild the local integration scoreboard/contact sheet.

Reference images and generated reports stay outside the repository. Run after capture,
or use --watch to score each completed shot while the capture process continues.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
from pathlib import Path
import time


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def pose_id(shot):
    return shot['file'].split('-', 1)[1].rsplit('-', 1)[0]


def valid(shot):
    c = shot['capture']
    s = c.get('scenery')
    scenery_ready = not s or (not s['cataloguePending'] and not s['pending'] and not s['errors']
                             and s['loaded'] == s['total'])
    return c['qa_ready'] and not c['qa_error'] and not c['incomplete_tiles'] and not c['failed_assets_final'] and scenery_ready


def image_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def references(refs, pose):
    # Raised Google 3D cameras cannot establish street-level image fidelity.
    return [p for p in refs[pose] if '/streetview/' in p] if pose.startswith('pano-') else refs[pose]


def scoreboard(root, categories):
    lines = ['# Integration visual scoreboard', '',
             'Absolute scores are noisy; use same-pose A/B judgments to accept improvements. '
             'Evening/night scores use daylight references and are tracked separately. '
             'Their lighting scores are not a reason to turn night into daylight.', '']
    status = root / 'STATUS.md'
    if status.exists():
        current = next((line for line in status.read_text().splitlines() if line.strip() and not line.startswith('#')), '')
        lines += ['## Current preview', '', current, '',
                  '[Live integration status](integrator/STATUS.md). Scores below describe the recorded captures; '
                  'they do not certify a newer preview build.', '']
    lines += ['## Daylight means', '', '| Iteration | Valid scored poses | ' + ' | '.join(categories) + ' |',
              '|---|---:|' + '|'.join(['---:'] * len(categories)) + '|']
    for iteration in sorted(root.glob('iter-*')):
        if not iteration.is_dir():
            continue
        shots = {s['file']: s for s in read_json(iteration / 'shots.json', {}).get('shots', [])}
        rows = [row['result']['scores'] for file, row in read_json(iteration / 'scores.json', {}).items()
                if 'result' in row and file in shots and valid(shots[file]) and shots[file]['time'] == '13:00']
        if rows:
            means = []
            for category in categories:
                values = [r[category] for r in rows if isinstance(r.get(category), (int, float))]
                means.append(f'{sum(values) / len(values):.2f}' if values else '—')
            lines.append('| ' + ' | '.join([iteration.name, str(len(rows)), *means]) + ' |')
    lines += ['', '## Medium camera benchmarks', '',
              '| Iteration | Valid | Measured frames | Aggregate FPS | ≥30 FPS |', '|---|---|---:|---:|---|']
    for file in sorted(root.glob('iter-*/bench/summary.json')):
        bench = read_json(file, {})
        lines.append(f'| {file.parents[1].name} | {bench.get("valid")} | {bench.get("frames")} | '
                     f'{bench.get("fps", 0):.2f} | {bench.get("meets30fps")} |')
    lines += ['', '## Per pose and time', '',
             '| Iteration | Build | Pose | Time | ' + ' | '.join(categories) + ' |',
             '|---|---|---|---|' + '|'.join(['---:'] * len(categories)) + '|']
    for iteration in sorted(root.glob('iter-*'), key=lambda p: p.name):
        if not iteration.is_dir():
            continue
        shots = {s['file']: s for s in read_json(iteration / 'shots.json', {}).get('shots', [])}
        for file, row in sorted(read_json(iteration / 'scores.json', {}).items()):
            shot = shots.get(file)
            if 'result' not in row or not shot or not valid(shot):
                continue
            scores = row['result']['scores']
            values = ['—' if scores.get(c) is None else str(scores[c]) for c in categories]
            lines.append('| ' + ' | '.join([iteration.name, shot.get('build', '?'), row['pose'], shot['time'], *values]) + ' |')
    (root.parent / 'scoreboard.md').write_text('\n'.join(lines) + '\n')


def contact_sheet(root, iteration, refs):
    from PIL import Image, ImageDraw, ImageOps
    current = read_json(iteration / 'shots.json', {}).get('shots', [])
    baseline = read_json(root / 'iter-0/shots.json', {}).get('shots', [])
    before = {(pose_id(s), s['time']): s for s in baseline if valid(s)}
    after = {(pose_id(s), s['time']): s for s in current if valid(s)}
    times = ['13:00', '19:30', '22:00']
    width, height, label = 384, 240, 25
    columns = [f'{version} {at}' for at in times for version in ['Baseline', iteration.name]] + ['Reference 1', 'Reference 2']
    sheet = Image.new('RGB', (width * len(columns), 65 + (height + label) * len(refs)), '#20252c')
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 7), f'Turku3D integration — {iteration.name} — {datetime.now().astimezone().isoformat(timespec="minutes")}', fill='white')
    for n, title in enumerate(columns):
        draw.text((n * width + 8, 37), title, fill='white')
    for row, pose in enumerate(refs):
        files = []
        for at in times:
            for source, directory in [(before, root / 'iter-0'), (after, iteration)]:
                shot = source.get((pose, at))
                files.append(directory / shot['file'] if shot else None)
        rr = references(refs, pose)
        files.extend(Path(rr[i]) if i < len(rr) else None for i in range(2))
        y = 65 + row * (height + label)
        for col, file in enumerate(files):
            x = col * width
            draw.text((x + 6, y + 5), pose, fill='#e2e8f0')
            if file and file.exists():
                with Image.open(file) as im:
                    thumb = ImageOps.contain(im.convert('RGB'), (width, height))
                    sheet.paste(thumb, (x + (width - thumb.width) // 2, y + label + (height - thumb.height) // 2))
            else:
                draw.text((x + 12, y + 70), 'Pending / no reference', fill='#9aa6b2')
    output = root.parent / f'progress-{datetime.now():%H%M}.jpg'
    sheet.save(output, quality=90)
    sheet.save(iteration / 'contact-sheet.jpg', quality=90)
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path.home() / 'turku3d/loops/integrator')
    ap.add_argument('--iteration', default='iter-0')
    ap.add_argument('--judge', type=Path, default=Path.home() / 'turku3d/tools/visual-judge/judge.py')
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--expected', type=int, default=30)
    ap.add_argument('--parallel', type=int, default=2)
    ap.add_argument('--report-only', action='store_true')
    ap.add_argument('--observations', type=Path,
                    default=Path.home() / 'turku3d/data/facade-characterization/observations')
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('visual_judge', args.judge)
    judge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(judge)
    refs = read_json(args.root.parent / 'refs.json', {})
    iteration = args.root / args.iteration
    iteration.mkdir(parents=True, exist_ok=True)
    scores_path = iteration / 'scores.json'
    scores = read_json(scores_path, {})

    def score(shot):
        pose = pose_id(shot)
        captured_hash = image_hash(iteration / shot['file'])
        try:
            result = judge.score(str(iteration / shot['file']), references(refs, pose), pose=pose + '@' + shot['time'])
            if image_hash(iteration / shot['file']) != captured_hash:
                raise RuntimeError('Capture changed during judging; retry against the new image')
            return {'pose': pose, 'time': shot['time'], 't': datetime.now(timezone.utc).isoformat(),
                    'image_hash': captured_hash, 'result': result}
        except Exception as exc:
            return {'pose': pose, 'time': shot['time'], 'image_hash': captured_hash,
                    'error': str(exc)}

    last_report = None
    while True:
        shots = read_json(iteration / 'shots.json', {}).get('shots', [])
        pending = [s for s in shots if valid(s) and (s['file'] not in scores or
                   scores[s['file']].get('image_hash') != image_hash(iteration / s['file']))]
        if not args.report_only:
            with ThreadPoolExecutor(args.parallel) as pool:
                jobs = {pool.submit(score, shot): shot for shot in pending}
                for future in as_completed(jobs):
                    shot = jobs[future]
                    scores[shot['file']] = future.result()
                    write_json(scores_path, scores)
                    # Preserve vision findings as building-data evidence. Pose-level findings must be
                    # associated with surveyed buildings before becoming per-building overrides.
                    if 'result' in scores[shot['file']]:
                        args.observations.mkdir(parents=True, exist_ok=True)
                        write_json(args.observations / f'integrator-{args.iteration}-{Path(shot["file"]).stem}.json',
                                   {**scores[shot['file']], 'refs': references(refs, pose_id(shot)),
                                    'building_association': 'pending', 'override': False})
                    scoreboard(args.root, judge.CATEGORIES)
                    print(shot['file'], json.dumps(scores[shot['file']]), flush=True)
        report_key = json.dumps([shots, scores], sort_keys=True)
        if report_key != last_report:
            scoreboard(args.root, judge.CATEGORIES)
            if shots:
                print('Contact sheet:', contact_sheet(args.root, iteration, refs), flush=True)
            last_report = report_key
        if not args.watch or sum(valid(s) for s in shots) >= args.expected or args.report_only:
            break
        time.sleep(10)
    invalid = [s['file'] for s in shots if not valid(s)]
    errors = [file for file, row in scores.items() if 'error' in row]
    print(json.dumps({'captured': len(shots), 'scored': len(scores) - len(errors), 'invalid': invalid, 'judge_errors': errors}), flush=True)
    return bool(invalid or errors)


if __name__ == '__main__':
    raise SystemExit(main())

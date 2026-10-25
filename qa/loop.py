#!/usr/bin/env python3
"""Render every selected reference pose, judge in parallel and report failures as failures."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
from PIL import Image
from judge.batch import judge_pairs
from judge.judge import DEFAULT_MODEL, PROMPT, SCHEMA
from qalib import registry
from qalib.paths import QA_DIR, data_dir, rel, resolve
from qalib.provenance import digest, file_digest, git_state, view_digest
from report.report import build_report


def default_run_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + git_state()['commit'][:8]


def validate_render(view, rdir):
    """Never accept stale screenshots left behind by a failed pose/ID pass."""
    meta = json.loads((rdir / f"{view['id']}.meta.json").read_text())
    if meta['pose'] != view['pose'] or meta['width'] != view['width'] or meta['height'] != view['height'] or meta.get('time') != view.get('time'):
        raise ValueError('render metadata does not match reference pose/viewport/time')
    for name, suffix in [('color', '.png'), ('id', '.id.png')]:
        p = meta['passes'][name]
        if p.get('error') or not p.get('ready'):
            raise ValueError(f"{name} pass failed: {p.get('error')}")
        for key, value in view['pose'].items():
            echoed = p.get('pose', {}).get(key)
            if not isinstance(echoed, (int, float)) or abs(echoed - value) > 0.001:
                raise ValueError(f'{name} pose mismatch: {key}')
        with Image.open(rdir / f"{view['id']}{suffix}") as image:
            if image.size != (view['width'], view['height']):
                raise ValueError(f'{name} image size mismatch')
    table = json.loads((rdir / f"{view['id']}.id.json").read_text())
    if not isinstance(table, dict) or not table:
        raise ValueError('missing object ID table')
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--views', default=str(registry.default_registry()))
    ap.add_argument('--ids')
    ap.add_argument('--url')
    ap.add_argument('--serve')
    ap.add_argument('--renders', type=Path)
    ap.add_argument('--backend', choices=['webgpu', 'webgl'], default='webgpu')
    ap.add_argument('--channel', default='chrome', choices=['chrome', 'chromium'])
    ap.add_argument('--run-id')
    ap.add_argument('--no-judge', action='store_true')
    ap.add_argument('--no-id-image', action='store_true')
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--cap', type=float)
    ap.add_argument('--manifest', type=Path, help='assembled package manifest, recorded by hash')
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= 8:
        ap.error('--workers must be 1..8')
    views = registry.load(a.views)
    if a.ids:
        wanted = set(a.ids.split(','))
        if wanted - {v['id'] for v in views}:
            ap.error('unknown view IDs')
        views = [v for v in views if v['id'] in wanted]
    if not views:
        ap.error('no views selected')
    run_id = a.run_id or default_run_id()
    if Path(run_id).name != run_id or run_id in ('.', '..'):
        ap.error('run-id must be a filename')
    rd = data_dir() / 'runs' / run_id
    if (rd / 'run.json').exists():
        ap.error('run-id already exists; use a new ID to preserve evidence')
    rd.mkdir(parents=True, exist_ok=True)
    references = rd / 'references'
    references.mkdir(exist_ok=True)
    # Validate reference availability before opening the browser or spending quota.
    try:
        view_hashes = {v['id']: view_digest(v) for v in views}
        for v in views:
            source = resolve(v['image'])
            with Image.open(source) as image:
                if image.size != (v['width'], v['height']):
                    raise ValueError(f"{v['id']}: reference dimensions differ from registry")
            shutil.copyfile(source, references / f"{v['id']}{source.suffix}")
    except (OSError, ValueError) as e:
        print(f'BLOCKED: {e}'); return 2
    run = {'run_id': run_id, 'kind': 'stub' if a.serve == 'stub' else 'render', **git_state(),
           'views': a.views, 'expected_views': [v['id'] for v in views], 'view_hashes': view_hashes,
           'backend': a.backend, 'with_id_image': not a.no_id_image, 'model': DEFAULT_MODEL, 'complete': False, 'errors': [], 'pairs': [],
           'manifest_sha256': file_digest(a.manifest) if a.manifest else None,
           'manifest': json.loads(a.manifest.read_text()) if a.manifest else None}
    run['comparison_key'] = digest({'views': view_hashes, 'backend': a.backend, 'channel': a.channel,
                                    'model': DEFAULT_MODEL, 'prompt': file_digest(PROMPT),
                                    'schema': file_digest(SCHEMA), 'id_image': not a.no_id_image})
    rdir = a.renders.resolve() if a.renders else rd / 'renders'
    selected = rd / 'views.jsonl'
    selected.write_text(''.join(json.dumps(v) + '\n' for v in views))
    if not a.renders:
        if not (a.url or a.serve):
            ap.error('need --url, --serve or --renders')
        cmd = ['node', str(QA_DIR / 'render/render.mjs'), '--views', str(selected), '--out', str(rdir),
               '--backend', a.backend, '--channel', a.channel]
        cmd += ['--url', a.url] if a.url else ['--serve', a.serve]
        if subprocess.run(cmd, cwd=QA_DIR / 'render').returncode:
            run['errors'].append('render driver failed')
    for v in views:
        try:
            validate_render(v, rdir)
        except (OSError, ValueError, KeyError) as e:
            run['errors'].append(f"{v['id']}: {e}")
            continue
        run['pairs'].append({'pair_id': v['id'], 'view_id': v['id'], 'label': 'render', 'ref': rel(references / f"{v['id']}{resolve(v['image']).suffix}"),
                             'render': rel(rdir / f"{v['id']}.png"), 'id_image': rel(rdir / f"{v['id']}.id.png"),
                             'id_table': rel(rdir / f"{v['id']}.id.json"),
                             'context': f"reference captured {v.get('capture_date')}, source {v['source']['kind']}; "
                                        f"pose uncertainty {json.dumps(v['pose_confidence'])}."})
    results = {}
    if not a.no_judge and run['pairs']:
        results = judge_pairs(run['pairs'], rd / 'judge', workers=a.workers, cap=a.cap, with_ids=not a.no_id_image)
        for v in views:
            if not results.get(v['id'], {}).get('ok'):
                run['errors'].append(f"{v['id']}: judge failed or not run")
    run['complete'] = not a.no_judge and not run['errors'] and len(results) == len(views)
    run['render_complete'] = len(run['pairs']) == len(views) and not any('render' in e for e in run['errors'])
    (rd / 'run.json').write_text(json.dumps(run, indent=2))
    print(build_report(rd))
    if run['errors']:
        print('\n'.join(run['errors']))
        return 3 if any(r.get('quota') for r in results.values()) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

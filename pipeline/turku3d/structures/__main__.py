"""Build structures only inside the worker sandbox; never fetch shared inputs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
from turku3d import config as C
from .build import build
from .inputs import Ground, office_bounds, read_surveys, survey_sources, lidar_paths, load_lidar
from .layer import write_layer, sha256, cached_manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ground-layer', type=Path, default=C.work_root() / 'build/layers/ground')
    a = ap.parse_args(argv)
    start = time.monotonic()
    report_dir = C.work_root() / 'reports/structures'
    report_dir.mkdir(parents=True, exist_ok=True)
    metrics = {'status': 'BLOCKED', 'issues': []}
    try:
        bounds = office_bounds()
        records = read_surveys(bounds)
        metrics['input_counts'] = dict(Counter(r['code'] for r in records))
        ground = Ground(a.ground_layer)
        paths = lidar_paths(bounds)
        inputs = {str(p): sha256(p) for p in [*survey_sources(), *ground.paths, *paths]}
        out = C.work_root() / 'build/layers/structures'
        params = {'bbox': list(bounds.as_tuple())}
        manifest = cached_manifest(out, inputs, params)
        if manifest is not None:
            metrics = json.loads((out / 'metrics.json').read_text())
            metrics['cached'] = True
        else:
            lidar = load_lidar(paths, bounds)
            meshes, metrics = build(records, lidar, ground)
            if meshes:
                manifest, cached = write_layer(meshes, out, inputs=inputs, params=params, metrics=metrics)
                metrics['layer_tiles'] = manifest['tiles']
                metrics['cached'] = cached
    except (FileNotFoundError, ValueError) as e:
        metrics['issues'].append({'object_id': 'inputs', 'reason': str(e)})
    metrics['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (report_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False) + '\n')
    (report_dir / 'report.md').write_text('# Structures acceptance report\n\n'
        + f"Status: **{metrics['status']}**\n\n```json\n" + json.dumps(metrics, indent=2, allow_nan=False) + '\n```\n')
    print(json.dumps(metrics, indent=2, allow_nan=False))
    return 1 if metrics['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Read-only comparison of old delivery and safe retained delivery on real OCI products."""
import argparse
import importlib.util
import json
import time
from pathlib import Path
from types import MethodType, SimpleNamespace

from turku3d import config as C
from turku3d.coarse.collision import current
from turku3d.coarse.collision_key import current_code as structural_code
from turku3d.coarse.packaging import current_code
from turku3d.coarse.service import Builder
from turku3d.coarse.sources import Sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/srv/turku3d/work/coarse'))
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--tiles', nargs='+', default=[f't0_{i}_-2' for i in range(-1, 6)])
    args = parser.parse_args()
    if args.candidate:
        spec = importlib.util.spec_from_file_location('turku3d.coarse.serving', args.candidate)
        serving = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serving)
    else:
        from turku3d.coarse import serving
    sources = Sources()
    code, collision_code = current_code(), structural_code()
    builder = SimpleNamespace(root=args.root, code=lambda: code, collision_code=lambda: collision_code,
        input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
        structural_input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile), first_pass=True),
        manifest=lambda tile: args.root / tile / 'manifest.json')
    builder.built = MethodType(Builder.built, builder)
    rows = []
    for tile in args.tiles:
        start = time.monotonic()
        old = current(builder, tile)
        old_full = builder.built(tile) and json.loads(builder.manifest(tile).read_text()).get('pass_kind') == 'full'
        value = serving.retained_fragment(builder, tile)
        rows.append(dict(tile=tile, old_collision_pass=old.get('pass_kind') if old else None,
            old_current_full=bool(old_full), retained_pass=value.get('pass_kind') if value else None,
            stale=bool(value and value.get('stale')), seconds=round(time.monotonic()-start, 4),
            collision_matches_current=bool(value and old and value['collision'] == old['collision'])))
    print(json.dumps(dict(code=code, collision_code=collision_code, rows=rows,
        old_current_full=sum(row['old_current_full'] for row in rows),
        retained_full=sum(row['retained_pass']=='full' for row in rows)), indent=2))


if __name__ == '__main__':
    main()

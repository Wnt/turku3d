#!/usr/bin/env python3
"""osgallery-dev: what a bulk host needs for the given migrated aliases, from the farm's own records.

  resolve.py [--farm-root ROOT] ALIAS [ALIAS ...]

Prints one JSON object: per alias its revision, code revision, generation, base generation, source version and
step generations, and the unions the stage needs: product generations (each alias's own, every step's and spot
rebuild's, and the base), code revisions, and source versions (with their data roots).
All aliases must share one source version.
"""
import argparse
import json
from pathlib import Path


def resolve(root: Path, aliases):
    names = json.loads((root / 'aliases.json').read_text())
    out = dict(aliases={}, products=[], codes=[], source_versions={})
    for alias in aliases:
        rev = names.get(alias, alias)
        meta = json.loads((root / 'revisions' / rev / 'revision.json').read_text())
        if meta.get('kind') != 'migrated':
            raise SystemExit(f'{alias} is not a migrated revision')
        base = json.loads((root / 'revisions' / meta['base_revision'] / 'revision.json').read_text())
        gens = [meta['generation'], meta['base_generation']]
        gens += [s[k] for s in meta['migrations'] for k in ('generation', 'rebuild_generation') if s.get(k)]
        out['aliases'][alias] = dict(revision=rev, code=meta['code_revision'], generation=meta['generation'],
                                     base_generation=meta['base_generation'],
                                     source_version=meta['base_source_version'],
                                     # the alias's own and its steps' generations (outputs; spot rebuilds are inputs)
                                     steps=[meta['generation']] + [s['generation'] for s in meta['migrations']])
        out['products'] += [g for g in gens if (root / 'products' / g).is_dir() and g not in out['products']]
        out['codes'] += [meta['code_revision']] if meta['code_revision'] not in out['codes'] else []
        out['source_versions'][meta['base_source_version']] = base['data_root']
    if len(out['source_versions']) != 1:
        raise SystemExit(f'aliases span several source versions: {sorted(out["source_versions"])}')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--farm-root', type=Path, default=Path('/home/wnt/turku3d-local/loops/tiles/farm'))
    ap.add_argument('aliases', nargs='+')
    a = ap.parse_args()
    print(json.dumps(resolve(a.farm_root, a.aliases), indent=1))


if __name__ == '__main__':
    main()

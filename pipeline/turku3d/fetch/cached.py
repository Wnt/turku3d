"""Read-only readiness check for builds. Fetching shared data is a separate preparation step."""
import argparse
import json
from .. import config as C
from .common import Registry
from .sources import WFS_LAYERS, WMS_LAYERS


def check(area):
    bad = []
    registries = {}
    for kind in ('laz', 'wfs', 'ortho', 'osm'):
        root = C.raw_dir(kind)
        if not root.is_dir():
            bad.append(str(root))
            continue
        reg = Registry(root)
        registries[kind] = reg
        selected = [root / p for p in reg.records() if area.name in p]
        if kind == 'laz':
            selected = [root / ds / f'{t.name}.laz' for ds in C.LAZ_DATASETS for t in area.laz_tiles]
        if not selected:
            bad.append(f'{kind}: no files for {area.name}')
        bad.extend(str(p) for p in selected if not reg.is_complete(p))
    for layer in WFS_LAYERS:
        p = C.raw_dir('wfs') / area.name / f'{layer.slug}.summary.json'
        s = json.loads(p.read_text()) if p.exists() else {}
        if s.get('status') not in ('ok', 'empty') and not (layer.expect_401 and s.get('status') == 'unauthorized'):
            bad.append(str(p))
        if s.get('status') in ('ok', 'empty'):
            output = p.with_name(f'{layer.slug}.parquet')
            if 'wfs' not in registries or not registries['wfs'].is_complete(output):
                bad.append(str(output))
    for layer in WMS_LAYERS:
        p = C.raw_dir('ortho') / layer.slug / f'{area.name}.summary.json'
        s = json.loads(p.read_text()) if p.exists() else {}
        if not s or s.get('errors') or s.get('chunks', 0) != s.get('chunks_ok', 0) + s.get('chunks_empty', 0):
            bad.append(str(p))
        if s.get('chunks_ok', 0) and not (p.parent / f'{area.name}.vrt').is_file():
            bad.append(str(p.parent / f'{area.name}.vrt'))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--area', choices=sorted(C.AREAS), default='work-3x3')
    a = ap.parse_args()
    bad = check(C.AREAS[a.area])
    if bad:
        print('\n'.join(bad))
        print(f'Prepare shared inputs once: make fetch-aoi AREA={a.area}')
        return 1
    print(f'Cached inputs {a.area}: OK (read-only, no network)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

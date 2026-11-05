#!/usr/bin/env python3
"""Real-city baseline for the patrol: Google Street View panoramas (local only, never in git).

  PY=~/turku3d/envs/pipeline/bin/python   (has streetlevel, PIL, numpy)
  $PY tools/patrol/panos.py select IN.json OUT.json [--radius 60]
      IN: a spots file whose spots give an approximate E/N (district point on a street). Each spot is snapped to
      the nearest panorama within --radius m: E/N become the pano's, `heading` its travel direction (grid
      bearing), and `pano` records id, date, camera elevation and distance. Spots without a pano keep their E/N.
  $PY tools/patrol/panos.py refs RUN_DIR
      For every captured view of a spot with a pano (RUN_DIR/poses.json): the perspective crop of the panorama at
      the view's yaw/pitch/fov and the capture size -> RUN_DIR/ref/<spot>-<view>.jpg (+ ref/index.json).
Equirects are cached in ~/turku3d/loops/patrol/panos/ (PATROL_PANOS overrides). Pano headings are true azimuths;
the game's yaw is a grid bearing (EPSG:3877), converted with qalib.geo.
"""
import json, math, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'qa'))
from PIL import Image  # noqa: E402
from qalib import geo  # noqa: E402
from qalib.pano import perspective  # noqa: E402

CACHE = Path(os.environ.get('PATROL_PANOS', Path.home() / 'turku3d/loops/patrol/panos'))


def sv():
    # streetlevel imports pyexiv2 only to write EXIF on files it saves itself; the patrol saves with PIL.
    import types
    stub = types.ModuleType('pyexiv2'); stub.ImageData = stub.Image = object
    sys.modules.setdefault('pyexiv2', stub)
    from streetlevel import streetview
    return streetview


def equirect(pano_id, zoom=4):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f'gsv_{pano_id}_z{zoom}.jpg'; mp = CACHE / f'gsv_{pano_id}.json'
    if p.exists() and mp.exists():
        return json.load(open(mp)), Image.open(p)
    meta = sv().find_panorama_by_id(pano_id)
    if meta is None:
        raise SystemExit(f'pano {pano_id} not found')
    img = sv().get_panorama(meta, zoom=zoom); img.save(p, quality=92)
    e, n = geo.wgs84_to_en(meta.lat, meta.lon)
    m = {'id': meta.id, 'E': e, 'N': n, 'heading_true': math.degrees(meta.heading), 'elevation': float(meta.elevation or 0),
         'date': str(meta.date) if meta.date else None}
    json.dump(m, open(mp, 'w')); return m, img


def cmd_select(src, dst, radius):
    cfg = json.load(open(src))
    for s in cfg['spots']:
        if 'E' not in s: continue
        lat, lon = geo.en_to_wgs84(s['E'], s['N'])
        p = sv().find_panorama(lat, lon, radius=radius)
        if p is None:
            print(s['id'], 'no pano within', radius, 'm'); continue
        e, n = geo.wgs84_to_en(p.lat, p.lon)
        d = geo.distance(e, n, s['E'], s['N'])
        hd = geo.true_to_grid_bearing(e, n, math.degrees(p.heading)) % 360
        s.update(E=round(e, 2), N=round(n, 2), x=round(e - 23459500, 2), z=round(6704500 - n, 2), heading=round(hd, 1),
                 pano={'id': p.id, 'date': str(p.date) if p.date else None, 'elevation': round(float(p.elevation or 0), 2), 'moved_m': round(d, 1)})
        print(f"{s['id']:24s} pano {p.id} {p.date} moved {d:5.1f} m heading {hd:5.1f}")
    json.dump(cfg, open(dst, 'w'), indent=1, ensure_ascii=False)


def cmd_refs(run):
    run = Path(run); poses = json.load(open(run / 'poses.json'))
    spots = {s['id']: s for s in json.load(open(run / 'spots.json'))['spots']} if (run / 'spots.json').exists() else {}
    out = run / 'ref'; out.mkdir(exist_ok=True); index = {}
    for sid, rec in poses.items():
        pid = (spots.get(sid, {}).get('pano') or {}).get('id')
        if not pid or not rec.get('views'): continue
        meta, eq = equirect(pid)
        heading_grid = geo.true_to_grid_bearing(meta['E'], meta['N'], meta['heading_true'])
        w, h = Image.open(run / f'{sid}-h0-color.png').size if (run / f'{sid}-h0-color.png').exists() else (960, 540)
        for v in rec['views']:
            if not v.get('street'): continue
            # the game camera is at the pano position; pitch/yaw/fov as captured
            pose = {'E': meta['E'], 'N': meta['N'], 'H': meta['elevation'], 'yaw': v['yaw'], 'pitch': v['pitch'], 'roll': 0, 'fov': v['fov']}
            f = out / f"{sid}-{v['view']}.jpg"
            perspective(eq, heading_grid, pose, w, h).save(f, quality=90)
            index[f'{sid}-{v["view"]}'] = {'ref': str(f.relative_to(run)), 'game': f"{sid}-{v['view']}-color.png", 'pano': pid,
                                            'date': meta['date'], 'offset_m': round(geo.distance(meta['E'], meta['N'], v['E'], v['N']), 2)}
        print(sid, pid, meta['date'])
    json.dump(index, open(out / 'index.json', 'w'), indent=1)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['select']: cmd_select(a[1], a[2], float(a[a.index('--radius') + 1]) if '--radius' in a else 60)
    elif a[:1] == ['refs']: cmd_refs(a[1])
    else: print(__doc__); sys.exit(2)

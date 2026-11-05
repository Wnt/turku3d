#!/usr/bin/env python3
"""Visual patrol detectors (tools/patrol/README.md): turn one capture folder into candidate findings.

  ~/turku3d/envs/pipeline/bin/python tools/patrol/analyze.py RUN_DIR

Reads the colour and id passes written by capture.mjs and writes
  RUN_DIR/candidates.json   every detector hit with metrics, image, coordinates, suspected cause and owner area
  RUN_DIR/crops/*.png        an annotated image per candidate (the judge looks at these)
  RUN_DIR/spots/*.png        per-spot contact sheets (stills, temporal maps, ground grid)
Detectors: temporal shimmer and wander (still camera), see-through holes (id pass), pits and ditches (collision
grid), blank facades and block-LOD shells at street level, population blobs drawn flat white (unlit cars), and on
drives LOD pops (isolated block-difference spikes) and stutter (frame intervals).
"""
import json, math, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

E0, N0 = 23459500, 6704500
RUN = Path(sys.argv[1])
CROPS = RUN / 'crops'; CROPS.mkdir(exist_ok=True)
SHEETS = RUN / 'spots'; SHEETS.mkdir(exist_ok=True)
spots_cfg = {s['id']: s for s in json.load(open(Path(__file__).with_name('spots.json')))['spots']}
poses = json.load(open(RUN / 'poses.json'))
cands = []

OWNER = {
    'shimmer:vegetation': ('vegetation / TAA velocity (foliagestable, treelook)', 'alpha-tested leaves without a converged TRAA history'),
    'shimmer:building': ('facades / TAA', 'facade texture or thin-geometry aliasing that TRAA does not resolve'),
    'shimmer:ground': ('ground surfaces / TAA', 'ground texture aliasing, z-fighting decals or coplanar layers'),
    'shimmer:water': ('water (SSR reflection)', 'reflection resolve noise on a still camera'),
    'shimmer:other': ('structures / props', 'coplanar or thin geometry flicker'),
    'shimmer:sky': ('sky', 'animated clouds (expected unless blocky)'),
    'wander': ('TAA / camera', 'static scene drifts over the sequence (history reprojection or LOD dissolve)'),
    'hole': ('ground QA / bridges (groundqa, roadfix)', 'missing ground or deck triangles: the sky shows through'),
    'pit': ('ground generator (lidar terrain)', 'collision and visual ground follow a lidar pit or trench where the street is flat'),
    'blank-facade': ('facades', 'facade drawn without windows (glass share near zero)'),
    'lod-shell': ('streaming / LOD (lodflicker, perfstream)', 'block-LOD shell drawn where the HD building should be'),
    'white-car': ('cars (carpaint)', 'car drawn flat white: unlit or unshaded program'),
    'pop': ('streaming / LOD fades (lodfade, lodflicker)', 'geometry appears or swaps within one frame while driving'),
    'stutter': ('frame pacing (framepace, perfstream)', 'long frames while driving'),
    'not-ready': ('streaming / tile service', 'the spot did not settle (QA ready timeout)'),
}


def load_rgb(p):
    return np.asarray(Image.open(p).convert('RGB'), dtype=np.float32)


def load_ids(p):
    a = np.asarray(Image.open(p).convert('RGB'), dtype=np.int64)
    return a[..., 0] << 16 | a[..., 1] << 8 | a[..., 2]


def luma(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def classes(ids, table):
    """Per-pixel class from the id pass: sky (0), vegetation, building, ground, water, other (incl. unlabelled)."""
    kinds = {'sky': ids == 0}
    lut = {}
    for k, e in table.items():
        kind = e['kind']
        if e.get('layer') == 'building_lod' or e.get('id') == 'block-lod-tile': kind = 'lod'
        lut[int(k)] = kind
    flat = ids.ravel(); u, inv = np.unique(flat, return_inverse=True)
    names = np.array([('sky' if v == 0 else lut.get(int(v), 'other')) for v in u])[inv].reshape(ids.shape)
    for k in ('vegetation', 'building', 'ground', 'water', 'prop', 'other', 'lod'):
        kinds[k] = names == k
    return kinds, names


def horizon_row(h, pitch, fov):
    f = (h / 2) / math.tan(math.radians(fov) / 2)
    return h / 2 + f * math.tan(math.radians(pitch))


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None


def annotate(img_path, box, name, extra=None, label=''):
    im = Image.open(img_path).convert('RGB')
    if extra is not None: im = Image.fromarray(np.concatenate([np.asarray(im), np.asarray(extra.convert('RGB').resize(im.size))], axis=1))
    d = ImageDraw.Draw(im)
    if box:
        x0, y0, x1, y1 = box; pad = 6
        d.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(255, 0, 255), width=3)
    if label: d.text((8, 8), label, fill=(255, 0, 255))
    out = CROPS / f'{name}.png'; im.save(out)
    return str(out.relative_to(RUN))


def heatmap(a, scale):
    return Image.fromarray(np.clip(a * scale, 0, 255).astype(np.uint8)).convert('RGB')


def add(spot, view, kind, sub, metrics, image, pose, severity_hint, centre, street, note=''):
    owner, cause = OWNER.get(f'{kind}:{sub}' if sub else kind, OWNER.get(kind, ('?', '?')))
    cands.append({'n': len(cands) + 1, 'spot': spot, 'view': view, 'detector': kind, 'class': sub, 'metrics': metrics,
                  'image': image, 'E': round(pose['E'], 2), 'N': round(pose['N'], 2), 'H': round(pose['H'], 2),
                  'x': round(pose['E'] - E0, 2), 'z': round(-(pose['N'] - N0), 2), 'yaw': pose.get('yaw'), 'pitch': pose.get('pitch'),
                  'cam': ','.join(str(round(v, 2)) for v in (pose['E'], pose['N'], pose['H'], pose.get('yaw', 0), pose.get('pitch', 0), pose.get('fov', 60))),
                  'auto_severity': severity_hint, 'centre': centre, 'street': street, 'owner': owner, 'cause': cause, 'note': note})


for sid, rec in poses.items():
    cfg = spots_cfg.get(sid, {}); centre = cfg.get('centre', 0.5)
    views = {v['view']: v for v in rec.get('views', [])}
    if rec.get('error'):
        print(sid, 'capture error', rec['error']); continue
    for r in rec.get('ready', []):
        if r.get('err') and 'view' in r:
            add(sid, r['view'], 'not-ready', None, {'err': r['err'][:200], 'ms': r.get('ms')}, None, views.get(r['view'], views.get('h0')), 2, centre, True)
    sheet = []
    for vname, v in views.items():
        cp, ip = RUN / f'{sid}-{vname}-color.png', RUN / f'{sid}-{vname}-id.png'
        tp = RUN / (rec.get('idTable') or f'{sid}-idtable.json')
        if not cp.exists(): continue
        sheet.append(cp)
        if not ip.exists() or not tp.exists(): continue
        rgb, ids, table = load_rgb(cp), load_ids(ip), json.load(open(tp))
        h, w = ids.shape
        kinds, names = classes(ids, table)
        hz = horizon_row(h, v['pitch'], v['fov'])
        cam_h = max(1.0, v['H'] - (rec.get('ground') or v['H'] - 1.7))
        # --- holes: id 0 clearly below the horizon (closer than ~300 m on flat ground), sky-coloured in the colour pass
        dep = math.atan(cam_h / 300.0); f = (h / 2) / math.tan(math.radians(v['fov']) / 2)
        row0 = hz + f * math.tan(dep) + 0.01 * h
        rows = np.arange(h)[:, None] * np.ones((1, w))
        sky_ref = rgb[:max(4, int(min(h * 0.08, max(hz, 4))))][kinds['sky'][:max(4, int(min(h * 0.08, max(hz, 4))))]]
        skyc = sky_ref.mean(axis=0) if len(sky_ref) else np.array([200, 215, 230], np.float32)
        hole = kinds['sky'] & (rows > row0)
        lab, nlab = ndi.label(hole, structure=np.ones((3, 3)))
        for i, sl in enumerate(ndi.find_objects(lab), start=1):
            m = lab[sl] == i; area = int(m.sum())
            if area < 0.0004 * h * w: continue
            col = rgb[sl][m]; dist = float(np.linalg.norm(col.mean(axis=0) - skyc)); bright = float((col.min(axis=1) > 215).mean())
            skylike = dist < 45 or bright > 0.5
            box = [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop]
            near = (box[3] - hz) / h  # how far below the horizon (0..1): closer to the camera when larger
            add(sid, vname, 'hole', None, {'area_px': area, 'area_share': round(area / (h * w), 4), 'sky_dist': round(dist, 1),
                'bright_share': round(bright, 2), 'skylike': skylike, 'below_horizon': round(near, 2)},
                annotate(cp, box, f'{sid}-{vname}-hole{i}', extra=Image.fromarray((hole * 255).astype(np.uint8)), label=f'hole {area}px'),
                v, 4 if skylike else 1, centre, v['street'])
        # --- block-LOD shells at street level: the HD building is missing near the street
        if v['street']:
            lod = kinds['lod'] & (rows > hz - 0.25 * h)
            share = float(lod.mean())
            if share > 0.03:
                add(sid, vname, 'lod-shell', None, {'share': round(share, 3)}, annotate(cp, bbox(lod), f'{sid}-{vname}-lod', label=f'block LOD {share:.1%}'), v, 2, centre, True)
        # --- blank facades: big building surfaces with almost no window-like pixels
        if v['street']:
            L = luma(rgb); b_r = rgb[..., 2] - rgb[..., 0]
            u, cnt = np.unique(ids[kinds['building']], return_counts=True)
            for bid, c in zip(u, cnt):
                if c < 0.05 * h * w: continue
                m = ids == bid; e = table.get(str(int(bid)), {})
                if e.get('material') not in ('facade_kit', 'component_wall', 'component_slab', 'landmark_procedural'): continue
                med = float(np.median(L[m]))
                glass = ((L < 0.72 * med) | ((b_r > 14) & (L < med))) & m
                share = float(glass.sum() / c)
                if share < 0.035:
                    add(sid, vname, 'blank-facade', None, {'glass_share': round(share, 3), 'area_share': round(c / (h * w), 3), 'building': e.get('building'), 'material': e.get('material')},
                        annotate(cp, bbox(m), f'{sid}-{vname}-blank{bid}', label=f'glass {share:.1%}'), v, 2, centre, True)
        # --- unlit cars: population blobs (colour with vs without population) that are flat near-white
        npp = RUN / f'{sid}-{vname}-nopop.png'
        if npp.exists():
            diff = np.abs(rgb - load_rgb(npp)).max(axis=2) > 18
            diff = ndi.binary_opening(diff, iterations=1)
            lab2, n2 = ndi.label(diff)
            for i, sl in enumerate(ndi.find_objects(lab2), start=1):
                m = lab2[sl] == i; area = int(m.sum())
                if area < 250: continue
                col = rgb[sl][m]; Lb = luma(col[None])[0]
                white = float((col.min(axis=1) > 205).mean()); std = float(Lb.std())
                if white > 0.55 and std < 14:
                    box = [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop]
                    add(sid, vname, 'white-car', None, {'white_share': round(white, 2), 'luma_std': round(std, 1), 'area_px': area},
                        annotate(cp, box, f'{sid}-{vname}-white{i}', label='flat white blob'), v, 3, centre, True)
    # --- temporal (still camera) at h0, classes from the h0 id image
    seq = rec.get('sequence'); v0 = views.get('h0')
    if seq and v0 and (RUN / f'{sid}-h0-id.png').exists():
        h, w = seq['h'], seq['w']
        d1 = np.fromfile(RUN / f'{sid}-seq-d1.f32', np.float32).reshape(h, w)
        d2 = np.fromfile(RUN / f'{sid}-seq-d2.f32', np.float32).reshape(h, w)
        dn = np.fromfile(RUN / f'{sid}-seq-dn.f32', np.float32).reshape(h, w)
        ids = load_ids(RUN / f'{sid}-h0-id.png'); table = json.load(open(RUN / f'{sid}-idtable.json'))
        kinds, names = classes(ids, table)
        B = 24; gh, gw = h // B, w // B
        blk = lambda a: a[:gh * B, :gw * B].reshape(gh, B, gw, B).mean(axis=(1, 3))
        b2, bn = blk(d2), blk(dn)
        per = {k: {'share': round(float(m.mean()), 3), 'd2': round(float(d2[m].mean()), 2) if m.any() else None,
                   'dn': round(float(dn[m].mean()), 2) if m.any() else None} for k, m in kinds.items()}
        rec['temporal'] = per
        dom = lambda sl: max(((k, int(m[sl].sum())) for k, m in kinds.items()), key=lambda t: t[1])[0]
        for kind_name, bmap, thr, other, label in (('shimmer', b2, 2.5, None, 'd2'), ('wander', bn, 9.0, b2, 'dn')):
            hot = bmap > thr
            if other is not None: hot &= other < 1.5
            lab, n = ndi.label(hot)
            for i, sl in enumerate(ndi.find_objects(lab), start=1):
                m = lab[sl] == i; nb = int(m.sum())
                if nb < 3: continue
                box = [sl[1].start * B, sl[0].start * B, sl[1].stop * B, sl[0].stop * B]
                psl = (slice(box[1], box[3]), slice(box[0], box[2]))
                sub = dom(psl)
                if sub == 'sky' and kind_name == 'shimmer': sub = 'sky'
                val = float(bmap[sl][m].mean())
                sev = 1 if sub == 'sky' else (3 if val > 2 * thr else 2)
                img = annotate(RUN / f'{sid}-seq-f00.png', box, f'{sid}-{kind_name}{i}', extra=heatmap(d2 if label == 'd2' else dn, 25 if label == 'd2' else 8), label=f'{kind_name} {label}={val:.1f}')
                add(sid, 'h0', kind_name, sub if kind_name == 'shimmer' else None, {'blocks': nb, label: round(val, 2), 'class': sub, 'frames': seq['frames']}, img, v0, sev, centre, True)
    # --- pits and ditches in the collision grid
    gp = RUN / f'{sid}-ground.json'
    if gp.exists() and v0:
        g = json.load(open(gp)); a = np.array([[np.nan if y is None else y for y in r] for r in g['rows']], np.float32)
        n = a.shape[0]; yy, xx = np.mgrid[:n, :n]; rr = np.hypot(yy - n // 2, xx - n // 2) * g['step']
        ok = np.isfinite(a) & (rr <= 30)
        if ok.sum() > 50:
            med = float(np.median(a[ok])); low = ok & (a < med - 1.0)
            lab, nl = ndi.label(low)
            for i, sl in enumerate(ndi.find_objects(lab), start=1):
                m = lab[sl] == i; area = float(m.sum() * g['step'] ** 2)
                if area < 24: continue
                depth = float(med - np.nanmin(a[sl][m]))
                cy, cx = [float(c) for c in ndi.center_of_mass(lab == i)]
                dx, dz = (cx - n // 2) * g['step'], (cy - n // 2) * g['step']
                pose = dict(v0, E=v0['E'] + dx, N=v0['N'] - dz)
                vis = Image.fromarray(np.nan_to_num(np.clip((a - med + 3) * 40, 0, 255)).astype(np.uint8)).resize((240, 240), Image.NEAREST)
                img = annotate(RUN / f'{sid}-raised-color.png', None, f'{sid}-pit{i}', extra=vis, label=f'pit {area:.0f} m2, {depth:.1f} m below median')
                cam_in = bool(lab[n // 2, n // 2] == i)
                add(sid, 'raised', 'pit', None, {'area_m2': round(area), 'depth_m': round(depth, 2), 'median_ground': round(med, 2), 'camera_inside': cam_in,
                    'centroid_dx_dz': [round(dx, 1), round(dz, 1)]}, img, pose, 3 if depth > 1.5 and area > 60 else 2, centre, True)
    # contact sheet
    if sheet:
        ims = [Image.open(p).convert('RGB').resize((480, 270)) for p in sheet]
        for extra in ('seq-d2', 'seq-dn'):
            fp = RUN / f'{sid}-{extra}.f32'
            if fp.exists() and seq: ims.append(heatmap(np.fromfile(fp, np.float32).reshape(seq['h'], seq['w']), 25 if 'd2' in extra else 8).resize((480, 270)))
        cols = 3; rows_n = math.ceil(len(ims) / cols)
        S = Image.new('RGB', (480 * cols, 270 * rows_n))
        for k, im in enumerate(ims): S.paste(im, ((k % cols) * 480, (k // cols) * 270))
        S.save(SHEETS / f'{sid}.png')

# --- drives: pops and stutter
for tr in sorted(RUN.glob('drive-*/trace.json')):
    d = json.load(open(tr)); did = d['id']; B = np.array(d['blocks'][1:], np.float32)  # frames x 336 blocks
    iv = np.array(d['iv'][2:], np.float32); P = d['poses']
    if len(B) < 20: continue
    T = len(B); events = []
    for t in range(3, T - 3):
        ctx = np.concatenate([B[max(0, t - 8):t - 1], B[t + 2:t + 9]])
        base = np.median(ctx, axis=0) + 1.5
        ratio = B[t] / base
        hot = (ratio > 4) & (B[t] > 14)
        if hot.sum() >= 2:
            events.append((t + 1, int(hot.sum()), float(B[t][hot].mean()), np.nonzero(hot)[0].tolist()))
    # merge consecutive frames into one event
    merged = []
    for e in events:
        if merged and e[0] - merged[-1][0] <= 2: merged[-1] = max(merged[-1], e, key=lambda q: q[1]); continue
        merged.append(e)
    med_iv = float(np.median(iv)) if len(iv) else 0
    hitches = int((iv > max(3 * med_iv, 100)).sum()) if len(iv) else 0
    d['analysis'] = {'pops': len(merged), 'iv_p50': round(med_iv, 1), 'iv_p95': round(float(np.percentile(iv, 95)), 1) if len(iv) else None,
                     'iv_max': round(float(iv.max()), 1) if len(iv) else None, 'hitches': hitches}
    for k, (t, nb, val, blocks) in enumerate(sorted(merged, key=lambda q: -q[1])[:4]):
        fr = [tr.parent / f'f{t + o:04d}.jpg' for o in (-1, 0, 1)]
        if not all(p.exists() for p in fr): continue
        ims = [Image.open(p).convert('RGB') for p in fr]
        S = Image.new('RGB', (384 * 3, 216)); [S.paste(im, (384 * j, 0)) for j, im in enumerate(ims)]
        dr = ImageDraw.Draw(S)
        for bi in blocks:
            by, bx = divmod(bi, 24)
            for j in range(3): dr.rectangle([384 * j + bx * 16, by * 15.4, 384 * j + bx * 16 + 16, by * 15.4 + 15.4], outline=(255, 0, 255))
        out = CROPS / f'{did}-pop{k}.png'; S.save(out)
        x, z, y = P[min(t, len(P) - 1)]
        pose = {'E': x + E0, 'N': N0 - z, 'H': y + 1.5, 'yaw': None, 'pitch': -2, 'fov': 60}
        add(did, f'frame{t}', 'pop', None, {'frame': t, 'blocks': nb, 'diff': round(val, 1), 'pops_in_drive': len(merged)}, str(out.relative_to(RUN)), pose, 2, 0.9, True)
    if hitches:
        x, z, y = P[len(P) // 2]
        add(did, 'all', 'stutter', None, d['analysis'], None, {'E': x + E0, 'N': N0 - z, 'H': y + 1.5, 'yaw': None, 'pitch': -2, 'fov': 60}, 1 + (hitches > 5), 0.9, True)
    json.dump(d['analysis'], open(tr.parent / 'analysis.json', 'w'))
    print(did, d['analysis'])

json.dump(cands, open(RUN / 'candidates.json', 'w'), indent=1)
json.dump({k: v.get('temporal') for k, v in poses.items()}, open(RUN / 'temporal.json', 'w'), indent=1)
from collections import Counter
print(len(cands), 'candidates', dict(Counter(c['detector'] for c in cands)))

"""Perpendicular ground profiles across kantakartta lines (default: 62520 carriageway edges) from a LAZ tile.
usage: curb_profile.py tile.laz liikennevayla.gml [halflen=4] [corridor=0.25] [bin=0.1]"""
import sys, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
laz, gml = sys.argv[1], sys.argv[2]
HL = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
CW = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25
BIN = float(sys.argv[5]) if len(sys.argv) > 5 else 0.1
las = laspy.read(laz)
cls = np.asarray(las.classification)
X, Y, Z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
xmin, ymin, xmax, ymax = X.min(), Y.min(), X.max(), Y.max()
import os
if os.environ.get('BBOX'): xmin, ymin, xmax, ymax = map(float, os.environ['BBOX'].split(','))
# ground = class 2 plus class-12 (overlap) points within 0.15 m of local class-2 min (overlap strips hold ground too)
g = cls == 2
kd = cKDTree(np.c_[X[g], Y[g]])
o = np.where(cls == 12)[0]
d, i = kd.query(np.c_[X[o], Y[o]], k=8, distance_upper_bound=0.5)
ok = np.isfinite(d).all(1)
gz = Z[g]
med = np.full(len(o), np.inf); med[ok] = np.median(gz[np.minimum(i[ok], len(gz) - 1)], 1)
o_ground = o[np.abs(Z[o] - med) < 0.08]
sel = np.r_[np.where(g)[0], o_ground]
print(f'ground pts: class2 {g.sum()}, +class12 near-ground {len(o_ground)}')
GX, GY, GZ, GC = X[sel], Y[sel], Z[sel], cls[sel]
gkd = cKDTree(np.c_[GX, GY])
root = ET.parse(gml).getroot()
lines = []
for f in root.iter():
    if f.tag.endswith('}Liikennevayla') or f.tag.endswith('}Rakenne') or f.tag.endswith('}Kevyenliikenteenvayla'):
        code = next((c.text for c in f if c.tag.endswith('kplkoodi')), None)
        co = [e.text for e in f.iter() if e.tag.endswith('coordinates')]
        if co and code in sys.argv[6:] if len(sys.argv) > 6 else code == '62520':
            pts = np.array([[float(v) for v in p.split(',')[:2]] for p in co[0].split()])
            if len(pts) > 1: lines.append((code, pts))
print(len(lines), 'lines')
res = []
for code, pts in lines:
    for a, b in zip(pts[:-1], pts[1:]):
        L = np.linalg.norm(b - a)
        if L < 3: continue
        t = (b - a) / L; n = np.array([-t[1], t[0]])
        for s in np.arange(1.0, L - 1.0, 2.0):
            c = a + t * s
            if not (xmin + HL < c[0] < xmax - HL and ymin + HL < c[1] < ymax - HL): continue
            idx = gkd.query_ball_point(c, HL + 0.5)
            if not idx: continue
            idx = np.array(idx)
            d = np.c_[GX[idx], GY[idx]] - c
            u = d @ n; v = d @ t
            m = (np.abs(v) < CW) & (np.abs(u) < HL)
            if m.sum() < 40: continue
            u, z = u[m], GZ[idx][m]
            bins = np.floor(u / BIN).astype(int)
            prof = {}
            for k in np.unique(bins):
                zz = z[bins == k]
                if len(zz) >= 2: prof[k] = np.median(zz)
            # step = median z on side +0.3..+1.5 minus side -1.5..-0.3
            sp = z[(u > 0.3) & (u < 1.5)]; sn = z[(u < -0.3) & (u > -1.5)]
            if len(sp) < 8 or len(sn) < 8: continue
            # detrend: fit slope on each side separately and evaluate at 0
            def at0(uu, zz):
                A = np.c_[uu, np.ones_like(uu)]; k, c0 = np.linalg.lstsq(A, zz, rcond=None)[0]; return c0, np.std(zz - (A @ [k, c0]))
            zp, rp = at0(u[(u > 0.2) & (u < 1.5)], z[(u > 0.2) & (u < 1.5)])
            zn, rn = at0(u[(u < -0.2) & (u > -1.5)], z[(u < -0.2) & (u > -1.5)])
            res.append(dict(code=code, x=c[0], y=c[1], step=zp - zn, noise=max(rp, rn), n=int(m.sum()), prof=prof))
steps = np.array([r['step'] for r in res]); noise = np.array([r['noise'] for r in res])
res = [r for r in res if abs(r['step']) < 0.5]
import collections
print('histogram |step| cm (5 cm bins):', sorted(collections.Counter((np.abs([r['step'] for r in res])*100//5*5).astype(int).tolist()).items()))
steps = np.array([r['step'] for r in res]); noise = np.array([r['noise'] for r in res])
print(f'{len(res)} profiles; |step| median {np.median(np.abs(steps))*100:.1f} cm, p25 {np.percentile(np.abs(steps),25)*100:.1f}, p75 {np.percentile(np.abs(steps),75)*100:.1f}; '
      f'residual noise median {np.median(noise)*100:.1f} cm; fraction |step|>=7cm: {(np.abs(steps)>=0.07).mean():.2f}')
for r in sorted(res, key=lambda r: -abs(r['step']))[:3] + sorted(res, key=lambda r: abs(r['step']-np.median(steps)))[:2]:
    print(f"profile at {r['x']:.2f},{r['y']:.2f} step {r['step']*100:+.1f} cm noise {r['noise']*100:.1f} cm n={r['n']}")
    ks = sorted(r['prof'])
    print('   u(m):z  ' + ' '.join(f"{k*BIN:+.1f}:{r['prof'][k]:.3f}" for k in ks if -1.6 <= k*BIN <= 1.6))

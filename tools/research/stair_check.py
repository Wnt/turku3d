"""Count kanta:Rakenne codes in a GML; profile lidar ground along the stair whose 66618 step lines are densest.
usage: stair_check.py rakenne.gml tile.laz"""
import sys, collections, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
feats = []
for f in ET.parse(sys.argv[1]).getroot().iter():
    if not f.tag.endswith('}Rakenne'): continue
    code = next((x.text for x in f if x.tag.endswith('kplkoodi')), None)
    typ = next((x.text for x in f if x.tag.endswith('tyyppi')), None)
    co = next((e.text for e in f.iter() if e.tag.endswith('coordinates')), None)
    if co: feats.append((code, typ, np.array([[float(v) for v in p.split(',')[:2]] for p in co.split()])))
print('kanta:Rakenne codes:', collections.Counter((c, t) for c, t, _ in feats).most_common())
steps = [p for c, _, p in feats if c == '66618' and len(p) == 2 or (c == '66618' and len(p) > 1)]
mids = np.array([p.mean(0) for p in steps])
kd = cKDTree(mids); cnt = np.array([len(kd.query_ball_point(m, 4)) for m in mids])
best = mids[cnt.argmax()]; grp = [steps[i] for i in kd.query_ball_point(best, 4)]
print(f'densest stair at {best[0]:.1f},{best[1]:.1f}: {len(grp)} step-edge lines within 4 m')
d = grp[0][-1] - grp[0][0]; t = d / np.linalg.norm(d); n = np.array([-t[1], t[0]])
las = laspy.read(sys.argv[2]); X, Y, Z, C = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
k = (np.abs(X - best[0]) < 8) & (np.abs(Y - best[1]) < 8)
u = (np.c_[X[k], Y[k]] - best) @ n; v = (np.c_[X[k], Y[k]] - best) @ t; z = Z[k]; c = C[k]
sel = (np.abs(v) < 0.5) & (np.abs(u) < 5)
print('classes on stair strip:', collections.Counter(c[sel].tolist()).most_common())
pos = sorted(((p.mean(0) - best) @ n) for p in grp)
print('step-edge positions along stair (m):', ' '.join(f'{p:+.2f}' for p in pos))
gs = sel & np.isin(c, [2, 12, 1])
b = np.floor(u[gs] / 0.1).astype(int)
prof = {i: np.percentile(z[gs][b == i], 20) for i in np.unique(b) if (b == i).sum() >= 2}
print('profile u:z (10 cm bins, 20th pct of classes 1/2/12):')
print(' '.join(f'{i/10:+.1f}:{prof[i]:.2f}' for i in sorted(prof)))

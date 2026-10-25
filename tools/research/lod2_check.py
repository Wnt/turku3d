"""Compare CityGML LOD2 RoofSurface heights with lidar class-6 (building) points.
usage: lod2_check.py bldg.gml tile.laz"""
import sys, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
G = '{http://www.opengis.net/gml}'
rng = np.random.default_rng(0); samples = []
nb = 0
for b in ET.parse(sys.argv[1]).getroot().iter('{http://www.opengis.net/citygml/building/2.0}Building'):
    nb += 1
    for rs in b.iter('{http://www.opengis.net/citygml/building/2.0}RoofSurface'):
        for poly in rs.iter(G + 'Polygon'):
            ext = poly.find(f'{G}exterior/{G}LinearRing')
            pl = ext.find(G + 'posList')
            if pl is not None:
                v = np.array(pl.text.split(), float).reshape(-1, 3)
            else:
                v = np.array([[float(t) for t in p.text.split()] for p in ext.iter(G + 'pos')])
            if len(v) < 4: continue
            for i in range(1, len(v) - 2):   # triangle fan
                a, bb, c = v[0], v[i], v[i + 1]
                area = np.linalg.norm(np.cross(bb - a, c - a)) / 2
                for _ in range(int(min(5, area / 10)) + (1 if area > 2 else 0)):
                    r1, r2 = rng.random(2)
                    if r1 + r2 > 1: r1, r2 = 1 - r1, 1 - r2
                    samples.append(a + r1 * (bb - a) + r2 * (c - a))
S = np.array(samples)
las = laspy.read(sys.argv[2]); m = np.asarray(las.classification) == 6
X, Y, Z = np.asarray(las.x)[m], np.asarray(las.y)[m], np.asarray(las.z)[m]
kd = cKDTree(np.c_[X, Y])
inside = (S[:, 0] > X.min() + 1) & (S[:, 0] < X.max() - 1) & (S[:, 1] > Y.min() + 1) & (S[:, 1] < Y.max() - 1)
d = []
for s in S[inside]:
    i = kd.query_ball_point(s[:2], 0.4)
    if len(i) >= 3: d.append(s[2] - np.median(Z[i]))
d = np.array(d)
print(f'{nb} buildings, {len(S)} roof samples, {len(d)} with lidar: median {np.median(d)*100:+.1f} cm, MAD {np.median(np.abs(d - np.median(d)))*100:.1f} cm, '
      f'|d|<0.3 m {np.mean(np.abs(d) < 0.3):.2f}, |d|<1 m {np.mean(np.abs(d) < 1):.2f}')

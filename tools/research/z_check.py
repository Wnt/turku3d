"""Compare kantakartta Korkeuspiste spot heights (and maanpinta key points) against lidar class-2 ground.
usage: z_check.py tile.laz korkeuspiste.gml maanpinta.laz"""
import sys, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
las = laspy.read(sys.argv[1]); c = np.asarray(las.classification) == 2
X, Y, Z = np.asarray(las.x)[c], np.asarray(las.y)[c], np.asarray(las.z)[c]
kd = cKDTree(np.c_[X, Y])
def gz(x, y, r=0.3):
    i = kd.query_ball_point([x, y], r)
    return (np.median(Z[i]), len(i)) if len(i) >= 3 else (np.nan, len(i))
d = []
for f in ET.parse(sys.argv[2]).getroot().iter():
    if not f.tag.endswith('}Korkeuspiste'): continue
    k = next((x.text for x in f if x.tag.endswith('korkeus')), None)
    p = next((e.text for e in f.iter() if e.tag.endswith('coordinates')), None)
    pv = next((x.text for x in f if x.tag.endswith('alkuPvm')), '')
    if k and p:
        x, y = map(float, p.split(',')[:2]); z, n = gz(x, y)
        if np.isfinite(z): d.append((float(k) - z, pv[:4]))
d = np.array([v for v, _ in d])
if len(d): print(f'Korkeuspiste vs lidar ground (r 0.3 m): n={len(d)} median {np.median(d)*100:+.1f} cm, MAD {np.median(np.abs(d-np.median(d)))*100:.1f} cm, '
               f'|d|<5cm {np.mean(np.abs(d)<0.05):.2f}, |d|<10cm {np.mean(np.abs(d)<0.10):.2f}')
m = laspy.read(sys.argv[3]); mx, my, mz = np.asarray(m.x), np.asarray(m.y), np.asarray(m.z)
r = [mz[i] - gz(mx[i], my[i], 0.2)[0] for i in range(0, len(mx), 20)]
r = np.array([v for v in r if np.isfinite(v)])
print(f'maanpinta key points vs class-2 (r 0.2 m): n={len(r)} median {np.median(r)*100:+.1f} cm, MAD {np.median(np.abs(r-np.median(r)))*100:.1f} cm')

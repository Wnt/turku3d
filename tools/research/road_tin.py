"""Analyse CityGML tran:Road TIN triangles: steep faces (curbs?), edge lengths, z vs lidar ground.
usage: road_tin.py road.gml [tile.laz]"""
import sys, re, numpy as np, xml.etree.ElementTree as ET, collections
NS = '{http://www.opengis.net/gml}'
tris, attrs = [], collections.Counter()
for road in ET.parse(sys.argv[1]).getroot().iter('{http://www.opengis.net/citygml/transportation/2.0}Road'):
    k = next((s.find('{http://www.opengis.net/citygml/generics/2.0}value').text for s in road.iter('{http://www.opengis.net/citygml/generics/2.0}stringAttribute') if s.get('name') == 'katuosalaji'), '?')
    attrs[k] += 1
    for t in road.iter(NS + 'Triangle'):
        p = np.array([[float(v) for v in e.text.split()] for e in t.iter(NS + 'pos')])
        if len(p) >= 3: tris.append((k, p[:3]))
print('roads by katuosalaji', dict(attrs), 'triangles', len(tris))
P = np.array([p for _, p in tris])
n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]); area = np.linalg.norm(n, axis=1) / 2
nz = np.abs(n[:, 2]) / np.maximum(np.linalg.norm(n, axis=1), 1e-9)
edges = np.r_[np.linalg.norm(P[:, 1]-P[:, 0], axis=1), np.linalg.norm(P[:, 2]-P[:, 1], axis=1), np.linalg.norm(P[:, 0]-P[:, 2], axis=1)]
print(f'edge length median {np.median(edges):.2f} m p10 {np.percentile(edges,10):.2f} p90 {np.percentile(edges,90):.2f}; area total {area.sum():.0f} m2')
steep = nz < 0.6
print(f'steep triangles (slope > 53 deg): {steep.sum()} ({area[steep].sum():.1f} m2); slope>20%: {(nz < 0.98).sum()}')
dz = P[:, :, 2].max(1) - P[:, :, 2].min(1)
print('triangle z-range: median %.3f, p99 %.3f' % (np.median(dz), np.percentile(dz, 99)))
if len(sys.argv) > 2:
    import laspy; from scipy.spatial import cKDTree
    las = laspy.read(sys.argv[2]); c = np.asarray(las.classification) == 2
    X, Y, Z = np.asarray(las.x)[c], np.asarray(las.y)[c], np.asarray(las.z)[c]
    kd = cKDTree(np.c_[X, Y]); V = P.reshape(-1, 3)
    V = V[(V[:, 0] > X.min()) & (V[:, 0] < X.max()) & (V[:, 1] > Y.min()) & (V[:, 1] < Y.max())][::5]
    d = []
    for v in V:
        i = kd.query_ball_point(v[:2], 0.3)
        if len(i) >= 3: d.append(v[2] - np.median(Z[i]))
    d = np.array(d)
    print(f'TIN vertex z - lidar ground: n={len(d)} median {np.median(d)*100:+.1f} cm MAD {np.median(np.abs(d-np.median(d)))*100:.1f} cm p95 |d| {np.percentile(np.abs(d),95)*100:.1f} cm')

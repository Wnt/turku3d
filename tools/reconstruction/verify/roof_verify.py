"""Vertical residuals of class-6 lidar points against a building GLB (local origin x0,y0; glTF Y-up)."""
import sys, numpy as np, laspy, trimesh, open3d as o3d
laz, glb, x0, y0 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
m = trimesh.load(glb, force='mesh'); V = np.asarray(m.vertices); V = np.c_[V[:, 0], -V[:, 2], V[:, 1]]
print('mesh bounds', V.min(0).round(1), V.max(0).round(1), 'tris', len(m.faces))
L = laspy.read(laz); c = np.asarray(L.classification); k = c == 6
P = np.c_[np.asarray(L.x)[k]-x0, np.asarray(L.y)[k]-y0, np.asarray(L.z)[k]]
P = P[np.random.default_rng(0).choice(len(P), 400000, replace=False)]
zoff = 0.0
if abs(np.median(P[:, 2]) - np.median(V[:, 2])) > 200: print('z offset mismatch?')
sc = o3d.t.geometry.RaycastingScene(); sc.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(np.asarray(m.faces).astype(np.uint32)))
rays = np.c_[P[:, :2], np.full(len(P), 300.), np.zeros((len(P), 2)), -np.ones(len(P))].astype(np.float32)
h = sc.cast_rays(o3d.core.Tensor(rays))['t_hit'].numpy(); hit = np.isfinite(h)
dz = P[hit, 2] - (300. - h[hit]); a = np.abs(dz)
print('hit frac %.2f  |dz| median %.3f  p75 %.3f  p90 %.3f  frac<=0.3m %.2f  signed median %.3f' % (hit.mean(), np.median(a), np.percentile(a, 75), np.percentile(a, 90), (a <= .3).mean(), np.median(dz)))

"""Independent curb check: own lidar step estimator + Open3D raycast profiles across 62520 lines.
usage: curb_verify.py <laz> <Liikennevayla.geojson> <x0> <y0> <size> <glb>..."""
import sys, json, numpy as np, laspy, trimesh, open3d as o3d
from scipy.spatial import cKDTree
from shapely.geometry import shape, box
laz, gj, x0, y0, S = sys.argv[1], sys.argv[2], *map(float, sys.argv[3:6]); glbs = sys.argv[6:]
L = laspy.read(laz); c = np.asarray(L.classification)
X, Y, Z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
k = (c == 2) & (X > x0-2) & (X < x0+S+2) & (Y > y0-2) & (Y < y0+S+2)
P = np.c_[X[k]-x0, Y[k]-y0]; PZ = Z[k]; T = cKDTree(P)
inner = box(3, 3, S-3, S-3)
stations = []
for f in json.load(open(gj))['features']:
    if f['properties'].get('kplkoodi') != '62520': continue
    g = shape(f['geometry'])
    from shapely.affinity import translate
    g = translate(g, -x0, -y0).intersection(inner)
    for part in getattr(g, 'geoms', [g]):
        if part.is_empty or part.length < 2: continue
        for d in np.arange(0.5, part.length-0.5, 1.7):   # different spacing/phase than original (1 m)
            p = np.array(part.interpolate(d).coords[0]); q = np.array(part.interpolate(d+0.1).coords[0])
            t = (q-p)/np.linalg.norm(q-p); n = np.array([-t[1], t[0]])
            stations.append((p, t, n))
print('stations', len(stations))
def lidar_step(p, t, n):
    idx = T.query_ball_point(p, 1.3); 
    if len(idx) < 10: return None
    v = P[idx]-p; a = v@t; o = v@n; z = PZ[idx]; m = np.abs(a) < 0.6
    lo = m & (o > -1.0) & (o < -0.25); hi = m & (o > 0.25) & (o < 1.0)
    if lo.sum() < 4 or hi.sum() < 4: return None
    # side planes: use 30th percentile to reduce vegetation/car noise (different estimator than median)
    return np.percentile(z[hi], 30) - np.percentile(z[lo], 30)
steps = np.array([lidar_step(*s) if lidar_step(*s) is not None else np.nan for s in stations])
ok = ~np.isnan(steps); a = np.abs(steps[ok])
print('lidar |step| median %.3f  p75 %.3f  frac>=8cm %.2f  n %d' % (np.median(a), np.percentile(a, 75), (a >= .08).mean(), ok.sum()))
sel = [i for i in range(len(stations)) if ok[i] and abs(steps[i]) >= 0.08]
print('curb stations >=8cm', len(sel))
import os
if os.environ.get('DUMP'): json.dump([dict(p=stations[i][0].tolist(), n=stations[i][1].tolist() and stations[i][2].tolist(), step=float(steps[i])) for i in sel], open(os.environ['DUMP'],'w'))
off = np.arange(-0.6, 0.6001, 0.01)
res = {}
for gpath in glbs:
    m = trimesh.load(gpath, force='mesh'); V = np.asarray(m.vertices); V = np.c_[V[:, 0], -V[:, 2], V[:, 1]]
    sc = o3d.t.geometry.RaycastingScene(); sc.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(np.asarray(m.faces).astype(np.uint32)))
    W, SL, ST = [], [], []
    for i in sel:
        p, t, n = stations[i]
        xy = p[None]+off[:, None]*n[None]
        rays = np.c_[xy, np.full(len(off), 200.), np.zeros((len(off), 2)), -np.ones(len(off))].astype(np.float32)
        h = sc.cast_rays(o3d.core.Tensor(rays))['t_hit'].numpy(); z = 200.-h
        if not np.all(np.isfinite(z)): continue
        zl, zh = np.median(z[:15]), np.median(z[-15:]); dz = zh-zl
        ST.append(abs(dz-steps[i]))
        if abs(dz) < 0.03: W.append(np.nan); SL.append(np.degrees(np.arctan(np.max(np.abs(np.diff(z)))/0.01))); continue
        f = (z-zl)/dz; i10 = np.argmax(f >= 0.1); i90 = np.argmax(f >= 0.9)
        W.append(abs(off[i90]-off[i10])); SL.append(np.degrees(np.arctan(np.max(np.abs(np.diff(z)))/0.01)))
    W = np.array(W); r = dict(n=len(W), width_median=float(np.nanmedian(W)), frac_le_15cm=float(np.nanmean(W <= 0.15)),
         maxslope_median=float(np.median(SL)), step_err_median=float(np.median(ST)), tris=len(m.faces))
    res[gpath.split('/')[-1]] = r; print(gpath.split('/')[-1], r)

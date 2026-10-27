#!/usr/bin/env python3
"""Curb-preserving ground mesh experiment for one 100x100 m patch.

Usage: ground_mesh.py <laz> <vectors_dir> <x0> <y0> <size> <outdir>

Steps
 1. read LAZ (laspy+lazrs), crop patch + margin, class histogram
 2. step analysis per vector line class over the whole tile (which kantakartta codes are real height steps)
 3. meshes of the patch:
      A  regular grid DTM 0.5 m   (typical "terrain heightmap")
      A2 regular grid DTM 0.25 m
      B  constrained Delaunay (Triangle) on thinned ground + double breaklines along curb lines
      C  0.1 m grid -> quadric decimation (Open3D) to the triangle budget of B (lidar-only, no vectors)
 4. evaluation at stations along curb lines: step height + max slope across the curb on each mesh vs lidar
 5. GLB export in local coordinates (origin = patch SW corner, Z as is), Y-up for glTF
"""
import json, os, sys, time
import numpy as np
import laspy
from scipy.spatial import cKDTree
from scipy import ndimage
from shapely.geometry import shape, box, LineString
import triangle as tr
import trimesh

T0 = time.time()


def log(*a):
    print(f'[{time.time() - T0:6.1f}s]', *a, flush=True)


laz, vdir, X0, Y0, S, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
os.makedirs(out, exist_ok=True)
M = 5.0  # margin
res = {}

# ---------- 1. read
las = laspy.read(laz)
x, y, z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
cls = np.asarray(las.classification)
log('tile points', len(x), 'density/m2', round(len(x) / 250000, 1))
res['tile_points'] = int(len(x))
res['tile_class_hist'] = {int(k): int(v) for k, v in zip(*np.unique(cls, return_counts=True))}
g = cls == 2
gx, gy, gz = x[g], y[g], z[g]
log('ground points (tile)', len(gx))
gtree = cKDTree(np.c_[gx, gy])

# ---------- 2. load vectors
def lines(fname, codes=None):
    d = json.load(open(os.path.join(vdir, fname)))
    for f in d['features']:
        if f['geometry']['type'] != 'LineString':
            continue
        k = f['properties'].get('kplkoodi')
        if codes and k not in codes:
            continue
        yield k, f['properties'].get('tyyppi'), LineString([c[:2] for c in f['geometry']['coordinates']])

ALL = list(lines('wfs_kanta_Liikennevayla.geojson')) + list(lines('wfs_kanta_Kevyenliikenteenvayla.geojson')) \
    + list(lines('wfs_kanta_Rakenne.geojson'))


def stations(ls, step=0.5):
    n = max(2, int(ls.length / step) + 1)
    d = np.linspace(0, ls.length, n)
    p = np.array([ls.interpolate(t).coords[0] for t in d])
    t = np.gradient(p, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-12
    nrm = np.c_[-t[:, 1], t[:, 0]]
    return p, nrm


def side_heights(p, nrm, tree, pz, o0=0.15, o1=0.6, r=0.25):
    """robust ground height left/right of each station (band o0..o1 m off the line)"""
    out = np.full((len(p), 2), np.nan)
    for s, sign in enumerate((1, -1)):
        zs = []
        for o in np.arange(o0, o1 + 1e-6, 0.15):
            q = p + sign * o * nrm
            zs.append([np.median(pz[i]) if len(i) >= 3 else np.nan for i in tree.query_ball_point(q, r)])
        out[:, s] = np.nanmedian(np.array(zs), axis=0)
    return out


log('step analysis per line class (whole tile)')
step_stats = {}
for k, typ, ls in ALL:
    p, nrm = stations(ls, 1.0)
    h = side_heights(p, nrm, gtree, gz)
    d = np.abs(h[:, 0] - h[:, 1])
    d = d[~np.isnan(d)]
    st = step_stats.setdefault(f'{k} {typ or ""}'.strip(), [])
    st.extend(d.tolist())
res['step_by_class'] = {}
for k, v in sorted(step_stats.items()):
    v = np.array(v)
    if len(v) == 0:
        continue
    res['step_by_class'][k] = dict(n=len(v), median_m=round(float(np.median(v)), 3), p25=round(float(np.percentile(v, 25)), 3),
                                   p75=round(float(np.percentile(v, 75)), 3), frac_ge_8cm=round(float((v >= 0.08).mean()), 2))
    log(' ', k, res['step_by_class'][k])

# ---------- 3. patch
pb = box(X0, Y0, X0 + S, Y0 + S)
mb = box(X0 - M, Y0 - M, X0 + S + M, Y0 + S + M)
sel = (gx >= X0 - M) & (gx <= X0 + S + M) & (gy >= Y0 - M) & (gy <= Y0 + S + M)
px, py, pz = gx[sel] - X0, gy[sel] - Y0, gz[sel]
allsel = (x >= X0) & (x <= X0 + S) & (y >= Y0) & (y <= Y0 + S)
res['patch'] = dict(x0=X0, y0=Y0, size=S, points_all=int(allsel.sum()), density_all=round(allsel.sum() / S / S, 1),
                    ground_points=int(((px >= 0) & (px <= S) & (py >= 0) & (py <= S)).sum()),
                    class_hist={int(k): int(v) for k, v in zip(*np.unique(cls[allsel], return_counts=True))})
res['patch']['ground_density'] = round(res['patch']['ground_points'] / S / S, 1)
log('patch', res['patch'])
ptree = cKDTree(np.c_[px, py])
# hold-out 10 % of ground points for accuracy checks
rng = np.random.default_rng(0)
hold = rng.random(len(px)) < 0.1
inpatch = (px >= 0) & (px <= S) & (py >= 0) & (py <= S)


def grid_dtm(cell, xs, ys, zs, ext=S + 2 * M, off=-M, stat='median'):
    n = int(round(ext / cell))
    ix = np.clip(((xs - off) / cell).astype(int), 0, n - 1)
    iy = np.clip(((ys - off) / cell).astype(int), 0, n - 1)
    key = iy * n + ix
    order = np.argsort(key)
    k, zs2 = key[order], zs[order]
    uk, start = np.unique(k, return_index=True)
    med = np.array([np.median(a) for a in np.split(zs2, start[1:])])
    G = np.full(n * n, np.nan)
    G[uk] = med
    G = G.reshape(n, n)
    # fill holes with nearest
    msk = np.isnan(G)
    if msk.any():
        idx = ndimage.distance_transform_edt(msk, return_distances=False, return_indices=True)
        G = G[tuple(idx)]
    return G, n


def grid_mesh(G, cell, off=-M):
    n = G.shape[0]
    cx = off + (np.arange(n) + 0.5) * cell
    XX, YY = np.meshgrid(cx, cx)
    V = np.c_[XX.ravel(), YY.ravel(), G.ravel()]
    i = np.arange(n - 1)
    a = (i[None, :] + i[:, None] * n).ravel()
    F = np.r_[np.c_[a, a + 1, a + n + 1], np.c_[a, a + n + 1, a + n]]
    return V, F


def crop_mesh(V, F):
    c = V[F].mean(axis=1)
    keep = (c[:, 0] >= 0) & (c[:, 0] <= S) & (c[:, 1] >= 0) & (c[:, 1] <= S)
    m = trimesh.Trimesh(V, F[keep], process=False)
    m.remove_unreferenced_vertices()
    return m


meshes = {}
t = time.time()
for name, cell in (('A_grid_0.5m', 0.5), ('A2_grid_0.25m', 0.25)):
    G, n = grid_dtm(cell, px[~hold], py[~hold], pz[~hold])
    V, F = grid_mesh(G, cell)
    meshes[name] = crop_mesh(V, F)
    log(name, len(meshes[name].faces), 'tris')
res['time_grid_s'] = round(time.time() - t, 2)

# ---------- B: constrained Delaunay with curb breaklines
t = time.time()
CURB_CODES = None  # decided from step analysis: use classes whose median step >= 5 cm
curb_classes = [k for k, v in res['step_by_class'].items() if v['median_m'] >= 0.05 and v['n'] >= 20]
res['breakline_classes'] = curb_classes
log('breakline classes', curb_classes)
BL = []
for k, typ, ls in ALL:
    if f'{k} {typ or ""}'.strip() not in curb_classes:
        continue
    c = ls.intersection(mb)
    if c.is_empty:
        continue
    for part in getattr(c, 'geoms', [c]):
        if part.geom_type == 'LineString' and part.length > 0.5:
            BL.append((k, LineString([(a - X0, b - Y0) for a, b in part.coords])))
log('breakline segments in patch', len(BL), 'total length', round(sum(l.length for _, l in BL), 1))

inside = lambda q: (q[:, 0] > 1) & (q[:, 0] < S - 1) & (q[:, 1] > 1) & (q[:, 1] < S - 1)
offs = np.round(np.arange(-0.6, 0.6001, 0.05), 3)


def width_of(prof):
    """prof: (len(offs), n). returns step (left-right) and 10-90 % transition width (m)"""
    l = np.nanmedian(prof[offs >= 0.3], axis=0)
    r = np.nanmedian(prof[offs <= -0.3], axis=0)
    d = l - r
    u = (prof - r) / np.where(np.abs(d) < 1e-6, np.nan, d)  # 0 on right(-) side, 1 on left(+)
    w = np.full(prof.shape[1], np.nan)
    for j in range(prof.shape[1]):
        uj = u[:, j]
        if np.isnan(uj).any():
            continue
        uj = np.maximum.accumulate(np.clip(uj, 0, 1))
        try:
            o10 = np.interp(0.1, uj + np.arange(len(uj)) * 1e-9, offs)
            o90 = np.interp(0.9, uj + np.arange(len(uj)) * 1e-9, offs)
            w[j] = abs(o90 - o10)
        except Exception:
            pass
    return d, w


def lidar_profiles(p, nrm, along=0.35, binw=0.1):
    prof = np.full((len(offs), len(p)), np.nan)
    for j, nb in enumerate(ptree.query_ball_point(p, 0.9)):
        if len(nb) < 5:
            continue
        dx, dy = px[nb] - p[j, 0], py[nb] - p[j, 1]
        o = dx * nrm[j, 0] + dy * nrm[j, 1]
        t = -dx * nrm[j, 1] + dy * nrm[j, 0]
        keep = np.abs(t) <= along
        o, zz = o[keep], pz[nb][keep]
        for i, oc in enumerate(offs):
            s = np.abs(o - oc) <= binw / 2
            if s.sum() >= 1:
                prof[i, j] = np.median(zz[s])
    # fill gaps along the profile by interpolation
    for j in range(prof.shape[1]):
        c = prof[:, j]
        ok = ~np.isnan(c)
        if ok.sum() >= 4:
            prof[:, j] = np.interp(offs, offs[ok], c[ok])
    return prof



OFF = 0.03  # half-width of the vertical step (vertices 6 cm apart)


def line_stations(snap):
    out = []
    shifts = []
    for k, ls in BL:
        p, nrm = stations(ls, 0.5)
        if snap:
            prof = lidar_profiles(p, nrm)
            gr = np.abs(np.diff(prof, axis=0))  # between offs[i], offs[i+1]
            gr = ndimage.uniform_filter1d(np.nan_to_num(gr), 3, axis=0)
            mid = (offs[1:] + offs[:-1]) / 2
            sel_ = np.abs(mid) <= 0.45
            best = mid[sel_][np.argmax(gr[sel_], axis=0)]
            strong = gr[sel_].max(axis=0) >= 0.02  # >= 2 cm per 5 cm bin
            sh = np.where(strong, best, 0.0)
            sh = ndimage.median_filter(sh, size=5, mode='nearest')
            p = p + sh[:, None] * nrm
            shifts.append(sh)
        h = side_heights(p, nrm, ptree, pz, 0.12, 0.5, 0.2)
        for s in range(2):
            col = h[:, s]
            if np.isnan(col).all():
                col[:] = np.nanmedian(pz)
            nan = np.isnan(col)
            col[nan] = np.interp(np.flatnonzero(nan), np.flatnonzero(~nan), col[~nan])
            h[:, s] = ndimage.median_filter(col, size=3, mode='nearest')
        out.append((k, p, nrm, h))
    if snap and shifts:
        s = np.abs(np.concatenate(shifts))
        res['snap_shift'] = dict(median_m=round(float(np.median(s)), 3), p90_m=round(float(np.percentile(s, 90)), 3),
                                 frac_gt_10cm=round(float((s > 0.1).mean()), 2))
        log('snap shift', res['snap_shift'])
    return out


def build_cdt(info, cell, excl=0.2):
    verts, segs = [], []
    for k, p, nrm, h in info:
        for s, sign in enumerate((1, -1)):
            base = len(verts)
            q = p + sign * OFF * nrm
            verts += [(q[j, 0], q[j, 1], h[j, s]) for j in range(len(q))]
            segs += [(base + j, base + j + 1) for j in range(len(q) - 1)]
    verts = np.array(verts) if verts else np.zeros((0, 3))
    G, n = grid_dtm(cell, px[~hold], py[~hold], pz[~hold])
    V0, _ = grid_mesh(G, cell)
    if len(verts):
        dist, _ = cKDTree(verts[:, :2]).query(V0[:, :2])
        V0 = V0[dist > excl]
    P = np.r_[V0, verts]
    segs = np.array(segs, dtype=int) + len(V0) if len(segs) else np.zeros((0, 2), int)
    A = dict(vertices=P[:, :2], segments=segs) if len(segs) else dict(vertices=P[:, :2])
    Tm = tr.triangulate(A, 'pc' if len(segs) else '')  # 'c': keep convex hull (else Triangle eats unprotected boundary)
    Vb = Tm['vertices']
    zb = np.full(len(Vb), np.nan)
    zb[:len(P)] = P[:, 2]
    if len(Vb) > len(P):  # Steiner points at crossing segments: nearest original z
        _, ii = cKDTree(P[:, :2]).query(Vb[len(P):])
        zb[len(P):] = P[ii, 2]
    return crop_mesh(np.c_[Vb, zb], Tm['triangles'])


bl_station_info = line_stations(False)
meshes['B_cdt_breaklines'] = build_cdt(bl_station_info, 0.3)
res['time_cdt_s'] = round(time.time() - t, 2)
log('B', len(meshes['B_cdt_breaklines'].faces), 'tris', res['time_cdt_s'], 's')
t = time.time()
snapped = line_stations(True)
meshes['B2_cdt_snapped'] = build_cdt(snapped, 0.3)
meshes['B3_cdt_snapped_base0.5'] = build_cdt(snapped, 0.5, 0.3)
res['time_cdt_snapped_s'] = round(time.time() - t, 2)
log('B2/B3', len(meshes['B2_cdt_snapped'].faces), len(meshes['B3_cdt_snapped_base0.5'].faces), 'tris', res['time_cdt_snapped_s'], 's')

# ---------- C: lidar-only dense grid + quadric decimation to B's budget
t = time.time()
try:
    import open3d as o3d
    G, n = grid_dtm(0.1, px[~hold], py[~hold], pz[~hold])
    V, F = grid_mesh(G, 0.1)
    full = crop_mesh(V, F)
    meshes['C0_grid_0.1m'] = full
    om = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(full.vertices), o3d.utility.Vector3iVector(full.faces))
    om = om.simplify_quadric_decimation(target_number_of_triangles=len(meshes['B_cdt_breaklines'].faces))
    meshes['C_grid0.1_quadric'] = trimesh.Trimesh(np.asarray(om.vertices), np.asarray(om.triangles), process=False)
    res['time_quadric_s'] = round(time.time() - t, 2)
    log('C', len(meshes['C_grid0.1_quadric'].faces), 'tris', res['time_quadric_s'], 's')
except Exception as e:
    log('C failed', e)
    res['C_error'] = str(e)

# ---------- 4. evaluation
import open3d as o3d


def height_fn(m):
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(m.vertices.astype(np.float32)), o3d.core.Tensor(m.faces.astype(np.uint32)))

    def f(qx, qy):
        rays = np.c_[qx, qy, np.full(len(qx), 500.0), np.zeros(len(qx)), np.zeros(len(qx)), -np.ones(len(qx))].astype(np.float32)
        t = sc.cast_rays(o3d.core.Tensor(rays))['t_hit'].numpy()
        zz = 500.0 - t
        zz[~np.isfinite(t)] = np.nan
        return zz
    return f


GROUPS = {'curb (62520 road edge)': ['62520'], 'stairs (66626 porras)': ['66626 porras'],
          'retaining wall top (66634)': ['66634 tukimuurin yläreuna']}
st_by_group = {}
for (k, p, nrm, h), (k2, ls) in zip(bl_station_info, BL):
    lab = [g for g, cs in GROUPS.items() if any(c.split()[0] == k for c in cs)]
    if not lab:
        continue
    ok = inside(p)
    st_by_group.setdefault(lab[0], []).append((p[ok], nrm[ok]))
for gname in st_by_group:
    P_ = np.concatenate([a for a, b in st_by_group[gname]])
    N_ = np.concatenate([b for a, b in st_by_group[gname]])
    st_by_group[gname] = (P_, N_)

evalr = {}
hx, hy, hz = px[hold & inpatch], py[hold & inpatch], pz[hold & inpatch]
truth = {}
for gname, (P_, N_) in st_by_group.items():
    prof = lidar_profiles(P_, N_)
    d, w = width_of(prof)
    big = np.abs(d) >= 0.08
    truth[gname] = (d, big)
    res.setdefault('lidar_truth', {})[gname] = dict(stations=int(len(d)), stations_step_ge_8cm=int(big.sum()),
        step_median_m=round(float(np.nanmedian(np.abs(d[big]))), 3) if big.any() else None,
        step_p90_m=round(float(np.nanpercentile(np.abs(d[big]), 90)), 3) if big.any() else None,
        width10_90_median_m=round(float(np.nanmedian(w[big])), 3) if big.any() else None)
    log('lidar', gname, res['lidar_truth'][gname])

for name, m in meshes.items():
    f = height_fn(m)
    e = f(hx, hy) - hz
    e = e[~np.isnan(e)]
    evalr[name] = dict(tris=int(len(m.faces)), verts=int(len(m.vertices)),
                       holdout_rmse_m=round(float(np.sqrt(np.mean(e ** 2))), 3),
                       holdout_p95_abs_m=round(float(np.percentile(np.abs(e), 95)), 3))
    for gname, (P_, N_) in st_by_group.items():
        d0, big = truth[gname]
        if not big.any():
            continue
        prof = np.array([f(P_[:, 0] + o * N_[:, 0], P_[:, 1] + o * N_[:, 1]) for o in offs])
        d, w = width_of(prof)
        slope = np.nanmax(np.abs(np.diff(prof, axis=0)), axis=0) / 0.05
        evalr[name][gname] = dict(
            step_abs_err_median_m=round(float(np.nanmedian(np.abs(np.abs(d[big]) - np.abs(d0[big])))), 3),
            step_ratio_median=round(float(np.nanmedian(np.abs(d[big]) / np.abs(d0[big]))), 2),
            width10_90_median_m=round(float(np.nanmedian(w[big])), 3),
            frac_width_le_0_15m=round(float(np.nanmean(w[big] <= 0.15)), 2),
            max_slope_deg_median=round(float(np.degrees(np.arctan(np.nanmedian(slope[big])))), 1))
    log(name, json.dumps(evalr[name]))
res['eval'] = evalr

# ---------- 5. export GLB (glTF is Y-up: (x, z, -y)), origin = patch SW corner, z relative to N2000 0
for name, m in meshes.items():
    v = m.vertices
    g2 = trimesh.Trimesh(np.c_[v[:, 0], v[:, 2], -v[:, 1]].astype(np.float32), m.faces, process=False)
    pth = os.path.join(out, f'{name}.glb')
    g2.export(pth)
    res['eval'].setdefault(name, {})['glb_bytes'] = os.path.getsize(pth)
# lidar profile samples for the report (a few strong stations)
json.dump(res, open(os.path.join(out, 'result.json'), 'w'), indent=1)
log('done')

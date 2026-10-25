"""Cross-street ground sections perpendicular to GIS:Keskilinjat 'Ajorata' centrelines; detects curb steps.
usage: street_section.py tile.laz keskilinjat.gml out.png [halfwidth=14]"""
import sys, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw
laz, gml, outpng = sys.argv[1:4]
HW = float(sys.argv[4]) if len(sys.argv) > 4 else 14
las = laspy.read(laz)
cls = np.asarray(las.classification); X, Y, Z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
g = cls == 2
gkd = cKDTree(np.c_[X[g], Y[g]]); o = np.where(cls == 12)[0]
dd_, ii_ = gkd.query(np.c_[X[o], Y[o]], k=8, distance_upper_bound=0.5); ok_ = np.isfinite(dd_).all(1)
med = np.full(len(o), np.inf); med[ok_] = np.median(Z[g][np.minimum(ii_[ok_], g.sum() - 1)], 1)
keep = np.r_[np.where(g)[0], o[np.abs(Z[o] - med) < 0.08]]   # class 12 = overlap strip; keep only near-ground ones
X, Y, Z, C = X[keep], Y[keep], Z[keep], cls[keep]
kd = cKDTree(np.c_[X, Y])
xmin, ymin, xmax, ymax = las.header.mins[0], las.header.mins[1], las.header.maxs[0], las.header.maxs[1]
root = ET.parse(gml).getroot()
secs = []
for f in root.iter():
    if not f.tag.endswith('}Keskilinjat'): continue
    d = {c.tag.split('}')[1]: (c.text or '') for c in f}
    if d.get('Katuosalaji') != 'Ajorata': continue
    co = [e.text for e in f.iter() if e.tag.endswith('coordinates')][0]
    P = np.array([[float(v) for v in p.split(',')[:2]] for p in co.split()])
    for a, b in zip(P[:-1], P[1:]):
        L = np.linalg.norm(b - a)
        if L < 8: continue
        c = (a + b) / 2
        if not (xmin + HW < c[0] < xmax - HW and ymin + HW < c[1] < ymax - HW): continue
        secs.append((d['Kadunnimi'], c, (b - a) / L))
print(len(secs), 'sections')
BIN = 0.05
rows = []
for name, c, t in secs:
    n = np.array([-t[1], t[0]])
    idx = np.array(kd.query_ball_point(c, HW + 1))
    dd = np.c_[X[idx], Y[idx]] - c
    u, v, z, cl = dd @ n, dd @ t, Z[idx], C[idx]
    k = (np.abs(v) < 0.3) & (np.abs(u) < HW)
    u, z, cl = u[k], z[k], cl[k]
    nb = int(2 * HW / BIN)
    b = ((u + HW) / BIN).astype(int)
    prof = np.full(nb, np.nan)
    for i in range(nb):
        zz = z[b == i]
        if len(zz) >= 2: prof[i] = np.percentile(zz, 30)
    # smooth over 3 bins and find steps: |z(i+3) - z(i-3)| over 30 cm window
    steps = []
    for i in range(4, nb - 4):
        lo, hi = prof[i - 4:i - 1], prof[i + 2:i + 5]
        if np.isnan(lo).sum() > 1 or np.isnan(hi).sum() > 1: continue
        dz = np.nanmedian(hi) - np.nanmedian(lo)
        if 0.06 <= abs(dz) <= 0.35: steps.append((i, dz))
    # non-max suppression
    picked = []
    for i, dz in sorted(steps, key=lambda s: -abs(s[1])):
        if all(abs(i - j) > 8 for j, _ in picked): picked.append((i, dz))
    picked.sort()
    rows.append((name, c, prof, picked, len(u)))
    print(f"{name:14s} at {c[0]:.1f},{c[1]:.1f}: {len(u)} pts in 0.6 m strip ({len(u)/(2*HW*0.6):.0f}/m2); "
          f"steps: " + ', '.join(f"{(i*BIN-HW):+.2f} m {dz*100:+.1f} cm" for i, dz in picked))
# plot up to 6 sections, 1 px = 1 cm horizontally? use 2 px per 5 cm bin, z exaggerated
W, H = int(2 * HW / BIN) * 2 + 80, 220
img = Image.new('RGB', (W, H * min(6, len(rows))), 'white'); dr = ImageDraw.Draw(img)
for r, (name, c, prof, picked, npts) in enumerate(rows[:6]):
    y0 = r * H; ok = ~np.isnan(prof)
    zmin, zmax = np.nanmin(prof), np.nanmax(prof)
    sc = 100.0 if zmax - zmin < 1.6 else (H - 50) / (zmax - zmin)   # 1 px = 1 cm vertically
    dr.text((5, y0 + 3), f"{name} @ {c[0]:.0f},{c[1]:.0f}  z {zmin:.2f}..{zmax:.2f} m  1 px = {100/sc:.1f} cm vert, 2.5 cm horiz; grid 10 cm", fill='black')
    # 10 cm gridlines
    for zz in np.arange(np.floor(zmin*10)/10, zmax + 0.1, 0.1):
        yy = y0 + H - 15 - (zz - zmin) * sc; dr.line([(40, yy), (W, yy)], fill=(225, 225, 225))
    pts = [(40 + i * 2, y0 + H - 15 - (prof[i] - zmin) * sc) for i in range(len(prof)) if ok[i]]
    for p in pts: dr.rectangle([p[0], p[1], p[0] + 1, p[1] + 1], fill='black')
    for i, dz in picked:
        x = 40 + i * 2; dr.line([(x, y0 + 15), (x, y0 + H - 10)], fill='red'); dr.text((x + 2, y0 + 15), f"{dz*100:+.0f}cm", fill='red')
img.save(outpng)

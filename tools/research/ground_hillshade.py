"""10 cm ground raster (class 2 + near-ground class 12) -> slope image with kantakartta lines overlaid.
usage: ground_hillshade.py tile.laz xmin,ymin,xmax,ymax cell out.png gml [gml...]"""
import sys, numpy as np, laspy, xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
from scipy import ndimage
from PIL import Image, ImageDraw
laz, bb, cell, out = sys.argv[1], [float(v) for v in sys.argv[2].split(',')], float(sys.argv[3]), sys.argv[4]
las = laspy.read(laz)
cls = np.asarray(las.classification); X, Y, Z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
inb = (X > bb[0]-1) & (X < bb[2]+1) & (Y > bb[1]-1) & (Y < bb[3]+1)
X, Y, Z, cls = X[inb], Y[inb], Z[inb], cls[inb]
g = cls == 2; kd = cKDTree(np.c_[X[g], Y[g]]); o = np.where(cls == 12)[0]
d, i = kd.query(np.c_[X[o], Y[o]], k=8, distance_upper_bound=0.5); ok = np.isfinite(d).all(1)
med = np.full(len(o), np.inf); med[ok] = np.median(Z[g][np.minimum(i[ok], g.sum()-1)], 1)
keep = np.r_[np.where(g)[0], o[np.abs(Z[o]-med) < 0.08]]
X, Y, Z = X[keep], Y[keep], Z[keep]
nx, ny = int((bb[2]-bb[0])/cell), int((bb[3]-bb[1])/cell)
ix = ((X-bb[0])/cell).astype(int); iy = ((bb[3]-Y)/cell).astype(int)
m = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
s = np.zeros((ny, nx)); c = np.zeros((ny, nx))
np.add.at(s, (iy[m], ix[m]), Z[m]); np.add.at(c, (iy[m], ix[m]), 1)
dem = np.where(c > 0, s/np.maximum(c, 1), np.nan)
print(f'cells with ground: {np.mean(c>0)*100:.1f}%  mean pts/cell {c[c>0].mean():.2f}')
hole = np.isnan(dem)
idx = ndimage.distance_transform_edt(hole, return_distances=False, return_indices=True)
demf = dem[tuple(idx)]
demf = ndimage.median_filter(demf, 3)
small = ndimage.binary_closing(~hole, iterations=1)  # fill single-cell holes only
gy, gx = np.gradient(demf, cell)
slope = np.hypot(gx, gy)
v = np.clip(slope / 0.3, 0, 1)   # 30 % slope = black
img = (255 * (1 - v)).astype(np.uint8)
rgb = np.stack([img]*3, -1); rgb[~small] = (255, 225, 190)
im = Image.fromarray(rgb); dr = ImageDraw.Draw(im)
col = {'62520': (255, 0, 0), '62522': (0, 160, 255), '62523': (0, 160, 255), '66626': (0, 200, 0), '66618': (0, 200, 0), '66634': (255, 0, 255), '66636': (255, 160, 0)}
for gm in sys.argv[5:]:
    for f in ET.parse(gm).getroot().iter():
        code = next((x.text for x in f if x.tag.endswith('kplkoodi')), None)
        if code not in col: continue
        for e in f.iter():
            if e.tag.endswith('coordinates'):
                P = [((float(p.split(',')[0])-bb[0])/cell, (bb[3]-float(p.split(',')[1]))/cell) for p in e.text.split()]
                if len(P) > 1: dr.line(P, fill=col[code], width=1)
dr.text((5, 5), f'ground slope, {cell*100:.0f} cm cells; red 62520, blue 62522/3, green stairs, magenta retaining wall, orange fence; tan = no ground pts', fill=(0, 0, 0))
im.save(out)

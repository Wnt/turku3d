#!/usr/bin/env python3
"""Lidar tree-top detection (CHM local maxima) vs Turku street-tree register (GIS:Puut).

Usage: trees.py <laz> <puut.geojson> <out.json>
Writes detected trees (x, y, ground z, height, crown radius) -> instancing input for the engine.
"""
import json, sys, time
import numpy as np, laspy
from scipy import ndimage
from scipy.spatial import cKDTree

t0 = time.time()
las = laspy.read(sys.argv[1])
x, y, z, c = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
x0, y0, cell = np.floor(x.min()), np.floor(y.min()), 0.5
n = int(np.ceil(max(x.max() - x0, y.max() - y0) / cell))
ix, iy = ((x - x0) / cell).astype(int).clip(0, n - 1), ((y - y0) / cell).astype(int).clip(0, n - 1)
g = c == 2
DTM = np.full((n, n), np.nan)
np.fmin.at(DTM, (iy[g], ix[g]), z[g])  # lowest ground per cell (fmin ignores nan)
msk = np.isnan(DTM)
DTM = DTM[tuple(ndimage.distance_transform_edt(msk, return_distances=False, return_indices=True))]
v = np.isin(c, (4, 5))  # medium + high vegetation
H = np.zeros((n, n))
np.maximum.at(H, (iy[v], ix[v]), z[v] - DTM[iy[v], ix[v]])
Hs = ndimage.gaussian_filter(H, 1.0)
peaks = (Hs == ndimage.maximum_filter(Hs, size=9)) & (Hs >= 4.0)  # 4.5 m window, >= 4 m tall
py_, px_ = np.nonzero(peaks)
# crown radius: distance to where CHM drops below 50 % of the top, median over 8 rays
trees = []
for a, b in zip(py_, px_):
    h = H[a, b]
    rs = []
    for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        for r in np.arange(1, 20):
            aa, bb = int(round(a + r * np.sin(ang))), int(round(b + r * np.cos(ang)))
            if not (0 <= aa < n and 0 <= bb < n) or H[aa, bb] < 0.5 * h:
                rs.append(r * cell)
                break
    trees.append(dict(x=round(x0 + (b + 0.5) * cell, 2), y=round(y0 + (a + 0.5) * cell, 2), z=round(float(DTM[a, b]), 2),
                      h=round(float(h), 1), crown_r=round(float(np.median(rs)) if rs else 1.0, 1)))
reg = [f['geometry']['coordinates'][:2] for f in json.load(open(sys.argv[2]))['features']]
reg = np.array([p for p in reg if x0 <= p[0] <= x0 + n * cell and y0 <= p[1] <= y0 + n * cell])
det = np.array([[t['x'], t['y']] for t in trees])
d_reg, _ = cKDTree(det).query(reg)
res = dict(detected=len(trees), register_trees=len(reg), register_matched_within_3m=int((d_reg <= 3).sum()),
           recall=round(float((d_reg <= 3).mean()), 2), median_match_dist_m=round(float(np.median(d_reg[d_reg <= 3])), 2),
           height_median_m=float(np.median([t['h'] for t in trees])), seconds=round(time.time() - t0, 1))
print(json.dumps(res))
json.dump(dict(stats=res, trees=trees), open(sys.argv[3], 'w'))

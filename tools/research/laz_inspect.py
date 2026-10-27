"""Inspect a LAZ tile: header, CRS, point format, density, classes, returns, RGB/intensity, scan angles, gps time."""
import sys, numpy as np, laspy, collections
f = sys.argv[1]
las = laspy.read(f)
h = las.header
print('file', f)
print('version', h.version, 'point_format', h.point_format.id, 'dims', list(h.point_format.dimension_names))
print('count', h.point_count, 'scales', h.scales, 'offsets', h.offsets)
print('mins', h.mins, 'maxs', h.maxs)
print('system_id', h.system_identifier, 'software', h.generating_software, 'date', h.creation_date)
try: print('crs', h.parse_crs())
except Exception as e: print('crs err', e)
for v in h.vlrs: print('vlr', v.user_id, v.record_id, v.description)
x, y = las.x, las.y
area = (h.maxs[0]-h.mins[0])*(h.maxs[1]-h.mins[1])
print('area m2 %.0f  density all %.1f pts/m2' % (area, h.point_count/area))
cls = np.asarray(las.classification)
c = collections.Counter(cls.tolist())
for k in sorted(c): print('class', k, c[k], '%.1f%%' % (100*c[k]/len(cls)), '%.1f pts/m2' % (c[k]/area))
rn = np.asarray(las.return_number); nr = np.asarray(las.number_of_returns)
print('return_number', collections.Counter(rn.tolist()).most_common())
print('number_of_returns', collections.Counter(nr.tolist()).most_common())
first = (rn == 1)
print('first-return density %.1f pts/m2' % (first.sum()/area))
# density per 1 m cell (first returns) stats
ix = (x - h.mins[0]).astype(int); iy = (y - h.mins[1]).astype(int)
grid = np.zeros((int(h.maxs[1]-h.mins[1])+1, int(h.maxs[0]-h.mins[0])+1), int)
np.add.at(grid, (iy[first], ix[first]), 1)
g = grid[grid > 0]
print('first-return per m2 cell: median %d p10 %d p90 %d, empty cells %.1f%%' % (np.median(g), np.percentile(g,10), np.percentile(g,90), 100*(grid==0).mean()))
gm = cls == 2
gg = np.zeros_like(grid); np.add.at(gg, (iy[gm], ix[gm]), 1)
print('ground per m2: median over nonempty %d, cells with ground %.1f%%' % (np.median(gg[gg>0]), 100*(gg>0).mean()))
for d in ['intensity', 'red', 'green', 'blue', 'nir', 'scan_angle_rank', 'scan_angle', 'gps_time', 'point_source_id', 'user_data', 'scanner_channel']:
    if d in h.point_format.dimension_names:
        a = np.asarray(las[d]).astype(float)
        print(d, 'min %.3f max %.3f mean %.3f uniq %d' % (a.min(), a.max(), a.mean(), len(np.unique(a[:200000]))))
if 'gps_time' in h.point_format.dimension_names:
    gt = np.asarray(las.gps_time)
    import datetime
    # adjusted standard GPS time: +1e9 s since 1980-01-06
    base = datetime.datetime(1980,1,6)
    for v in [gt.min(), gt.max()]:
        print('gps_time', v, 'as adjusted std ->', base + datetime.timedelta(seconds=float(v)+1e9))
    print('point sources (flight lines):', collections.Counter(np.asarray(las.point_source_id).tolist()).most_common(12))

#!/usr/bin/env python3
"""roofer CityJSONSeq (.city.jsonl) -> GLB (one mesh per LoD, local origin) + quality stats.

Usage: cityjsonseq2glb.py in.city.jsonl out_prefix origin_x origin_y [lod=2.2]
glTF Y-up: (x - ox, z, -(y - oy)). Prints roofer RMSE / plane stats and triangle counts.
"""
import json, sys
import numpy as np
import mapbox_earcut as earcut
import trimesh

src, outp, ox, oy = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
LOD = sys.argv[5] if len(sys.argv) > 5 else '2.2'


def tri_polygon(rings, V):
    """rings: list of index lists (outer + holes) in 3D; returns triangles as index triples"""
    pts = np.array([V[i] for r in rings for i in r], float)
    idx = [i for r in rings for i in r]
    n = np.zeros(3)
    o = rings[0]
    for a, b in zip(o, o[1:] + o[:1]):  # Newell normal
        pa, pb = V[a], V[b]
        n += [(pa[1] - pb[1]) * (pa[2] + pb[2]), (pa[2] - pb[2]) * (pa[0] + pb[0]), (pa[0] - pb[0]) * (pa[1] + pb[1])]
    drop = int(np.argmax(np.abs(n)))
    p2 = np.delete(pts, drop, axis=1)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    t = earcut.triangulate_float64(p2, ends).reshape(-1, 3)
    return [[idx[a] for a in tt] for tt in t]


Vall, Fall, Sall = [], [], []
stats = dict(buildings=0, success=0, rmse=[], planes=[], roof_type={}, run_ms=[])
for line in open(src):
    o = json.loads(line)
    if 'transform' in o:  # first line (metadata) carries the shared transform
        sc, tr_ = o['transform']['scale'], o['transform']['translate']
    if not o.get('CityObjects'):
        continue
    V = np.array(o['vertices'], float) * sc + tr_
    base = len(Vall)
    Vall.extend(V.tolist())
    for cid, co in o['CityObjects'].items():
        a = co.get('attributes', {})
        if co['type'] == 'Building':
            stats['buildings'] += 1
            stats['success'] += bool(a.get('rf_success'))
            if a.get('rf_rmse_lod22') is not None:
                stats['rmse'].append(a['rf_rmse_lod22'])
            stats['planes'].append(a.get('rf_roof_planes') or 0)
            stats['roof_type'][a.get('rf_roof_type')] = stats['roof_type'].get(a.get('rf_roof_type'), 0) + 1
            stats['run_ms'].append(a.get('rf_t_run') or 0)
        for g in co.get('geometry', []):
            if g['lod'] != LOD or g['type'] != 'Solid':
                continue
            sem = g.get('semantics', {})
            for si, shell in enumerate(g['boundaries']):
                for fi, face in enumerate(shell):
                    st = None
                    if sem:
                        v = sem['values'][si][fi]
                        st = sem['surfaces'][v]['type'] if v is not None else None
                    for t in tri_polygon(face, V):
                        Fall.append([base + i for i in t])
                        Sall.append(st)
V = np.array(Vall)
F = np.array(Fall)
col = {'RoofSurface': [170, 60, 50, 255], 'WallSurface': [220, 215, 200, 255], 'GroundSurface': [90, 90, 90, 255]}
fc = np.array([col.get(s, [200, 200, 200, 255]) for s in Sall], np.uint8)
m = trimesh.Trimesh(np.c_[V[:, 0] - ox, V[:, 2], -(V[:, 1] - oy)].astype(np.float32), F, face_colors=fc, process=True)
m.export(outp + f'_lod{LOD}.glb')
r = np.array(stats['rmse'])
print(json.dumps(dict(buildings=stats['buildings'], success=stats['success'], lod=LOD, triangles=int(len(m.faces)),
                      vertices=int(len(m.vertices)), watertight=bool(m.is_watertight),
                      rmse_lod22_median=round(float(np.median(r)), 3), rmse_lod22_p90=round(float(np.percentile(r, 90)), 3),
                      roof_planes_median=float(np.median(stats['planes'])), roof_planes_max=int(max(stats['planes'])),
                      roof_type=stats['roof_type'], run_ms_median=float(np.median(stats['run_ms'])),
                      surface_counts={k: Sall.count(k) for k in set(Sall)})))

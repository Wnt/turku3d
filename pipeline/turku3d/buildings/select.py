"""Robust residuals measured against the final snapped shell, including uncovered points."""
import numpy as np
import shapely
from .shells import make_shell
from .distance import surface_distances
from turku3d.col.materials import mat_id


def residuals(shell, points, footprint):
    keep = shapely.intersects_xy(footprint, points[:, 0], points[:, 2])
    points = points[keep]
    if not len(points):
        return {'points': 0, 'covered': 0, 'median_m': None, 'p75_m': None, 'pass': False}
    heights = np.full(len(points), -np.inf)
    for tri in shell.positions[shell.indices[shell.material == mat_id('roof')]]:
        a, b, c = tri
        matrix = np.array([[b[0]-a[0], c[0]-a[0]], [b[2]-a[2], c[2]-a[2]]])
        if abs(np.linalg.det(matrix)) < 1e-10:
            continue
        select = ((points[:, 0] >= tri[:, 0].min()-1e-7) & (points[:, 0] <= tri[:, 0].max()+1e-7)
                  & (points[:, 2] >= tri[:, 2].min()-1e-7) & (points[:, 2] <= tri[:, 2].max()+1e-7))
        ids = np.flatnonzero(select)
        uv = (points[ids][:, [0, 2]] - a[[0, 2]]) @ np.linalg.inv(matrix).T
        inside = (uv[:, 0] >= -1e-7) & (uv[:, 1] >= -1e-7) & (uv.sum(axis=1) <= 1+1e-7)
        ids, uv = ids[inside], uv[inside]
        heights[ids] = np.maximum(heights[ids], a[1] + uv[:, 0]*(b[1]-a[1]) + uv[:, 1]*(c[1]-a[1]))
    errors = np.abs(points[:, 1]-heights)
    covered = np.isfinite(errors)
    # Uncovered points count as infinite residual; never improve a score by dropping them.
    ordered = np.sort(errors)
    def percentile(q):
        position = q*(len(ordered)-1)
        lo, hi = int(np.floor(position)), int(np.ceil(position))
        if not np.isfinite(ordered[hi]):
            return float('inf')
        return float(ordered[lo] + (ordered[hi]-ordered[lo])*(position-lo))
    median, p75 = percentile(.5), percentile(.75)
    return {'points': len(points), 'covered': int(covered.sum()),
            'median_m': median if np.isfinite(median) else None,
            'p75_m': p75 if np.isfinite(p75) else None,
            'pass': bool(median <= .10 and p75 <= .25)}


def choose(building, candidates, points):
    metrics, valid = {}, []
    for name, surfaces in sorted(candidates.items()):
        try:
            shell = make_shell(surfaces, building.footprint)
            vertical = residuals(shell, points, building.footprint)
            inside = shapely.intersects_xy(building.footprint, points[:, 0], points[:, 2])
            errors = surface_distances(shell.positions, shell.indices, points[inside])
            median, p75 = np.quantile(errors, [.5, .75]) if len(errors) else (np.inf, np.inf)
            fit = {'points': len(errors), 'median_m': float(median) if np.isfinite(median) else None,
                   'p75_m': float(p75) if np.isfinite(p75) else None,
                   'pass': bool(median <= .10 and p75 <= .25),
                   'metric': 'euclidean_point_to_shell', 'roof_vertical': vertical}
            metrics[name] = {**fit, 'triangles': len(shell.indices), 'hausdorff_m': shell.hausdorff,
                             'closed': True}
            # Satisfying BOTH acceptance limits beats any failing candidate, then median/p75.
            rank = (not fit['pass'], fit['median_m'] if fit['median_m'] is not None else float('inf'),
                    fit['p75_m'] if fit['p75_m'] is not None else float('inf'), name)
            valid.append((rank, name, shell))
        except (ValueError, shapely.errors.GEOSException) as exc:
            metrics[name] = {'pass': False, 'error': str(exc)}
    if not valid:
        return None, {'building_id': building.id, 'choice': None, 'pass': False, 'candidates': metrics}
    _, name, shell = min(valid, key=lambda item: item[0])
    return shell, {'building_id': building.id, 'choice': name, 'pass': metrics[name]['pass'], 'candidates': metrics}

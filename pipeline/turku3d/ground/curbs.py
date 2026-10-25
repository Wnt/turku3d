"""B3 double breaklines, independent odd-station QA, and slope-fitted profiles."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.ndimage import median_filter


@dataclass
class Curb:
    object_id: str
    distances: np.ndarray
    fitting_distances: np.ndarray
    centers: np.ndarray
    normals: np.ndarray
    heights: np.ndarray
    shifts: np.ndarray
    kind: str
    validation: list

    def lines(self, half_width=0.03):
        return [np.column_stack((self.centers + sign * half_width * self.normals, self.heights[:, side]))
                for side, sign in enumerate((-1, 1))]


def stations(line):
    d = np.arange(0, line.length + 1e-8, 1.0)
    if len(d) < 3:
        return None
    p = np.array([line.interpolate(t).coords[0][:2] for t in d])
    tangent = np.array([np.subtract(line.interpolate(min(line.length, t + .1)).coords[0][:2],
                                   line.interpolate(max(0, t - .1)).coords[0][:2]) for t in d])
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
    return d, p, np.c_[-tangent[:, 1], tangent[:, 0]]


def fit_profile(surface, center, normal, snap=True, water=None, water_level=None, along_limit=.35):
    """B3 gradient snap, followed by robust sloping side-plane fits.

    Fitting stations use +/-35 cm longitudinal support. Validation can use a wider
    strip of independently withheld returns. Sparse, ill-conditioned or implausibly
    steep side planes are unsupported rather than extrapolated into artificial walls.
    """
    ii = surface.tree.query_ball_point(center, max(1.6, np.hypot(1.2, along_limit)))
    p = surface.points[ii]
    delta = p[:, [0, 2]] - center
    across = delta @ normal
    along = delta @ np.array([-normal[1], normal[0]])
    band = (np.abs(along) <= along_limit) & (np.abs(across) <= 1.2)
    across, along, height = across[band], along[band], p[band, 1]
    shift = 0.
    if snap:
        offsets = np.arange(-1.1, 1.11, .1)
        medians = np.array([np.median(height[np.abs(across - x) <= .05])
                            if np.any(np.abs(across - x) <= .05) else np.nan for x in offsets])
        valid = np.isfinite(medians)
        if valid.sum() >= 6:
            medians = np.interp(offsets, offsets[valid], medians[valid])
            gradient = np.diff(medians)
            gradient = np.abs(gradient - np.median(gradient))
            smoothed = np.convolve(gradient, np.ones(3) / 3, mode='same')
            mid = (offsets[1:] + offsets[:-1]) / 2
            eligible = np.flatnonzero(np.abs(mid) <= .45)
            edge = eligible[np.argmax(smoothed[eligible])]
            if smoothed[edge] >= .015:
                peak = (np.abs(mid - mid[edge]) <= .151) & (np.abs(mid) <= .45)
                shift = float(np.average(mid[peak], weights=gradient[peak] + 1e-12))
    intercepts = []
    for sign in (-1, 1):
        mask = ((across - shift) * sign >= .12) & ((across - shift) * sign <= .8)
        if mask.sum() < 5:
            if water is not None and water_level is not None:
                from shapely.geometry import Point
                if water.contains(Point(center + (shift + sign * .5) * normal)):
                    intercepts.append(water_level)
                    continue
            return None
        a = np.c_[across[mask] - shift, along[mask], np.ones(mask.sum())]
        y = height[mask]
        good = np.ones(len(y), bool)
        for _ in range(3):
            if good.sum() < 5 or np.linalg.cond(a[good]) > 100:
                return None
            coeff = np.linalg.lstsq(a[good], y[good], rcond=None)[0]
            residual = y - a @ coeff
            mad = np.median(np.abs(residual - np.median(residual)))
            good = np.abs(residual) <= max(.025, 3 * mad)
        if good.sum() < 5 or np.linalg.norm(coeff[:2]) > 1.:
            return None
        intercepts.append(coeff[2])
    return shift, np.array(intercepts)


def fit_curbs(lines, surface, semantics=None, water=None, water_level=None):
    result, rejected = [], []
    for object_id, line in lines:
        station = stations(line)
        if station is None:
            continue
        distances, p, n = station
        fitted, held = [], []
        for i in range(len(p)):
            profile = fit_profile(surface, p[i], n[i], water=water, water_level=water_level)
            if profile is None:
                continue
            shift, heights = profile
            if i % 2:
                held.append({'distance': float(distances[i]), 'center': p[i].tolist(), 'normal': n[i].tolist(),
                             'shift': shift, 'heights': heights.tolist()})
            else:
                fitted.append((i, shift, heights))
        if len(fitted) < 2:
            rejected.append(object_id)
            continue
        ix = np.array([f[0] for f in fitted])
        # Only even stations can set the geometry, classification, or snapping.
        shifts = median_filter(np.array([f[1] for f in fitted]), size=3, mode='nearest')
        heights = np.array([f[2] for f in fitted])
        step = float(np.median(np.abs(np.diff(heights, axis=1))))
        kind = 'quay' if step > 1 else ('curb_dropped' if step < .03 else 'curb')
        if kind == 'curb_dropped' and semantics is not None and semantics.intersects(line):
            kind = 'crossing'
        # Preserve source-vector corners without introducing additional lidar fitting
        # stations. Heights and snap offsets at corners use only even-station fits.
        coords = np.asarray(line.coords)[:, :2]
        corners = np.r_[0., np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))]
        corners = corners[(corners >= distances[ix[0]]) & (corners <= distances[ix[-1]])]
        geometry_d = np.unique(np.r_[distances[ix], corners])
        geometry_p = np.array([line.interpolate(d).coords[0][:2] for d in geometry_d])
        tangent = np.array([np.subtract(line.interpolate(min(line.length, d + .1)).coords[0][:2],
                                        line.interpolate(max(0, d - .1)).coords[0][:2]) for d in geometry_d])
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
        geometry_n = np.c_[-tangent[:, 1], tangent[:, 0]]
        geometry_shift = np.interp(geometry_d, distances[ix], shifts)
        geometry_h = np.column_stack([np.interp(geometry_d, distances[ix], heights[:, side]) for side in (0, 1)])
        result.append(Curb(object_id, geometry_d, distances[ix], geometry_p + geometry_shift[:, None] * geometry_n,
                           geometry_n, geometry_h, geometry_shift, kind, held))
    return result, rejected

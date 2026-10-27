"""Shared B3 gradient snapping and robust two-sided slope fits (from W1-01)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class Profile:
    shift: float
    heights: tuple[float, float]  # negative-normal side, positive-normal side
    counts: tuple[int, int]
    residual_mad: tuple[float, float]
    slopes: tuple[float, float]
    confidence: float

    @property
    def step(self):
        return self.heights[1] - self.heights[0]

    def as_dict(self):
        return {**asdict(self), "step_m": self.step}


def stations(line, spacing=1.0):
    """Chainage, xz centres and positive-normal vectors; includes both endpoints."""
    if spacing <= 0 or not np.isfinite(spacing):
        raise ValueError("station spacing must be positive")
    if line.is_empty or line.length <= 0:
        raise ValueError("nonzero LineString required")
    d = np.unique(np.r_[np.arange(0, line.length, spacing), line.length])
    p = np.array([line.interpolate(t).coords[0][:2] for t in d])
    tangent = np.array(
        [
            np.subtract(
                line.interpolate(min(line.length, t + 0.1)).coords[0][:2],
                line.interpolate(max(0, t - 0.1)).coords[0][:2],
            )
            for t in d
        ]
    )
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
    return d, p, np.c_[-tangent[:, 1], tangent[:, 0]]


def measure_profile(
    surface,
    center,
    normal,
    *,
    snap=True,
    along_limit=0.75,
    max_shift=0.45,
    side_min=0.12,
    side_max=0.8,
    water=None,
    water_level=None,
):
    """Fit two independent robust planes extrapolated to the same edge, never raw means.

    Horizontal inputs use engine x/z; positive normal is (-tangent_z, tangent_x).
    This is the right side in a north-up map (engine z points south). Side names in
    exported files are explicit normal/negative-normal, avoiding handedness ambiguity.
    """
    center, normal = np.asarray(center), np.asarray(normal, float)
    if normal.shape != (2,) or not np.isclose(np.linalg.norm(normal), 1):
        raise ValueError("normal must be a unit xz vector")
    radius = np.hypot(side_max + max_shift, along_limit)
    ii = surface.tree.query_ball_point(center, radius)
    p = surface.points[ii]
    delta = p[:, [0, 2]] - center
    across = delta @ normal
    along = delta @ np.array([-normal[1], normal[0]])
    band = (np.abs(along) <= along_limit) & (np.abs(across) <= side_max + max_shift)
    across, along, height = across[band], along[band], p[band, 1]
    shift = 0.0
    if snap:
        offsets = np.arange(-1.1, 1.11, 0.1)
        medians = np.array(
            [
                np.median(height[np.abs(across - x) <= 0.05])
                if np.any(np.abs(across - x) <= 0.05)
                else np.nan
                for x in offsets
            ]
        )
        valid = np.isfinite(medians)
        if valid.sum() >= 6:
            medians = np.interp(offsets, offsets[valid], medians[valid])
            gradient = np.diff(medians)
            gradient = np.abs(gradient - np.median(gradient))
            smoothed = np.convolve(gradient, np.ones(3) / 3, mode="same")
            mid = (offsets[1:] + offsets[:-1]) / 2
            eligible = np.flatnonzero(np.abs(mid) <= max_shift)
            if len(eligible):
                edge = eligible[np.argmax(smoothed[eligible])]
                if smoothed[edge] >= 0.015:
                    peak = (np.abs(mid - mid[edge]) <= 0.151) & (np.abs(mid) <= max_shift)
                    shift = float(np.average(mid[peak], weights=gradient[peak] + 1e-12))
    intercepts, counts, noise, slopes = [], [], [], []
    for sign in (-1, 1):
        mask = ((across - shift) * sign >= side_min) & ((across - shift) * sign <= side_max)
        if mask.sum() < 5:
            if water is not None and water_level is not None:
                from shapely.geometry import Point

                if water.contains(Point(center + (shift + sign * 0.5) * normal)):
                    intercepts.append(float(water_level))
                    counts.append(0)
                    noise.append(0.0)
                    slopes.append(0.0)
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
            mad = np.median(np.abs(residual[good] - np.median(residual[good])))
            good = np.abs(residual) <= max(0.025, 3 * mad)
        if good.sum() < 5 or np.linalg.norm(coeff[:2]) > 1.0:
            return None
        coeff = np.linalg.lstsq(a[good], y[good], rcond=None)[0]
        intercepts.append(float(coeff[2]))
        counts.append(int(good.sum()))
        noise.append(float(np.median(np.abs(y[good] - a[good] @ coeff))))
        slopes.append(float(coeff[0]))
    confidence = min(1.0, min(counts) / 12) * np.exp(-max(noise) / 0.04)
    return Profile(
        shift, tuple(intercepts), tuple(counts), tuple(noise), tuple(slopes), float(confidence)
    )


def fit_profile(surface, center, normal, snap=True, water=None, water_level=None, along_limit=0.35):
    """W1-01 adapter; explicit water fallback has zero measured confidence."""
    result = measure_profile(
        surface,
        center,
        normal,
        snap=snap,
        along_limit=along_limit,
        water=water,
        water_level=water_level,
    )
    return None if result is None else (result.shift, np.asarray(result.heights))


def profiles(surface, line, spacing=1.0, **kwargs):
    d, centers, normals = stations(line, spacing)
    return [
        (float(s), p, n, measure_profile(surface, p, n, **kwargs))
        for s, p, n in zip(d, centers, normals, strict=True)
    ]


def relief_class(profile):
    step = abs(profile.step)
    if step > 1:
        return "quay"
    if step > 0.3:
        return "wall_planter_edge"
    if step > 0.18:
        return "step"
    if step >= 0.03:
        return "curb"
    if max(abs(s) for s in profile.slopes) > 0.08:
        return "ramp_edge"
    return "kerb_drop"

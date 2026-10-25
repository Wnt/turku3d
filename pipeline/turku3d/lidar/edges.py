"""Vector-independent raster discontinuities linked into measured relief polylines."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy.ndimage import median_filter
from shapely.geometry import LineString

from .profiles import measure_profile, relief_class, stations


def edge_lines(rasters, min_step=0.035, min_length=1.5):
    """Link grid faces whose height jump exceeds their local background slope.

    Nearest-fill support is bounded by surfaces(); both cells must have confidence.
    Branches split deterministically. The output is only a candidate: raw-point
    two-sided profiles must confirm it before it becomes a breakline.
    """
    h = rasters.ground
    graph = defaultdict(set)
    for axis in (0, 1):
        delta = np.diff(h, axis=axis)
        background = median_filter(np.nan_to_num(delta), size=7, mode="nearest")
        residual = np.abs(delta - background)
        a, b = (
            (rasters.confidence[:-1], rasters.confidence[1:])
            if axis == 0
            else (rasters.confidence[:, :-1], rasters.confidence[:, 1:])
        )
        mask = np.isfinite(delta) & (residual >= min_step) & (np.minimum(a, b) >= 0.1)
        for row, col in np.argwhere(mask):
            if axis == 1:
                u, v = (int(row), int(col + 1)), (int(row + 1), int(col + 1))
            else:
                u, v = (int(row + 1), int(col)), (int(row + 1), int(col + 1))
            graph[u].add(v)
            graph[v].add(u)
    used = set()
    lines = []
    nodes = sorted(graph, key=lambda n: (len(graph[n]) == 2, n))
    for start in nodes:
        for nxt in sorted(graph[start]):
            edge = tuple(sorted((start, nxt)))
            if edge in used:
                continue
            used.add(edge)
            chain = [start, nxt]
            previous, current = start, nxt
            while len(graph[current]) == 2:
                following = next(n for n in sorted(graph[current]) if n != previous)
                edge = tuple(sorted((current, following)))
                if edge in used:
                    break
                used.add(edge)
                chain.append(following)
                previous, current = current, following
            xy = [
                (
                    rasters.bbox.xmin + c * rasters.resolution,
                    rasters.bbox.zmin + r * rasters.resolution,
                )
                for r, c in chain
            ]
            line = LineString(xy).simplify(rasters.resolution * 0.8)
            if line.length >= min_length:
                lines.append(line)
    return lines


def measured_breaklines(
    surface, line, object_id, provenance, spacing=1.0, min_confidence=0.25, require_step=False
):
    """Export supported adjacent station runs in the W1-01 breakline list contract.

    Unsupported stations terminate a run; different relief classes terminate a run.
    Extra fields retain uncertainty and source geometry for coverage accounting.
    """
    distances, centers, normals = stations(line, spacing)
    measured = []
    for p, n in zip(centers, normals, strict=True):
        result = measure_profile(surface, p, n)
        if result is None:
            result = measure_profile(surface, p, n, along_limit=1.25)
        if result is not None and (
            result.confidence < min_confidence or (require_step and abs(result.step) < 0.05)
        ):
            result = None
        measured.append(result)
    output, run = [], []

    def emit():
        if len(run) < 2:
            return
        profiles = [measured[i] for i in run]
        kind = relief_class(profiles[len(profiles) // 2])
        shifted = [centers[i] + measured[i].shift * normals[i] for i in run]
        if LineString(shifted).length < 0.05:
            return
        output.append(
            {
                "object_id": f"{object_id}:{run[0]}",
                "centers_xz": [p.tolist() for p in shifted],
                "normals_xz": normals[run].tolist(),
                "side_heights": [list(p.heights) for p in profiles],
                "kind": "quay"
                if kind == "quay"
                else ("curb_dropped" if kind in ("kerb_drop", "ramp_edge") else "curb"),
                "relief_class": kind,
                "provenance": provenance,
                "confidence": [p.confidence for p in profiles],
                "step_m": [p.step for p in profiles],
                "upper_side": ["plus_normal" if p.step >= 0 else "minus_normal" for p in profiles],
                "lower_side": ["minus_normal" if p.step >= 0 else "plus_normal" for p in profiles],
                "source_chainage": distances[run].tolist(),
                "source_centers_xz": centers[run].tolist(),
            }
        )

    for i, profile in enumerate(measured):
        if profile is None:
            emit()
            run = []
        else:
            if run and (
                relief_class(profile) != relief_class(measured[run[-1]])
                or profile.step * measured[run[-1]].step < -0.0009
            ):
                # Keep the transition segment measured, with per-vertex signed heights.
                run.append(i)
                emit()
                run = [i]
            else:
                run.append(i)
    emit()
    return output, measured


def detect_edges(surface, rasters, **kwargs):
    result = []
    for i, line in enumerate(edge_lines(rasters, **kwargs)):
        fitted, _ = measured_breaklines(surface, line, f"lidar:{i}", "lidar", require_step=True)
        result.extend(fitted)
    return result

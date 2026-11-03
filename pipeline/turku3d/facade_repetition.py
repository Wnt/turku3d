"""Expand reviewed metric bay samples inside their explicitly surveyed frontage.

A repeated sample is a layout prior between measured points, not evidence for
another wall. Its source geometry anchors phase across roof and passage splits.
"""

import copy
import math


def expand(wall):
    profile = wall.get("repeated_bay")
    if not profile or profile.get("support", 0) < 0.6:
        return wall
    pitch = profile.get("pitch_m", 0)
    openings = profile.get("windows_m", [])
    if not isinstance(pitch, (int, float)) or not math.isfinite(pitch) or pitch < 0.5:
        return wall
    width, height = wall["width_m"], wall["height_m"]
    if not openings or width / pitch > 256:
        return wall
    for rect in openings:
        if (
            len(rect) != 4
            or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in rect)
            or min(rect) < 0
            or min(rect[2:]) <= 0
            or rect[0] + rect[2] > pitch
            or rect[1] + rect[3] > height
        ):
            return wall
    result = copy.deepcopy(wall)
    rectangles = []
    for i in range(math.ceil(width / pitch)):
        for x, y, w, h in openings:
            if x + i * pitch + w <= width:
                rectangles.append([x + i * pitch, y, w, h])
    result["windows"]["rectangles_m"] = rectangles
    result["windows"]["head_shapes"] = [profile.get("head_shape", "flat")] * len(rectangles)
    result["windows"]["surround_shapes"] = [profile.get("surround_shape", "none")] * len(rectangles)
    result["windows"]["opening_roles"] = ["window"] * len(rectangles)
    result["observation"].update(
        needs_review=False,
        scope="full-wall",
        dimension_source="reviewed metric bay repeated within surveyed frontage",
    )
    return result


def through_arcade(wall):
    """An explicitly reviewed through-depth opens the opposite face of the same strip.

    Only the aperture transfers: rear windows and materials keep their own priors.
    Ordinary recessed arcades never acquire a through-cut by inference.
    """
    import numpy as np

    if wall.get("through_parent"):
        return []
    features = [
        c
        for c in wall.get("geometry_components", [])
        if c.get("through")
        and c.get("kind") == "arcade"
        and c.get("support", 0) >= 0.6
        and 0 < c.get("depth_m", 0) <= 12
    ]
    if not features:
        return []
    result = []
    for component in features:
        g = wall["geometry"]
        normal = np.asarray(g["normal"], float)
        if normal.shape != (2,) or not np.isfinite(normal).all() or np.linalg.norm(normal) < 0.9:
            continue
        normal /= np.linalg.norm(normal)
        depth = component["depth_m"]
        opposite = copy.deepcopy(wall)
        opposite.pop("repeated_bay", None)
        opposite.pop("structure", None)
        opposite["wall_id"] += ":through-back"
        opposite["through_parent"] = wall["wall_id"]
        a, b = [np.asarray(p, float) - depth * normal for p in (g["p0"], g["p1"])]
        opposite["geometry"] = {
            **g,
            "p0": b.tolist(),
            "p1": a.tolist(),
            "normal": (-normal).tolist(),
        }
        x, y, w, h = component["bounds"]
        opposite["geometry_components"] = [
            {**component, "bounds": [1 - x - w, y, w, h], "no_columns": True, "no_returns": True}
        ]
        for field in (
            "windows",
            "base",
            "doors",
            "wall",
            "trim",
            "corners",
            "bays",
            "balconies",
            "bay_windows",
            "material_bands",
            "split",
        ):
            opposite.pop(field, None)
        opposite["render_sections"] = []  # No rear paint was observed.
        opposite["observation"] = {
            "needs_review": False,
            "scope": "geometry-only",
            "source": "reviewed through-colonnade depth",
        }
        result.append(opposite)
    return result

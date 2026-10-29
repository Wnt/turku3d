"""Refine a panorama rotation from facade line directions before final sampling.

Only a bounded camera rotation is fitted, never image stretching. Detected line
endpoints are lifted back into panorama rays; their wall-plane intersections must
be vertical or horizontal. Sparse or inconsistent evidence retains the input pose.
"""

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from turkutex.streetview.camera import EquirectModel
from turkutex.streetview.rectify import rectified_points, sample_wall


def fit_rotation(rays, vertical, rotation, camera, corners):
    corners, camera = np.asarray(corners), np.asarray(camera)
    tangent = corners[1] - corners[0]
    tangent /= np.linalg.norm(tangent)
    normal = np.cross(tangent, [0, 0, 1])
    distance = (corners[0] - camera) @ normal

    def residual(degrees):
        adjusted = Rotation.from_rotvec(np.radians(degrees)).as_matrix() @ rotation
        directions = rays @ adjusted.T
        denominators = directions @ normal
        denominators = np.where(abs(denominators) < 1e-6, 1e-6, denominators)
        hits = camera + directions * (distance / denominators)[..., None]
        delta = hits[:, 1] - hits[:, 0]
        length = np.maximum(np.linalg.norm(delta, axis=1), 0.1)
        return np.where(vertical, delta @ tangent, delta[:, 2]) / length

    before = residual(np.zeros(3))
    result = least_squares(residual, np.zeros(3), bounds=(-6, 6), loss="soft_l1", f_scale=0.015)
    after = residual(result.x)
    accepted = bool(
        np.median(abs(after)) < np.median(abs(before)) * 0.85
        and np.median(abs(after)) < np.sin(np.radians(1))
        and max(abs(result.x)) < 5.95
        and np.linalg.norm(result.x) < 8
        and np.linalg.matrix_rank(result.jac) == 3
    )
    correction = result.x if accepted else np.zeros(3)
    return Rotation.from_rotvec(np.radians(correction)).as_matrix() @ rotation, dict(
        accepted=accepted,
        max_axis_correction_degrees=6,
        correction_grid_rotvec_degrees=correction.tolist(),
        proposed_grid_rotvec_degrees=result.x.tolist(),
        median_before_degrees=float(np.degrees(np.arcsin(min(1, np.median(abs(before)))))),
        median_after_degrees=float(np.degrees(np.arcsin(min(1, np.median(abs(after)))))),
        vertical_lines=int(vertical.sum()),
        horizontal_lines=int((~vertical).sum()),
    )


def refine(image, view):
    corners = np.asarray(view["facade_corners_EN_H"], float)
    rotation = np.asarray(view["image_to_grid"], float)
    camera = np.asarray(view["camera"]["pos_EN_H"], float)
    points = rectified_points(corners, pixels_per_metre=24, max_side=1000)
    preview, _ = sample_wall(image, EquirectModel(0, rotation=rotation), points, camera)
    gray = cv2.cvtColor(np.clip(preview, 0, 255).astype("uint8"), cv2.COLOR_RGB2GRAY)
    lines = cv2.createLineSegmentDetector().detect(gray)[0]
    if lines is None:
        return rotation, dict(accepted=False, reason="no architectural lines")
    lines = lines.reshape(-1, 2, 2)
    d = lines[:, 1] - lines[:, 0]
    length = np.linalg.norm(d, axis=1)
    vertical = abs(d[:, 0]) < abs(d[:, 1]) * 0.20
    horizontal = abs(d[:, 1]) < abs(d[:, 0]) * 0.20
    keep = (length >= 25) & (vertical | horizontal)
    # Ground vehicles and pavement lines are not facade pose evidence.
    keep &= lines[:, :, 1].max(1) < gray.shape[0] * 0.83
    lines, vertical = lines[keep], vertical[keep]
    if vertical.sum() < 5 or (~vertical).sum() < 5:
        return rotation, dict(
            accepted=False,
            reason="fewer than five lines in both directions",
            vertical_lines=int(vertical.sum()),
            horizontal_lines=int((~vertical).sum()),
        )
    wallpoints = (
        corners[0]
        + (lines[..., 0:1] + 0.5) / gray.shape[1] * (corners[1] - corners[0])
        + (1 - (lines[..., 1:2] + 0.5) / gray.shape[0]) * (corners[3] - corners[0])
    )
    rays = (wallpoints - camera) @ rotation
    return fit_rotation(rays, vertical, rotation, camera, corners)

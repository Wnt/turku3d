"""Conservative shape-based small-object candidates, never authoritative semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
from scipy.ndimage import find_objects, label

ObjectKind = Literal["pole", "bollard", "bench", "low_wall", "planter"]


@dataclass
class SmallObject:
    object_id: str
    kind: ObjectKind
    center_xyz: tuple[float, float, float]
    height: float
    length: float
    width: float
    yaw: float
    confidence: float
    observed_cells: int

    def as_dict(self):
        return asdict(self)


def small_objects(rasters, min_height=0.2, max_height=5.0):
    """Connected nDSM blobs + PCA shape. Building/canopy blobs are rejected by size.

    These are typed hypotheses for later props, not ready-to-ship collision objects.
    A planter and bench of similar footprint cannot be distinguished reliably by height.
    """
    ndsm = rasters.ndsm
    mask = (
        np.isfinite(ndsm)
        & (ndsm >= min_height)
        & (ndsm <= max_height)
        & (rasters.confidence >= 0.1)
    )
    labels, _ = label(mask, structure=np.ones((3, 3)))
    objects = []
    res = rasters.resolution
    for ident, region in enumerate(find_objects(labels), 1):
        if region is None:
            continue
        rr, cc = np.nonzero(labels[region] == ident)
        rr += region[0].start
        cc += region[1].start
        height = float(np.quantile(ndsm[rr, cc], 0.9))
        xy = np.c_[cc, rr].astype(float) * res
        centered = xy - xy.mean(axis=0)
        if len(xy) >= 3:
            _, axes = np.linalg.eigh(centered.T @ centered / len(xy))
            major = axes[:, -1]
        else:
            major = np.array([1.0, 0.0])
        minor = np.array([-major[1], major[0]])
        length = float(np.ptp(centered @ major) + res)
        width = float(np.ptp(centered @ minor) + res)
        length, width = max(length, width), min(length, width)
        if length <= 0.65:
            kind = "pole" if height > 1.3 else "bollard"
        elif length / width >= 4 and length <= 20 and height <= 2:
            kind = "low_wall"
        elif 0.8 <= length <= 3 and width <= 1.1 and 0.35 <= height <= 1.3:
            kind = "bench"
        elif length <= 4 and width <= 3 and height <= 1.5:
            kind = "planter"
        else:
            continue
        ground = float(np.nanmedian(rasters.ground[rr, cc]))
        center = (
            float(rasters.bbox.xmin + xy[:, 0].mean() + res / 2),
            ground,
            float(rasters.bbox.zmin + xy[:, 1].mean() + res / 2),
        )
        confidence = float(np.mean(rasters.confidence[rr, cc]) * 0.6)
        objects.append(
            SmallObject(
                f"object:{ident}",
                kind,
                center,
                height,
                length,
                width,
                float(np.arctan2(major[1], major[0])),
                confidence,
                len(rr),
            )
        )
    return objects

"""Recover semantic faces from unlabelled city LoD1 solids without changing surveys."""

import numpy as np

KINDS = ("GroundSurface", "RoofSurface", "WallSurface")


def classify_surfaces(frame):
    """Keep explicit LoD2 semantics; infer only complete, unlabelled LoD1 solids.

    Vertical faces have no horizontal projected area. The solid's lowest horizontal
    faces are floors; other nonvertical faces form its roof. Terrain intersection
    curves and redundant LoD1 solids accompanying semantic LoD2 are excluded.
    """
    frame = frame[frame.geom_type == "Polygon"].copy()
    if "surface_type" not in frame:
        return frame.iloc[:0].assign(surface_type="")
    for _, group in frame.groupby("feature_key", sort=False):
        if group.surface_type.isin(KINDS).any() or "geom_role" not in group:
            continue
        solid = group[group.geom_role == "lod1Solid"]
        if solid.empty:
            continue
        faces = []
        for row in solid.itertuples():
            xyz = np.asarray(row.geometry.exterior.coords)
            if xyz.shape[1] != 3 or not np.isfinite(xyz).all():
                continue
            # Translation keeps the shoelace area stable at national-grid coordinates.
            xy = xyz[:, :2] - xyz[0, :2]
            area = abs(np.sum(xy[:-1, 0] * xy[1:, 1] - xy[1:, 0] * xy[:-1, 1])) / 2
            faces.append((row.Index, area, xyz[:, 2].min(), xyz[:, 2].max()))
        if not faces:
            continue
        bottom = min(lo for _, _, lo, _ in faces)
        floors = [i for i, area, _, hi in faces if area > 0.01 and hi <= bottom + 0.05]
        roofs = [i for i, area, lo, _ in faces if area > 0.01 and lo > bottom + 0.05]
        if not floors or not roofs:
            continue
        for i, area, _, _ in faces:
            frame.at[i, "surface_type"] = (
                "GroundSurface" if i in floors else "RoofSurface" if area > 0.01 else "WallSurface"
            )
    return frame[frame.surface_type.isin(KINDS)]

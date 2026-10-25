#!/usr/bin/env python3
"""Conservative photo-to-lidar intensity registration using SIFT and held-out PnP.

Reject weak cross-modal matches; never overwrite the original registry automatically.
Coordinates in point caches are EPSG:3877/N2000, not float32 engine positions.
"""
import argparse
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image
from qalib import camera, registry
from qalib.paths import data_dir, resolve


def cloud_cache(laz_path, out):
    import laspy
    rows = []
    with laspy.open(laz_path) as reader:
        for chunk in reader.chunk_iterator(1_000_000):
            xyz = np.column_stack((chunk.x, chunk.y, chunk.z))
            # A decimetre voxel retains building edges while bounding memory.
            keep = (xyz[:, 2] > -2) & (xyz[:, 2] < 100)
            xyz = xyz[keep]
            _, ids = np.unique(np.floor(xyz / 0.15).astype(np.int64), axis=0, return_index=True)
            rows.append(np.column_stack((xyz[ids], np.asarray(chunk.intensity)[keep][ids])))
    points = np.concatenate(rows)
    np.savez_compressed(out, points=points)
    return points


def render_lidar(points, pose, w, h):
    origin = np.array([pose['E'], pose['N'], pose['H']])
    f, right, up = camera.basis(pose['yaw'], pose['pitch'], pose['roll'])
    xyz = points[:, :3] - origin
    z = xyz @ f
    use = z > 0.5
    xyz, z, rows = xyz[use], z[use], points[use]
    focal = h / (2 * math.tan(math.radians(pose['fov']) / 2))
    x = np.floor(w / 2 + focal * (xyz @ right) / z).astype(int)
    y = np.floor(h / 2 - focal * (xyz @ up) / z).astype(int)
    valid = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    x, y, z, rows = x[valid], y[valid], z[valid], rows[valid]
    order = np.argsort(z)
    _, first = np.unique((y * w + x)[order], return_index=True)
    selected = order[first]
    image = np.zeros((h, w), np.uint8)
    world = np.full((h, w, 3), np.nan)
    if len(selected):
        low, high = np.percentile(rows[:, 3], [2, 98])
        values = np.clip((rows[:, 3] - low) / max(1, high-low) * 220 + 30, 0, 255).astype(np.uint8)
        image[y[selected], x[selected]] = values[selected]
        world[y[selected], x[selected]] = rows[selected, :3]
    return image, world


def match_pose(photo, lidar, world, pose):
    sift = cv2.SIFT_create(nfeatures=3000)
    ka, da = sift.detectAndCompute(photo, None)
    kb, db = sift.detectAndCompute(lidar, None)
    result = {'method': 'SIFT intensity + ratio 0.7 + PnP RANSAC + held-out validation',
              'photo_features': len(ka), 'lidar_features': len(kb), 'accepted': False}
    if da is None or db is None:
        return result | {'reason': 'no descriptors'}
    matches = [m for pair in cv2.BFMatcher().knnMatch(da, db, k=2) if len(pair) == 2
               for m, n in [pair] if m.distance < 0.7 * n.distance]
    pixels, points = [], []
    used = set()
    for m in matches:
        x, y = np.round(kb[m.trainIdx].pt).astype(int)
        if 0 <= y < world.shape[0] and 0 <= x < world.shape[1] and np.isfinite(world[y, x]).all() and m.trainIdx not in used:
            pixels.append(ka[m.queryIdx].pt); points.append(world[y, x]); used.add(m.trainIdx)
    result['matches_with_depth'] = len(points)
    if len(points) < 16:
        return result | {'reason': 'fewer than 16 distinct depth-backed matches'}
    center = np.array([pose['E'], pose['N'], pose['H']])
    points, pixels = np.array(points) - center, np.array(pixels)
    h, w = photo.shape
    focal = h / (2 * math.tan(math.radians(pose['fov']) / 2))
    K = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.]])
    train = np.arange(len(points)) % 4 != 0
    cv2.setRNGSeed(17)
    ok, rotation, translation, inliers = cv2.solvePnPRansac(points[train], pixels[train], K, None,
        iterationsCount=500, reprojectionError=3, confidence=.999, flags=cv2.SOLVEPNP_EPNP)
    if not ok or inliers is None or len(inliers) < 12:
        return result | {'reason': 'insufficient geometrically consistent PnP inliers'}
    projected, _ = cv2.projectPoints(points[~train], rotation, translation, K, None)
    residual = np.linalg.norm(projected[:, 0] - pixels[~train], axis=1)
    R, _ = cv2.Rodrigues(rotation)
    position = -R.T @ translation[:, 0]
    f = R[2]
    yaw = math.degrees(math.atan2(f[0], f[1])) % 360
    pitch = math.degrees(math.asin(np.clip(f[2], -1, 1)))
    roll = math.degrees(math.atan2(-R[0] @ camera.basis(yaw, pitch)[2], R[0] @ camera.basis(yaw, pitch)[1]))
    displacement = float(np.linalg.norm(position))
    angle = abs((yaw-pose['yaw']+180)%360-180)
    accepted = bool(np.quantile(residual, .75) < 3 and displacement < 3 and angle < 5 and abs(pitch-pose['pitch']) < 5)
    return result | {'accepted': accepted, 'inliers': len(inliers), 'holdout_p75_px': float(np.quantile(residual, .75)),
                     'displacement_m': displacement, 'reason': 'validated' if accepted else 'holdout or pose bound failed',
                     'candidate_pose': {**pose, **dict(zip(['E','N','H'], center+position)), 'yaw': yaw, 'pitch': pitch, 'roll': roll}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--views', default=str(registry.default_registry()))
    ap.add_argument('--ids', required=True)
    ap.add_argument('--laz', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=data_dir()/'registration')
    a = ap.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    cache = a.out / (a.laz.stem + '-points.npz')
    points = np.load(cache)['points'] if cache.exists() else cloud_cache(a.laz, cache)
    results=[]
    for v in registry.load(a.views):
        if v['id'] not in a.ids.split(','): continue
        w, h = 640, round(640 * v['height']/v['width'])
        photo = np.array(Image.open(resolve(v['image'])).convert('L').resize((w,h)))
        lidar, world = render_lidar(points, v['pose'], w, h)
        Image.fromarray(lidar).save(a.out/f"{v['id']}-lidar.png")
        result = {'view_id': v['id'], **match_pose(photo, lidar, world, v['pose'])}
        results.append(result); print(json.dumps(result), flush=True)
    (a.out/'results.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__': main()

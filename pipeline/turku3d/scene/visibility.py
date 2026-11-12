"""Prepare indirect-light visibility from final supports and source roof planes.

This reuses the existing one-metre solid height-field horizon bake. It changes
indirect light only; no sun direction, photographed shading or pigment is baked.
Tree porosity and multi-level bridge occlusion require separate source models.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from scipy.interpolate import NearestNDInterpolator

from turku3d.coarse import visibility as ambient
from turku3d.ground.mesh import HeightQuery, Mesh


def bake_scene(scene, ground_points, buildings, waters=()):
    by_id = {mesh['id']: mesh for mesh in scene['meshes']}
    positions, indices, offset = [], [], 0
    for support in scene['supports']:
        mesh = by_id[support['meshId']]
        vertices = np.asarray(mesh['positions']).reshape(-1, 3)
        positions.append(vertices)
        indices.append(np.asarray(mesh['indices']).reshape(-1, 3) + offset)
        offset += len(vertices)
    if not positions:
        raise ValueError('Ambient preparation requires final support geometry')
    positions, indices = np.concatenate(positions), np.concatenate(indices)
    query = HeightQuery(Mesh(positions, indices, np.zeros(len(indices), np.uint8)))
    # Sample a source halo as well as the playable area. Clamping the pilot's
    # ground boundary would otherwise invent level terrain beyond its edge.
    x0, z0, x1, z1 = scene['bounds']
    pad = ambient.FAR + 2 * ambient.CELL
    origin = np.array([x0-pad, 0, z0-pad])
    size = max(x1-x0, z1-z0) + 2 * pad
    stations = np.linspace(0, size, int(np.ceil(size / ambient.CELL)) + 1)
    xx, zz = np.meshgrid(stations + origin[0], stations + origin[2])
    xz = np.c_[xx.ravel(), zz.ravel()]
    heights = query(xz)
    missing = ~np.isfinite(heights)
    points = np.asarray(ground_points)
    heights[missing] = NearestNDInterpolator(points[:, [0, 2]], points[:, 1])(xz[missing])
    if waters:
        water = shapely.union_all(waters)
        surface = next((m['water']['point'][1] for m in scene['materials'] if 'water' in m), 0.)
        heights[shapely.contains_xy(water, xz[:, 0], xz[:, 1])] = surface
    field = ambient.height_field(SimpleNamespace(origin=origin, size=size), buildings, heights.reshape(xx.shape))
    count, vertices = 0, 0
    for mesh in scene['meshes']:
        if mesh.get('render') is False or mesh['materialId'] in ('water', 'glass'):
            continue
        local = np.asarray(mesh['positions']).reshape(-1, 3) - origin
        normals = np.asarray(mesh['normals']).reshape(-1, 3)
        values = ambient.sample(field, local, normals)
        mesh['bakedVisibility'] = np.rint(values * 255).astype(np.uint8).ravel().tolist()
        count += 1
        vertices += len(local)
    recipe = Path(__file__).read_bytes() + Path(ambient.__file__).read_bytes()
    scene['sources'].append(dict(id=scene['id']+':ambient-visibility', kind='compiled-ambient-visibility',
        sha256=hashlib.sha256(recipe).hexdigest(), uncertaintyM=ambient.CELL))
    return dict(meshes=count, vertices=vertices, terrainFallbackSamples=int(missing.sum()))

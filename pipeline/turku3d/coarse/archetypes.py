"""Whole-building HD models, independently packed and addressed by immutable payload bytes.

Run after wall ownership/assembly so facade programs and vfix surface corrections
remain authoritative. Placement coordinates use the common engine frame; model
vertices and conservative occluders use metres relative to the placement origin.
"""
from __future__ import annotations

import copy
import hashlib
import math
import json

import numpy as np
import shapely

from turku3d import config as C
from turku3d.layers import write_visual
from turku3d.wall_surfaces import instanced_layer, layer_meshes
from .packaging import package_visual


def partition(tile, meshes, shells):
    """Separate assembled surfaces without clipping or reinterpreting their materials."""
    by_building = {}
    terrain = []
    for mesh in meshes:
        key = mesh.extras.get("building_id")
        if mesh.extras.get("layer") != "buildings" or key is None:
            terrain.append(mesh)
        else:
            by_building.setdefault(str(key), []).append(mesh)
    sources = {}
    for building, shell in shells:
        sources.setdefault(str(building.facade_key), []).append((building, shell))
    models = []
    for key, parts in sorted(by_building.items()):
        if key not in sources:
            raise ValueError(f"Building model {key} has no owned source shell")
        buildings = sources[key]
        footprint = shapely.union_all([b.footprint for b, _ in buildings])
        centre = footprint.centroid
        origin = np.array([centre.x, 0., centre.y])
        delta = np.asarray(tile.origin) - origin
        local = []
        for mesh in parts:
            placed = copy.copy(mesh)
            placed.positions = np.asarray(mesh.positions, dtype=np.float64) + delta
            placed.extras = copy.deepcopy(mesh.extras)
            box = placed.extras.get("component_box")
            if box:
                box["center"] = (np.asarray(box["center"]) + delta).tolist()
            local.append(placed)
        models.append((key, origin, local, buildings))
    return terrain, models


def view_distance(bounds):
    """24-pixel projected diameter at 1080p/60 degrees, bounded for HD-only residency.

    The minimum keeps nearby small buildings intact before the HLOD parent exists.
    Distance is measured from the model AABB by the runtime, not from its centre.
    """
    diameter = float(np.linalg.norm(np.asarray(bounds[1]) - bounds[0]))
    return round(max(250., min(2000., diameter * 1080 / (2 * math.tan(math.pi / 6) * 24))), 3)


def publish(tile, layer_files, shells, stage, work, library):
    from .build import _write

    static = [path for path in layer_files if not instanced_layer(path)]
    terrain, models = partition(tile, layer_meshes(static), shells)
    terrain_path = stage / "terrain-placements.vis.glb"
    write_visual(terrain_path, terrain, root_translation=tile.origin)
    layers = [path for path in layer_files if path not in static] + [terrain_path]
    descriptors, placements, metrics = [], [], {}
    all_bounds = [np.array([tile.origin[0], 0., tile.origin[2]]),
                  np.array([tile.origin[0] + tile.size, 0., tile.origin[2] + tile.size])]
    for mesh in terrain:
        if len(mesh.positions):
            all_bounds.extend([np.asarray(mesh.positions).min(axis=0) + tile.origin,
                               np.asarray(mesh.positions).max(axis=0) + tile.origin])
    for key, origin, meshes, buildings in models:
        identity = hashlib.sha256(key.encode()).hexdigest()
        directory = stage / "archetypes" / identity
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "model.vis.glb"
        write_visual(source, meshes, root_translation=(0., 0., 0.))
        payload, stats = package_visual(C.VisualTile(0, 0, 0), [source], directory, work, library,
                                        cache_unit=f"archetypes/{identity}")
        digest = hashlib.sha256(payload).hexdigest()
        destination = work / "shared" / f"{digest}.glb"
        if not destination.exists():
            _write(destination, payload)
        bounds = stats["bounds"]
        descriptor = dict(id=digest, uri=f"../shared/{digest}.glb", bytes=len(payload),
                          tris=stats["tris"], draws=stats["draws"],
                          bounds=dict(min=bounds[0], max=bounds[1]))
        descriptors.append(descriptor)
        placements.append(dict(id=key, model=digest, position=origin.tolist(),
                               viewDistance=view_distance(bounds)))
        all_bounds.extend([origin + bounds[0], origin + bounds[1]])
        metrics[key] = dict(tris=stats["tris"], draws=stats["draws"], bytes=len(payload),
                            package=json.loads((directory / 'package-times.json').read_text()))
    points = np.asarray(all_bounds)
    result = dict(version=1, models=descriptors, placements=placements,
                  bounds=dict(min=points.min(axis=0).tolist(), max=points.max(axis=0).tolist()))
    return layers, result, metrics

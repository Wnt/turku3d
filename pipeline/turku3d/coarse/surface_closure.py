"""Resolve dry ground against actual emitted floors; fill cuts with source terrain.

Runs after terrain enrichment and structural producers, before both exporters.
A source cut polygon is never evidence of coverage: only emitted triangles own it.
Tunnel clearance remains authoritative after filling. Vertically separated decks
are different sheets and do not erase lower ground.
"""
from pathlib import Path

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh
from turku3d.stage_cache import source_key
from . import banks, stairs, surfaces
from .surface_audit import normals, triangles, Rays


def footprint(mesh):
    t = triangles(mesh)
    good = abs(normals(t)[:, 1]) > 1e-9
    return shapely.union_all(shapely.polygons(t[good][:, :, [0, 2]]))


def clean(mesh):
    """Weld identical positions and discard duplicate/degenerate emitted faces."""
    p, inverse = np.unique(mesh.positions, axis=0, return_inverse=True)
    ix = inverse[mesh.indices]
    area = np.linalg.norm(normals(p[ix]), axis=1) / 2
    valid = area >= 1e-6
    keys = np.c_[np.sort(ix, axis=1), mesh.material]
    _, first = np.unique(keys, axis=0, return_index=True)
    valid &= np.isin(np.arange(len(ix)), first)
    return Mesh(p, ix[valid].astype(np.uint32), mesh.material[valid]).compact()


def floor_coverage(meshes, reference, *, same_sheet=False):
    """Only walkable top faces, never railings, slab bottoms or wall caps."""
    parts = []
    source = Rays(triangles(reference)) if same_sheet else None
    for m in meshes:
        if not m.visual or not m.metadata.get('collision', True):
            continue
        if m.material in ('wall', 'railing', 'fence', 'water', 'building', 'roof'):
            continue
        t = triangles(m)
        n = normals(t)
        keep = n[:, 1] > np.linalg.norm(n, axis=1) * .25
        t = t[keep]
        if same_sheet and len(t):
            mid = t.mean(axis=1)
            y = source.cast(mid[:, [0, 2]], mid[:, 1])
            t = t[np.isfinite(y) & (abs(y - mid[:, 1]) <= .3)]
        parts.extend(shapely.polygons(t[:, :, [0, 2]]))
    return shapely.union_all(parts)


def clip_to(mesh, domain):
    if domain.is_empty:
        return Mesh(np.empty((0,3)), np.empty((0,3), np.uint32), np.empty(0,np.uint8))
    # Reuse the exact constrained overlay and original plane interpolation.
    outside = shapely.box(*mesh_bounds(mesh)).buffer(1).difference(domain)
    return stairs.cut_ground(mesh, [outside])


def mesh_bounds(mesh):
    p = mesh.positions[:, [0, 2]]
    return (*p.min(axis=0), *p.max(axis=0))


def resolve(reference, dry, solid, structures, decks, openings, excluded):
    original_count = len(dry.indices)
    dry = clean(dry)
    # Structures > stair/deck floors > paving > fallback terrain. Paving/terrain
    # already share one constrained material mesh; resolve actual structural top
    # coverage, including the portions lost when landing cuts were too generous.
    occupied = floor_coverage(structures, reference)
    occupied = occupied.union(floor_coverage(decks, reference, same_sheet=True))
    dry = stairs.cut_ground(dry, [occupied]) if not occupied.is_empty else dry
    solid = stairs.cut_ground(solid, [occupied]) if not occupied.is_empty else solid
    domain = footprint(reference).difference(excluded)
    missing = domain.difference(footprint(dry).union(occupied))
    # Preserve all meaningful holes, including long narrow cracks. Microscopic
    # overlay remnants below the COL minimum face area cannot represent a floor.
    missing = shapely.union_all([p for p in shapely.get_parts(missing) if p.area >= 1e-6])
    fallback = clip_to(reference, missing)
    fallback = openings.ground(fallback)
    fallback = clean(fallback)
    dry = clean(banks.join(dry, fallback))
    solid = clean(banks.join(solid, fallback))
    # No producer follows this with another terrain fill. Clearance therefore
    # cannot be restored accidentally by a late bridge apron or attachment.
    dry, solid = openings.ground(dry), openings.ground(solid)
    return dry, solid, dict(filled_area_m2=round(footprint(fallback).area, 6),
                           fallback_triangles=len(fallback.indices),
                           triangles_before=original_count, triangles_after=len(dry.indices),
                           uncovered_area_m2=round(domain.difference(footprint(dry).union(occupied)).area, 6))


def stage(cache, reference, dry, solid, structures, decks, openings, excluded):
    return cache.run('surface_closure',
                     source_key(Path(__file__), Path(surfaces.__file__), stairs.cut_ground,
                                Path(__file__).with_name('openings.py'),
                                Path(__file__).with_name('surface_audit.py')),
                     [reference, dry, solid, structures, decks, openings, excluded],
                     lambda: resolve(reference, dry, solid, structures, decks, openings, excluded))

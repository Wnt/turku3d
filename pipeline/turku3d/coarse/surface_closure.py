"""Seal open vertical seams in the walkable ground; one shared visual + collision face set.

Runs after every ground producer (stairs, ledges, aprons, bridge tapers, kerbs) and
before both exporters. A seam is a boundary edge of the dry ground sheet where the
surface just across the edge differs in height and no emitted face (bank, stair
side, retaining wall, kerb) closes the gap. Seen from the side it shows the void
below the higher sheet. Each such edge gets a vertical stone face spanning the
height difference, shipped as a structure mesh in the visual and collision alike.

The stage never deletes or moves emitted ground. Producers own their cuts; the
earlier whole-triangle coverage fill removed ground under elevated decks and in
practice filled under 0.3 m2 per tile at about 16 s per tile.
"""
from pathlib import Path

import numpy as np
import shapely

from turku3d.ground.mesh import Mesh
from turku3d.stage_cache import source_key
from turku3d.structures.geometry import Faces
from .surface_audit import normals, triangles, Rays

STEP = .08       # height differences below this are paving joints, not seams
ACROSS = .2      # plan distance at which the neighbouring surface is sampled
REACH = 6.       # vertical search window for the neighbouring surface


def clean(mesh):
    """Weld identical positions and discard duplicate/degenerate emitted faces."""
    p, inverse = np.unique(mesh.positions, axis=0, return_inverse=True)
    ix = inverse.reshape(-1)[mesh.indices]
    area = np.linalg.norm(normals(p[ix]), axis=1) / 2
    valid = area >= 1e-6
    keys = np.c_[np.sort(ix, axis=1), mesh.material]
    _, first = np.unique(keys, axis=0, return_index=True)
    valid &= np.isin(np.arange(len(ix)), first)
    return Mesh(p, ix[valid].astype(np.uint32), mesh.material[valid]).compact()


def boundary(mesh):
    """Plan boundary edges (a, b) with the outward plan normal of their single owner."""
    ix = mesh.indices.astype(np.int64)
    edges = np.stack([ix[:, [0, 1]], ix[:, [1, 2]], ix[:, [2, 0]]], axis=1).reshape(-1, 2)
    third = np.stack([ix[:, 2], ix[:, 0], ix[:, 1]], axis=1).reshape(-1)
    key = np.sort(edges, axis=1)
    _, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    single = counts[inverse.reshape(-1)] == 1
    edges, third = edges[single], third[single]
    p = mesh.positions
    a, b, c = p[edges[:, 0]], p[edges[:, 1]], p[third]
    d = (b - a)[:, [0, 2]]
    length = np.linalg.norm(d, axis=1)
    n = np.c_[-d[:, 1], d[:, 0]] / np.maximum(length, 1e-12)[:, None]
    flip = np.sum(n * (c - a)[:, [0, 2]], axis=1) > 0
    n[flip] *= -1
    keep = length > .02
    return a[keep], b[keep], n[keep], length[keep]


def crossed(tris, start, end, height):
    """Whether any triangle crosses each horizontal segment start->end at its height."""
    hit = np.zeros(len(start), bool)
    if not len(start) or not len(tris):
        return hit
    tree = shapely.STRtree(shapely.box(*tris[:, :, [0, 2]].min(1).T, *tris[:, :, [0, 2]].max(1).T))
    si, ti = tree.query(shapely.linestrings(np.stack([start, end], axis=1)), predicate='intersects')
    t, h = tris[ti], height[si]
    y = t[:, :, 1] - h[:, None]
    # Plane section of each candidate triangle at the segment height.
    pts, count = np.zeros((len(t), 2, 2)), np.zeros(len(t), int)
    for i, j in ((0, 1), (1, 2), (2, 0)):
        cut = (y[:, i] * y[:, j] < 0) | ((y[:, i] == 0) & (y[:, j] != 0))
        w = y[:, i] / np.where(cut, y[:, i] - y[:, j], 1)
        q = t[:, i, [0, 2]] + w[:, None] * (t[:, j, [0, 2]] - t[:, i, [0, 2]])
        slot = np.minimum(count, 1)
        pts[cut, slot[cut]] = q[cut]
        count += cut
    ok = count >= 2
    if not ok.any():
        return hit
    p1, p2 = pts[ok, 0], pts[ok, 1]
    s1, s2 = start[si[ok]], end[si[ok]]

    def orient(a, b, c):
        return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])

    eps = 1e-9
    o1, o2 = orient(s1, s2, p1), orient(s1, s2, p2)
    o3, o4 = orient(p1, p2, s1), orient(p1, p2, s2)
    cross = (o1 * o2 <= eps) & (o3 * o4 <= eps)
    np.logical_or.at(hit, si[ok][cross], True)
    return hit


def seams(dry, surfaces, walls, excluded, bounds):
    """Open seams of dry: arrays a, b (edge ends), ha, hb (heights across), length."""
    a, b, n, length = boundary(dry)
    x0, z0, x1, z1 = bounds
    mid = (a + b)[:, [0, 2]] / 2 + n * ACROSS
    inside = (mid[:, 0] > x0) & (mid[:, 0] < x1) & (mid[:, 1] > z0) & (mid[:, 1] < z1)
    if excluded is not None and not excluded.is_empty:
        inside &= ~shapely.intersects_xy(excluded, mid[:, 0], mid[:, 1])
    a, b, n, length, mid = a[inside], b[inside], n[inside], length[inside], mid[inside]
    rays = Rays(surfaces)
    own = (a[:, 1] + b[:, 1]) / 2
    across = rays.cast(mid, own, above=REACH)
    found = np.isfinite(across) & (abs(across - own) > STEP) & (own - across < REACH)
    holes = ~np.isfinite(across)
    # The higher side owns the face; a lower dry edge facing a higher dry edge
    # leaves the face to that edge, but faces any higher structure itself.
    higher = found & (across > own)
    if higher.any():
        dry_side = Rays(triangles(dry)).cast(mid[higher], across[higher], above=.05)
        lift = np.zeros(len(a), bool)
        lift[np.flatnonzero(higher)] = ~(np.isfinite(dry_side) & (abs(dry_side - across[higher]) < .05))
        found &= ~higher | lift
    ids = np.flatnonzero(found)
    edge_mid = (a[ids] + b[ids])[:, [0, 2]] / 2
    closed = crossed(walls, edge_mid - n[ids] * .05, edge_mid + n[ids] * (ACROSS + .1),
                     (own[ids] + across[ids]) / 2)
    ids = ids[~closed]
    # Sample the neighbouring heights near each end, so a sloped seam stays tight.
    along = (b - a)[ids][:, [0, 2]] / np.maximum(length[ids], 1e-12)[:, None]
    inset = np.minimum(.1, length[ids] / 3)[:, None]
    end_a = a[ids][:, [0, 2]] + along * inset + n[ids] * ACROSS
    end_b = b[ids][:, [0, 2]] - along * inset + n[ids] * ACROSS
    ha = rays.cast(end_a, a[ids, 1], above=REACH)
    hb = rays.cast(end_b, b[ids, 1], above=REACH)
    ha = np.where(np.isfinite(ha), ha, across[ids])
    hb = np.where(np.isfinite(hb), hb, across[ids])
    return a[ids], b[ids], ha, hb, length[ids], float(length[holes].sum())


def faces(a, b, ha, hb, tile_id):
    from .bridge_details import appearance

    if not len(a):
        return []
    lo_a, lo_b = a.copy(), b.copy()
    lo_a[:, 1], lo_b[:, 1] = ha, hb
    # Overlap the neighbouring sheet slightly so no hairline remains at its edge.
    sign_a, sign_b = np.sign(a[:, 1] - ha), np.sign(b[:, 1] - hb)
    lo_a[:, 1] -= sign_a * .03
    lo_b[:, 1] -= sign_b * .03
    f = Faces()
    # Both windings: either side of a seam may be the one in view.
    f.quads(np.stack([a, b, lo_b, lo_a], axis=1))
    f.quads(np.stack([b, a, lo_a, lo_b], axis=1))
    return [appearance(f.mesh(f"seam:{tile_id}", "wall", metadata=dict(kind="retaining_wall")), "stone")]


def resolve(dry, structures, decks, bank, openings, excluded, bounds, tile_id='tile'):
    dry = clean(dry)
    floors = [triangles(m) for m in [*structures, *decks]
              if m.visual and m.material not in ('railing', 'fence', 'water')]
    surfaces = np.concatenate([triangles(dry), *floors, triangles(bank)]) if floors else \
        np.concatenate([triangles(dry), triangles(bank)])
    walls = np.concatenate([surfaces, *[triangles(m) for m in structures if m.visual]])
    a, b, ha, hb, length, hole_length = seams(dry, surfaces, walls, excluded, bounds)
    meshes = openings.structures(faces(a, b, ha, hb, tile_id))
    span = np.abs((a[:, 1] + b[:, 1]) / 2 - (ha + hb) / 2)
    return meshes, dict(sealed_seams=int(len(a)), sealed_length_m=round(float(length.sum()), 3),
                        sealed_area_m2=round(float((span * length).sum()), 3),
                        max_step_m=round(float(span.max()), 3) if len(span) else 0.,
                        open_hole_edges_m=round(hole_length, 3))


def stage(cache, tile, dry, structures, decks, bank, openings, excluded):
    from .bridge_details import appearance

    return cache.run('surface_closure',
                     source_key(Path(__file__), Path(__file__).with_name('openings.py'),
                                Path(__file__).with_name('surface_audit.py'),
                                Path(__file__).parents[1] / 'structures/geometry.py', appearance),
                     [tile, dry, structures, decks, bank, openings, excluded],
                     lambda: resolve(dry, structures, decks, bank, openings, excluded,
                                     tile.bbox.as_tuple(), tile.id))

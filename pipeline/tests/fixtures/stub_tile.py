"""Legacy Phase 0 synthetic packaging fixture; never used by production assembly."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from fixtures import ground as G
from turku3d import config as C
from turku3d.col.format import ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.ground.materials import surface_material as katualue_material
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.package.manifest import DistTree

STEP = 12.5  # Tiny fixtures still exercise all 64 collision tiles and their shared edges.
CELLS_PER_COL = int(round(C.COLLISION_TILE_SIZE / STEP))
DEFAULT_MATERIAL = "grass"


class MaterialMap:
    """Point -> material id from street-area polygons (EPSG:3877). Without polygons: DEFAULT_MATERIAL."""

    def __init__(self, polys=None, names=None):
        self.default = mat_id(DEFAULT_MATERIAL)
        self.polys, self.ids = polys, None
        if polys is not None and len(polys):
            import shapely

            self.tree = shapely.STRtree(polys)
            self.ids = np.array([mat_id(n) for n in names], np.uint8)

    def lookup(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        out = np.full(len(x), self.default, np.uint8)
        if self.ids is None:
            return out
        import shapely

        pts = shapely.points(x + C.ORIGIN_E, C.ORIGIN_N - z)
        pi, gi = self.tree.query(pts, predicate="within")
        out[pi] = self.ids[gi]
        return out


def synthetic_points(tile: C.VisualTile, seed: int = 7) -> np.ndarray:
    """Gentle slope + a 1.5 m mound, ~1 pt / 100 m²; for tests and --synthetic.

    The points cover the tile's 500 m L1 parent (like a LAZ tile), so every L0 tile of the same parent
    interpolates from the identical point set and L0 seams match (see fixtures.ground.load_ground_points)."""
    p = tile.parent() if tile.level == 0 else tile
    rng = np.random.default_rng([seed, p.i % 2**32, p.j % 2**32])
    bb = p.bbox.buffered(20)
    n = int((bb.xmax - bb.xmin) * (bb.zmax - bb.zmin) / 100)
    x = rng.uniform(bb.xmin, bb.xmax, n)
    z = rng.uniform(bb.zmin, bb.zmax, n)
    cx, cz = (p.bbox.xmin + p.bbox.xmax) / 2, (p.bbox.zmin + p.bbox.zmax) / 2
    h = 2.0 + 0.01 * (x - bb.xmin) + 1.5 * np.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2 * 30.0**2))
    return np.stack([x, z, h], 1)


def build(
    tile_id: str,
    out: Path,
    *,
    area: str = "office-block",
    synthetic: bool = False,
    ortho: bool = True,
    tex_px: int = 2048,
    code: str | None = None,
) -> dict:
    t0 = time.time()
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError("stub_tile builds L0 tiles only")
    bb = tile.bbox
    n = int(round(tile.size / STEP))
    if not synthetic:
        raise ValueError(
            "stub fixtures require synthetic=True; use turku3d.package.assemble for layers"
        )
    pts = synthetic_points(tile)
    H = G.heightfield(pts, bb.xmin, bb.zmin, n, n, STEP)
    point_count = len(pts)
    mats = MaterialMap()
    tree = DistTree(out, areas=[area if not synthetic else "synthetic"])

    # collision: 4x4 tiles, each a 6x6-node slice of the same heightfield (exact shared seams)
    col_stats = []
    for ct in tile.collision_tiles():
        di, dj = ct.i - tile.i * C.COLLISION_PER_VISUAL, ct.j - tile.j * C.COLLISION_PER_VISUAL
        sub = H[
            dj * CELLS_PER_COL : (dj + 1) * CELLS_PER_COL + 1,
            di * CELLS_PER_COL : (di + 1) * CELLS_PER_COL + 1,
        ]
        pos, tris = G.grid_mesh(sub, ct.bbox.xmin, ct.bbox.zmin, STEP)
        cen = pos[tris].mean(1)
        m = mats.lookup(cen[:, 0], cen[:, 2])
        # AOI means the playable Phase 1 visual envelope, not each L0 tile separately.
        aoi = {c.id for v in C.OFFICE_BLOCK.visual_tiles for c in v.collision_tiles()}
        edge = any(
            C.CollisionTile(ct.i + dx, ct.j + dz).id not in aoi
            for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1))
        )
        ctile = ColTile.for_tile(
            ct.i,
            ct.j,
            pos,
            tris,
            m,
            aoi_border=edge,
            meta={
                "tile": ct.id,
                "stub": "phase0-heightfield",
                "step_m": STEP,
                "source": "synthetic" if synthetic else "maanpinta key points + GIS:Katualueet",
            },
        )
        data = write_col(ctile)
        e = tree.add_collision(
            ct,
            data,
            tris=len(tris),
            prims=0,
            h=(float(sub.min()), float(sub.max())),
            flags=ctile.flags,
        )
        col_stats.append((ct.id, e["bytes"]))

    # visual: one ground mesh over the whole L0 tile, tile-local, ortho texture
    pos, tris = G.grid_mesh(H, bb.xmin, bb.zmin, STEP)
    local = (pos - np.asarray(tile.origin)).astype(np.float32)
    uv = np.stack([(pos[:, 0] - bb.xmin) / tile.size, (pos[:, 2] - bb.zmin) / tile.size], 1).astype(
        np.float32
    )
    img = None
    gid = f"ground_{tile.id}"
    obj = {"kind": "ground", "id": gid, "tile": tile.id, "material": "ground"}
    mesh = GlbMesh(gid, local, tris, uvs=uv, image=img, color=(0.45, 0.5, 0.4, 1.0), extras=obj)
    glb = build_glb(
        [mesh],
        root_translation=tile.origin,
        root_name=tile.id,
        root_extras={"tile": tile.id, "objects": [obj], "stub": True},
    )
    tree.add_visual(tile, glb, tris=len(tris), h=(float(H.min()), float(H.max())))
    m = tree.commit(code=code)
    summary = {
        "tile": tile.id,
        "out": str(out),
        "ground_points": int(point_count),
        "h": [float(H.min()), float(H.max())],
        "visual_bytes": len(glb),
        "visual_tris": int(len(tris)),
        "textured": img is not None,
        "collision_tiles": len(col_stats),
        "collision_bytes": sum(b for _, b in col_stats),
        "manifest_tiles": {"visual": len(m["visual"]), "collision": len(m["collision"])},
        "seconds": round(time.time() - t0, 2),
    }
    return summary

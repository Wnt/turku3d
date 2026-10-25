"""Phase 0 stub tile builder: `python -m turku3d.package.stub_tile --tile t0_0_0`.

Builds one visual L0 tile and its 16 collision tiles into dist/tiles/ from cached raw data:
  ground   maanpinta key points -> 1.25 m heightfield (turku3d.ground.stub), shared by visual + collision
  material per collision triangle from GIS:Katualueet street areas (road/sidewalk/cobblestone/...), else grass
  texture  2022 true ortho cropped to the tile (2048 px JPEG), grey where the AOI download has no pixels
  output   visual/0/<tile>.glb, collision/<tile>/c_i_j.col (.col v1.0 via turku3d.col), tileset.json, manifest.json

`--synthetic` replaces every input with generated data (no raw files, no geopandas/rasterio/laspy needed);
the pipeline tests use it. This is a stub: no curbs, stairs, buildings or KTX2/meshopt in the GLB yet.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.format import ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.ground import stub as G
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.package.manifest import DistTree, verify_tree

CELLS_PER_COL = int(round(C.COLLISION_TILE_SIZE / G.STEP))  # 50
DEFAULT_MATERIAL = "grass"

# GIS:Katualueet -> materials.md v1 (Katuosalaji first, then Pintamateriaali)
_KATUOSA = {"Ajorata": "road", "Jalkakäytävä": "sidewalk", "Pyöräkaista": "cycleway",
            "Yhdistetty kevyen liikenteen väylä": "sidewalk", "Silta": "bridge_deck", "Portaat": "paving_slab"}
_PINTA = [("noppakivi", "cobblestone"), ("nupukivi", "cobblestone"), ("luonnonkivi", "cobblestone"),
          ("kenttäkivi", "cobblestone"), ("graniitti", "cobblestone"), ("betonilaatta", "paving_slab"),
          ("betonikivi", "paving_slab"), ("betoni", "paving_slab"), ("sora", "gravel"), ("asfalt", "road")]


def katualue_material(katuosalaji: str | None, pinta: str | None) -> str:
    if isinstance(katuosalaji, str) and katuosalaji in _KATUOSA:
        return _KATUOSA[katuosalaji]
    p = pinta.lower() if isinstance(pinta, str) else ""
    for key, name in _PINTA:
        if key in p:
            return name
    return "paving_slab"


class MaterialMap:
    """Point -> material id from street-area polygons (EPSG:3877). Without polygons: DEFAULT_MATERIAL."""

    def __init__(self, polys=None, names=None):
        self.default = mat_id(DEFAULT_MATERIAL)
        self.polys, self.ids = polys, None
        if polys is not None and len(polys):
            import shapely

            self.tree = shapely.STRtree(polys)
            self.ids = np.array([mat_id(n) for n in names], np.uint8)

    @classmethod
    def from_wfs(cls, area: str) -> "MaterialMap":
        f = C.raw_dir("wfs") / area / "GIS_Katualueet.parquet"
        if not f.exists():
            print(f"stub_tile: {f} missing -> all ground is {DEFAULT_MATERIAL}", file=sys.stderr)
            return cls()
        import geopandas as gpd

        g = gpd.read_parquet(f, columns=["geometry", "Katuosalaji", "Pintamateriaali"])
        g = g[g.geometry.notna() & g.geom_type.isin(["Polygon", "MultiPolygon"])]
        names = [katualue_material(k, p) for k, p in zip(g["Katuosalaji"], g["Pintamateriaali"])]
        return cls(np.asarray(g.geometry.values), names)

    def lookup(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        out = np.full(len(x), self.default, np.uint8)
        if self.ids is None:
            return out
        import shapely

        pts = shapely.points(x + C.ORIGIN_E, C.ORIGIN_N - z)
        pi, gi = self.tree.query(pts, predicate="within")
        out[pi] = self.ids[gi]
        return out


def ortho_texture(tile: C.VisualTile, px: int = 2048, layer: str = "ortho2022to", area: str = "office-block",
                  quality: int = 85) -> bytes | None:
    """JPEG of the ortho mosaic over the tile (row 0 = north edge = tile z0). None if no mosaic."""
    vrt = C.raw_dir("ortho") / layer / f"{area}.vrt"
    if not vrt.exists():
        print(f"stub_tile: {vrt} missing -> untextured ground", file=sys.stderr)
        return None
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from PIL import Image

    b = tile.bbox.to_en()
    with rasterio.open(vrt) as ds:
        win = from_bounds(b.emin, b.nmin, b.emax, b.nmax, transform=ds.transform)
        rgb = ds.read([1, 2, 3], window=win, out_shape=(3, px, px), boundless=True, fill_value=0,
                      resampling=Resampling.average)
        mask = ds.read_masks(1, window=win, out_shape=(px, px), boundless=True, resampling=Resampling.nearest)
    img = np.moveaxis(rgb, 0, -1).astype(np.uint8)
    img[mask == 0] = (118, 122, 112)  # outside the downloaded AOI: neutral grey-green
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def synthetic_points(tile: C.VisualTile, seed: int = 7) -> np.ndarray:
    """Gentle slope + a 1.5 m mound, ~1 pt / 100 m²; for tests and --synthetic.

    The points cover the tile's 500 m L1 parent (like a LAZ tile), so every L0 tile of the same parent
    interpolates from the identical point set and L0 seams match (see ground.stub.load_ground_points)."""
    p = tile.parent() if tile.level == 0 else tile
    rng = np.random.default_rng([seed, p.i % 2**32, p.j % 2**32])
    bb = p.bbox.buffered(20)
    n = int((bb.xmax - bb.xmin) * (bb.zmax - bb.zmin) / 100)
    x = rng.uniform(bb.xmin, bb.xmax, n); z = rng.uniform(bb.zmin, bb.zmax, n)
    cx, cz = (p.bbox.xmin + p.bbox.xmax) / 2, (p.bbox.zmin + p.bbox.zmax) / 2
    h = 2.0 + 0.01 * (x - bb.xmin) + 1.5 * np.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2 * 30.0**2))
    return np.stack([x, z, h], 1)


def build(tile_id: str, out: Path, *, area: str = "office-block", synthetic: bool = False, ortho: bool = True,
          tex_px: int = 2048, code: str | None = None) -> dict:
    t0 = time.time()
    tile = C.VisualTile.parse(tile_id)
    if tile.level != 0:
        raise ValueError("stub_tile builds L0 tiles only")
    bb = tile.bbox
    n = int(round(tile.size / G.STEP))
    if synthetic:
        pts = synthetic_points(tile)
        H = G.heightfield(pts, bb.xmin, bb.zmin, n, n)
        point_count = len(pts)
    else:
        Harea, abb, point_count = G.area_heightfield(C.AREAS[area])
        i, j = round((bb.xmin - abb.xmin) / G.STEP), round((bb.zmin - abb.zmin) / G.STEP)
        if i < 0 or j < 0 or i + n >= Harea.shape[1] or j + n >= Harea.shape[0]:
            raise ValueError(f"{tile.id} outside {area}")
        H = Harea[j:j+n+1, i:i+n+1]
    mats = MaterialMap() if synthetic else MaterialMap.from_wfs(area)
    tree = DistTree(out, areas=[area if not synthetic else "synthetic"])

    # collision: 4x4 tiles, each a 51x51-node slice of the same heightfield (exact shared seams)
    col_stats = []
    for ct in tile.collision_tiles():
        di, dj = ct.i - tile.i * C.COLLISION_PER_VISUAL, ct.j - tile.j * C.COLLISION_PER_VISUAL
        sub = H[dj * CELLS_PER_COL: (dj + 1) * CELLS_PER_COL + 1, di * CELLS_PER_COL: (di + 1) * CELLS_PER_COL + 1]
        pos, tris = G.grid_mesh(sub, ct.bbox.xmin, ct.bbox.zmin)
        cen = pos[tris].mean(1)
        m = mats.lookup(cen[:, 0], cen[:, 2])
        # AOI means the playable Phase 1 visual envelope, not each L0 tile separately.
        aoi = {c.id for v in C.OFFICE_BLOCK.visual_tiles for c in v.collision_tiles()}
        edge = any(C.CollisionTile(ct.i + dx, ct.j + dz).id not in aoi
                   for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        ctile = ColTile.for_tile(ct.i, ct.j, pos, tris, m, aoi_border=edge,
                                 meta={"tile": ct.id, "stub": "phase0-heightfield", "step_m": G.STEP,
                                       "source": "synthetic" if synthetic else "maanpinta key points + GIS:Katualueet"})
        data = write_col(ctile)
        e = tree.add_collision(ct, data, tris=len(tris), prims=0, h=(float(sub.min()), float(sub.max())),
                               flags=ctile.flags)
        col_stats.append((ct.id, e["bytes"]))

    # visual: one ground mesh over the whole L0 tile, tile-local, ortho texture
    pos, tris = G.grid_mesh(H, bb.xmin, bb.zmin)
    local = (pos - np.asarray(tile.origin)).astype(np.float32)
    uv = np.stack([(pos[:, 0] - bb.xmin) / tile.size, (pos[:, 2] - bb.zmin) / tile.size], 1).astype(np.float32)
    img = ortho_texture(tile, tex_px, area=area) if (ortho and not synthetic) else None
    gid = f"ground_{tile.id}"
    obj = {"kind": "ground", "id": gid, "tile": tile.id, "material": "ground"}
    mesh = GlbMesh(gid, local, tris, uvs=uv, image=img, color=(0.45, 0.5, 0.4, 1.0), extras=obj)
    glb = build_glb([mesh], root_translation=tile.origin, root_name=tile.id,
                    root_extras={"tile": tile.id, "objects": [obj], "stub": True})
    tree.add_visual(tile, glb, tris=len(tris), h=(float(H.min()), float(H.max())))
    m = tree.commit(code=code)
    summary = {"tile": tile.id, "out": str(out), "ground_points": int(point_count), "h": [float(H.min()), float(H.max())],
               "visual_bytes": len(glb), "visual_tris": int(len(tris)), "textured": img is not None,
               "collision_tiles": len(col_stats), "collision_bytes": sum(b for _, b in col_stats),
               "manifest_tiles": {"visual": len(m["visual"]), "collision": len(m["collision"])},
               "seconds": round(time.time() - t0, 2)}
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.package.stub_tile", description=__doc__.split("\n")[0])
    ap.add_argument("--tile", nargs="+", default=[C.VisualTile.at(*C.en_to_local(C.OFFICE_E, C.OFFICE_N)[::2]).id],
                    help="visual L0 tile id(s) (default: the office tile t0_0_0)")
    ap.add_argument("--out", type=Path, default=None, help="dist/tiles directory (default <data_root>/dist/tiles)")
    ap.add_argument("--area", default="office-block", help="raw WFS/ortho area name")
    ap.add_argument("--synthetic", action="store_true", help="generated inputs only (tests)")
    ap.add_argument("--no-ortho", action="store_true")
    ap.add_argument("--tex-px", type=int, default=2048)
    a = ap.parse_args(argv)
    out = a.out or (C.dist_dir() / "tiles")
    for t in a.tile:
        s = build(t, out, area=a.area, synthetic=a.synthetic, ortho=not a.no_ortho, tex_px=a.tex_px)
        print(json.dumps(s))
    from turku3d.package.seams import verify_seams
    bad = verify_tree(out) + verify_seams(out)
    print(f"dist check {out}: " + ("OK" if not bad else "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

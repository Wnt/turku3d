"""Coarse visual-only DTM coverage from cached maanpinta and ortho, never L0/collision fallback."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh


def discover(tile: C.VisualTile) -> dict | None:
    """Require complete core LAZ and ortho coverage; do not fabricate terrain outside the inputs."""
    root = C.raw_dir('laz') / 'maanpinta'
    core = [root / (t.name + '.laz') for t in C.laz_tiles_for_bbox(tile.bbox)]
    if not core or not all(p.exists() for p in core):
        return None
    import rasterio
    bbox = tile.bbox.to_en()
    for ortho in sorted((C.raw_dir('ortho') / 'ortho2022to').glob('*.vrt')):
        with rasterio.open(ortho) as ds:
            b = ds.bounds
            if b.left > bbox.emin or b.right < bbox.emax or b.bottom > bbox.nmin or b.top < bbox.nmax:
                continue
        images = []
        for node in ET.parse(ortho).iter('SourceFilename'):
            p = Path(node.text)
            images.append(ortho.parent / p if node.get('relativeToVRT') == '1' else p)
        if not images or not all(p.is_file() for p in images):
            continue
        laz = sorted({root / (t.name + '.laz') for t in C.laz_tiles_for_bbox(tile.bbox.buffered(C.MESH_MARGIN))
                      if (root / (t.name + '.laz')).exists()})
        return {'laz': laz, 'ortho': ortho, 'files': sorted(set(laz + images + [ortho]))}
    return None


def sample_grid(points: np.ndarray, tile: C.VisualTile):
    """15.625 m grid, inverse-distance blend of the four nearest observed ground key points."""
    from scipy.spatial import cKDTree
    if len(points) < 4 or not np.isfinite(points).all():
        raise ValueError('coarse DTM requires at least four finite ground points')
    cells = int(round(tile.size / (C.COLLISION_TILE_SIZE / 4)))
    axis = np.linspace(0, tile.size, cells + 1)
    x, z = np.meshgrid(axis + tile.origin[0], axis + tile.origin[2])
    query = np.column_stack([x.ravel(), z.ravel()])
    distance, indices = cKDTree(points[:, [0, 2]]).query(query, k=4)
    weights = 1 / np.maximum(distance, 1e-9)**2
    heights = (weights * points[indices, 1]).sum(axis=1) / weights.sum(axis=1)
    pos = np.column_stack([query[:, 0], heights, query[:, 1]])
    a = (np.arange(cells)[:, None] * (cells + 1) + np.arange(cells)).ravel()
    idx = np.concatenate([np.column_stack([a, a+cells+1, a+1]),
                          np.column_stack([a+1, a+cells+1, a+cells+2])]).astype(np.uint32)
    uv = (query - np.array(tile.origin)[[0, 2]]) / tile.size
    return pos, idx, uv


def build(tile: C.VisualTile, sources: dict, path: Path, points_cache: dict) -> Path:
    import laspy
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from PIL import Image
    for source in sources['laz']:
        if source not in points_cache:
            las = laspy.read(source)
            points_cache[source] = np.column_stack([np.asarray(las.x)-C.ORIGIN_E, np.asarray(las.z),
                                                   C.ORIGIN_N-np.asarray(las.y)])
    pos, idx, uv = sample_grid(np.concatenate([points_cache[p] for p in sources['laz']]), tile)
    bbox = tile.bbox.to_en()
    with rasterio.open(sources['ortho']) as ds:
        win = from_bounds(bbox.emin, bbox.nmin, bbox.emax, bbox.nmax, ds.transform)
        rgb = ds.read([1, 2, 3], window=win, out_shape=(3, 1024, 1024), resampling=Resampling.bilinear)
    stream = io.BytesIO()
    Image.fromarray(np.moveaxis(rgb, 0, -1).astype(np.uint8)).save(stream, format='PNG')
    mesh = GlbMesh(tile.id+'-dtm', pos-np.array(tile.origin), idx, uvs=uv, image=stream.getvalue(),
                   extras=dict(layer='ground', object_id=tile.id+'-dtm', material_key='ortho',
                               texture_uv='normalized', coarse_dtm=True))
    write_visual(path, [mesh], root_translation=tile.origin)
    return path

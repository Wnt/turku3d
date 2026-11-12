"""The city's WFS register layers over any box, read per 500 m tile and fetched on demand.

`layer('GIS:Katualueet', box)` returns the features that intersect the box (survey frame, EPSG:3877) with every
attribute as a string. A tile comes from the read-only raw mirror when an earlier fetch covers it, otherwise from
the shared source cache, which is filled from the city's server on first use.
"""
from __future__ import annotations

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import shapely

from turku3d import config as C

from .lidar import sources_root

TILE = 500.0


def _slug(name: str) -> str:
    return name.replace(':', '_')


@lru_cache(maxsize=None)
def _mirror(slug: str) -> tuple:
    """Raw-mirror files of a layer with the box each covers, smallest first."""
    found = []
    for summary in C.raw_dir('wfs').glob(f'*/{slug}.summary.json'):
        try:
            meta = json.loads(summary.read_text())
        except (OSError, ValueError):
            continue
        path = summary.with_name(f'{slug}.parquet')
        if meta.get('status') in ('ok', 'empty') and not meta.get('gap') and path.exists():
            found.append((tuple(meta['bbox_en']), path))
    return tuple(sorted(found, key=lambda item: (item[0][2] - item[0][0]) * (item[0][3] - item[0][1])))


@lru_cache(maxsize=64)
def _read(path: Path):
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    return frame.drop(columns=[c for c in ('geometry_local',) if c in frame.columns])


def _fetch(name: str, box) -> 'gpd.GeoDataFrame':
    """Every feature of the layer that intersects the box, by quadtree paging under the server's feature cap."""
    from turku3d.fetch import wfs
    from turku3d.fetch.common import http_get
    from turku3d.fetch.sources import WFS_FEATURE_CAP, WFS_LAYERS, WFS_URL

    layer = next(item for item in WFS_LAYERS if item.name == name)
    with tempfile.TemporaryDirectory(prefix='wfs-pages-') as scratch:
        pages, stack = [], [tuple(box)]
        while stack:
            part = stack.pop()
            body, _ = http_get(WFS_URL, wfs.request_params(layer, part), timeout=120)
            wfs.check_body(body, name)
            if wfs.count_features(body) >= WFS_FEATURE_CAP and part[2] - part[0] > wfs.MIN_PAGE_M:
                stack.extend(wfs.split4(part))
                continue
            page = Path(scratch) / f'{wfs.page_key(part)}.gml'
            page.write_bytes(body)
            pages.append(page)
        frame, _ = wfs.pages_to_frame(sorted(pages), name)
    return frame.drop(columns=[c for c in ('geometry_local',) if c in frame.columns])


def _tile(name: str, e0: float, n0: float):
    slug = _slug(name)
    box = (e0, n0, e0 + TILE, n0 + TILE)
    for cover, path in _mirror(slug):
        if cover[0] <= box[0] and cover[1] <= box[1] and cover[2] >= box[2] and cover[3] >= box[3]:
            return _read(path)
    cached = sources_root() / 'wfs' / slug / f'{int(e0)}_{int(n0)}.parquet'
    if not cached.exists():
        frame = _fetch(name, box)
        cached.parent.mkdir(parents=True, exist_ok=True)
        part = cached.with_name(f'{cached.name}.{os.getpid()}.part')
        frame.to_parquet(part, index=False)
        part.replace(cached)
    return _read(cached)


def layer(name: str, box):
    """Features of a register layer intersecting `box` = (e_min, n_min, e_max, n_max), one row per geometry."""
    import geopandas as gpd
    import pandas as pd

    e_min, n_min, e_max, n_max = box
    window = shapely.box(*box)
    frames = []
    seen_paths = set()
    east = int(e_min // TILE)
    while east * TILE < e_max:
        north = int(n_min // TILE)
        while north * TILE < n_max:
            frame = _tile(name, east * TILE, north * TILE)
            if id(frame) not in seen_paths and len(frame):
                seen_paths.add(id(frame))
                frames.append(frame[frame.geometry.intersects(window)])
            north += 1
        east += 1
    if not frames:
        return gpd.GeoDataFrame({'layer': [], 'feature_key': []}, geometry=[], crs=C.CRS_HORIZONTAL)
    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates(['feature_key', 'geom_index']).reset_index(drop=True)

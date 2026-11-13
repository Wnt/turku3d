"""OpenStreetMap features over any box, from the clipped GeoParquet files of the raw mirror.

The mirror holds one clip per fetch area; a clip is found by the box its directory name states (`coarse-<E>_<N>` is
the 500 m square, `coarse-osm-<E>_<N>` the 2 km square, whose north-west corner is E, N). Rows keep the common tags
as columns and every tag in `tags` (JSON).

The clips cover the inner city only. A box they do not cover reads the city-wide table cut from the national extract
(world/sources/osm.py: same tag columns, same survey frame), so ground, water, rail and trees of the suburbs and the
countryside see the open map too (until 2026-11-13 they saw nothing there: no verges, parks, fields or shores).
"""
from __future__ import annotations

import re
from functools import lru_cache

import shapely

from turku3d import config as C

_NAMED = re.compile(r'^coarse-(osm-)?(\d+)_(\d+)$')


@lru_cache(maxsize=1)
def _clips() -> tuple:
    """(box, parquet) of every clip whose extent its name states, smallest first."""
    found = []
    for folder in C.raw_dir('osm').iterdir():
        match = _NAMED.match(folder.name)
        files = sorted(folder.glob('*.parquet')) if match else []
        if files:
            side = 2000.0 if match.group(1) else 500.0
            east, north = float(match.group(2)), float(match.group(3))
            found.append(((east, north - side, east + side, north), files[-1]))
    return tuple(sorted(found, key=lambda item: item[0][2] - item[0][0]))


@lru_cache(maxsize=16)
def _read(path):
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    return frame.drop(columns=[c for c in ('geometry_local',) if c in frame.columns])


@lru_cache(maxsize=1)
def _city():
    from ..sources import Sources

    return Sources(log=lambda *_: None).osm


def features(box):
    """OSM rows whose geometry intersects `box` = (e_min, n_min, e_max, n_max): from the clips where they cover the
    box, else from the city-wide table (see the module text)."""
    import pandas as pd

    window = shapely.box(*box)
    for cover, path in _clips():
        if cover[0] <= box[0] and cover[1] <= box[1] and cover[2] >= box[2] and cover[3] >= box[3]:
            frame = _read(path)
            return frame[frame.geometry.intersects(window)]
    touching = [(cover, path) for cover, path in _clips() if shapely.box(*cover).intersects(window)]
    if not shapely.union_all([shapely.box(*cover) for cover, _ in touching]).covers(window):
        return _city().features(box)
    # A box that straddles clips: stitch the clips that touch it, each object once.
    parts = [_read(path) for cover, path in touching]
    parts = [frame[frame.geometry.intersects(window)] for frame in parts]
    parts = [frame for frame in parts if len(frame)]
    if not parts:
        return None
    # A closed way is two rows of a clip, its line and its area: each object once per geometry type, or a stitched box
    # keeps a building's or park's outline line and loses its area.
    merged = pd.concat(parts, ignore_index=True)
    return merged.loc[~merged.assign(_kind=merged.geom_type).duplicated(['osm_type', 'osm_id', '_kind'])].reset_index(drop=True)

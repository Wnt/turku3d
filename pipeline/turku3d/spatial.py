"""Bounded per-worker area indexes; select complete source features before Python work."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely


@lru_cache(maxsize=16)
def _area(path, size, modified):
    import geopandas as gpd

    try:
        data = gpd.read_parquet(path)
    except ValueError as error:
        # Minimal source fixtures also use ordinary Parquet with local WKB.
        if "geo" not in str(error).lower():
            raise
        import pyarrow.parquet as pq

        data = pq.read_table(path).to_pandas()
        for name in ("geometry", "geometry_local"):
            if name in data:
                data[name] = gpd.GeoSeries(shapely.from_wkb(data[name].values), index=data.index)
        data = gpd.GeoDataFrame(
            data, geometry="geometry" if "geometry" in data else "geometry_local"
        )
    data = data.reset_index(drop=True)
    geometry = shapely.force_2d(data.geometry_local.values)
    return data, geometry, shapely.STRtree(geometry)


def frame(path, bbox=None, *, whole_features=False):
    """A private frame in original row order, with complete uncut local geometries.

    Building surfaces select entire feature groups, including faces outside the
    query window, so LoD1 classification and multipart ownership stay unchanged.
    Raw files are immutable; changed size/mtime invalidates the bounded index.
    """
    path = Path(path)
    stat = path.stat()
    data, geometry, tree = _area(str(path), stat.st_size, stat.st_mtime_ns)
    if bbox is None:
        return data.copy()
    # Bounds also retain closed OSM ways enclosing the query. Consumers may turn
    # those lines into polygons before their precise intersection test.
    ids = np.sort(tree.query(shapely.box(*bbox.as_tuple())))
    if whole_features:
        keys = data.iloc[ids].feature_key.unique()
        return data[data.feature_key.isin(keys)].copy()
    return data.iloc[ids].copy()


def rows(path, bbox=None):
    """Original row identities alongside records, including already decoded geometry."""
    selected = frame(path, bbox)
    return zip(selected.index, selected.to_dict("records"), strict=True)

"""City surface-material mapping with an orthophoto vegetation fallback."""

from __future__ import annotations

import numpy as np

from turku3d import config as C
from turku3d.col.materials import mat_id


def surface_material(kind, surface):
    """Physical paving overrides the road-use label; otherwise use functional class."""
    surface = surface.lower() if isinstance(surface, str) else ""
    kind = kind if isinstance(kind, str) else ""
    for token, name in [
        ("noppakivi", "cobblestone"),
        ("nupukivi", "cobblestone"),
        ("luonnonkivi", "cobblestone"),
        ("kenttäkivi", "cobblestone"),
        ("graniitti", "cobblestone"),
        ("sora", "gravel"),
        ("kivituhka", "gravel"),
    ]:
        if token in surface:
            return name
    # Bridge support uses this class to keep decks above water. Asphalt cycle
    # lanes also retain their transport semantics while sharing the road map.
    if "asfalt" in surface:
        return {"Silta": "bridge_deck", "Pyöräkaista": "cycleway"}.get(kind, "road")
    # Asphalt concrete (asfalttibetoni) was handled above. Concrete paving
    # must not inherit asphalt just because the polygon is a carriageway.
    if "betoni" in surface:
        return {"Silta": "bridge_deck", "Jalkakäytävä": "sidewalk"}.get(kind, "paving_slab")
    return {
        "Ajorata": "road",
        "Jalkakäytävä": "sidewalk",
        "Pyöräkaista": "cycleway",
        "Yhdistetty kevyen liikenteen väylä": "sidewalk",
        "Silta": "bridge_deck",
        "Portaat": "paving_slab",
    }.get(kind, "paving_slab")


class Materials:
    def __init__(self, frame, ortho):
        import shapely

        self.polygons, self.ids = [], []
        for _, row in frame.iterrows():
            if row.geometry_local.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            self.polygons.append(shapely.make_valid(shapely.force_2d(row.geometry_local)))
            self.ids.append(mat_id(surface_material(row.Katuosalaji, row.Pintamateriaali)))
        self.tree = shapely.STRtree(self.polygons)
        self.ids = np.array(self.ids, np.uint8)
        self.ortho = ortho
        self.raster = None

    def lookup(self, xz):
        import rasterio
        import shapely
        from rasterio.enums import Resampling

        xz = np.asarray(xz)
        out = np.full(len(xz), mat_id("paving_slab"), np.uint8)
        # One downsampled orthophoto, not one GDAL read per triangle. Conservative RGB
        # vegetation fallback: unknown non-green areas remain generic paving.
        if self.raster is None:
            with rasterio.open(self.ortho) as ds:
                scale = max(1, max(ds.width, ds.height) / 4096)
                w, h = int(ds.width / scale), int(ds.height / scale)
                self.raster = ds.read([1, 2, 3], out_shape=(3, h, w), resampling=Resampling.average)
                self.transform = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
        row, col = rasterio.transform.rowcol(
            self.transform, xz[:, 0] + C.ORIGIN_E, C.ORIGIN_N - xz[:, 1]
        )
        valid = (
            (row >= 0) & (row < self.raster.shape[1]) & (col >= 0) & (col < self.raster.shape[2])
        )
        rgb = self.raster[
            :, np.clip(row, 0, self.raster.shape[1] - 1), np.clip(col, 0, self.raster.shape[2] - 1)
        ].astype(float)
        green = valid & (rgb[1] > 1.08 * rgb[0]) & (rgb[1] > 1.08 * rgb[2]) & (rgb[1] > 25)
        out[green] = mat_id("grass")
        qi, pi = self.tree.query(shapely.points(xz), predicate="within")
        out[qi] = self.ids[pi]
        return out

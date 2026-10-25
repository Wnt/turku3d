"""Region selection without changing the canonical coordinate/grid definitions."""

from pathlib import Path

import yaml
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from turku3d import config as C


def load_area(name: str, directory: Path | None = None) -> C.Area:
    if name in C.AREAS:
        return C.AREAS[name]
    if Path(name).name != name:
        raise ValueError("area must be a name, not a path")
    path = (directory or C.DATA_MANIFESTS / "areas") / f"{name}.yaml"
    data = yaml.safe_load(path.read_text())
    if data.get("crs", C.CRS_COMPOUND) not in (C.CRS_COMPOUND, C.CRS_HORIZONTAL):
        raise ValueError("area coordinates must use EPSG:3877")
    if ("tiles" in data) == ("polygon_en" in data):
        raise ValueError("define exactly one of tiles or polygon_en")
    if "tiles" in data:
        tiles = sorted({C.VisualTile.parse(t) for t in data["tiles"]})
        if not tiles or any(t.level != 0 for t in tiles):
            raise ValueError("tiles must contain L0 visual tile IDs")
        geometry = unary_union([box(*t.bbox.as_tuple()) for t in tiles])
    else:
        geometry = Polygon(
            [(C.en_to_local(e, n)[0], C.en_to_local(e, n)[2]) for e, n in data["polygon_en"]]
        )
        if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
            raise ValueError("polygon_en must be a valid, nonempty polygon")
        tiles = [
            t
            for t in C.visual_tiles_for_bbox(C.BBoxLocal(*geometry.bounds))
            if geometry.intersection(box(*t.bbox.as_tuple())).area > 0
        ]
    margin = float(data.get("margin_m", C.MESH_MARGIN))
    if margin < 0:
        raise ValueError("margin_m must be nonnegative")
    return RegionArea(
        name,
        data.get("description", name),
        C.BBoxLocal(*geometry.bounds),
        margin,
        extra={"tiles": tiles, "geometry": geometry},
    )


class RegionArea(C.Area):
    @property
    def visual_tiles(self):
        return self.extra["tiles"]

    @property
    def collision_tiles(self):
        return [c for t in self.visual_tiles for c in t.collision_tiles()]

    @property
    def laz_tiles(self):
        return sorted(
            {
                laz_tile
                for t in self.visual_tiles
                for laz_tile in C.laz_tiles_for_bbox(t.bbox.buffered(self.margin))
            }
        )


def geometry(area):
    """The shipped tile envelope, including all of office-block's four visual tiles."""
    return unary_union([box(*t.bbox.as_tuple()) for t in area.visual_tiles])

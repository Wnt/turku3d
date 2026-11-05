"""MML 0.5 p laser scanning -> raw/laz/pistepilvi/<block>.laz in Turku's classes (a city without open lidar).

The keyless MML mirror holds the newest 0.5 p scan per 3 x 3 km sheet (Espoo: 2008, about 1 point/m²,
classes 1 unclassified, 2 ground, 3 low points, 9 water). Turku's classified ``pistepilvi`` blocks carry
building (6) and low/medium/high vegetation (3/4/5) classes, which roof refinement, LiDAR tree crowns,
fence/stair/retaining surveys and wires read. Each block is reclassified against today's sources:

* height above ground from the block's ground key points (``raw/laz/maanpinta``);
* points inside a current LoD2 footprint more than ``ROOF_HAG`` above ground are building returns (6),
  unless that building's points disagree with its LoD2 roof by more than ``CHANGED_M``: the building was
  built or rebuilt after the scan, so its returns are dropped instead of pulling today's roof to 2008's;
* other unclassified returns become vegetation by height (3 < 1 m, 4 < 2.5 m, 5 above); vegetation over
  today's carriageways and within ``FOOTPRINT_BUFFER`` of a building is dropped (gone since the scan);
* ground, water and noise keep their classes. Heights are N2000 (MML 2008-2019 data, verified per block
  against the ground key points: ``ground_offset_m`` in the registry record).
"""

from __future__ import annotations

import numpy as np

from .. import config
from ..cities import MML_FUNET
from .common import Registry, curl_download, curl_head, human, kind_dir

ROOF_HAG = 2.0
CHANGED_M = 2.5
FOOTPRINT_BUFFER = 1.0
LAZ_CRS = "EPSG:3067"
VERSION = 1


def _sheets(block):
    """(relative path, url) of the MML sheets under the block (index in raw/mml/laz-index)."""
    import geopandas as gpd
    import shapely
    from pyproj import Transformer

    root = config.raw_dir("mml")
    index = gpd.read_file(root / "laz-index" / "2008_latest.shp")
    b = block.bbox_en
    t = Transformer.from_crs(config.CRS_HORIZONTAL, LAZ_CRS, always_xy=True)
    x, y = t.transform([b.emin, b.emax, b.emin, b.emax], [b.nmin, b.nmin, b.nmax, b.nmax])
    hit = index[index.intersects(shapely.box(min(x) - 5, min(y) - 5, max(x) + 5, max(y) + 5))]
    return [(p.split("2008_latest/", 1)[1], f"{MML_FUNET}/{p.removeprefix('mml/')}") for p in hit.path]


def sheet_paths(block, log=print):
    root = config.raw_dir("mml")
    reg = Registry(root)
    out = []
    for rel, url in _sheets(block):
        dest = root / "laz" / rel
        if not reg.is_complete(dest):
            head = curl_head(url)
            curl_download(url, dest, reg, expected_bytes=head["content_length"],
                          last_modified=head["last_modified"], etag=head["etag"])
            log(f"mml  {rel}: downloaded {human(dest.stat().st_size)}")
        out.append((dest, url))
    return out


def _ground_grid(block):
    """Bilinear sampler over the block's 2 m ground key points (city CRS E/N)."""
    import laspy

    path = config.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
    las = laspy.read(path)
    e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
    b = block.bbox_en
    step = 2.0
    nx, ny = int(round((b.emax - b.emin) / step)), int(round((b.nmax - b.nmin) / step))
    grid = np.full((ny, nx), np.nan)
    i = np.clip(((e - b.emin) / step).astype(int), 0, nx - 1)
    j = np.clip(((b.nmax - n) / step).astype(int), 0, ny - 1)
    grid[j, i] = h
    if np.isnan(grid).any():  # sparse key points: nearest fill
        from scipy.ndimage import distance_transform_edt

        idx = distance_transform_edt(np.isnan(grid), return_distances=False, return_indices=True)
        grid = grid[tuple(idx)]

    def sample(qe, qn):
        fx = np.clip((qe - b.emin) / step - 0.5, 0, nx - 1.001)
        fy = np.clip((b.nmax - qn) / step - 0.5, 0, ny - 1.001)
        x0, y0 = fx.astype(int), fy.astype(int)
        x1, y1 = np.minimum(x0 + 1, nx - 1), np.minimum(y0 + 1, ny - 1)
        tx, ty = fx - x0, fy - y0
        return ((grid[y0, x0] * (1 - tx) + grid[y0, x1] * tx) * (1 - ty)
                + (grid[y1, x0] * (1 - tx) + grid[y1, x1] * tx) * ty)

    return sample


def _buildings(block):
    """[(footprint polygon in E/N, LoD2 roof median height)] of the block's current buildings."""
    import geopandas as gpd
    import shapely

    from turku3d.coarse.sources import AREA_PREFIX

    path = config.raw_dir("wfs") / f"{AREA_PREFIX}{block.name}" / "bldg_Building_LOD2.parquet"
    if not path.exists():
        return []
    f = gpd.read_parquet(path, columns=["gml_id", "geometry", "surface_type"])
    out = []
    for _, g in f.groupby("gml_id"):
        roofs = g[g.surface_type == "RoofSurface"].geometry
        if not len(roofs):
            continue
        z = shapely.get_coordinates(roofs.values, include_z=True)[:, 2]
        flat = [shapely.make_valid(shapely.force_2d(p)) for p in roofs.values]
        footprint = shapely.union_all([p for p in flat if not p.is_empty]).buffer(0)
        if not footprint.is_empty:
            out.append((footprint, float(np.median(z))))
    return out


def _carriageways(block):
    import geopandas as gpd
    import shapely

    from turku3d.coarse.sources import AREA_PREFIX

    path = config.raw_dir("wfs") / f"{AREA_PREFIX}{block.name}" / "GIS_Katualueet.parquet"
    if not path.exists():
        return None
    f = gpd.read_parquet(path, columns=["geometry", "Katuosalaji"])
    roads = [shapely.force_2d(g) for g, k in zip(f.geometry, f.Katuosalaji, strict=True) if k == "Ajorata"]
    return shapely.union_all(roads) if roads else None


def reclassify(block, points, ground, buildings, roads):
    """(E, N, Z, class, return, returns) arrays -> kept arrays in Turku's classes, and stats."""
    import shapely

    e, n, z, cls, ret, nret = points
    hag = z - ground(e, n)
    out = cls.copy()
    keep = np.ones(len(e), bool)
    pts = shapely.points(e, n)
    stats = {"buildings": len(buildings), "changed_buildings": 0}
    inside = np.zeros(len(e), bool)
    if buildings:
        tree = shapely.STRtree([b for b, _ in buildings])
        hits_point, hits_building = tree.query(pts, predicate="within")
        owner = np.full(len(e), -1)
        owner[hits_point] = hits_building
        inside = owner >= 0
        for k, (_, roof) in enumerate(buildings):
            mine = (owner == k) & np.isin(cls, (1, 3)) & (hag > ROOF_HAG)
            if not mine.any():
                continue
            if abs(float(np.median(z[mine])) - roof) > CHANGED_M:
                keep &= ~(owner == k) | np.isin(cls, (2, 9))
                stats["changed_buildings"] += 1
            else:
                out[mine] = 6
        near = np.zeros(len(e), bool)
        buffered = shapely.STRtree([b.buffer(FOOTPRINT_BUFFER) for b, _ in buildings])
        near[np.unique(buffered.query(pts, predicate="within")[0])] = True
    else:
        near = inside
    veg = np.isin(cls, (1, 3)) & ~inside
    out[veg & (hag >= 0.3) & (hag < 1.0)] = 3
    out[veg & (hag >= 1.0) & (hag < 2.5)] = 4
    out[veg & (hag >= 2.5)] = 5
    out[veg & (hag < 0.3)] = 1
    gone = veg & (hag >= 0.3) & near
    if roads is not None and not roads.is_empty:
        on_road = np.zeros(len(e), bool)
        on_road[shapely.STRtree([roads]).query(pts, predicate="within")[0]] = True
        gone |= veg & (hag >= 0.3) & on_road
    keep &= ~gone
    ground_pts = cls == 2
    stats.update(points=int(keep.sum()), dropped=int((~keep).sum()),
                 ground_offset_m=round(float(np.median(hag[ground_pts])), 3) if ground_pts.any() else None,
                 by_class={int(c): int(v) for c, v in zip(*np.unique(out[keep], return_counts=True), strict=True)})
    return (e[keep], n[keep], z[keep], out[keep], ret[keep], nret[keep]), stats


def write_laz(path, e, n, z, cls, ret, nret):
    import laspy

    header = laspy.LasHeader(point_format=1, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([float(int(config.ORIGIN_E // 1e6) * 1e6), 6000000.0, 0.0])
    las = laspy.LasData(header)
    las.x, las.y, las.z = e, n, z
    las.classification = cls.astype(np.uint8)
    las.return_number = ret.astype(np.uint8)
    las.number_of_returns = nret.astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.laz")
    las.write(tmp)
    tmp.replace(path)


def fetch_blocks(blocks, log=print) -> list[dict]:
    import laspy
    from pyproj import Transformer

    root = kind_dir("laz")
    reg = Registry(root)
    to_city = Transformer.from_crs(LAZ_CRS, config.CRS_HORIZONTAL, always_xy=True)
    out = []
    for block in blocks:
        dest = root / "pistepilvi" / f"{block.name}.laz"
        rec = reg.get(dest) if dest.exists() else None
        if rec and rec.get("bytes") == dest.stat().st_size and rec.get("reclass_version") == VERSION:
            out.append(rec)
            log(f"mml  pistepilvi/{block.name}: cached")
            continue
        b = block.bbox_en
        parts = []
        sheets = sheet_paths(block, log)
        for path, _ in sheets:
            las = laspy.read(path)
            e, n = to_city.transform(np.asarray(las.x), np.asarray(las.y))
            m = (e >= b.emin) & (e < b.emax) & (n > b.nmin) & (n <= b.nmax)
            parts.append((e[m], n[m], np.asarray(las.z)[m], np.asarray(las.classification)[m],
                          np.asarray(las.return_number)[m], np.asarray(las.number_of_returns)[m]))
        points = tuple(np.concatenate([p[i] for p in parts]) for i in range(6))
        kept, stats = reclassify(block, points, _ground_grid(block), _buildings(block), _carriageways(block))
        write_laz(dest, *kept)
        rec = reg.record(dest, url=sheets[0][1] if sheets else None, dataset="pistepilvi", tile=block.name,
                         source="mml-0.5p-reclassified", reclass_version=VERSION,
                         sheets=[u for _, u in sheets], crs=config.CRS_HORIZONTAL, **stats)
        log(f"mml  pistepilvi/{block.name}: {stats['points']} points, classes {stats['by_class']}, "
            f"{stats['changed_buildings']}/{stats['buildings']} buildings changed since the scan, "
            f"ground offset {stats['ground_offset_m']} m, {human(rec['bytes'])}")
        out.append(rec)
    return out

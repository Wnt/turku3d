"""Water bodies (OSM areas, one level each from lidar) and the synthetic collision bed."""

import numpy as np

from turku3d.col.materials import mat_id

from .mesh import Mesh


def add_river_bed(mesh, level=None, depth=3.0):
    """Close solid banks to a flat bed; leave the independent water sensor intact.

    This is a collision fallback, never a claim of measured bathymetry. The caller
    retains the original mesh for visuals, so the synthetic bed/banks are not rendered.
    The bed lies `depth` below `level`, or, without one, below each wet vertex (ground
    meshes give every wet vertex its own body's level).
    """
    wet = mesh.material == mat_id("water")
    if not wet.any():
        return mesh
    wf = mesh.indices[wet]
    ids, inverse = np.unique(wf, return_inverse=True)
    bed = mesh.positions[ids].copy()
    bed[:, 1] = (bed[:, 1] if level is None else level) - depth
    bed_faces = inverse.reshape(-1, 3) + len(mesh.positions)
    bed_index = np.zeros(len(mesh.positions), dtype=np.uint32)
    bed_index[ids] = np.arange(len(ids)) + len(mesh.positions)
    edges = np.concatenate([wf[:, [0, 1]], wf[:, [1, 2]], wf[:, [2, 0]]])
    _, first, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_index=True, return_counts=True
    )
    boundary = edges[first[counts == 1]]
    dry_ids = np.unique(mesh.indices[~wet])
    banks = np.empty((0, 3), dtype=np.uint32)
    if len(dry_ids):
        from scipy.spatial import cKDTree  # imported on use: dry tiles never need it

        tree = cKDTree(mesh.positions[dry_ids][:, [0, 2]])
        distances, nearest = tree.query(mesh.positions[boundary][:, :, [0, 2]])
        shore = (distances < 1e-7).all(axis=1)
        a, b = dry_ids[nearest[shore]].T
        ba, bb = bed_index[boundary[shore]].T
        banks = np.r_[np.c_[a, b, ba], np.c_[b, bb, ba]].astype(np.uint32)
    added = np.r_[bed_faces, banks].astype(np.uint32)
    return Mesh(
        np.r_[mesh.positions, bed],
        np.r_[mesh.indices, added],
        np.r_[mesh.material, np.full(len(added), mat_id("soil"), np.uint8)],
    ).compact()


# OSM areas that hold standing or flowing water: lakes, ponds, rivers, basins, pools.
WATER_AREA = {"natural": {"water"}, "waterway": {"riverbank"}, "landuse": {"basin", "reservoir"}}


def is_water(tags: dict) -> bool:
    return any(tags.get(key) in values for key, values in WATER_AREA.items())


def bodies(osm_path, bounds=None) -> dict:
    """Water areas of an OSM clip by feature (`way/123`): complete polygons, or only those
    meeting bounds. A feature split into several rows (outline and area) is united."""
    import json

    import pyarrow.parquet as pq
    import shapely

    from .mesh import parts

    window = None if bounds is None else shapely.box(*bounds.as_tuple())
    found = {}
    table = pq.read_table(osm_path, columns=["osm_type", "osm_id", "tags", "geometry_local"])
    for row in table.to_pylist():
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if not is_water(tags):
            continue
        geometry = shapely.make_valid(shapely.force_2d(shapely.from_wkb(row["geometry_local"])))
        polygons = list(parts(geometry, ("Polygon",)))
        if not polygons:
            continue
        geometry = shapely.union_all(polygons)
        if window is not None and not geometry.intersects(window):
            continue
        key = f"{row['osm_type']}/{row['osm_id']}"
        found[key] = shapely.union_all([found[key], geometry]) if key in found else geometry
    return dict(sorted(found.items()))


def level_from_points(polygon, points, classes):
    """A water body's N2000 level from lidar returns, with its provenance.

    Class 9 (water) returns inside the body give the median. Otherwise the lowest well
    supported 5 cm mode of the returns more than 3 m inside it (water is often labelled
    overlap or ground in this source; decks and boats lie above the surface). A body too
    small for either uses the 5th percentile of the class-2 ground within 3 m of its shore,
    20 cm lower (a pond or pool surface sits below its rim)."""
    import shapely

    inside = shapely.contains_xy(polygon, points[:, 0], points[:, 2])
    water = points[inside & (classes == 9), 1]
    if len(water) >= 20:
        return float(np.median(water)), f"median of {len(water)} class-9 returns"
    interior = polygon.buffer(-3)
    wet = points[shapely.contains_xy(interior, points[:, 0], points[:, 2]), 1]
    if len(wet) >= 20:
        bins, counts = np.unique(np.rint(wet / 0.05).astype(int), return_counts=True)
        significant = counts >= max(20, counts.max() * 0.1)
        sample = wet[np.abs(wet - bins[significant].min() * 0.05) <= 0.1]
        return float(np.median(sample)), f"lowest supported interior mode ({len(sample)} returns)"
    shore = polygon.buffer(3).difference(polygon)
    rim = points[(classes == 2) & shapely.contains_xy(shore, points[:, 0], points[:, 2]), 1]
    if len(rim) >= 5:
        return float(
            np.percentile(rim, 5)
        ) - 0.2, f"shore ground 5th percentile - 0.2 m ({len(rim)} returns)"
    raise ValueError("no lidar returns in or around the water body")

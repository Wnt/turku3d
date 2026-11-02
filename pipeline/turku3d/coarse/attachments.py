"""Lidar-supported horizontal decks outside LoD2 building footprints.

Wall-aligned support cells retain terrace edges and narrow stair treads. Surveyed
footprints own their interiors; no roof, vegetation or unsupported fill is added.
"""

import hashlib
from types import SimpleNamespace

import numpy as np
import shapely
from scipy.ndimage import binary_closing, find_objects, label
from scipy.signal import find_peaks

from turku3d.structures.geometry import Faces

from .retaining_survey import ground_points

CELL = 0.25
REACH = 8.0


def measure(buildings, points, classes, context=()):
    """Find connected level patches with lower-side evidence, retaining source heights."""
    points, classes = np.asarray(points), np.asarray(classes)
    keep = np.isin(classes, (1, 2, 6, 8, 10, 12, 17))
    points, classes = points[keep], classes[keep]
    if len(points) < 20 or not buildings:
        return []
    footprints = shapely.union_all([b.footprint for b in [*buildings, *context]])
    outside = ~shapely.contains_xy(footprints, points[:, 0], points[:, 2])
    points, classes = points[outside], classes[outside]
    ground = ground_points(SimpleNamespace(points=points, records={"classification": classes}))
    records = []
    for building in sorted(buildings, key=lambda b: b.key):
        footprint = shapely.orient_polygons(building.footprint)
        near = shapely.intersects_xy(footprint.buffer(REACH + 2), points[:, 0], points[:, 2])
        cloud, labels = points[near], classes[near]
        lower_ground = ground[
            shapely.intersects_xy(footprint.buffer(REACH + 2), ground[:, 0], ground[:, 2])
        ]
        if len(cloud) < 20:
            continue
        accepted = []
        for ring in [footprint.exterior, *footprint.interiors]:
            coords = np.asarray(ring.coords)
            for a, b in zip(coords[:-1], coords[1:], strict=True):
                length = np.linalg.norm(b - a)
                if length < 2:
                    continue
                along = (b - a) / length
                outward = np.array([along[1], -along[0]])
                uv = (cloud[:, [0, 2]] - a) @ np.array([along, outward]).T
                use = (
                    (uv[:, 0] >= 0)
                    & (uv[:, 0] < length)
                    & (uv[:, 1] >= 0)
                    & (uv[:, 1] < REACH)
                    & (cloud[:, 1] < building.top - 2)
                    & (cloud[:, 1] > building.base + 0.2)
                )
                q, local, cls = cloud[use], uv[use], labels[use]
                if len(q) < 20:
                    continue
                # Height modes preserve separate slabs at the same plan position.
                low = np.floor(q[:, 1].min() / 0.05) * 0.05
                bins = np.floor((q[:, 1] - low) / 0.05).astype(int)
                counts = np.bincount(bins)
                peaks, _ = find_peaks(np.r_[0, counts, 0], distance=3, prominence=8)
                candidates = []
                for peak in peaks - 1:
                    level = low + (peak + 0.5) * 0.05
                    band = np.abs(q[:, 1] - level) < 0.065
                    cells = np.floor(local[band] / CELL).astype(int)
                    if len(cells) < 12:
                        continue
                    unique, support = np.unique(cells, axis=0, return_counts=True)
                    unique = unique[support >= 2]
                    if len(unique) < 8:
                        continue
                    mask = np.zeros((int(np.ceil(length / CELL)), int(REACH / CELL)), bool)
                    mask[tuple(unique.T)] = True
                    # Only single-cell holes enclosed by measured neighbours close.
                    mask |= binary_closing(mask, structure=np.ones((3, 3)))
                    # Floor returns under a facade overhang can be occluded.
                    # Bridge at most two support cells back to the surveyed wall.
                    for row in mask:
                        measured = np.flatnonzero(row)
                        if len(measured) and measured[0] <= 2:
                            row[: measured[0]] = True
                    groups, _ = label(mask)
                    for group, slc in enumerate(find_objects(groups), 1):
                        if slc is None:
                            continue
                        ij = np.argwhere(groups[slc] == group) + [slc[0].start, slc[1].start]
                        if len(ij) < 8:
                            continue
                        corners = (
                            a + ij[:, 0, None] * CELL * along + ij[:, 1, None] * CELL * outward
                        )
                        quads = corners[:, None] + CELL * np.array(
                            [[0, 0], along, along + outward, outward]
                        )
                        region = shapely.union_all(shapely.polygons(quads)).simplify(0.08)
                        region = shapely.make_valid(region.difference(footprints))
                        if region.is_empty or region.area < 0.5:
                            continue
                        observations = q[band & shapely.intersects_xy(region, q[:, 0], q[:, 2])]
                        if len(observations) < 12:
                            continue
                        height = float(np.median(observations[:, 1]))
                        if np.ptp(np.percentile(observations[:, 1], [10, 90])) > 0.12:
                            continue
                        design = np.c_[
                            observations[:, [0, 2]] - observations[:, [0, 2]].mean(axis=0),
                            np.ones(len(observations)),
                        ]
                        plane = np.linalg.lstsq(design, observations[:, 1], rcond=None)[0]
                        if np.linalg.norm(plane[:2]) > 0.04:
                            continue  # A clipped height band on a ramp is not a tread.
                        observed = band & shapely.intersects_xy(region, q[:, 0], q[:, 2])
                        if np.mean(np.isin(cls[observed], (2, 6, 8, 12, 17))) < 0.6:
                            continue  # Unclassified car roofs/furniture cannot seed decks.
                        candidates.append((region, height, len(observations)))
                # A platform needs broad support and a measured height break. A
                # flat lawn or pavement beside a building is not a raised deck.
                decks = []
                for region, height, support in sorted(candidates, key=lambda r: -r[0].area):
                    if (
                        region.area < 3
                        or region.buffer(-0.35).is_empty
                        or region.distance(footprint) > 0.6
                    ):
                        continue
                    edge = region.buffer(1.5).difference(region.buffer(0.1))
                    lower = lower_ground[
                        shapely.intersects_xy(edge, lower_ground[:, 0], lower_ground[:, 2])
                        & (lower_ground[:, 1] < height - 0.3)
                        & (lower_ground[:, 1] > height - 5)
                    ]
                    if len(lower) < 12:
                        continue
                    # Equal-area support avoids a vertical facade/one dense scan
                    # masquerading as the lower landing.
                    if len(np.unique(np.floor(lower[:, [0, 2]] / 0.5), axis=0)) < 6:
                        continue
                    region = region.difference(
                        shapely.union_all([g for g, h in accepted if abs(h - height) < 0.10])
                    )
                    if region.area < 3:
                        continue
                    under = cloud[
                        shapely.contains_xy(region.buffer(-0.2), cloud[:, 0], cloud[:, 2])
                        & (cloud[:, 1] < height - 2.3)
                    ]
                    suspended = len(under) >= 12
                    bottom = height - 0.22 if suspended else float(np.median(lower[:, 1]))
                    records.append(
                        dict(
                            building=building.key,
                            region=region,
                            height=height,
                            bottom=bottom,
                            kind="balcony" if suspended else "terrace",
                            support=support,
                            source="classified lidar horizontal surface",
                        )
                    )
                    decks.append((region, height))
                    accepted.append((region, height))
                # A tread is accepted only in a regular connected sequence that
                # reaches a supported deck, never from isolated horizontal noise.
                chain = [(r, h, n) for r, h, n in candidates if 0.5 <= r.area <= 40]
                reached = list(decks)
                steps = []
                for _ in range(32):
                    found = [
                        item
                        for item in chain
                        if any(
                            0.10 <= abs(item[1] - top) <= 0.24 and item[0].distance(deck) <= 0.4
                            for deck, top in reached
                        )
                    ]
                    if not found:
                        break
                    steps.extend(found)
                    reached.extend((r, h) for r, h, _ in found)
                    chain = [item for item in chain if not any(item is f for f in found)]
                if len({round(h / 0.1) for _, h, _ in steps}) >= 3:
                    for region, height, support in steps:
                        region = region.difference(
                            shapely.union_all([g for g, h in accepted if abs(h - height) < 0.10])
                        )
                        if region.area < 0.5:
                            continue
                        records.append(
                            dict(
                                building=building.key,
                                region=region,
                                height=height,
                                bottom=height - 0.24,
                                kind="tread",
                                support=support,
                                source="classified lidar regular step sequence",
                            )
                        )
                        accepted.append((region, height))
    return records


def prepare(tile, vector, paths):
    from turku3d import config as C
    from turku3d.lidar import TileStore
    from turku3d.stage_cache import StageCache, source_key
    from .inputs import buildings as read_buildings

    if vector is None or not paths:
        return []
    buildings = read_buildings(vector, tile, context=True)
    survey = {p.stem: p for p in paths}
    store = TileStore(source_paths=survey)
    records = []
    # Query complete buildings canonically on both sides of a tile edge.
    window = shapely.box(*tile.bbox.as_tuple())
    for building in buildings:
        if building.footprint.distance(window) > REACH:
            continue
        bounds = C.BBoxLocal(*building.footprint.buffer(REACH + 1).bounds)
        if not all(b.name in survey for b in C.laz_tiles_for_bbox(bounds)):
            continue
        cloud = store.query(bounds)
        context = [b for b in buildings if b.footprint.distance(building.footprint) < REACH + 2]
        identity = hashlib.sha256(building.key.encode()).hexdigest()[:20]
        cache = StageCache(C.work_root() / ".stages" / "building-attachments" / identity)
        records.extend(
            cache.run(
                "survey",
                source_key(measure, ground_points),
                [
                    CELL,
                    REACH,
                    [(b.key, b.footprint, b.base, b.top) for b in context],
                    cloud.points,
                    cloud.records["classification"],
                ],
                lambda: measure(
                    [building], cloud.points, cloud.records["classification"], context=context
                ),
            )
        )
    return records


def produce(tile, records):
    from .gardens import horizontal
    from .geometry import clip_mesh

    meshes, openings = [], []
    for index, record in enumerate(records):
        region, height, bottom = (
            shapely.orient_polygons(record["region"]),
            record["height"],
            record["bottom"],
        )
        faces = Faces()
        horizontal(faces, region, height)
        underside = Faces()
        horizontal(underside, region, bottom)
        if underside.indices:
            faces.triangles(
                np.asarray(underside.positions),
                np.asarray(underside.indices)[:, ::-1],
                np.asarray(underside.uvs),
            )
        for polygon in shapely.get_parts(region):
            if polygon.geom_type != "Polygon":
                continue
            for ring in [polygon.exterior, *polygon.interiors]:
                coords = np.asarray(ring.coords)
                for a, b in zip(coords[:-1], coords[1:], strict=True):
                    faces.face(
                        [
                            [a[0], bottom, a[1]],
                            [b[0], bottom, b[1]],
                            [b[0], height, b[1]],
                            [a[0], height, a[1]],
                        ][::-1]
                    )
        if not faces.indices:
            continue
        mesh = clip_mesh(
            faces.mesh(
                f"attachment:{record['building']}:{index}",
                "paving_slab",
                metadata=dict(surface="concrete", kind=record["kind"], building=record["building"]),
            ),
            tile.bbox,
        )
        if mesh is not None:
            meshes.append(mesh)
            if record["kind"] != "balcony":
                openings.append(region.intersection(shapely.box(*tile.bbox.as_tuple())))
    return meshes, openings


def stats(tile, records):
    window = shapely.box(*tile.bbox.as_tuple())
    return [
        dict(
            building=r["building"],
            kind=r["kind"],
            height_m=round(r["height"], 3),
            bottom_m=round(r["bottom"], 3),
            area_m2=round(r["region"].intersection(window).area, 3),
            support=r["support"],
            source=r["source"],
        )
        for r in records
        if r["region"].intersects(window)
    ]


def sampler(sample, records):
    """Ground props use reconstructed terraces; overhead balconies keep their void."""
    ground = [r for r in records if r["kind"] != "balcony"]
    regions = shapely.STRtree([r["region"] for r in ground])

    def height(x, z):
        point = shapely.Point(x, z)
        owners = regions.query(point, predicate="intersects")
        return max(ground[i]["height"] for i in owners) if len(owners) else sample(x, z)

    return height

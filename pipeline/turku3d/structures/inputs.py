"""Read-only raw adapters and ground-layer contact sampling."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import STRtree, from_wkb, from_wkt
from shapely import box as boxes
from shapely.geometry import LineString, Point, box

from turku3d import config as C
from turku3d.col.materials import mat_id

from .survey import Lidar


def office_bounds(area="office-block"):
    return C.area(area).core


def local_geometry(value):
    if isinstance(value, str):
        return from_wkt(value)
    if isinstance(value, bytes):
        return from_wkb(value)
    return value


def survey_sources():
    return [
        C.raw_dir("wfs") / "work-3x3/kanta_Rakenne.parquet",
        C.raw_dir("osm") / "work-3x3/work-3x3-261024.parquet",
    ]


def read_surveys(bounds):
    import geopandas as gpd

    city_path, osm_path = survey_sources()
    city = gpd.read_parquet(city_path)
    region = box(*bounds.to_en().as_tuple())
    city = city[city.geometry.intersects(region)]
    records = []
    local_region = box(*bounds.as_tuple())
    for row in city.itertuples():
        if row.kplkoodi not in ("66626", "66618", "66634", "62540", "66630", "66632", "66636"):
            continue
        line = local_geometry(row.geometry_local).intersection(local_region)
        parts = list(line.geoms) if line.geom_type == "MultiLineString" else [line]
        for k, part in enumerate(parts):
            if part.geom_type == "LineString" and part.length > 0:
                records.append(
                    {
                        "object_id": f"city:{row.feature_key}:{k}",
                        "code": row.kplkoodi,
                        "geometry": part,
                        "source": "city",
                    }
                )
    city_groups = {
        "steps": [r["geometry"] for r in records if r["code"] in ("66626", "66618")],
        "wall": [r["geometry"] for r in records if r["code"] == "66634"],
        "fence": [r["geometry"] for r in records if r["code"] in ("66630", "66632", "66636")],
    }
    trees = {kind: STRtree(lines) for kind, lines in city_groups.items()}
    osm = gpd.read_parquet(osm_path)
    osm = osm[osm.geometry.intersects(region)]
    for row in osm.itertuples():
        tags = json.loads(row.tags)
        if tags.get("highway") == "steps":
            kind = "steps"
        elif tags.get("barrier") in (
            "fence",
            "railing",
            "guard_rail",
            "handrail",
            "wall",
            "city_wall",
            "retaining_wall",
        ):
            kind = {"guard_rail": "railing", "handrail": "railing", "city_wall": "wall"}.get(
                tags["barrier"], tags["barrier"]
            )
        else:
            continue
        line = local_geometry(row.geometry_local)
        if line.geom_type != "LineString":
            continue
        group = (
            "steps"
            if kind == "steps"
            else "wall"
            if kind in ("wall", "retaining_wall")
            else "fence"
        )
        relevant = [
            city_groups[group][i]
            for i in trees[group].query(line, predicate="dwithin", distance=1.0)
        ]
        # Survey position wins. Only omit an OSM way if its entire length is covered;
        # mere intersection would silently lose a long quay/fence gap.
        remaining = line.intersection(local_region)
        for survey in relevant:
            remaining = remaining.difference(survey.buffer(1.0))
        parts = list(remaining.geoms) if remaining.geom_type == "MultiLineString" else [remaining]
        for k, part in enumerate(parts):
            if part.is_empty or part.geom_type != "LineString" or part.length < 0.3:
                continue
            records.append(
                {
                    "object_id": f"osm:{row.osm_type}:{row.osm_id}:{k}",
                    "code": kind,
                    "geometry": part,
                    "source": "osm",
                    "tags": tags,
                }
            )
    return records


def lidar_paths(bounds):
    paths = []
    for tile in C.laz_tiles_for_bbox(bounds):
        candidates = [
            C.raw_dir("laz") / "pistepilvi" / f"{tile.name}.laz",
            C.raw_dir("laz-3x3") / f"{tile.name}.laz",
        ]
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise FileNotFoundError(
                f"missing lidar {tile.name}; prepare shared inputs with make fetch-aoi AREA=work-3x3"
            )
        paths.append(path)
    return paths


def load_lidar(paths, bounds):
    import laspy

    positions, classes = [], []
    for path in paths:
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(1_000_000):
                x, z = np.asarray(chunk.x) - C.ORIGIN_E, C.ORIGIN_N - np.asarray(chunk.y)
                cls = np.asarray(chunk.classification)
                keep = (
                    (x >= bounds.xmin)
                    & (x <= bounds.xmax)
                    & (z >= bounds.zmin)
                    & (z <= bounds.zmax)
                    & np.isin(cls, [1, 2, 12])
                )
                positions.append(np.column_stack((x[keep], np.asarray(chunk.z)[keep], z[keep])))
                classes.append(cls[keep])
    return Lidar(np.concatenate(positions), np.concatenate(classes))


class Ground:
    """Barycentric ground contacts; no nearest-height fallback across walls or water.

    Quay classification comes exclusively from ground's quays.json export. There is no second >1 m classifier here.
    """

    def __init__(self, root):
        self.root = Path(root)
        manifest = self.root / "MANIFEST.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"W1-01 ground layer is required: {manifest}")
        metadata = json.loads(manifest.read_text())
        if metadata.get("layer") != "ground":
            raise ValueError("expected the ground layer MANIFEST.json")
        self.paths = [manifest, *sorted(self.root.glob("*.col.npz"))]
        if len(self.paths) < 2:
            raise ValueError("ground manifest has no collision tiles")
        triangles, materials = [], []
        for path in self.paths[1:]:
            with np.load(path, allow_pickle=False) as data:
                p, i, m = data["positions"], data["indices"], data["material"]
                if p.dtype != np.float64 or i.dtype != np.uint32 or m.dtype != np.uint8:
                    raise ValueError(f"ground layer dtype mismatch: {path}")
                if (
                    p.ndim != 2
                    or p.shape[1] != 3
                    or i.ndim != 2
                    or i.shape[1] != 3
                    or m.shape != (len(i),)
                ):
                    raise ValueError(f"ground layer array shape mismatch: {path}")
                if not np.isfinite(p).all() or (len(i) and i.max() >= len(p)):
                    raise ValueError(f"invalid ground geometry: {path}")
                triangles.append(p[i])
                materials.append(m)
        self.triangles, self.materials = np.concatenate(triangles), np.concatenate(materials)
        norms = np.cross(
            self.triangles[:, 1] - self.triangles[:, 0], self.triangles[:, 2] - self.triangles[:, 0]
        )
        mask = (np.abs(norms[:, 1]) > 1e-10) & (self.materials != mat_id("water"))
        self.surface = self.triangles[mask]
        if not len(self.surface):
            raise ValueError("ground has no non-water surface")
        # Query triangle bounds directly: one large river-bed triangle must not
        # expand every contact query to the maximum radius of the whole AOI.
        low = self.surface[:, :, [0, 2]].min(axis=1)
        high = self.surface[:, :, [0, 2]].max(axis=1)
        self.tree = STRtree(
            boxes(low[:, 0] - 1e-8, low[:, 1] - 1e-8, high[:, 0] + 1e-8, high[:, 1] + 1e-8)
        )
        self.quays = self._quays()
        self.ramps = self._ramps()

    def height(self, xz, *, reference=None):
        xz = np.asarray(xz)
        ids = self.tree.query(Point(*xz))
        p = self.surface[ids]
        a = p[:, 1][:, [0, 2]] - p[:, 0][:, [0, 2]]
        b = p[:, 2][:, [0, 2]] - p[:, 0][:, [0, 2]]
        q = xz - p[:, 0][:, [0, 2]]
        determinant = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        u = (q[:, 0] * b[:, 1] - q[:, 1] * b[:, 0]) / determinant
        v = (a[:, 0] * q[:, 1] - a[:, 1] * q[:, 0]) / determinant
        valid = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
        heights = (p[:, 0, 1] + u * (p[:, 1, 1] - p[:, 0, 1]) + v * (p[:, 2, 1] - p[:, 0, 1]))[
            valid
        ].tolist()
        if not heights:
            raise ValueError(f"no ground contact at {xz.tolist()} (water or missing coverage)")
        return min(heights) if reference is None else min(heights, key=lambda h: abs(h - reference))

    def _quays(self):
        path = self.root / "quays.json"
        if path.is_file():
            self.paths.append(path)
            profiles = json.loads(path.read_text())
            result = []
            for profile in profiles:
                xz = np.asarray(profile["centers_xz"], dtype=float)
                heights = np.asarray(profile["side_heights"], dtype=float)
                if (
                    profile["kind"] != "quay"
                    or xz.ndim != 2
                    or xz.shape[1] != 2
                    or heights.shape != xz.shape
                ):
                    raise ValueError("invalid ground quays.json profile")
                if len(xz) < 2 or not np.isfinite(xz).all() or not np.isfinite(heights).all():
                    raise ValueError("invalid ground quay coordinates or elevations")
                result.append((profile["object_id"], xz, heights.min(axis=1), heights.max(axis=1)))
            return result
        return []  # Plain ground has no classified quay features; do not invent them.

    def _ramps(self):
        path = self.root / "ramps.json"
        if not path.is_file():
            return []
        self.paths.append(path)
        records = json.loads(path.read_text())
        result = []
        for record in records:
            line = LineString(record["line_xz"])
            length = float(record["length_m"])
            covered = float(record["covered_length_m"])
            solid = float(record.get("solid_covered_length_m", covered))
            classification = record.get("classification", "ramp")
            if not line.is_valid or line.length <= 0 or abs(line.length - length) > 1e-5:
                raise ValueError(f"invalid ground ramp survey: {record['object_id']}")
            if not 0 <= covered <= solid + 1e-5 or solid > length + 1e-5:
                raise ValueError(f"invalid ground ramp coverage: {record['object_id']}")
            if classification not in ("ramp", "wall", "missing"):
                raise ValueError(f"invalid ground ramp classification: {record['object_id']}")
            if classification == "ramp" and covered >= length - 0.01:
                result.append(line)
            elif (
                classification == "wall"
                and solid >= length - 0.01
                and record.get("wall_triangles", 0)
            ):
                result.append(line)
        return result

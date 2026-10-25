"""Read-only raw adapters and ground-layer contact sampling."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from shapely import from_wkb, from_wkt, STRtree
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.col.materials import mat_id
from .survey import Lidar


# Four visual tiles plus their meshing margin, from the shared grid definitions.
def office_bounds():
    tiles = C.OFFICE_BLOCK.visual_tiles
    return C.BBoxLocal(min(t.bbox.xmin for t in tiles), min(t.bbox.zmin for t in tiles),
                       max(t.bbox.xmax for t in tiles), max(t.bbox.zmax for t in tiles)).buffered(C.MESH_MARGIN)


def local_geometry(value):
    if isinstance(value, str):
        return from_wkt(value)
    if isinstance(value, bytes):
        return from_wkb(value)
    return value


def survey_sources():
    return [C.raw_dir('wfs') / 'work-3x3/kanta_Rakenne.parquet',
            C.raw_dir('osm') / 'work-3x3/work-3x3-261024.parquet']


def read_surveys(bounds):
    import geopandas as gpd
    city_path, osm_path = survey_sources()
    city = gpd.read_parquet(city_path)
    region = box(*bounds.to_en().as_tuple())
    city = city[city.geometry.intersects(region)]
    records = []
    local_region = box(*bounds.as_tuple())
    for row in city.itertuples():
        if row.kplkoodi not in ('66626', '66618', '66634', '62540', '66630', '66632', '66636'):
            continue
        line = local_geometry(row.geometry_local).intersection(local_region)
        parts = list(line.geoms) if line.geom_type == 'MultiLineString' else [line]
        for k, part in enumerate(parts):
            if part.geom_type == 'LineString' and part.length > 0:
                records.append({'object_id': f'city:{row.feature_key}:{k}', 'code': row.kplkoodi,
                                'geometry': part, 'source': 'city'})
    city_groups = {
        'steps': [r['geometry'] for r in records if r['code'] in ('66626', '66618')],
        'wall': [r['geometry'] for r in records if r['code'] == '66634'],
        'fence': [r['geometry'] for r in records if r['code'] in ('66630', '66632', '66636')],
    }
    trees = {kind: STRtree(lines) for kind, lines in city_groups.items()}
    osm = gpd.read_parquet(osm_path)
    osm = osm[osm.geometry.intersects(region)]
    for row in osm.itertuples():
        tags = json.loads(row.tags)
        if tags.get('highway') == 'steps':
            kind = 'steps'
        elif tags.get('barrier') in ('fence', 'railing', 'guard_rail', 'handrail', 'wall', 'city_wall', 'retaining_wall'):
            kind = {'guard_rail': 'railing', 'handrail': 'railing', 'city_wall': 'wall'}.get(tags['barrier'], tags['barrier'])
        else:
            continue
        line = local_geometry(row.geometry_local)
        if line.geom_type != 'LineString':
            continue
        group = 'steps' if kind == 'steps' else 'wall' if kind in ('wall', 'retaining_wall') else 'fence'
        relevant = [city_groups[group][i] for i in trees[group].query(line, predicate='dwithin', distance=1.0)]
        # Survey position wins. Only omit an OSM way if its entire length is covered;
        # mere intersection would silently lose a long quay/fence gap.
        remaining = line.intersection(local_region)
        for survey in relevant:
            remaining = remaining.difference(survey.buffer(1.0))
        parts = list(remaining.geoms) if remaining.geom_type == 'MultiLineString' else [remaining]
        for k, part in enumerate(parts):
            if part.is_empty or part.geom_type != 'LineString' or part.length < .3:
                continue
            records.append({'object_id': f'osm:{row.osm_type}:{row.osm_id}:{k}', 'code': kind,
                            'geometry': part, 'source': 'osm', 'tags': tags})
    return records


def lidar_paths(bounds):
    paths = []
    for tile in C.laz_tiles_for_bbox(bounds):
        candidates = [C.raw_dir('laz') / 'pistepilvi' / f'{tile.name}.laz',
                      C.raw_dir('laz-3x3') / f'{tile.name}.laz']
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise FileNotFoundError(f'missing lidar {tile.name}; prepare shared inputs with make fetch-aoi AREA=work-3x3')
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
                keep = ((x >= bounds.xmin) & (x <= bounds.xmax) & (z >= bounds.zmin) & (z <= bounds.zmax)
                        & np.isin(cls, [1, 2, 12]))
                positions.append(np.column_stack((x[keep], np.asarray(chunk.z)[keep], z[keep])))
                classes.append(cls[keep])
    return Lidar(np.concatenate(positions), np.concatenate(classes))


class Ground:
    """Barycentric ground contacts; no nearest-height fallback across walls or water.

    Quay classification comes exclusively from ground's explicit interchange file
    or its material-15 vertical faces. There is no second >1 m classifier here.
    """
    def __init__(self, root):
        self.root = Path(root)
        manifest = self.root / 'MANIFEST.json'
        if not manifest.is_file():
            raise FileNotFoundError(f'W1-01 ground layer is required: {manifest}')
        metadata = json.loads(manifest.read_text())
        if metadata.get('layer') != 'ground':
            raise ValueError('expected the ground layer MANIFEST.json')
        self.paths = [manifest, *sorted(self.root.glob('*.col.npz'))]
        if len(self.paths) < 2:
            raise ValueError('ground manifest has no collision tiles')
        triangles, materials = [], []
        for path in self.paths[1:]:
            with np.load(path, allow_pickle=False) as data:
                p, i, m = data['positions'], data['indices'], data['material']
                if p.dtype != np.float64 or i.dtype != np.uint32 or m.dtype != np.uint8:
                    raise ValueError(f'ground layer dtype mismatch: {path}')
                if p.ndim != 2 or p.shape[1] != 3 or i.ndim != 2 or i.shape[1] != 3 or m.shape != (len(i),):
                    raise ValueError(f'ground layer array shape mismatch: {path}')
                if not np.isfinite(p).all() or (len(i) and i.max() >= len(p)):
                    raise ValueError(f'invalid ground geometry: {path}')
                triangles.append(p[i]); materials.append(m)
        self.triangles, self.materials = np.concatenate(triangles), np.concatenate(materials)
        norms = np.cross(self.triangles[:, 1] - self.triangles[:, 0], self.triangles[:, 2] - self.triangles[:, 0])
        mask = (np.abs(norms[:, 1]) > 1e-10) & (self.materials != mat_id('water'))
        self.surface = self.triangles[mask]
        if not len(self.surface):
            raise ValueError('ground has no non-water surface')
        self.centres = self.surface[:, :, [0, 2]].mean(axis=1)
        self.radii = np.linalg.norm(self.surface[:, :, [0, 2]] - self.centres[:, None, :], axis=2).max(axis=1)
        self.tree = cKDTree(self.centres)
        self.quays = self._quays()

    def height(self, xz, *, reference=None):
        xz = np.asarray(xz)
        ids = self.tree.query_ball_point(xz, float(self.radii.max()) + 1e-8)
        heights = []
        for i in ids:
            p = self.surface[i]
            a = np.column_stack((p[1, [0, 2]] - p[0, [0, 2]], p[2, [0, 2]] - p[0, [0, 2]]))
            u, v = np.linalg.solve(a, xz - p[0, [0, 2]])
            if min(u, v, 1 - u - v) >= -1e-9:
                heights.append(float(p[0, 1] + u * (p[1, 1] - p[0, 1]) + v * (p[2, 1] - p[0, 1])))
        if not heights:
            raise ValueError(f'no ground contact at {xz.tolist()} (water or missing coverage)')
        return min(heights) if reference is None else min(heights, key=lambda h: abs(h - reference))

    def _quays(self):
        path = self.root / 'quays.geojson'
        if path.is_file():
            self.paths.append(path)
            data = json.loads(path.read_text())
            if data.get('coordinate_space') != 'world-local-xz':
                raise ValueError('quays.geojson requires coordinate_space=world-local-xz')
            result = []
            for feature in data['features']:
                properties = feature['properties']
                xz = np.asarray(feature['geometry']['coordinates'], dtype=float)
                lower, upper = np.asarray(properties['lower_y']), np.asarray(properties['upper_y'])
                if feature['geometry']['type'] != 'LineString' or xz.shape != (len(lower), 2) or lower.shape != upper.shape:
                    raise ValueError('invalid ground quay profile')
                if not np.isfinite([*xz.ravel(), *lower, *upper]).all() or np.any(upper <= lower):
                    raise ValueError('invalid ground quay elevations')
                result.append((str(feature['id']), xz, lower, upper))
            return result
        # Material-15 vertical quads already encode classified profiles, including
        # the exact contacts. Combine their triangles by horizontal endpoint pair.
        edges = {}
        for tri in self.triangles[self.materials == mat_id('quay')]:
            xz = np.unique(tri[:, [0, 2]], axis=0)
            if len(xz) != 2:
                continue
            key = tuple(map(tuple, xz))
            heights = edges.setdefault(key, [[], []])
            for v in tri:
                heights[0 if np.array_equal(v[[0, 2]], xz[0]) else 1].append(v[1])
        result = []
        for k, (key, heights) in enumerate(sorted(edges.items())):
            low, high = np.array([min(h) for h in heights]), np.array([max(h) for h in heights])
            if np.all(high > low):
                result.append((f'ground-quay:{k}', np.asarray(key), low, high))
        if not result:
            raise FileNotFoundError('ground has no classified quay profiles: provide quays.geojson (an explicit empty collection is valid) or vertical material-15 faces')
        return result

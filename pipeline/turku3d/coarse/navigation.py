"""City navigation metadata, independent of draft mesh residency.

Uses only cached OSM input. No geometry tiles are built. Drop-offs sit beside reachable
public driving streets and outside every mapped building; runtime ground validation still applies.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path

from turku3d import config as C
from turku3d.content.addresses import Graph, PIZZERIA, _project, slug
from turku3d.roads import CAR, FOOT, build_graph, way_flags, reversed_oneway

_LOCK = threading.Lock()
_BOUNDS = (-3500, -3500, 3500, 3500)


class StreetGraph(Graph):
    def __init__(self, graph):
        from shapely import STRtree
        from shapely.geometry import LineString
        super().__init__(graph)
        self.indexes = {}
        for mode in (CAR, FOOT):
            ids = [i for i, e in enumerate(self.edges) if e['flags'] & mode]
            self.indexes[mode] = (ids, STRtree([LineString(self.edges[i]['points']) for i in ids]))

    def snap(self, p, mode):
        from shapely.geometry import Point
        ids, tree = self.indexes[mode]
        if not ids:
            return None
        index = ids[int(tree.nearest(Point(p)))]; edge = self.edges[index]
        candidates = []
        for i, (a, b) in enumerate(zip(edge['points'], edge['points'][1:])):
            q, d = _project(p, a, b)
            candidates.append((index, edge['cum'][i] + math.dist(a, q), q, d))
        return min(candidates, key=lambda s: s[3])


def build(paths, voiced=None):
    import geopandas as gpd
    from shapely import STRtree, box, get_parts
    from shapely.geometry import Point

    clip = box(*_BOUNDS); seen = set(); ways = []; buildings = []; hints = []
    for path in paths:
        frame = gpd.read_parquet(path, columns=['osm_type', 'osm_id', 'tags', 'geometry_local'])
        for row in frame.itertuples():
            identity = (row.osm_type, row.osm_id)
            if identity in seen:
                continue
            seen.add(identity)
            geom = row.geometry_local
            if geom is None or geom.is_empty or not geom.intersects(clip):
                continue
            tags = json.loads(row.tags)
            if tags.get('building') and geom.geom_type in ('Polygon', 'MultiPolygon'):
                buildings.append(geom)
            if tags.get('addr:street') and tags.get('addr:housenumber'):
                hints.append((f"{tags['addr:street']} {tags['addr:housenumber']}", geom.representative_point()))
            flags = way_flags(tags.get('highway'), tags)
            if flags and geom.geom_type == 'LineString':
                for part in get_parts(geom.intersection(clip)):
                    if part.geom_type == 'LineString':
                        coords = [(x, z) for x, z, *_ in part.coords]
                        ways.append((coords[::-1] if reversed_oneway(tags) else coords, flags))
    # Adjacent cached OSM clips carry identical endpoints; build_graph joins them before routing.
    graph = build_graph(ways, _BOUNDS, 'delivery-districts'); roads = StreetGraph(graph)
    starts = {m: roads.snap(PIZZERIA, m) for m in (FOOT, CAR)}
    distances = {m: roads.distances(starts[m][:2], m) for m in starts if starts[m]}
    blockers = STRtree(buildings); addresses = []; used = set()
    for address, hint in sorted(hints, key=lambda h: h[0]):
        id = slug(address)
        if id in used or (voiced is not None and id not in voiced):
            continue
        car = roads.snap((hint.x, hint.y), CAR)
        if not car or car[3] > 45 or CAR not in distances:
            continue
        sx, sz = car[2]; dx, dz = hint.x - sx, hint.y - sz
        norm = math.hypot(dx, dz)
        if norm < 1:
            continue
        point = Point(sx + dx / norm * 3.5, sz + dz / norm * 3.5)
        if any(buildings[int(i)].buffer(0.5).covers(point) for i in blockers.query(point.buffer(0.5))):
            continue
        foot = roads.snap((point.x, point.y), FOOT)
        if not foot or foot[3] > 40 or FOOT not in distances:
            continue
        car_m = roads.route_length(starts[CAR][:2], distances[CAR], car[:2], CAR)
        foot_m = roads.route_length(starts[FOOT][:2], distances[FOOT], foot[:2], FOOT)
        if not math.isfinite(car_m + foot_m) or car_m < 60 or car_m > 7000:
            continue
        used.add(id)
        addresses.append(dict(id=id, address=address, tile=f't0_{math.floor(point.x / 250)}_{math.floor(point.y / 250)}',
            point=[round(point.x, 2), round(point.y, 2)], street=list(car[2]), foot=round(foot_m, 1), car=round(car_m, 1)))
    return dict(roads=graph, addresses=dict(format='turku3d-addresses', version=1, frame='engine-xz',
        origin=list(PIZZERIA), addresses=addresses))


def navigation(root: Path):
    """Content-keyed disk cache. A single metadata computation may run per service process."""
    paths = sorted(C.raw_dir('osm').glob('coarse-osm-*/*.parquet'))
    # Only clips intersecting the navigation bounds; their names encode the north-west EN corner.
    selected = []
    for p in paths:
        e, n = map(int, p.parent.name.removeprefix('coarse-osm-').split('_'))
        x, _, z = C.en_to_local(e, n)
        if x < _BOUNDS[2] and x + 2000 > _BOUNDS[0] and z < _BOUNDS[3] and z + 2000 > _BOUNDS[1]:
            selected.append(p)
    inventory = Path(__file__).resolve().parents[3] / 'audio/voice/addresses.json'
    key = hashlib.sha256(Path(__file__).read_bytes() + inventory.read_bytes())
    for p in selected:
        key.update(f'{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}'.encode())
    path = root / f'navigation-{key.hexdigest()[:16]}.json'
    with _LOCK:
        if not path.exists():
            data = build(selected, json.loads(inventory.read_text()))
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp'); temp.write_text(json.dumps(data, separators=(',', ':')))
            temp.replace(path)
        return path.read_bytes()


if __name__ == '__main__':
    # Prepare candidate metadata locally; callers select a small spoken inventory before publishing.
    output = Path(C.work_root()) / 'navigation-candidates.json'
    output.write_text(json.dumps(build(sorted(C.raw_dir('osm').glob('coarse-osm-*/*.parquet')))))
    print(output)

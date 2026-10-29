"""Pizzeria-outward route distances shared by offline surveys and idle generation.

Navigation coordinates are engine x/z. Optional tertiary polylines use EPSG:3877
from OSM because navigation groups tertiary and residential into one class.
"""

import heapq
from collections import defaultdict

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point

from turku3d import config


def graph_routes(nav, tertiary=()):
    nodes = np.asarray(nav["roads"]["nodes"]).reshape(-1, 2)
    edges = nav["roads"]["edges"]
    lines = [
        LineString([nodes[e[0]], *np.asarray(e[4:]).reshape(-1, 2), nodes[e[1]]]) for e in edges
    ]
    foot = [i for i, e in enumerate(edges) if e[2] & 1]
    tree = STRtree([lines[i] for i in foot])

    def snap(point):
        i = foot[int(tree.nearest(Point(point)))]
        line = lines[i]
        t = line.project(Point(point))
        e = edges[i]
        return i, [(e[0], t), (e[1], line.length - t)]

    origin = nav["addresses"]["origin"]
    start, seeds = snap(origin)
    routes = {start}
    foot_dist = None
    route_records = []
    for mode, bit in [("foot", 1), ("car", 2)]:
        adj = defaultdict(list)
        for i, e in enumerate(edges):
            a, b, flags, length = e[:4]
            if flags & bit:
                adj[a].append((b, length, i))
                if mode == "foot" or not flags & 4:
                    adj[b].append((a, length, i))
        dist = np.full(len(nodes), np.inf)
        parent = {}
        queue = []
        # The pizzeria is on an ordinary two-way street; snap to this same street.
        for n, d in seeds:
            dist[n] = d
            heapq.heappush(queue, (d, n))
        while queue:
            d, n = heapq.heappop(queue)
            if d != dist[n]:
                continue
            for other, length, i in adj[n]:
                if d + length < dist[other]:
                    dist[other] = d + length
                    parent[other] = (n, i)
                    heapq.heappush(queue, (dist[other], other))
        if mode == "foot":
            foot_dist = dist
        for destination in nav["addresses"]["addresses"]:
            edge, ends = snap(destination["street"])
            n, tail = min(ends, key=lambda p: dist[p[0]] + p[1])
            if not np.isfinite(dist[n]):
                route_records.append(dict(id=destination["id"], mode=mode, unreachable=True))
                continue
            path = [edge]
            while n in parent:
                n, i = parent[n]
                path.append(i)
            routes.update(path)
            route_records.append(dict(id=destination["id"], mode=mode, edges=path))
    # Navigation groups tertiary with residential. Retain the original OSM class
    # from the frozen parquet snapshot instead of including every residential road.
    tertiary_lines = [
        LineString([(x - config.ORIGIN_E, config.ORIGIN_N - y) for x, y, *_ in c]) for c in tertiary
    ]
    tertiary_tree = STRtree(tertiary_lines)
    tertiary_edges = set()
    if tertiary_lines:
        for i, line in enumerate(lines):
            if edges[i][2] >> 3 != 2:
                continue
            midpoint = line.interpolate(0.5, normalized=True)
            other = tertiary_lines[int(tertiary_tree.nearest(midpoint))]
            if (
                midpoint.distance(other) < 0.3
                and line.intersection(other.buffer(0.3)).length > line.length * 0.8
            ):
                tertiary_edges.add(i)
    corridor = sorted(routes | tertiary_edges | {i for i, e in enumerate(edges) if e[2] >> 3 == 3})
    return lines, edges, foot_dist, corridor, route_records

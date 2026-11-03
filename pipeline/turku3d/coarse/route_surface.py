"""Navigation follows the final physics triangles, including stacked walking surfaces."""

import json

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.roads import encode
from turku3d.roads.tile_graph import with_heights


class Surface:
    def __init__(self, cells):
        triangles = []
        for cell in cells:
            faces = cell.world_positions()[cell.indices]
            normal = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
            # Water is a sensor; walls/soffits cannot support a standing character.
            keep = ((cell.materials != mat_id("water"))
                    & (normal[:, 1] > np.linalg.norm(normal, axis=1) * .25))
            triangles.append(faces[keep])
        self.triangles = np.concatenate(triangles) if triangles else np.empty((0, 3, 3))
        self.polygons = shapely.polygons(self.triangles[:, :, [0, 2]])
        self.tree = shapely.STRtree(self.polygons)

    def heights(self, points, reference):
        """Pick the physical floor nearest the route's level, never an overhead deck."""
        points, reference = np.asarray(points), np.asarray(reference)
        # A standing foot bridges millimetre clipping seams just as the player's
        # capsule does. Do not turn such a seam into a ray down to the river bed.
        q, t = self.tree.query(shapely.points(points), predicate="dwithin", distance=.01)
        triangles = self.triangles[t]
        a = triangles[:, 0]
        b, c = triangles[:, 1] - a, triangles[:, 2] - a
        d = points[q] - a[:, [0, 2]]
        det = b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0]
        u = (d[:, 0] * c[:, 2] - d[:, 1] * c[:, 0]) / det
        v = (b[:, 0] * d[:, 1] - b[:, 2] * d[:, 0]) / det
        hit = a[:, 1] + u * b[:, 1] + v * c[:, 1]
        outside = shapely.distance(shapely.points(points[q]), self.polygons[t]) > 1e-5
        # Prefer a direct floor hit over a nearby edge at the same level. The
        # footprint tolerance only wins when it prevents a substantial fall.
        order = np.argsort(abs(hit - reference[q]) + outside * .25, kind="stable")
        _, first = np.unique(q[order], return_index=True)
        result = reference.copy()
        selected = order[first]
        result[q[selected]] = hit[selected]
        return result

    def stations(self, line):
        """Retain triangle breaks so an eight-metre chord cannot cut through a ramp or stair."""
        ids = self.tree.query(line, predicate="intersects")
        crossings = shapely.get_coordinates(shapely.intersection(
            line, shapely.boundary(self.polygons[ids])))
        breaks = shapely.line_locate_point(line, shapely.points(crossings))
        # Two samples across a tread discontinuity keep its rise local. Collinear
        # samples are removed below, so coplanar collision seams cost no wire bytes.
        return np.unique(np.clip(np.r_[0, line.length, breaks, breaks - .1, breaks + .1],
                                 0, line.length))


def bake(roads, cells, sample):
    if roads is None:
        return None
    original = json.loads(roads["json"])
    graph = json.loads(with_heights(roads, sample)["json"])
    if graph.get("revision", 1) < 2:
        return roads
    nodes = np.asarray(graph["nodes"]).reshape(-1, 2)
    # Explicit bridge/tunnel levels also own their connected approach endpoint.
    # The old DEM can be several metres lower at precisely that shared node.
    anchors = {}
    for edge, ys in zip(original["edges"], original.get("edgeHeights", []), strict=False):
        if ys is not None:
            for node, y in ((edge[0], ys[0]), (edge[1], ys[-1])):
                anchors.setdefault(node, []).append(y)
    anchors = {node: float(np.median(ys)) for node, ys in anchors.items()}
    surface = Surface(cells)
    for edge, ys in zip(graph["edges"], graph["edgeHeights"], strict=True):
        if ys is None:
            continue
        points = np.vstack([nodes[edge[0]], np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]])
        line = shapely.LineString(points)
        if line.length == 0:
            continue
        distances = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        stations = np.unique(np.r_[distances, surface.stations(line)])
        dense = shapely.get_coordinates(shapely.line_interpolate_point(line, stations))
        # Walkers use the road graph's decimetre lattice. Bake at those actual
        # positions, rather than assigning a height then moving it at runtime.
        dense = dense.round(1)
        unique = np.r_[True, np.any(np.diff(dense, axis=0) != 0, axis=1)]
        dense, stations = dense[unique], stations[unique]
        reference = np.interp(stations, distances, ys)
        reference += np.interp(stations, [0, line.length],
                               [anchors.get(edge[0], ys[0]) - ys[0],
                                anchors.get(edge[1], ys[-1]) - ys[-1]])
        heights = surface.heights(dense, reference)
        # Retain physical grade changes, but avoid transmitting the lateral
        # decimetre rounding jitter as thousands of artificial path bends.
        keep = [0]
        p = np.c_[dense[:, 0], heights, dense[:, 1]]
        for i in range(1, len(p) - 1):
            delta = p[i + 1] - p[keep[-1]]
            t = np.dot((p[i] - p[keep[-1]])[[0, 2]], delta[[0, 2]]) / max(
                np.dot(delta[[0, 2]], delta[[0, 2]]), 1e-20)
            error = p[i] - (p[keep[-1]] + t * delta)
            if np.linalg.norm(error[[0, 2]]) > .071 or abs(error[1]) > .005:
                keep.append(i)
        keep.append(len(p) - 1)
        edge[4:] = dense[keep[1:-1]].round(5).ravel().tolist()
        ys[:] = heights[keep].round(3).tolist()
    return {**roads, "json": encode(graph)}

"""Canonical two-sided support planes from a frozen raw-lidar wall survey.

The survey owns the vertical discontinuity. Its outer rails meet the existing
ground exactly; both ground and wall products use the resulting boundary table.
"""
from __future__ import annotations

import numpy as np
import shapely


def shore_walks(walks, water, records):
    """Only the measured riverside strip owns the continuous lower paving.

    Crossing access ways can carry upper-landing fits. Their wider survey windows
    must not punch those heights through an otherwise supported lower quay.
    Missing strip knots are filled only by a nearby independent raw wall survey.
    """
    result = []
    for patch in walks:
        if np.nanmedian(patch["heights"][:,3]) >= 2:
            continue
        knots = shapely.line_interpolate_point(patch["line"], patch["knots"])
        if not water.is_empty and np.median(shapely.distance(knots, water)) > 2:
            continue
        patch = dict(patch, heights=patch["heights"].copy())
        for index in np.flatnonzero(~np.isfinite(patch["heights"][:,3])):
            candidates = [(line.distance(knots[index]), line, tags["_survey"]) for line,tags,_ in records]
            if not candidates:
                continue
            distance, line, profile = min(candidates, key=lambda item:item[0])
            if distance > 3:
                continue
            centres=np.asarray(profile["centres"])
            station=np.r_[0,np.cumsum(np.linalg.norm(np.diff(centres,axis=0),axis=1))]
            level=np.interp(line.project(knots[index]),station,np.min(profile["heights"],axis=1))
            patch["heights"][index,2:5]=level
        result.append(patch)
    return result


class RetainingField:
    def __init__(self, records, sample, dry):
        self.planes = []
        self.lines = []
        claimed = shapely.Polygon()
        for line, tags, _ in records:
            profile = tags["_survey"]
            centres = np.asarray(profile["centres"], float)
            levels = np.asarray(profile["heights"], float).copy()
            # A measured run ends where the raw fit loses support. Resolve its
            # last station to the unchanged terrain instead of leaving an open
            # crack. Only the terminal station is inferred, at survey uncertainty.
            levels[[0, -1]] = sample(centres[[0, -1]])[:, None]
            rails = []
            for side in ("left", "right"):
                offset = np.asarray(profile[side]) - centres
                offset *= 2. / np.linalg.norm(offset, axis=1)[:, None]
                rails.append(centres + offset)
            band = shapely.Polygon(np.r_[rails[0], rails[1][::-1]])
            if band.intersection(claimed).area > 1e-5:
                raise ValueError("Measured retaining aprons overlap; resolve the source profile ownership")
            claimed = shapely.union_all([claimed, band])
            self.lines.append((tags["source"], shapely.LineString(centres)))
            for side, rail in enumerate(rails):
                outer = sample(rail)
                for j in range(len(centres)-1):
                    xz = np.array([centres[j], centres[j+1], rail[j+1], rail[j]])
                    y = np.array([levels[j, side], levels[j+1, side], outer[j+1], outer[j]])
                    for ids in ([0,1,2], [0,2,3]):
                        vertices = xz[ids]
                        polygon = shapely.Polygon(vertices).intersection(dry)
                        if polygon.area < 2e-6:
                            continue
                        coefficient = np.linalg.solve(np.c_[vertices, np.ones(3)], y[ids])
                        self.planes.append((polygon, coefficient))
        self.tree = shapely.STRtree([polygon for polygon,_ in self.planes])

    @property
    def boundaries(self):
        return [polygon.boundary for polygon, _ in self.planes]

    def owner(self, polygon):
        point = polygon.representative_point()
        hits = self.tree.query(point, predicate="intersects")
        return self.planes[int(min(hits))][1] if len(hits) else None

    def height(self, xz, base, owner=None):
        xz = np.asarray(xz, float)
        xy1 = np.c_[xz, np.ones(len(xz))]
        if owner is not None:
            return xy1 @ owner
        y = np.array(base, copy=True)
        # Also constrain neighbouring ground vertices on the apron rails. This
        # is essential: sampling the old terrain independently would reopen seams.
        points, polygons = self.tree.query(shapely.points(xz), predicate="dwithin", distance=2e-5)
        # Stable source order resolves the two sides only for a free query exactly
        # on a vertical edge. Owned support polygons always select their own side.
        for index in np.argsort(polygons)[::-1]:
            point, polygon = int(points[index]), int(polygons[index])
            y[point] = xy1[point] @ self.planes[polygon][1]
        return y

    def wall_meshes(self, boundaries, additional_owners=()):
        from .compile import digest, mesh_record
        from turku3d.structures.geometry import Faces
        pairs = {}
        for boundary in boundaries:
            p = np.asarray(boundary["positions"])
            key = digest(np.round(p[:,[0,2]], 5).tobytes())
            pairs.setdefault(key, []).append(boundary)
        output = []
        for key, edges in pairs.items():
            if len(edges) < 2:
                continue
            edges = sorted(edges, key=lambda e: np.mean(np.asarray(e["positions"])[:,1]))
            low, high = edges[0], edges[-1]
            a, b = np.asarray(low["positions"]), np.asarray(high["positions"])
            if np.max(b[:,1]-a[:,1]) < .01:
                continue
            midpoint = shapely.Point(np.mean(a[:,[0,2]], axis=0))
            source, distance = min(((source, line.distance(midpoint)) for source,line in [*self.lines,*additional_owners]), key=lambda x:x[1])
            if distance > 3e-5:
                raise ValueError(f"Discontinuous ground edge at {midpoint.wkt} has no explicit retaining or approach owner (distance {distance})")
            faces = Faces()
            face = np.array([a[0], a[1], b[1], b[0]])
            faces.face(face)
            faces.face(face[::-1])
            mesh = mesh_record("retaining:"+key[:16], source, "quay-wall", faces.positions, faces.indices, uv=faces.uvs)
            mesh["boundaryIds"] = [low["id"], high["id"]]
            output.append(mesh)
        return output

"""The street-object records of the city's registers and OpenStreetMap, normalised, for one survey box.

Every reader returns plain dicts with a stable `id`, a survey position and a `source` reference, so a kind's
`discover` can group them into entities and name where each fact came from.
"""

from __future__ import annotations

import json
import math

import numpy as np
import shapely

from .lidar import ObjectLidar

Box = tuple[float, float, float, float]
# kanta:Rakenne survey codes of the linear street objects (the others are walls, steps and ramps: w2-street).
FENCE_CODES = {"66636": "wood-fence", "66630": "stone-fence", "66632": "stone-fence", "62534": "railing"}
GATE_POST = "66114"
# Street-light register layers, best surveyed first; the registers repeat most poles about a metre apart.
LAMP_LAYERS = ("Kiilai:uv-pylväs mitattu", "rr:js,pylväs,valaisin", "rr:js,uv-pylväs", "Kiilai:uv-pylväs digitoitu")
LAMP_DUPLICATE = 1.5
CAR_PARTS = ("Ajorata", "Pyöräkaista")


def lidar_of(sources) -> ObjectLidar:
    """One object-lidar reader per Sources instance."""
    if not hasattr(sources, "_object_lidar"):
        sources._object_lidar = ObjectLidar(log=sources.log)
    return sources._object_lidar


def ground_height(sources, e: float, n: float) -> float:
    """The ground under a point, from the reader every kind shares so that things meet."""
    height = float(sources.terrain.heights(e, n)[0])
    return height if math.isfinite(height) else lidar_of(sources).ground(e, n)


def _point(feature):
    for geom in feature.geoms:
        if geom.type == "Point":
            return geom.shape
    return None


def signs(sources, box: Box) -> list[dict]:
    """GIS:Liikennemerkit rows: one per sign plate, with the Finnish sign code and the register's note."""
    rows = []
    for feature in sources.wfs.features("GIS:Liikennemerkit", box):
        point = _point(feature)
        if point is None:
            continue
        attrs = feature.attrs
        key = attrs.get("Id", "")
        rows.append({
            "id": key if key not in ("", "0") else f"x{round(point.x * 10)}y{round(point.y * 10)}",
            "code": attrs.get("Varustelaji_koodi", ""), "label": attrs.get("Varustelaji", ""),
            "note": attrs.get("Huomautus", ""), "e": round(point.x, 2), "n": round(point.y, 2),
            "source": {"source": "turku-wfs:GIS:Liikennemerkit", "key": key or feature.key},
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def lamp_posts(sources, box: Box) -> list[dict]:
    """frn:CityFurniture_LOD2 street-light poles, one row per real pole (the registers overlap)."""
    rows = []
    for feature in sources.wfs.features("frn:CityFurniture_LOD2", box):
        point = _point(feature)
        layer = feature.attrs.get("Tasonimi", "")
        if point is None or layer not in LAMP_LAYERS:
            continue
        rows.append({"id": feature.gml_id.replace("CityFurniture_", "") or feature.key, "layer": layer, "e": round(point.x, 2),
                     "n": round(point.y, 2), "h": round(point.z, 2) if point.has_z else None,
                     "source": {"source": "turku-wfs:frn:CityFurniture_LOD2", "key": feature.gml_id or feature.key}})
    rows.sort(key=lambda row: (LAMP_LAYERS.index(row["layer"]), row["id"]))
    kept: list[dict] = []
    for row in rows:
        if all(math.hypot(row["e"] - k["e"], row["n"] - k["n"]) > LAMP_DUPLICATE for k in kept):
            kept.append(row)
    return kept


def barriers(sources, box: Box) -> tuple[list[dict], list[dict]]:
    """kanta:Rakenne fences, stone fences and railings as lines, and gate posts as points."""
    lines, posts = [], []
    for feature in sources.wfs.features("kanta:Rakenne", box):
        code = feature.attrs.get("kplkoodi", "")
        for geom in feature.geoms:
            if code in FENCE_CODES and geom.type == "LineString" and geom.shape.length > 0.2:
                coords = np.round(np.asarray(geom.shape.coords)[:, :2], 2)
                a, b = tuple(coords[0]), tuple(coords[-1])
                first = min(a, b)
                lines.append({"id": f"r{round(first[0] * 10)}_{round(first[1] * 10)}_{round(geom.shape.length * 10)}",
                              "type": FENCE_CODES[code], "code": code, "line": coords.tolist(),
                              "surveyed": feature.attrs.get("alkuPvm", "")[:10],
                              "source": {"source": "turku-wfs:kanta:Rakenne", "key": feature.key}})
            elif code == GATE_POST and geom.type == "Point":
                posts.append({"id": f"g{round(geom.shape.x * 10)}_{round(geom.shape.y * 10)}", "e": round(geom.shape.x, 2),
                              "n": round(geom.shape.y, 2), "source": {"source": "turku-wfs:kanta:Rakenne", "key": feature.key}})
    lines.sort(key=lambda row: row["id"])
    posts.sort(key=lambda row: row["id"])
    return lines, posts


def furniture(sources, box: Box) -> list[dict]:
    """GIS:Varusteet rows (benches, bins, boards…), bike racks, ticket machines and city-bike stations."""
    rows = []
    for layer, kind in (("GIS:Varusteet", None), ("GIS:Polkupyoraparkki", "bike-rack"),
                        ("GIS:Pysakointiautomaatit", "ticket-machine"), ("GIS:Follariasemat", "bike-station")):
        for feature in sources.wfs.features(layer, box):
            point = _point(feature)
            if point is None:
                continue
            attrs = feature.attrs
            key = attrs.get("Id") or attrs.get("ObjectId") or ""
            rows.append({"id": (key if key not in ("", "0") else f"x{round(point.x * 10)}y{round(point.y * 10)}"), "layer": layer,
                         "kind": kind, "type": attrs.get("Tyyppi", ""), "species": attrs.get("Varustelaji", ""),
                         "model": attrs.get("Malli", ""), "maker": attrs.get("Valmistaja", ""),
                         "places": attrs.get("Pyorapaikkojen_lukumaara", ""), "e": round(point.x, 2), "n": round(point.y, 2),
                         "source": {"source": f"turku-wfs:{layer}", "key": key or feature.key}})
    rows.sort(key=lambda row: (row["layer"], row["id"], row["e"], row["n"]))
    return rows


class Streets:
    """Street centrelines and carriageway parts around a box, for orienting objects."""

    def __init__(self, sources, box: Box, margin: float = 40.0):
        wide = (box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin)
        self.cars, self.paths = [], []
        for feature in sources.wfs.features("GIS:Keskilinjat", wide):
            part = feature.attrs.get("Katuosalaji", "")
            for geom in feature.geoms:
                if geom.type == "LineString" and geom.shape.length > 1.0:
                    (self.cars if part in CAR_PARTS else self.paths).append(shapely.force_2d(geom.shape))
        self.car_tree = shapely.STRtree(self.cars) if self.cars else None
        self.path_tree = shapely.STRtree(self.paths) if self.paths else None

    @staticmethod
    def _nearest(tree, lines, e: float, n: float, reach: float):
        if tree is None:
            return None
        point = shapely.Point(e, n)
        best = None
        for k in tree.query(point.buffer(reach)):
            d = lines[int(k)].distance(point)
            if d <= reach and (best is None or d < best[0]):
                best = (d, lines[int(k)])
        if best is None:
            return None
        d, line = best
        s = line.project(point)
        a, b = line.interpolate(max(s - 1.0, 0.0)), line.interpolate(min(s + 1.0, line.length))
        length = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        q = line.interpolate(s)
        return {"distance": d, "foot": (q.x, q.y), "tangent": ((b.x - a.x) / length, (b.y - a.y) / length)}

    def car(self, e: float, n: float, reach: float = 25.0):
        return self._nearest(self.car_tree, self.cars, e, n, reach)

    def any(self, e: float, n: float, reach: float = 25.0):
        car, path = self.car(e, n, reach), self._nearest(self.path_tree, self.paths, e, n, reach)
        return min((w for w in (car, path) if w), key=lambda w: w["distance"], default=None)

    def sign_facing(self, e: float, n: float) -> dict | None:
        """The bearing a sign face at (e, n) looks towards: against the traffic that passes it on its right-hand
        side of the nearest carriageway (right-hand traffic), or along the nearest path."""
        way = self.car(e, n)
        method = "approach-on-the-sign's-side"
        if way is None:
            way, method = self.any(e, n), "along-nearest-path"
        if way is None:
            return None
        tx, ty = way["tangent"]
        px, py = e - way["foot"][0], n - way["foot"][1]
        left = tx * py - ty * px > 0
        dx, dy = (-tx, -ty) if left else (tx, ty)  # travel direction of the traffic the sign stands to the right of
        return {"bearing": round(math.degrees(math.atan2(-dx, -dy)) % 360, 1), "method": method,
                "distance": round(way["distance"], 2)}

    def towards_street(self, e: float, n: float, reach: float = 18.0) -> dict | None:
        """The bearing from a point to the nearest carriageway centreline (a lamp's arm, a bench's back)."""
        way = self.car(e, n, reach) or self.any(e, n, reach)
        if way is None or way["distance"] < 0.3:
            return None
        return {"bearing": round(math.degrees(math.atan2(way["foot"][0] - e, way["foot"][1] - n)) % 360, 1),
                "distance": round(way["distance"], 2)}


# -- OpenStreetMap ------------------------------------------------------------------------------------------------

FENCE_TAGS = ("fence_type", "height", "material")


def mapped_barriers(sources, box: Box) -> tuple[list[dict], list[dict]]:
    """What OpenStreetMap knows of barriers in a box: gates {id, e, n, way (the highway class through the gate),
    tags, source} and the fence lines that carry a type, height or material {line, tags}."""
    frame = sources.osm.features(box)
    gates, fences = [], []
    if not len(frame):
        return gates, fences
    ways = [(shapely.force_2d(g), str(h)) for g, h in zip(frame.geometry, frame.highway) if g.geom_type == "LineString" and isinstance(h, str)]
    tree = shapely.STRtree([line for line, _ in ways]) if ways else None
    for kind, ident, tags, barrier, geometry in zip(frame.osm_type, frame.osm_id, frame.tags, frame.barrier, frame.geometry):
        if barrier == "gate" and geometry.geom_type == "Point":
            through = [ways[int(k)] for k in tree.query(geometry.buffer(0.5))] if tree is not None else []
            through = sorted((line.distance(geometry), name) for line, name in through)
            gates.append({"id": f"o{ident}", "e": round(float(geometry.x), 2), "n": round(float(geometry.y), 2),
                          "way": through[0][1] if through and through[0][0] <= 0.5 else "",
                          "tags": json.loads(tags) if isinstance(tags, str) else dict(tags or {}),
                          "source": {"source": "osm", "key": f"{kind}/{ident}"}})
        elif barrier == "fence" and geometry.geom_type == "LineString":
            tags = json.loads(tags) if isinstance(tags, str) else dict(tags or {})
            if any(name in tags for name in FENCE_TAGS):
                fences.append({"line": shapely.force_2d(geometry), "tags": {name: tags[name] for name in FENCE_TAGS if name in tags}})
    gates.sort(key=lambda row: row["id"])
    return gates, fences

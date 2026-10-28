"""Source-driven quay passages at masonry bridge abutments."""

import json

import numpy as np
import shapely


def quay_passages(rows):
    """Discover lower walks through stone abutments from full OSM source geometry.

    Explicit tunnels win. An untagged transverse walk, or two aligned path ends
    at opposite bridge edges, supplies a missing passage. Sidewalks, crossings
    and steps cannot create inferred tunnels. Stone bridge profiles are photo
    evidence, not per-tile build exceptions.
    """
    from .bridge_details import PROFILES
    from .tunnels import selected

    records, bridges = {}, {}
    for row in rows:
        tags = json.loads(row["tags"] or "{}")
        key = str(row.get("osm_id"))
        geom = shapely.force_2d(shapely.from_wkb(row["geometry_local"]))
        if tags.get("man_made") == "bridge":
            if geom.geom_type == "LineString" and geom.is_ring:
                geom = shapely.Polygon(geom)
            if geom.geom_type in {"Polygon", "MultiPolygon"}:
                bridges[key] = (geom, tags)
        elif (
            tags.get("highway") in {"footway", "path", "pedestrian"}
            and tags.get("bridge", "no") == "no"
            and not tags.get("footway")
            and geom.geom_type == "LineString"
        ):
            records[key] = (geom, tags)
    out = []
    for bridge_id, (polygon, tags) in bridges.items():
        stone = (
            tags.get("material") in {"stone", "masonry", "granite"}
            or PROFILES.get(tags.get("wikidata"), {}).get("support") == "stone"
        )
        if not stone:
            continue
        rectangle = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords)[:4]
        edges = np.roll(rectangle, -1, axis=0) - rectangle
        axis = edges[np.argmax(np.linalg.norm(edges, axis=1))].copy()
        axis /= np.linalg.norm(axis)
        normal = np.array([-axis[1], axis[0]])
        centre = np.asarray(polygon.centroid.coords[0])
        extent = np.max(np.abs((rectangle - centre) @ axis))
        candidates, ends = [], []
        for key, (line, path_tags) in records.items():
            if line.distance(polygon) > 4:
                continue
            coords = np.asarray(line.coords)
            for k, neighbour in ((0, 1), (-1, -2)):
                point = coords[k]
                direction = point - coords[neighbour]
                direction /= max(np.linalg.norm(direction), 1e-9)
                # Only quay-level paths approaching an abutment sideways.
                if (
                    polygon.boundary.distance(shapely.Point(point)) < 4
                    and abs(direction @ axis) < 0.5
                    and abs((point - centre) @ axis) > extent - 5
                ):
                    ends.append((key, point, direction, path_tags))
            overlap = line.intersection(polygon)
            if overlap.is_empty or overlap.length < 2:
                continue
            delta = coords[-1] - coords[0]
            if (
                abs(delta @ axis) < 0.5 * np.linalg.norm(delta)
                and abs((np.asarray(overlap.centroid.coords[0]) - centre) @ axis) > extent - 5
                and line.length
                < 2 * np.linalg.norm(edges[np.argmin(np.linalg.norm(edges, axis=1))])
            ):
                candidates.append((key, line, path_tags))
        for i, (ka, a, da, ta) in enumerate(ends):
            for kb, b, db, _ in ends[i + 1 :]:
                delta = b - a
                length = np.linalg.norm(delta)
                if (
                    ka == kb
                    or length < 3
                    or length > 25
                    or abs(delta @ axis) > 0.35 * length
                    or (a - centre) @ normal * ((b - centre) @ normal) >= 0
                    or da @ (delta / length) < 0.85
                    or db @ (-delta / length) < 0.85
                ):
                    continue
                line = shapely.LineString([a, b])
                if line.intersection(polygon.buffer(2)).length < length * 0.5:
                    continue
                candidates.append((f"{ka}+{kb}", line, ta))
        # Prefer explicit tunnel ways and complete mapped walks over inferred gaps.
        accepted = []
        for key, line, path_tags in sorted(
            candidates, key=lambda item: (not selected(item[2]), "+" in item[0], item[0])
        ):
            if any(line.distance(other) < 3 for other in accepted):
                continue
            accepted.append(line)
            out.append(
                (
                    key if selected(path_tags) else f"quay-{bridge_id}-{key}",
                    line,
                    {
                        **path_tags,
                        "highway": "footway",
                        "tunnel": "yes",
                        "vault": "barrel",
                        "width": path_tags.get("width", "2.4"),
                        "maxheight": path_tags.get("maxheight", "2.7"),
                    },
                )
            )
    return out

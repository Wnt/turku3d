"""Join ranked landmarks to prepared city shells and estimate street visibility.

Consumes existing content-addressed survey products. No lidar blocks are rebuilt.
Visibility uses roof-height occlusion along actual street samples, not street count
alone. Results retain source files and distinguish LOD2 from lidar-supported roofs.
"""

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import shapely

from .ranking import rank


def consolidate(rows):
    """OSM ways and relations can describe one surveyed building: treat it once."""
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get("building_id") or row["id"], []).append(row)
    result = []
    for members in grouped.values():
        primary = max(members, key=lambda row: (bool(row["tags"].get("name")), row["score"]))
        primary["aliases"] = sorted({row["id"] for row in members if row["id"] != primary["id"]})
        result.append(primary)
    return sorted(result, key=lambda row: (-row["score"], row["id"]))


def measure(rows, roots):
    buildings = {}
    streets = {}
    sources = []
    files = []
    for root in roots:
        files.extend(root.glob("**/.inputs/*/wfs.*.pkl"))
    for path in sorted(set(files), key=lambda p: p.stat().st_mtime):
        try:
            value = pickle.loads(path.read_bytes())["value"]
        except (ValueError, EOFError):
            continue
        for b in value.get("buildings", []):
            buildings[str(b.facade_key)] = b
        sources.append(str(path))
    for root in roots:
        for path in root.glob("**/.inputs/*/appearance.*.pkl"):
            value = pickle.loads(path.read_bytes())["value"]
            for geometry, name, kind in value.get("streets", []):
                streets[geometry.wkb] = (geometry, name or kind)
    blds = list(buildings.values())
    polygons = [b.footprint for b in blds]
    tree = shapely.STRtree(polygons)
    street_rows = list(streets.values())
    street_tree = shapely.STRtree([geometry for geometry, _ in street_rows])
    for row in rows:
        p = shapely.Point(row["location_xz"])
        matches = tree.query(p, predicate="intersects")
        if not len(matches):
            continue
        footprint = shapely.from_wkt(row["footprint_wkt"])
        index = max(
            matches,
            key=lambda i: (
                polygons[i].intersection(footprint).area / polygons[i].union(footprint).area
            ),
        )
        b = blds[index]
        e = row["evidence"]
        row["building_id"] = str(b.facade_key)
        row["tile"] = getattr(b, "visual_owner", row["tile"])
        e["height_m"] = round(b.top - b.base, 3)
        e["base_n2000_m"] = b.base
        e["top_n2000_m"] = b.top
        e["height_evidence"] = getattr(b, "height_evidence", [])
        e["height_source"] = (
            "city LOD2 with stored lidar roof residuals"
            if e["height_evidence"]
            else "city LOD2; lidar support not available"
        )
        neighbours = [j for j in tree.query(p.buffer(500), predicate="intersects") if j != index]
        if neighbours:
            e["skyline_prominence_m"] = round(
                max(0, b.top - float(np.median([blds[j].top for j in neighbours]))), 2
            )
        # Rank every footprint; expensive visibility only resolves buildings that
        # could reach the candidate tier with the maximum twenty visibility points.
        preliminary = rank(row["tags"], e)
        if preliminary["score"] + 20 < 28:
            row.update(preliminary)
            continue
        seen = set()
        seen_streets = set()
        tested = set()
        for street_index in street_tree.query(p.buffer(300), predicate="intersects"):
            geometry, name = street_rows[street_index]
            if (
                geometry.geom_type not in ("LineString", "MultiLineString")
                or geometry.distance(p) > 300
            ):
                continue
            for fraction in np.linspace(0, 1, max(2, min(12, int(geometry.length / 40) + 1))):
                eye = geometry.interpolate(fraction, normalized=True)
                if eye.distance(p) > 300 or eye.distance(b.footprint) < 3:
                    continue
                position = (round(eye.x), round(eye.y))
                if position in tested:
                    continue
                tested.add(position)
                line = shapely.LineString([eye, p])
                blocked = False
                for j in tree.query(line, predicate="intersects"):
                    if j == index:
                        continue
                    hit = line.intersection(polygons[j]).representative_point()
                    t = line.project(hit, normalized=True)
                    # The target is the landmark's mid-height; a low foreground building
                    # may occlude its entrance while leaving its skyline visible.
                    sight_height = b.base + 1.7 + t * max(0, (b.top - b.base) * 0.5 - 1.7)
                    if blds[j].top > sight_height:
                        blocked = True
                        break
                if not blocked:
                    seen.add(position)
                    seen_streets.add(name)
        if streets:
            e["visible_viewpoints"] = len(seen)
            e["visible_streets"] = len(seen_streets)
            e["visibility_samples"] = len(tested)
            e["visibility_method"] = (
                "OSM streets sampled <=40m, within300m; LOD2 roof-height ray occlusion, landmark mid-height target; eye ground approximated from landmark base"
            )
        row.update(rank(row["tags"], e))
    return dict(prepared_buildings=len(blds), street_segments=len(streets), sources=sources)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ranked", type=Path, required=True)
    p.add_argument("--cache-root", type=Path, action="append", required=True)
    a = p.parse_args()
    data = json.loads(a.ranked.read_text())
    report = measure(data["buildings"], a.cache_root)
    data["geometry_coverage"] = report
    data["coverage"]["osm_footprints_before_consolidation"] = len(data["buildings"])
    data["buildings"] = consolidate(data["buildings"])
    a.ranked.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}))


if __name__ == "__main__":
    main()

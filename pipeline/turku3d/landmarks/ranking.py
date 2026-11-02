"""Explainable citywide landmark ranking from local OSM and measured enrichment.

Missing evidence is reported, never silently treated as a negative finding.
Optional register/lidar/visibility/Wikimedia evidence is keyed by OSM identity.
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def rank(tags, evidence=None):
    e = evidence or {}
    reasons = []

    def add(kind, points, value):
        if value not in (None, False, "", "no", "0", 0):
            reasons.append(dict(signal=kind, points=round(points, 2), value=value))

    add("historic", 18, tags.get("historic"))
    add("heritage", 22, tags.get("heritage"))
    add("protected_register", 25, e.get("protected"))
    add("national_heritage_rky", 22, e.get("rky"))
    add("place_of_worship", 18, tags.get("amenity") == "place_of_worship")
    add("tourism", 12, tags.get("tourism"))
    add("wikidata", 10, tags.get("wikidata"))
    add("wikipedia", 12, tags.get("wikipedia"))
    add(
        "monumental_use",
        14,
        tags.get("building") in ("cathedral", "church", "castle", "museum", "civic"),
    )
    views = e.get("monthly_pageviews")
    add("monthly_pageviews", min(18, 3 * math.log10(1 + max(0, views or 0))), views)
    height = e.get("height_m")
    if height is None:
        try:
            height = float(str(tags.get("height", "")).replace(" m", ""))
        except ValueError:
            height = None
    add(
        "height",
        min(18, max(0, (height or 0) - 20) * 0.3),
        height if height and height > 20 else None,
    )
    prominence = e.get("skyline_prominence_m")
    add("skyline_prominence", min(18, max(0, prominence or 0) * 0.4), prominence)
    rarity = e.get("footprint_rarity")
    if rarity:
        add("footprint_uniqueness", 8 * rarity["rarity"], rarity)
    else:
        compactness = e.get("compactness")
        add(
            "footprint_complexity",
            min(8, max(0, 0.65 - (compactness or 1)) * 20),
            compactness if compactness and compactness < 0.65 else None,
        )
    streets = e.get("visible_streets")
    views = e.get("visible_viewpoints")
    add("visible_streets", min(10, 2 * (streets or 0)), streets)
    add("visible_viewpoints", min(10, views or 0), views)
    score = round(sum(r["points"] for r in reasons), 2)
    return dict(
        score=score,
        tier="landmark" if score >= 55 else "candidate" if score >= 28 else "ordinary",
        reasons=reasons,
        missing_evidence=[
            k
            for k in [
                "protected",
                "rky",
                "monthly_pageviews",
                "height_m",
                "skyline_prominence_m",
                "visible_streets",
                "visible_viewpoints",
            ]
            if k not in e
        ],
    )


def footprint_rarity(rows):
    """Empirical shape rarity across the inventory, rather than complexity alone."""
    import shapely

    descriptors = []
    for row in rows:
        g = shapely.from_wkt(row["footprint_wkt"])
        compactness = 4 * math.pi * g.area / g.length**2
        descriptor = (
            int(math.log2(max(20, g.area)) / 2),
            min(9, int(compactness * 10)),
            min(6, int(math.log2(max(4, len(shapely.get_coordinates(g)) - 1)))),
        )
        descriptors.append(descriptor)
    counts = Counter(descriptors)
    for row, descriptor in zip(rows, descriptors, strict=True):
        frequency = counts[descriptor] / len(rows)
        row["evidence"]["footprint_rarity"] = dict(
            rarity=round(min(1, max(0, (-math.log10(frequency) - 1) / 3)), 4),
            matching_shapes=counts[descriptor],
            population=len(rows),
            descriptor=list(descriptor),
            method="city frequency of log-area, compactness decile and log-corner-count bins",
        )
        row.update(rank(row["tags"], row["evidence"]))


def inventory(paths, enrichment=None):
    import pyarrow.parquet as pq
    import shapely

    records = {}
    enrichment = enrichment or {}
    for path in sorted(paths):
        table = pq.read_table(path, columns=["osm_type", "osm_id", "tags", "geometry_local"])
        for row in table.to_pylist():
            tags = json.loads(row["tags"] or "{}")
            if not any(
                tags.get(k)
                for k in ("building", "historic", "heritage", "tourism", "wikidata", "wikipedia")
            ):
                continue
            key = f"{row['osm_type']}/{row['osm_id']}"
            if key in records:
                continue
            g = shapely.from_wkb(row["geometry_local"])
            if g.geom_type == "LineString" and g.is_ring:
                g = shapely.Polygon(g)
            # Structures need their own generators; a historic ship is not a church facade.
            if g.area < 20 or tags.get("building") in ("ship", "no"):
                continue
            if not tags.get("building") and tags.get("historic") not in (
                "castle",
                "manor",
                "church",
            ):
                continue
            evidence = dict(enrichment.get(key, {}))
            if g.area > 0:
                evidence["compactness"] = 4 * math.pi * g.area / g.length**2
            result = rank(tags, evidence)
            p = g.representative_point()
            records[key] = dict(
                id=key,
                name=(
                    tags.get("name")
                    or tags.get("addr:housename")
                    or tags.get("wikipedia", "").partition(":")[2]
                    or tags.get("description:en")
                    or tags.get("description")
                    or key
                ),
                tags=tags,
                location_xz=[p.x, p.y],
                footprint_wkt=g.wkt,
                tile=f"t0_{math.floor(p.x / 250)}_{math.floor(p.y / 250)}",
                evidence=evidence,
                **result,
            )
    rows = list(records.values())
    footprint_rarity(rows)
    return sorted(rows, key=lambda r: (-r["score"], r["id"]))


def main():
    from turku3d import config as C

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--osm-root", type=Path, default=C.raw_dir("osm"))
    p.add_argument(
        "--city",
        action="store_true",
        help="Extract the complete municipality from the cached Finland snapshot",
    )
    p.add_argument("--evidence", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    paths = list(a.osm_root.glob("**/*.parquet"))
    coverage = dict(osm_files=len(paths), scope="available local city OSM extracts")
    if a.city:
        from .city_source import extract

        snapshots = sorted((a.osm_root / "geofabrik").glob("finland-*.osm.pbf"))
        if not snapshots:
            p.error("--city requires an existing read-only Finland snapshot")
        path, coverage = extract(snapshots[-1], a.out.parent / "city-osm")
        paths = [path]
    evidence = (
        json.loads(a.evidence.read_text())
        if a.evidence
        else (
            {r["id"]: r.get("evidence", {}) for r in json.loads(a.out.read_text())["buildings"]}
            if a.out.exists()
            else {}
        )
    )
    rows = inventory(paths, evidence)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        json.dumps(
            dict(
                version=1,
                coverage=coverage,
                buildings=rows,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            dict(
                ranked=len(rows),
                landmarks=sum(r["tier"] == "landmark" for r in rows),
                top=[(r["name"], r["score"]) for r in rows[:15]],
            )
        )
    )


if __name__ == "__main__":
    main()

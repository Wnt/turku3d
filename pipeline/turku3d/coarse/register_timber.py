"""Facade material of the register (kanta:Rakennus) for buildings dated before 1940, city-wide.

The proven generator reads kanta:Rakennus only where a tile input carries it (the centre's
work-3x3 and a few tiles), so outside the centre an old wooden house is wood only when OSM tags
it. This table carries the one register fact the old-wooden-house rules need everywhere: the
facade material ("puu" or "muu materiaali") of every building dated before 1940.

Rules (``timber``), both ranked below AI descriptions and reviewed readings
(``facade_inference.building_material``):
- register "puu" (wood) before 1940: timber;
- register "muu materiaali" (other material) before 1940, at most two storeys, residential use:
  timber, inferred. All six such houses read in W2 were wooden houses (four boarded, two
  rendered over boards) and the 1920s houses photographed in Itäharju, Kähäri and Raunistula are
  boarded (W2 b31c6fc7). Scope: city-wide, because the evidence spans the centre and the suburbs.

``python -m turku3d.coarse.register_timber build`` writes the table from a city-wide building
table (key, material, year, storeys, use, geometry WKB in EPSG:3877) and the list of buildings
whose facade the rules can change (``migrations/old_timber_keys.json``) for the spot rebuild.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("register_timber.json")
KEYS = Path(__file__).parents[1] / "migrations" / "old_timber_keys.json"
BEFORE = 1940
WOOD, OTHER = "puu", "muu materiaali"


@lru_cache(maxsize=1)
def table() -> dict[str, str]:
    data = json.loads(DATA.read_text())
    return {key: material for material, keys in data["buildings"].items() for key in keys}


def timber(attrs: dict) -> dict | None:
    """The register's timber evidence for a building's resolved attributes, or None."""
    from turkutex.facade_archetypes import RESIDENTIAL_USES, building_use, parsed_year

    material = table().get(str(attrs.get("register_id") or ""))
    year = parsed_year(attrs.get("construction_year"))
    if material is None or year is None or year >= BEFORE:
        return None
    if material == WOOD:
        return dict(value="wood", support=0.35, source="register facade material (puu), dated before 1940")
    levels = attrs.get("levels") if attrs.get("levels_source") == "register" else None
    if (levels is None or levels <= 2) and building_use(attrs) in RESIDENTIAL_USES:
        return dict(value="wood", support=0.3,
                    source="inferred timber: register other material, residential, <= 2 storeys, before 1940")
    return None


def build(city: Path, osm: Path | None, extra: list[Path]) -> dict:
    """Write the table and the spot-rebuild key list; returns counts."""
    import pyarrow.parquet as pq
    import shapely
    from turkutex.facade_archetypes import RESIDENTIAL_USES

    rows = pq.read_table(city, columns=["key", "material", "year", "storeys", "use", "geometry"]).to_pylist()
    old = [r for r in rows if r["year"] and r["year"] == r["year"] and r["year"] < BEFORE]
    buildings = {m: sorted(r["key"] for r in old if r["material"] == m) for m in (WOOD, OTHER)}
    data = dict(format="turku3d-register-timber", version=1, before=BEFORE,
                source=f"Turku kanta:Rakennus julkisivumateriaali via {city.name}", buildings=buildings)
    DATA.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n")
    inferred = {r["key"] for r in old if r["material"] == OTHER and (r["storeys"] or 0) <= 2
                and str(r["use"]) in RESIDENTIAL_USES}
    keys = set(buildings[WOOD]) | inferred
    osm_wood = set()
    if osm:
        # The proven enrich rule: an OSM outline tagged wood that covers the footprint.
        import json as js

        tags = pq.read_table(osm, columns=["tags", "geometry"]).to_pylist()
        polygons = []
        for t in tags:
            tg = js.loads(t["tags"]) if t["tags"] else {}
            if not tg.get("building"):
                continue
            if "wood" not in (tg.get("building:material"), tg.get("building:cladding")):
                continue
            shape = shapely.make_valid(shapely.from_wkb(t["geometry"]))
            if shape.geom_type in ("LineString", "LinearRing") and shape.is_closed:
                shape = shapely.Polygon(shape)
            if shape.area >= 2:
                polygons.append(shape)
        tree = shapely.STRtree(polygons)
        for r in old:
            footprint = shapely.make_valid(shapely.from_wkb(r["geometry"]))
            for index in tree.query(footprint):
                overlap = footprint.intersection(polygons[index]).area
                if overlap / max(footprint.area, 1e-9) > 0.65 and overlap / polygons[index].area > 0.3:
                    osm_wood.add(r["key"])
                    break
    keys |= osm_wood
    reviewed = set()
    for path in extra:
        rows_extra = json.loads(path.read_text())
        ids = rows_extra.keys() if isinstance(rows_extra, dict) else [r.get("building_id") for r in rows_extra]
        reviewed |= {str(k).removeprefix("gis-") for k in ids}
    keys |= reviewed & {r["key"] for r in old}
    KEYS.write_text(json.dumps(dict(format="turku3d-old-timber-keys", version=1, keys=sorted(keys)),
                               separators=(",", ":")) + "\n")
    return dict(old=len(old), puu=len(buildings[WOOD]), other=len(buildings[OTHER]), inferred=len(inferred),
                osm_wood=len(osm_wood), reviewed=len(reviewed & {r["key"] for r in old}), keys=len(keys))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("build")
    b.add_argument("--city", type=Path, required=True)
    b.add_argument("--osm", type=Path)
    b.add_argument("--extra", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)
    print(json.dumps(build(args.city, args.osm, args.extra)))


if __name__ == "__main__":
    main()

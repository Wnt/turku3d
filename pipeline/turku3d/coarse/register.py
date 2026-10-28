"""Optional cached register enrichment, indexed once per prepared tile input."""

import re

MATERIALS = {
    "puu": "wood",
    "tiili": "brick",
    "betoni": "concrete",
    "metallilevy": "metal",
    "rappaus": "render",
}


def attributes(row):
    def value(*keys):
        return next((row[k] for k in keys if row.get(k) not in (None, "", "0")), None)

    material = str(value("julkisivumateriaali") or "").lower()
    year = re.search(r"(1[6-9]\d\d|20\d\d)", str(value("kottovuosi", "Valmistunut") or ""))
    year = int(year[0]) if year else None
    levels = str(value("kerrosluku", "Kerrosluku") or "")
    return dict(
        material_class=MATERIALS.get(material),
        levels=int(levels) if levels.isdigit() and int(levels) > 0 else None,
        era="unknown"
        if year is None
        else "pre-1850"
        if year < 1850
        else "1850-1919"
        if year < 1920
        else "1920-1959"
        if year < 1960
        else "1960+",
    )


def read_register(paths):
    import pyarrow.parquet as pq

    result = {}
    # Kanta facade material takes precedence; GIS contributes missing year/levels.
    for path in sorted(paths, key=lambda p: p.name):
        names = pq.read_schema(path).names
        columns = [
            k
            for k in (
                "rakennustunnus",
                "PysyvaRakennusTunnus",
                "julkisivumateriaali",
                "kerrosluku",
                "Kerrosluku",
                "kottovuosi",
                "Valmistunut",
            )
            if k in names
        ]
        for row in pq.read_table(path, columns=columns).to_pylist():
            key = row.get("rakennustunnus") or row.get("PysyvaRakennusTunnus")
            if not key:
                continue
            result.setdefault(str(key), {}).update(
                {k: v for k, v in attributes(row).items() if v is not None and v != "unknown"}
            )
    return result


def read_osm(tile, path):
    """OSM building outlines and shop POIs are optional independent appearance evidence."""
    import json

    import pyarrow.parquet as pq
    import shapely
    from PIL import ImageColor

    buildings, shops = [], []
    window = shapely.box(*tile.bbox.buffered(100).as_tuple())
    classes = {
        "plaster": "render",
        "brick": "brick",
        "bricks": "brick",
        "wood": "wood",
        "concrete": "concrete",
        "metal": "metal",
        "metal_plates": "metal",
        "stone": "stone",
        "glass": "glass",
        "tiles": "concrete",
    }
    for row in pq.read_table(path, columns=["tags", "geometry_local"]).to_pylist():
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if not tags.get("building") and not tags.get("shop"):
            continue
        geom = shapely.force_2d(shapely.make_valid(shapely.from_wkb(row["geometry_local"])))
        if not geom.intersects(window):
            continue
        if tags.get("shop") not in (None, "no", "vacant"):
            shops.append(geom.representative_point())
        if not tags.get("building"):
            continue
        if geom.geom_type == "LineString" and geom.is_ring:
            geom = shapely.make_valid(shapely.Polygon(geom))
        if geom.geom_type not in ("Polygon", "MultiPolygon") or geom.area < 4:
            continue
        d = attributes(
            dict(
                kerrosluku=tags.get("building:levels"),
                kottovuosi=tags.get("start_date", tags.get("construction_year")),
            )
        )
        d["material_class"] = classes.get(tags.get("building:material"))
        try:
            rgb = ImageColor.getrgb(tags.get("building:colour", ""))
            if len(rgb) == 3:
                d["wall_colour"] = "#{:02x}{:02x}{:02x}".format(*rgb)
        except ValueError:
            pass
        if tags.get("building") == "retail" or tags.get("shop"):
            d["ground_floor"] = "shops"
        if tags.get("building") in ("shed", "garage", "garages"):
            d["blank"] = True
        buildings.append((geom, {k: v for k, v in d.items() if v is not None and v != "unknown"}))
    return dict(buildings=buildings, shops=shops)


def enrich(building, registered, osm):
    """Spatially match appearance without changing shell identity or roof metadata."""
    footprint = building.footprint
    candidates = []
    for polygon, attrs in osm.get("buildings", []):
        if not footprint.intersects(polygon):
            continue
        overlap = footprint.intersection(polygon).area
        if overlap / footprint.area > 0.65 and overlap / polygon.area > 0.3:
            candidates.append((overlap / footprint.union(polygon).area, attrs))
    d = dict(building.attributes)
    if candidates:
        own = max(candidates, key=lambda r: r[0])[1]
        # Register/LOD2 storeys remain authoritative; OSM fills absent layout and adds appearance.
        d.update(
            {
                k: v
                for k, v in own.items()
                if k not in ("levels", "era") or not d.get(k) or d[k] == "unknown"
            }
        )
    d.update(registered.get(building.facade_key, {}))
    if any(footprint.covers(point) for point in osm.get("shops", [])):
        d["ground_floor"] = "shops"
    building.attributes = d

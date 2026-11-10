"""One-off: where gulls call in Turku, from the OSM tile clips (read-only): the Aura's centreline and the harbour.

Usage: ~/turku3d/envs/pipeline/bin/python -I water_zones.py OUT.json
Reads ~/turku3d/data/raw/osm/coarse-*/*.parquet (EPSG:3877), merges the river ways named Aurajoki into one line,
simplifies it, and lists named water and harbour features near the river mouth for choosing the harbour zone.
"""
import glob
import json
import os
import sys

import pyarrow.parquet as pq
from shapely import wkb
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union

E0, N0 = 23459500, 6704500  # game/src/core/city.ts, Turku origin
lines, waters, harbour = [], [], []
for path in sorted(glob.glob(os.path.expanduser("~/turku3d/data/raw/osm/coarse-*/*.parquet"))):
    table = pq.read_table(path, columns=["osm_type", "osm_id", "tags", "name", "natural", "landuse", "geometry"]).to_pandas()
    for row in table.itertuples():
        tags = json.loads(row.tags) if isinstance(row.tags, str) else {}
        name = row.name if isinstance(row.name, str) else tags.get("name", "")
        geometry = wkb.loads(row.geometry)
        if tags.get("waterway") in ("river", "riverbank") and geometry.geom_type in ("LineString", "MultiLineString"):
            lines.append((name, row.osm_id, geometry))
        elif (row.natural == "water" or tags.get("natural") in ("water", "bay", "strait")) and geometry.geom_type in ("Polygon", "MultiPolygon"):
            waters.append((name, tags.get("water", ""), row.osm_id, geometry))
        if tags.get("landuse") in ("harbour", "port") or tags.get("industrial") == "port" or "harbour" in tags or tags.get("leisure") == "marina" or tags.get("amenity") == "ferry_terminal":
            harbour.append((name, {k: tags[k] for k in ("landuse", "industrial", "harbour", "leisure", "amenity") if k in tags}, row.osm_id, geometry))
print("river ways:", sorted({(n, i) for n, i, _ in lines})[:20])
merged = linemerge(unary_union([g for n, _, g in lines if "Aura" in n or not n]))
parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
parts.sort(key=lambda g: -g.length)
print("merged parts:", [round(p.length) for p in parts[:6]])
river = parts[0].simplify(12)
engine = [[round(x - E0), round(-(y - N0))] for x, y in river.coords]
print("river points:", len(engine), "length", round(river.length))
print(engine)
seen = set()
print("named waters:")
for name, kind, osm_id, g in waters:
    if (name, osm_id) in seen or g.area < 3000:
        continue
    seen.add((name, osm_id))
    c = g.centroid
    print(f"  {name or '-':<28} {kind:<10} area {g.area/1e4:7.1f} ha  centroid engine ({c.x - E0:7.0f}, {-(c.y - N0):7.0f})  bounds x {g.bounds[0]-E0:.0f}..{g.bounds[2]-E0:.0f} z {-(g.bounds[3]-N0):.0f}..{-(g.bounds[1]-N0):.0f}")
seen = set()
print("harbour features:")
for name, tags, osm_id, g in harbour:
    if osm_id in seen:
        continue
    seen.add(osm_id)
    c = g.centroid
    print(f"  {name or '-':<34} {json.dumps(tags):<44} {g.geom_type:<12} engine ({c.x - E0:7.0f}, {-(c.y - N0):7.0f}) area {getattr(g, 'area', 0)/1e4:.1f} ha")
# The harbour basin: Linnanaukko (the bay at the castle, with the ferry terminals and the port's quays), simplified.
basin = unary_union([g for name, _, _, g in waters if name == "Linnanaukko"])
ring = (max(basin.geoms, key=lambda g: g.area) if basin.geom_type == "MultiPolygon" else basin).simplify(45).exterior
harbour_engine = [[round(x - E0), round(-(y - N0))] for x, y in ring.coords][:-1]
print("harbour ring:", len(harbour_engine), "points, area", round(basin.area / 1e4, 1), "ha")
print(harbour_engine)
# The urban river: from the Halinen rapids down to the mouth.
start = min(range(len(engine)), key=lambda i: (engine[i][0] - 2198) ** 2 + (engine[i][1] + 1569) ** 2)
print("urban river:", engine[start:])
if len(sys.argv) > 1:
    json.dump({"river_engine": engine[start:], "harbour_engine": harbour_engine}, open(sys.argv[1], "w"))

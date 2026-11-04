"""OSM bridges and tunnels of the whole city for the drive sweep (game/src/qa/drivetest/structures.ts).

Reads every coarse OSM clip under $TURKU3D_DATA/raw/osm (read-only), keeps the line ways tagged bridge=* (not
bridge=no), tunnel=* or covered=yes (road, rail, path, cable car), dedupes them by way id and writes one JSON with
their names, tags and engine x,z polylines (x = E - 23459500, z = -(N - 6704500)). Roads.json has no rail and no
names; this file supplies both: rail bridges become underpass candidates for the lanes below them.

  bash pipeline/env/run.sh python tools/drivetest/osm_structures.py OUT.json
"""
import glob
import json
import os
import sys

import geopandas as gpd

E0, N0 = 23459500, 6704500


def main(out):
    root = os.path.join(os.environ.get("TURKU3D_DATA", os.path.expanduser("~/turku3d/data")), "raw/osm")
    ways = {}
    for path in sorted(glob.glob(f"{root}/**/*.parquet", recursive=True)):
        g = gpd.read_parquet(path, columns=["osm_type", "osm_id", "tags", "name", "geometry"])
        g = g[(g["osm_type"] == "way") & g.geometry.geom_type.isin(["LineString"])]
        for row in g.itertuples():
            if row.osm_id in ways:
                continue
            tags = json.loads(row.tags) if isinstance(row.tags, str) else dict(row.tags or {})
            bridge = tags.get("bridge", "no") not in ("no", "")
            tunnel = tags.get("tunnel", "no") not in ("no", "") or tags.get("covered") == "yes"
            if not (bridge or tunnel):
                continue
            kind = "bridge" if bridge else "tunnel"
            mode = ("rail" if "railway" in tags else "road" if tags.get("highway") in CAR else
                    "path" if "highway" in tags else "other")
            coords = [(round(x - E0, 2), round(-(y - N0), 2)) for x, y in row.geometry.coords]
            ways[int(row.osm_id)] = {"way": int(row.osm_id), "kind": kind, "mode": mode, "name": tags.get("name") or tags.get("bridge:name") or "",
                                     "tags": {k: tags[k] for k in ("highway", "railway", "bridge", "tunnel", "covered", "layer", "lanes", "oneway", "maxspeed", "ref") if k in tags},
                                     "points": coords}
    rows = sorted(ways.values(), key=lambda w: w["way"])
    with open(out, "w") as f:
        json.dump({"format": "turku3d-osm-structures", "version": 1, "ways": rows}, f)
    by = {}
    for w in rows:
        by[(w["kind"], w["mode"])] = by.get((w["kind"], w["mode"]), 0) + 1
    print(json.dumps({f"{k}/{m}": n for (k, m), n in sorted(by.items())}))


CAR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service",
       "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "road"}

if __name__ == "__main__":
    main(sys.argv[1])

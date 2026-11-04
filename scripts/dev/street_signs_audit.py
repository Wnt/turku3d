#!/usr/bin/env python3
"""City-wide audit of traffic-signal and traffic-sign posts in finished tile products.

For every tile of a product generation directory (``<farm>/products/<generation>``) it counts,
before and after the rules of ``roads.signal_posts`` (signals@1) and ``props.signposts`` (signs@1):

* signal heads whose post stands on the surveyed carriageway (GIS:Katualueet Ajorata/Pyöräkaista);
* drawn sign rows that repeat a code within ``signposts.DUPLICATE`` (doubles);
* sign posts within ``signposts.POST`` of another post (register stacks drawn as separate posts);
* sign posts on the carriageway.

A tile whose product already carries the rules' output (``signalPosts`` / ``plates``) is audited
as drawn. Per-tile rows go to ``--out`` (JSONL) with the residual cases; ``--summary`` groups
the counts by district (the GIS:Katualueet ``Hoitoalue`` holding most street area in the tile).

    python3 scripts/dev/street_signs_audit.py --products $FARM/products/<generation> --out audit.jsonl \
        --summary audit.json [--jobs 4] [--tiles t0_0_0 t0_1_0]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

WINDOW = 60.0


def glb_json(path: Path) -> dict:
    with path.open("rb") as stream:
        head = stream.read(20)
        size = struct.unpack_from("<I", head, 12)[0]
        return json.loads(stream.read(size))


def sign_rows(document: dict):
    for node in document.get("nodes", []):
        extras = node.get("extras") or {}
        if extras.get("layer") != "props":
            continue
        for row in extras.get("objects", ()):
            if row.get("prop_type") == "sign" and row.get("pole_base"):
                yield row


def _street_parts(tile):
    """(carriageway union, district name) near a tile, from the source version's Katualueet."""
    import shapely
    from turku3d.coarse.sources import Sources
    from turku3d.roads import signal_posts
    from turku3d.spatial import frame

    found, path = Sources().wfs_source("GIS:Katualueet", tile)
    if not found:
        return shapely.Polygon(), "no survey"
    f = frame(path, tile.bbox.buffered(WINDOW))
    if not len(f):
        return shapely.Polygon(), "no survey"
    road = signal_posts.carriageway(zip(f.geometry_local, f.Katuosalaji, strict=True))
    area = Counter()
    box = shapely.box(*tile.bbox.as_tuple())
    for g, name in zip(f.geometry_local, f.get("Hoitoalue", [None] * len(f)), strict=True):
        if g is not None and isinstance(name, str) and name:
            area[name] += shapely.make_valid(g).intersection(box).area
    return road, (area.most_common(1)[0][0] if area else "unknown")


def audit_tile(args):
    products, tid = args
    import shapely
    from turku3d import config as C
    from turku3d.props import signposts
    from turku3d.roads import signal_posts

    directory = Path(products) / tid
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except (OSError, ValueError):
        return {"tile": tid, "error": "no manifest"}
    tile = C.VisualTile.parse(tid)
    out = {"tile": tid}
    graph = None
    if manifest.get("roads"):
        graph = json.loads((directory / manifest["roads"]["url"]).read_text())
    heads = list(signal_posts.heads(graph)) if graph and graph.get("revision", 1) >= 2 else []
    entry = (manifest.get("visual") or {}).get(tid)
    rows = list(sign_rows(glb_json(directory / entry["url"]))) if entry else []
    if not heads and not rows:
        return out
    road, district = _street_parts(tile)
    out["district"] = district
    boundary = None if road.is_empty else road.boundary

    def on_road(x, z):
        return not road.is_empty and road.covers(shapely.Point(x, z))

    # Signals: the nominal head (or the drawn post when the tile has signals@1) and the rule's post.
    drawn = {(r[0], r[1], r[2]): r for r in (graph or {}).get("signalPosts", ())}
    posted, stats = signal_posts.place(graph, road) if heads else ([], Counter())
    before = [(h[3], h[4]) if (h[0], h[1], h[2]) not in drawn else tuple(drawn[h[:3]][3:5]) for h in heads]
    out["signals"] = len(heads)
    out["signals_on_road_before"] = sum(on_road(x, z) for x, z in before)
    residual = [(r[3], r[4]) for r in posted if on_road(r[3], r[4])]
    out["signals_on_road_after"] = len(residual)
    out["signal_rules"] = dict(stats)
    out["signal_residual"] = [[round(x, 1), round(z, 1)] for x, z in residual]

    # Signs: drawn rows before, the rule's posts after.
    signs = [signposts.Sign(r["object_id"], r.get("sign_code") or "", r["pole_base"][0], r["pole_base"][2]) for r in rows]
    out["sign_rows"] = len(signs)
    by_code = defaultdict(list)
    doubles = 0
    for s in sorted(signs, key=lambda s: s.object_id):
        if any(math.hypot(q.x - s.x, q.z - s.z) <= signposts.DUPLICATE for q in by_code[s.code]):
            doubles += 1
        by_code[s.code].append(s)
    out["sign_doubles_before"] = doubles

    def crowded(points):
        n = 0
        for i, (x, z) in enumerate(points):
            if any(math.hypot(x - u, z - v) <= signposts.POST for j, (u, v) in enumerate(points) if j != i):
                n += 1
        return n

    out["sign_crowded_before"] = crowded([(s.x, s.z) for s in signs])
    out["sign_on_road_before"] = sum(on_road(s.x, s.z) for s in signs)
    posts, sstats = signposts.posts(signs, road)
    out["sign_posts_after"] = len(posts)
    out["sign_doubles_after"] = 0
    out["sign_crowded_after"] = crowded([(p.x, p.z) for p in posts])
    deep = [p for p in posts if on_road(p.x, p.z)]
    out["sign_on_road_after"] = len(deep)
    out["sign_rules"] = dict(sstats)
    out["sign_residual"] = [[p.plates[0].code, round(p.x, 1), round(p.z, 1),
                             round(boundary.distance(shapely.Point(p.x, p.z)), 1)] for p in deep]
    out["stack_sizes"] = dict(Counter(len(p.plates) for p in posts))
    return out


KEYS = ("signals", "signals_on_road_before", "signals_on_road_after", "sign_rows", "sign_doubles_before",
        "sign_doubles_after", "sign_crowded_before", "sign_crowded_after", "sign_on_road_before",
        "sign_on_road_after", "sign_posts_after")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--products", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--tiles", nargs="*")
    a = ap.parse_args(argv)
    tiles = a.tiles or sorted(p.name for p in a.products.iterdir() if p.name.startswith("t0_"))
    done = set()
    if a.out.exists():
        done = {json.loads(line)["tile"] for line in a.out.read_text().splitlines() if line.strip()}
    todo = [t for t in tiles if t not in done]
    with a.out.open("a") as sink, Pool(a.jobs) as pool:
        for n, row in enumerate(pool.imap_unordered(audit_tile, [(str(a.products), t) for t in todo], chunksize=4)):
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
            if n % 200 == 0:
                print(f"{n}/{len(todo)}", file=sys.stderr, flush=True)
    if a.summary:
        rows = [json.loads(line) for line in a.out.read_text().splitlines() if line.strip()]
        rows = [r for r in rows if r["tile"] in set(tiles)]
        by = defaultdict(Counter)
        stacks = Counter()
        rules = Counter()
        for r in rows:
            for k in KEYS:
                by[r.get("district", "no signs")][k] += r.get(k, 0)
                by["ALL"][k] += r.get(k, 0)
            stacks.update({int(k): v for k, v in r.get("stack_sizes", {}).items()})
            rules.update(r.get("signal_rules", {}))
            rules.update(r.get("sign_rules", {}))
        a.summary.write_text(json.dumps({"tiles": len(rows), "errors": [r["tile"] for r in rows if "error" in r],
                                         "districts": {k: dict(v) for k, v in sorted(by.items())},
                                         "stack_sizes": dict(sorted(stacks.items())), "rules": dict(sorted(rules.items()))},
                                        indent=1) + "\n")


if __name__ == "__main__":
    os.nice(19)
    main()

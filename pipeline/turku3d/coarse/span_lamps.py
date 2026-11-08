"""Lamps on lighting span wires the survey saw without their lamp.

The wires stage (``coarse.wires``) hangs a lamp where span wires meet in mid air or where a compact
blob of returns sits just below a wire. A lamp head is small and dark in the survey, and a span wire
over a wide street often comes out as a piece over the median or a footway: on Kaskenkatu (t0_2_2)
two span pieces cross a 45 m stretch with no lamp, a stretch the 2026 street panoramas show lit by a
lamp over each carriageway on every span.

One class rule, with no per-street data: a ``span`` wire that carries no detected lamp (none listed
on it, no wire lamp or register lamp hung over the street within ``CARRIED`` of it) gets a lamp
wherever it crosses the centreline of a lit street (an OSM way of ``STREETS``, ``lit`` not ``no``),
reaching at most ``EXTEND`` past its surveyed ends; the drawn wire is extended to its lamps. A
crossing with a lamp within ``SEPARATION`` already is lit. The tile owns the wires whose middle it
holds (``coarse.wires``), so it owns every lamp this rule hangs, and neighbours agree.
"""

from __future__ import annotations

import json

import numpy as np
import shapely
from shapely.strtree import STRtree

# Ways carrying traffic or foot traffic that a span wire is strung across to light.
STREETS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
    "pedestrian",
}
CARRIED = 1.5  # m (plan) from a span wire within which a lamp hangs on it
EXTEND = 8.0  # m past a surveyed end that a span still reaches: the rest of a span over a wide street
SEPARATION = 4.0  # m (plan) from any lamp within which a crossing is lit already
HANG = 7.0  # m: a lighting span hangs at least this far over the street (the survey's lamps: 8.7-9.9 m)
SEARCH = 30.0  # m around the tile for ways


def streets(osm, bbox):
    """Centrelines of the lit street ways near ``bbox`` (BBoxLocal)."""
    from turku3d.spatial import rows

    out = []
    for _, row in rows(osm, bbox.buffered(SEARCH)):
        tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else dict(row["tags"] or {})
        if tags.get("highway") not in STREETS or tags.get("lit") == "no" or tags.get("area") == "yes":
            continue
        if tags.get("tunnel", "no") != "no" or tags.get("covered", "no") != "no":
            continue
        line = shapely.force_2d(row["geometry_local"])
        if line is None or line.is_empty or line.geom_type not in ("LineString", "MultiLineString"):
            continue
        out.append(line)
    return out


def infer(record: dict | None, ways, hung=(), ground=None) -> tuple[dict | None, dict]:
    """The ``lights.wires`` record with lamps on the span wires that lack them (None when unchanged),
    and counts. ``ways``: lit street centrelines; ``hung``: (x, z) of register lamps hung over a
    street (``lights`` records of category ``hung``); ``ground(x, z)``: the street height there (None
    when unknown), so a lower wire (a sign or a telecom line) gets no lamp."""
    stats = {"span": 0, "lampless": 0, "inferred": 0, "extended": 0, "low": 0}
    if not record or not record.get("wires"):
        return None, stats
    span = record["kinds"].index("span") if "span" in record.get("kinds", ()) else -1
    lamps = [list(lamp) for lamp in record.get("lamps") or []]
    wires = [list(w) for w in record["wires"]]
    on = {int(lamp[3]) for lamp in lamps}
    lit = [(lamp[0], lamp[2]) for lamp in lamps] + [tuple(h) for h in hung]
    way_tree = STRtree(list(ways)) if ways else None
    added = []
    for i, w in enumerate(wires):
        if int(w[7]) != span:
            continue
        stats["span"] += 1
        a, b, sag = np.asarray(w[0:3], float), np.asarray(w[3:6], float), float(w[6])
        chord = shapely.LineString([(a[0], a[2]), (b[0], b[2])])
        length = chord.length
        if length < 1e-6 or i in on or any(chord.distance(shapely.Point(p)) <= CARRIED for p in lit):
            continue
        stats["lampless"] += 1
        if way_tree is None:
            continue
        u = (b - a) / length  # per plan metre, height included
        reach = shapely.LineString([(a[0] - u[0] * EXTEND, a[2] - u[2] * EXTEND),
                                    (b[0] + u[0] * EXTEND, b[2] + u[2] * EXTEND)])
        found = []
        for j in way_tree.query(reach, predicate="intersects"):
            for p in shapely.get_parts(reach.intersection(ways[j])):
                if p.geom_type == "Point":
                    found.append(float(np.dot([p.x - a[0], p.y - a[2]], [u[0], u[2]])))
        hung_here = []
        for along in sorted(set(round(f, 2) for f in found)):
            t = along / length
            # Past a surveyed end the span keeps that end's height.
            q = a + u * along
            q[1] = a[1] if t < 0 else b[1] if t > 1 else q[1] - sag * 4 * t * (1 - t)
            if any(np.hypot(q[0] - x, q[2] - z) < SEPARATION for x, z in lit):
                continue
            below = ground(q[0], q[2]) if ground is not None else None
            if below is not None and q[1] - below < HANG:
                stats["low"] += 1
                continue
            lamp = [round(float(q[0]), 2), round(float(q[1]), 2), round(float(q[2]), 2), i]
            lit.append((lamp[0], lamp[2]))
            added.append(lamp)
            hung_here.append(along)
        if not hung_here:
            continue
        stats["inferred"] += len(hung_here)
        lo, hi = min(0.0, min(hung_here)), max(length, max(hung_here))
        if lo < 0 or hi > length:
            stats["extended"] += 1
            ends = (a + u * lo, a + u * hi)
            ends[0][1], ends[1][1] = (a[1] if lo < 0 else ends[0][1]), (b[1] if hi > length else ends[1][1])
            w[0:3] = [round(float(v), 2) for v in ends[0]]
            w[3:6] = [round(float(v), 2) for v in ends[1]]
    if not added:
        return None, stats
    return {**record, "wires": wires, "lamps": lamps + added}, stats

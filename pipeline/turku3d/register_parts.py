"""Recover building-owned projection/recess/canopy/step evidence from cached GML.

The legacy flattened parquet attributes name the last nested RakennuksenOsa,
not the part owning each geometry. Read original cached GML without modifying it.
These 2D projections establish location and type, never balcony count or height.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

import shapely
from shapely.geometry import mapping

from turku3d.fetch.wfs import GML_NS, _ln, _ns, _shape, feature_elements

KINDS = {"uloke": "projection", "sisennys": "recess", "katos": "canopy", "porras": "steps"}


def direct(element, name):
    return next((c.text.strip() for c in element if _ln(c.tag) == name and c.text), None)


def extract(body):
    """One stable owner/type/geometry fact per actual nested building part."""
    for feature in feature_elements(ET.fromstring(body)):
        owner = direct(feature, "rakennustunnus")
        if not owner:
            continue
        for part in feature.iter():
            if _ln(part.tag) != "RakennuksenOsa":
                continue
            kind = KINDS.get(direct(part, "tyyppi"))
            if not kind:
                continue
            for element in part.iter():
                if _ns(element.tag) != GML_NS or _ln(element.tag) not in ("LineString", "Polygon"):
                    continue
                geometry = _shape(element, 2)
                if geometry is None or geometry.is_empty:
                    continue
                geometry = shapely.normalize(shapely.force_2d(geometry))
                identity = hashlib.sha256(owner.encode() + kind.encode() + geometry.wkb).hexdigest()[:24]
                yield owner, dict(id=identity, kind=kind, geometry=mapping(geometry),
                                  register_code=direct(part, "kplkoodi"), height_m=None,
                                  source="municipal WFS nested RakennuksenOsa geometry")


def collect(paths):
    buildings, sources = {}, {}
    for path in sorted(set(map(Path, paths))):
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        sources[digest] = path.name
        for owner, part in extract(body):
            parts = buildings.setdefault(owner, {})
            if part["id"] not in parts:
                parts[part["id"]] = {**part, "source_sha256": digest}
    return dict(schema="registered-building-parts-1", crs="EPSG:3877",
                buildings={k: sorted(v.values(), key=lambda p: p["id"]) for k, v in sorted(buildings.items())},
                sources=sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gml", type=Path, help="cached GML directory; input stays read-only")
    parser.add_argument("--out", required=True, type=Path, help="derived index outside raw/ and git")
    args = parser.parse_args()
    if "raw" in args.out.resolve().parts:
        parser.error("derived output must not be written under raw/")
    index = collect(args.gml.glob("*.gml"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(index, separators=(",", ":")) + "\n")
    temporary.replace(args.out)
    kinds = Counter(p["kind"] for rows in index["buildings"].values() for p in rows)
    print(json.dumps(dict(buildings=len(index["buildings"]), parts=sum(kinds.values()),
                          by_kind=dict(kinds), source_pages=len(index["sources"]), output=str(args.out))))


if __name__ == "__main__":
    main()

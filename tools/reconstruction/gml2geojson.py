#!/usr/bin/env python3
"""Convert Turku TeklaOGCWeb WFS 1.0.0 GML (kanta:*, GIS:*) to GeoJSON (EPSG:3877 coords, no reprojection).

Usage: gml2geojson.py in.xml out.geojson
Each featureMember -> one Feature per geometry (LineString / Polygon / Point); simple child elements
(kplkoodi, tyyppi, Kasvilaji, ...) become properties. Geometry parent tag (e.g. 'reunaviiva', 'pinta')
is stored as property 'geom_role'.
"""
import json, sys, xml.etree.ElementTree as ET

GML = '{http://www.opengis.net/gml}'


def parse_coords(txt):
    pts = []
    for tok in txt.split():
        v = [float(a) for a in tok.split(',')]
        pts.append(v)
    return pts


def main(src, dst):
    root = ET.parse(src).getroot()
    feats = []
    for fm in root.iter(GML + 'featureMember'):
        for feat in fm:
            props = {'layer': feat.tag.split('}')[1]}
            for el in feat.iter():
                if len(el) == 0 and el.text and el.text.strip() and not el.tag.startswith(GML):
                    props[el.tag.split('}')[1]] = el.text.strip()
            parent = {c: p for p in feat.iter() for c in p}
            for g in feat.iter():
                tag = g.tag.replace(GML, '')
                if tag not in ('LineString', 'Polygon', 'Point'):
                    continue
                role = parent.get(g)
                role = role.tag.split('}')[1] if role is not None else ''
                if tag == 'Polygon':
                    rings = [parse_coords(c.text) for c in g.iter(GML + 'coordinates')]
                    geom = {'type': 'Polygon', 'coordinates': rings}
                else:
                    c = g.find('.//' + GML + 'coordinates')
                    pts = parse_coords(c.text)
                    geom = {'type': tag, 'coordinates': pts if tag == 'LineString' else pts[0]}
                feats.append({'type': 'Feature', 'properties': dict(props, geom_role=role), 'geometry': geom})
    json.dump({'type': 'FeatureCollection', 'features': feats}, open(dst, 'w'))
    print(src, '->', dst, len(feats), 'features')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])

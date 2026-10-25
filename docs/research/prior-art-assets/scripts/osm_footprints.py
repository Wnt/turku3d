# OSM buildings (closed ways + simple multipolygon outers) -> GeoJSON in EPSG:3877, clipped to bbox
import sys, json, xml.etree.ElementTree as ET
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping
src, dst = sys.argv[1], sys.argv[2]
xmin,ymin,xmax,ymax = map(float, sys.argv[3:7])
t = Transformer.from_crs(4326, 3877, always_xy=True)
root = ET.parse(src).getroot()
nodes = {n.get("id"): t.transform(float(n.get("lon")), float(n.get("lat"))) for n in root.iter("node")}
ways = {w.get("id"): w for w in root.iter("way")}
def tags(e): return {x.get("k"): x.get("v") for x in e.findall("tag")}
feats=[]; clip=box(xmin,ymin,xmax,ymax)
def add(coords, tg, oid):
    if len(coords)<4: return
    p=Polygon(coords)
    if not p.is_valid: p=p.buffer(0)
    if p.is_empty or not p.intersects(clip) or p.centroid.within(clip) is False: return
    feats.append({"type":"Feature","properties":{"osm_id":oid,"building":tg.get("building"),"levels":tg.get("building:levels"),"roof_shape":tg.get("roof:shape"),"height":tg.get("height")},"geometry":mapping(p)})
for wid,w in ways.items():
    tg=tags(w)
    if "building" in tg:
        refs=[nd.get("ref") for nd in w.findall("nd")]
        if refs[0]==refs[-1] and all(r in nodes for r in refs): add([nodes[r] for r in refs], tg, "w"+wid)
for r in root.iter("relation"):
    tg=tags(r)
    if "building" in tg and tg.get("type")=="multipolygon":
        for m in r.findall("member"):
            if m.get("role")=="outer" and m.get("ref") in ways:
                refs=[nd.get("ref") for nd in ways[m.get("ref")].findall("nd")]
                if refs and refs[0]==refs[-1] and all(x in nodes for x in refs): add([nodes[x] for x in refs], tg, "r"+r.get("id"))
json.dump({"type":"FeatureCollection","crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:EPSG::3877"}},"features":feats}, open(dst,"w"))
print(len(feats),"footprints")

import json, sys, numpy as np
from streetlevel import streetview
from pyproj import Transformer
t = Transformer.from_crs(3877, 4326, always_xy=True)
lon, lat = t.transform(23459500, 6704500)
print("centre lat/lon", lat, lon)
p0 = streetview.find_panorama(lat, lon, radius=80)
print("nearest", p0.id, p0.lat, p0.lon, p0.date)
pano = streetview.find_panorama_by_id(p0.id, download_depth=True)
print("id", pano.id, pano.lat, pano.lon, pano.date, "elev", pano.elevation, "heading", pano.heading, "pitch", pano.pitch, "roll", pano.roll)
print("source", pano.source, "gen", getattr(pano,'build_info',None))
print("image_sizes", pano.image_sizes)
d = pano.depth
print("depth", None if d is None else (d.width, d.height, np.nanmin(d.data), np.nanmax(d.data)))
print("historical", [(h.id, str(h.date)) for h in pano.historical])
print("links", len(pano.links), "neighbors", len(pano.neighbors))
print("address", pano.address)

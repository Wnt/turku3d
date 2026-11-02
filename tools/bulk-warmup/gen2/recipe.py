"""Acquisition identity, independent of renderer and geometry-only generator revisions."""
import hashlib
from pathlib import Path
EXTRA_WFS={'kanta:Rakennus','GIS:Rakennukset','kanta:Liikennevayla','kanta:Kevyenliikenteenvayla','kanta:Rakenne'}
FILES=('pipeline/turku3d/config.py','pipeline/turku3d/coarse/sources.py',
       'pipeline/turku3d/coarse/imagery.py','pipeline/turku3d/fetch/common.py',
       'pipeline/turku3d/fetch/laz.py','pipeline/turku3d/fetch/wfs.py',
       'pipeline/turku3d/fetch/wms.py','pipeline/turku3d/fetch/osm.py',
       'pipeline/turku3d/fetch/sources.py','data-manifests/laz-tiles.csv')
def acquisition_recipe(repo):
    h=hashlib.sha256(Path(__file__).read_bytes())
    for name in FILES:
        h.update(name.encode());h.update((Path(repo)/name).read_bytes())
    return h.hexdigest()

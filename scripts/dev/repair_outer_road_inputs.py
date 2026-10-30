"""Re-ingest empty centreline metadata only for new, unsuccessfully built outer blocks."""

import argparse
import shlex

from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tiles", nargs="+")
    args = parser.parse_args()
    script = '''import json
import pyarrow.parquet as pq
from turku3d import config as C
from turku3d.coarse.sources import Sources,block_of,block_area,block_tiles
from turku3d.fetch.sources import WFS_LAYERS
from turku3d.fetch.wfs import fetch_layer
layer=next(w for w in WFS_LAYERS if w.name=='GIS:Keskilinjat')
for tile_id in TILES:
 tile=C.VisualTile.parse(tile_id); block=block_of(tile)
 root=C.work_root()/'coarse'
 found,path=Sources().wfs_source(layer.name,tile)
 if not path or pq.read_metadata(path).num_rows:
  print(json.dumps(dict(tile=tile_id,repair='not needed')));continue
 expected=C.raw_dir('wfs')/block_area(block).name/(layer.slug+'.parquet')
 if path!=expected:raise RuntimeError('Refusing to change a shared area: '+str(path))
 if any((root/t.id/'manifest.json').exists() for t in block_tiles(block)):
  raise RuntimeError('Refusing to change inputs of an already warmed block: '+block.name)
 summary=fetch_layer(layer,block_area(block),force_parquet=True)
 assert summary['status']=='empty' and summary['rows']==0
 print(json.dumps(dict(tile=tile_id,repair='empty centreline metadata',block=block.name)))
'''.replace("TILES", repr(args.tiles))
    print(remote("~/turku3d-drafts/run.sh python -c " + shlex.quote(script)))


if __name__ == "__main__":
    main()

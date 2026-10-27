"""Capture-only service: real producer and HTTP handlers, existing raw inputs only."""
import os
from pathlib import Path
from turku3d import config as C
from turku3d.coarse.service import Builder, serve
from turku3d.coarse.sources import Sources

root = Path(os.environ['TURKU3D_WORK']) / 'coarse'
root.mkdir(exist_ok=True)
sources = Sources()
builder = Builder(root, workers=2,
                  inputs=lambda tile: [u.key for u in sources.missing(C.VisualTile.parse(tile))],
                  input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)))
server, _, _ = serve(int(os.environ['TURKU3D_PORT_BASE']) + 5, root, builder=builder)
try:
    server.serve_forever()
finally:
    builder.stop()
    server.server_close()

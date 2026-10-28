"""Read-only cached-input draft service for manual host-slot latency diagnostics."""

import argparse
import os
import signal
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.service import Builder, serve
from turku3d.coarse.sources import Sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=C.work_root() / "reports/draft-speed/drive")
    parser.add_argument("--port", type=int, default=int(os.environ["TURKU3D_PORT_BASE"]) + 5)
    args = parser.parse_args()
    sources = Sources()
    builder = Builder(
        args.root,
        inputs=lambda tile: [u.key for u in sources.missing(C.VisualTile.parse(tile))],
        input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
    )
    server, _, _ = serve(args.port, args.root, builder=builder)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"cache-only drafts: http://127.0.0.1:{args.port}/coarse/status", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        builder.stop()
        server.server_close()


if __name__ == "__main__":
    main()

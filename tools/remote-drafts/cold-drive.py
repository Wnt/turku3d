"""Exercise the real public draft service with empty, isolated generated caches.

Run through host_slots.py on OCI. Raw inputs must already be provisioned: this
diagnostic never fetches or modifies raw data. The production service stays up.
"""

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True, help="an owned sandbox probe port")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--speed-kmh", type=float, default=120)
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=False)
    os.environ["TURKU3D_WORK"] = str(args.root)

    from public import public_handler
    from turku3d import config as C
    from turku3d.coarse.service import Builder, Server
    from turku3d.coarse.sources import Sources

    settings = json.loads(Path(__file__).with_name("config.json").read_text())
    root = args.root / "coarse"
    root.mkdir()
    sources = Sources()
    output = args.root / "ten-drivers.json"

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    with (args.root / "service.log").open("w") as log, contextlib.redirect_stdout(log):
        builder = Builder(
            root,
            workers=settings["workers"],
            inputs=lambda tile: [
                unit.key
                for unit in sources.missing_for_service(C.VisualTile.parse(tile), first_pass=True)
            ],
            input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
            structural_input_key=lambda tile: sources.input_key(
                C.VisualTile.parse(tile), first_pass=True
            ),
            enrichment_inputs=lambda tile: [
                unit.key for unit in sources.missing_for_service(C.VisualTile.parse(tile))
            ],
        )
        server = Server(("127.0.0.1", args.port), public_handler(builder, None, settings))
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("ten-drivers.py")),
                    "--base",
                    f"http://127.0.0.1:{args.port}/coarse/",
                    "--root",
                    str(root),
                    "--output",
                    str(output),
                    "--seconds",
                    str(args.seconds),
                    "--speed-kmh",
                    str(args.speed_kmh),
                ],
                check=True,
                timeout=550,
                stdout=log,
                stderr=log,
            )
        finally:
            server.shutdown()
            builder.stop()
            server.server_close()
            serving.join()
    report = json.loads(output.read_text())
    report["cache_state"] = "empty generated tile, input and shared area caches; existing raw data"
    report["service"] = "isolated real public handler and four persistent build workers"
    report["raw_fetch"] = False
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()

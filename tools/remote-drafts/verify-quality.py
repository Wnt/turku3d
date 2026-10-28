"""Bounded live check: fresh manifest requests must retain textured draft payloads."""

import argparse
import hashlib
import json
import struct
import time
import urllib.request
from pathlib import Path


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200, (url, response.status)
        return response.read()


def check(base, tile, seen):
    manifest = json.loads(fetch(f"{base}/{tile}/manifest.json"))
    visual = manifest["visual"][tile]
    key = tile, visual["sha256"]
    if key not in seen:
        data = fetch(f"{base}/{tile}/{visual['url']}")
        assert hashlib.sha256(data).hexdigest() == visual["sha256"], tile
        assert data[:4] == b"glTF", tile
        length, kind = struct.unpack_from("<II", data, 12)
        assert kind == 0x4E4F534A, tile
        document = json.loads(data[20 : 20 + length])
        images = document.get("images", [])
        assert images and document.get("textures"), f"{tile}: texture downgrade"
        for image in images:
            uri = image.get("uri")
            if uri and not uri.startswith("data:"):
                fetch(f"{base}/{tile}/{uri}")
        seen[key] = len(images)
    return dict(tile=tile, sha256=key[1], images=seen[key], stale=manifest.get("stale", False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://turku.lab.madekivi.fi/coarse")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--tiles", nargs="+", default=["t0_0_0", "t0_1_0", "t0_-1_1"])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    started, seen, samples = time.monotonic(), {}, []
    while True:
        rows = [check(args.base, tile, seen) for tile in args.tiles]
        sample = dict(seconds=round(time.monotonic() - started, 3), tiles=rows)
        samples.append(sample)
        print(json.dumps(sample), flush=True)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(samples, indent=2) + "\n")
        if time.monotonic() - started >= args.seconds:
            assert not any(row["stale"] for row in rows), "full replacements still pending"
            return
        time.sleep(2)


if __name__ == "__main__":
    main()

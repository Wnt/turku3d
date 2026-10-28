"""Measure request-to-servable draft latency with the Pages Origin and fetch every payload."""

import argparse
import hashlib
import json
import time
from urllib.request import Request, urlopen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tile")
    ap.add_argument("--base", default="https://turku.lab.madekivi.fi/coarse/")
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()
    started = time.monotonic()

    def get(path):
        request = Request(args.base.rstrip("/") + "/" + path)
        request.add_header("Origin", "https://wnt.github.io")
        with urlopen(request, timeout=30) as reply:
            assert reply.headers["Access-Control-Allow-Origin"] == "https://wnt.github.io"
            return reply.status, reply.read()

    while time.monotonic() - started < args.timeout:
        status, body = get(f"{args.tile}/manifest.json")
        if status == 200:
            break
        assert status == 202, (status, body)
        time.sleep(0.2)
    else:
        raise TimeoutError("Draft did not become servable")
    manifest_seconds = time.monotonic() - started
    manifest = json.loads(body)
    entries = [*manifest["visual"].values(), *manifest["collision"].values()]
    assert len(manifest["collision"]) == 16
    files = [f"{args.tile}/{entry['url']}" for entry in entries]
    for path, entry in zip(files, entries, strict=True):
        status, payload = get(path)
        assert status == 200 and payload, path
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], path
        assert len(payload) == entry["bytes"], path
    print(
        json.dumps(
            {
                "tile": args.tile,
                "manifest_seconds": round(manifest_seconds, 3),
                "payload_seconds": round(time.monotonic() - started, 3),
                "payloads": len(files),
                "origin": "https://wnt.github.io",
            }
        )
    )


if __name__ == "__main__":
    main()

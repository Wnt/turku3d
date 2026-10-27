"""Headless drive against a running coarse service: the game hook's requests without a browser.

    python -m turku3d.coarse.drive --port P --start X,Z --velocity VX,VZ --seconds S [--log FILE]

Every second, like ``game/src/world/coarseTiles.ts``: ``/coarse/want`` with the position and
velocity, then the manifest polls of the 3x3 window and of the cells within the next 30 s of travel
plus a one-tile ring (``aheadTiles``), each due every 2 s and at once when the build counter moves.
It records when each tile entered the 3x3 window ("ring") and when its build was seen finished, and
prints the ring-to-built latency of the tiles entered after the first kilometre, the raw bytes the
service's prefetcher fetched per kilometre, and its per-dataset counts.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request

L0 = 250.0
AHEAD_SECONDS = 30.0  # coarseTiles.ts PREFETCH_SECONDS
UNBUILDABLE = ("outside the city lidar index", "NoCoverage")


def window(x: float, z: float, published: set[str]) -> list[str]:
    ci, cj = math.floor(x / L0), math.floor(z / L0)
    cells = (f"t0_{i}_{j}" for i in range(ci - 1, ci + 2) for j in range(cj - 1, cj + 2))
    return [t for t in cells if t not in published]


def ahead(x: float, z: float, vx: float, vz: float, published: set[str]) -> set[str]:
    speed = math.hypot(vx, vz)
    if speed < 1:
        return set()
    reach = speed * AHEAD_SECONDS
    steps = math.ceil(reach / (L0 / 2))
    out = set()
    for k in range(steps + 1):
        px, pz = x + vx / speed * reach * k / steps, z + vz / speed * reach * k / steps
        out |= set(window(px, pz, published))
    return out


def distance(tile: str, x: float, z: float) -> float:
    i, j = map(int, tile.split("_")[1:])
    return math.hypot(max(i * L0 - x, 0, x - (i + 1) * L0), max(j * L0 - z, 0, z - (j + 1) * L0))


def published_tiles() -> set[str]:
    """L0 tiles of the live release, which the game never asks drafts for."""
    from turku3d import config as C

    path = C.data_root() / "live" / "tiles" / "manifest.json"
    if not path.is_file():
        return set()
    return {t for t in json.loads(path.read_text()).get("visual", {}) if t.startswith("t0_")}


def summary(tiles: dict, speed: float, metres: float, prefetch: dict) -> dict:
    """Latency of the buildable tiles that entered the ring after the first kilometre."""
    late = [
        e
        for e in tiles.values()
        if e["ring"] is not None
        and e["ring"] * speed >= 1000
        and not any(u in str(e.get("why")) for u in UNBUILDABLE)
    ]
    waits = sorted(max(0.0, e["built"] - e["ring"]) for e in late if e["built"] is not None)
    fetched = sum(d["bytes"] for d in prefetch.get("datasets", {}).values())
    return {
        "tiles": len(late),
        "built": len(waits),
        "under_10_s": sum(w < 10 for w in waits),
        "median_s": round(waits[len(waits) // 2], 1) if waits else None,
        "max_s": round(waits[-1], 1) if waits else None,
        "mb_per_km": round(fetched / 1e6 / (metres / 1000), 1) if metres else None,
        "units": {k: d["units"] for k, d in prefetch.get("datasets", {}).items()},
    }


def drive(port: int, x0: float, z0: float, vx: float, vz: float, seconds: float, log=None) -> dict:
    base = f"http://127.0.0.1:{port}/coarse/"

    def get(path: str):
        try:
            with urllib.request.urlopen(base + path, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    published = published_tiles()
    tiles: dict[str, dict] = {}
    start, version = time.time(), None
    while (now := time.time() - start) < seconds:
        x, z = x0 + vx * now, z0 + vz * now
        _, w = get(f"want?x={x:.1f}&z={z:.1f}&vx={vx:.1f}&vz={vz:.1f}")
        moved = version is not None and w.get("version") != version
        version = w.get("version")
        near = window(x, z, published)
        far = sorted(ahead(x, z, vx, vz, published) - set(near), key=lambda t: distance(t, x, z))
        for tile in near + far:
            e = tiles.setdefault(
                tile, {"ring": None, "built": None, "next": 0.0, "state": "pending"}
            )
            if tile in near and e["ring"] is None:
                e["ring"] = now
            if e["built"] is not None or not (
                now >= e["next"] or (moved and e["state"] == "pending")
            ):
                continue
            status, body = get(f"{tile}/manifest.json?d={distance(tile, x, z):.0f}")
            t = time.time() - start
            if status == 200:
                e["built"], e["state"] = t, "built"
            elif status == 202:
                e["state"], e["next"], e["why"] = "pending", t + 2.0, body.get("state")
            else:
                e["state"], e["next"], e["why"] = "failed", t + 120.0, body.get("error")
        if log:
            log.write(json.dumps({"t": round(now, 2), "x": round(x), "z": round(z), **w}) + "\n")
        time.sleep(0.1 if moved else max(0.0, 1.0 - (time.time() - start - now)))
    if log:
        for tile, e in tiles.items():
            log.write(json.dumps({"tile": tile, **e}) + "\n")
    prefetch = get("status")[1].get("prefetch", {})
    return summary(tiles, math.hypot(vx, vz), math.hypot(vx, vz) * seconds, prefetch)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m turku3d.coarse.drive",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--start", default="125,125", help="engine x,z (m)")
    ap.add_argument(
        "--velocity", default="27,0", help="engine vx,vz (m/s); 27 is the car's top speed"
    )
    ap.add_argument("--seconds", type=float, default=200.0)
    ap.add_argument("--log", type=argparse.FileType("w"))
    a = ap.parse_args(argv)
    (x, z), (vx, vz) = (map(float, a.start.split(",")), map(float, a.velocity.split(",")))
    print(json.dumps(drive(a.port, x, z, vx, vz, a.seconds, a.log)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

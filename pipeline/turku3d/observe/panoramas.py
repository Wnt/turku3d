"""Street panoramas around a place, from both providers, with the pose needed to project them onto walls.

Discovery and download are turkutex.streetview.panos (Street View and Look Around, cached in the shared panorama
cache, never in Git). This module adds what wall projection needs: the camera position in N2000, the grid heading of
the image centre and the panorama's world-up direction (qalib.upright: vertical lines, else its pitch and roll).
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from turku3d import config

REPO = Path(__file__).resolve().parents[3]
for extra in (REPO / "qa", REPO / "textures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

COVERAGE_MAX_AGE_S = 30 * 86400  # coverage tiles older than this are refetched, so newer imagery shows up
GOOGLE_ZOOM = 4  # 8192 x 4096: 23 px per degree
LOOKAROUND_ZOOM = 2


@dataclass
class Pano:
    meta: object  # turkutex PanoMeta
    position: np.ndarray  # camera (E, N, H)
    heading: float  # grid bearing of the equirect's centre column
    height_source: str
    width: int = 0  # pixels of the equirect as it will be fetched (0: the default zooms)

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def source(self) -> str:
        return self.meta.source

    @property
    def date(self) -> str:
        return self.meta.date or ""

    def to_dict(self) -> dict:
        return {"provider": self.source, "id": self.id, "date": self.meta.date,
                "position": [round(float(v), 2) for v in self.position], "height_source": self.height_source}


class Panoramas:
    # Buildings are prepared in several threads, each with its own Panoramas; a panorama two of them want is
    # downloaded and measured once.
    _locks: dict = {}
    _guard = threading.Lock()

    def _lock(self, pano: Pano):
        with Panoramas._guard:
            return Panoramas._locks.setdefault((pano.source, pano.id), threading.RLock())

    def __init__(self, cache_dir: Path | None = None, google_zoom: int = GOOGLE_ZOOM):
        """`google_zoom`: 4 for reading a facade (23 px a degree), 3 where colour is all that is asked (a quarter
        of the bytes)."""
        from turkutex.streetview.panos import PanoCache

        self.cache = PanoCache()
        self.google_zoom = google_zoom
        self.own = Path(cache_dir) if cache_dir else config.work_root() / "observe/panoramas"
        self.own.mkdir(parents=True, exist_ok=True)
        self._tiles: dict = {}
        self._pixels: dict = {}
        self.timings = {"coverage_s": 0.0, "meta_s": 0.0, "fetch_s": 0.0, "upright_s": 0.0}

    def _fresh(self, kind: str, tile) -> bool:
        path = self.cache.root / "coverage" / f"{kind}_z17_{tile[0]}_{tile[1]}.json"
        return path.exists() and time.time() - path.stat().st_mtime < COVERAGE_MAX_AGE_S

    def spots(self, bounds, margin: float = 0.0) -> list[dict]:
        """Every panorama spot in the bounds: Look Around panoramas and Street View coverage points (whose newest
        time layer is resolved by `resolve`). [{provider, id, E, N, date, meta?}]"""
        from turkutex.streetview.panos import z17_tile

        e0, n0, e1, n1 = bounds[0] - margin, bounds[1] - margin, bounds[2] + margin, bounds[3] + margin
        tiles = {}
        for e in np.arange(e0, e1 + 60, 60):
            for n in np.arange(n0, n1 + 60, 60):
                lat, lon = config.en_to_wgs84(min(e, e1), min(n, n1))
                tiles.setdefault(z17_tile(lat, lon), (lat, lon))
        out = {}
        started = time.time()
        for tile, (lat, lon) in tiles.items():
            if tile not in self._tiles:
                found = []
                try:
                    for m in self.cache.lookaround_coverage(lat, lon, refresh=not self._fresh("lookaround", tile)):
                        found.append({"provider": "lookaround", "id": m.id, "E": m.E, "N": m.N, "date": m.date,
                                      "meta": m})
                except Exception as error:  # noqa: BLE001 - one provider's outage must not lose the other
                    print(f"observe: Look Around coverage failed for tile {tile}: {error}", file=sys.stderr)
                try:
                    for p in self.cache.google_coverage(lat, lon, refresh=not self._fresh("google", tile)):
                        if len(p["id"]) != 22:
                            continue  # user photospheres: indoor, rooftops, unreliable poses
                        e, n = config.wgs84_to_en(p["lat"], p["lon"])
                        found.append({"provider": "google", "id": p["id"], "E": float(e), "N": float(n),
                                      "date": p["date"]})
                except Exception as error:  # noqa: BLE001
                    print(f"observe: Street View coverage failed for tile {tile}: {error}", file=sys.stderr)
                self._tiles[tile] = found
            for s in self._tiles[tile]:
                if e0 <= s["E"] <= e1 and n0 <= s["N"] <= n1:
                    out[(s["provider"], s["id"])] = s
        self.timings["coverage_s"] += time.time() - started
        return list(out.values())

    def resolve(self, spot: dict, base_h: float | None = None) -> Pano | None:
        """The newest panorama at a spot, with its camera position and heading."""
        from qalib import geo
        from turkutex.streetview.height import ground_height
        from turkutex.streetview.panos import camera_height

        started = time.time()
        try:
            m = spot.get("meta") or self.cache.google_newest(spot["id"])
        except Exception as error:  # noqa: BLE001 - a panorama that cannot be resolved is skipped
            print(f"observe: metadata of {spot['id']} failed: {error}", file=sys.stderr)
            m = None
        self.timings["meta_s"] += time.time() - started
        if m is None:
            return None
        if m.source == "lookaround":
            heading = geo.true_to_grid_bearing(m.E, m.N, -math.degrees(m.heading_rad) + 180)
            ground = ground_height(m.E, m.N)
            if ground is not None:
                h, why = ground + camera_height(m.source, m.date), "lidar ground + assumed camera height"
            elif base_h is not None:
                h, why = base_h + camera_height(m.source, m.date), "wall base + assumed camera height"
            else:
                h, why = m.elevation, "provider elevation"
        else:
            heading = geo.true_to_grid_bearing(m.E, m.N, math.degrees(m.heading_rad))
            if m.elevation is None:
                if base_h is None:
                    return None
                h, why = base_h + camera_height(m.source, m.date), "wall base + assumed camera height"
            else:
                h, why = m.elevation, "provider elevation"
        return Pano(m, np.array([m.E, m.N, float(h)]), float(heading), why,
                    0 if m.source == "lookaround" or self.google_zoom == GOOGLE_ZOOM else 8192 >> (GOOGLE_ZOOM - self.google_zoom))

    def layers(self, spot: dict, base_h: float | None = None) -> list[Pano]:
        """Every time layer at a spot, each with its own pose: a Street View spot has one per visit of the camera
        car (other years, other weather); a Look Around panorama is its own only layer."""
        if spot["provider"] != "google":
            pano = self.resolve(spot, base_h)
            return [pano] if pano else []
        try:
            first = self.cache.google_meta(spot["id"])
        except Exception as error:  # noqa: BLE001
            print(f"observe: metadata of {spot['id']} failed: {error}", file=sys.stderr)
            first = None
        if first is None:
            return []
        out = []
        for pid in dict.fromkeys([first.id] + [h["id"] for h in first.extra.get("historical", [])]):
            try:
                meta = first if pid == first.id else self.cache.google_meta(pid)
            except Exception as error:  # noqa: BLE001 - one layer that cannot be resolved is skipped
                print(f"observe: metadata of {pid} failed: {error}", file=sys.stderr)
                continue
            pano = self.resolve({"provider": "google", "id": pid, "meta": meta}, base_h) if meta else None
            if pano is not None:
                out.append(pano)
        return out

    def image(self, pano: Pano) -> Path:
        started = time.time()
        with self._lock(pano):
            if pano.source == "lookaround":
                path = self.cache.lookaround_equirect(pano.meta, zoom=LOOKAROUND_ZOOM)
            else:
                path = self.cache.google_image(pano.id, zoom=self.google_zoom)
        self.timings["fetch_s"] += time.time() - started
        return Path(path)

    def pixels(self, pano: Pano) -> np.ndarray:
        """The decoded equirect; the two most recent stay in memory."""
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        key = (pano.source, pano.id)
        if key not in self._pixels:
            with Image.open(self.image(pano)) as im:
                self._pixels[key] = np.asarray(im.convert("RGB"))
            for old in list(self._pixels)[:-2]:
                del self._pixels[old]
        return self._pixels[key]

    def upright(self, pano: Pano) -> dict:
        """World up in the panorama's frame (measured once per panorama and kept beside our crops)."""
        from qalib import upright

        path = self.own / f"{pano.source}-{pano.id}.upright.json"
        with self._lock(pano):
            if path.exists():
                return json.loads(path.read_text())
            started = time.time()
            meta_up = upright.metadata_up(pano.source, pano.heading, pano.meta.pitch_rad, pano.meta.roll_rad)
            result = upright.estimate_up(self.pixels(pano), pano.heading, meta_up)
            path.write_text(json.dumps(result))
            self.timings["upright_s"] += time.time() - started
            return result

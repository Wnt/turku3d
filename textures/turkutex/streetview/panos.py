"""Discover, download and cache Google Street View and Apple Look Around panoramas (streetlevel 0.12.13).

Cache layout (default $TURKU3D_DATA/cache/streetlevel; $TURKUTEX_STREETVIEW overrides):
  coverage/google_z17_<x>_<y>.json        coverage-tile pano list (id, lat, lon, date)
  coverage/lookaround_z17_<x>_<y>.json    Look Around coverage tile (incl. camera metadata)
  google/<id>/meta.json                   metadata incl. historical layers, EN position, has_depth
  google/<id>/depth.npy                   raw streetlevel depth (256x512, metres along ray, -1 sky);
                                          NOT flipped on disk, load_depth() applies np.fliplr
  google/<id>/pano_z<zoom>.jpg            equirect (zoom 5 = 16384x8192, 4 = 8192x4096)
  lookaround/<id>/face<k>_z<zoom>.heic    raw faces; equi_z<zoom>.jpg side-face equirect (no top/bottom)

Research gotchas applied (docs/research/streetview.md, Verification):
  - find_panorama() cannot return depth; use find_panorama_by_id(id, download_depth=True), retry
    without depth when the decoder raises (about 7 % of panos).
  - coverage-tile ids are not always the newest layer: newest_layer() picks max(date) over historical.
  - Google `elevation` is orthometric camera height (N2000-like); ground = elevation - camera height
    of the capture campaign (CAMERA_HEIGHT_BY_YEAR, n = 168 panos).
"""

from __future__ import annotations

import json
import math
import os
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from turku3d import config
from turku3d.region.cache import atomic_target, synchronized

from ..paths import streetview_dir

Image.MAX_IMAGE_PIXELS = None

# Google camera height above lidar ground per capture campaign (median, Verification #6 / D6).
CAMERA_HEIGHT_BY_YEAR = {2026: 2.31, 2024: 2.86, 2022: 2.85, 2019: 1.49}
CAMERA_HEIGHT_DEFAULT = 2.36  # all-campaign median
# Look Around: elevation is "height above MSL" from streetlevel; camera height on the car is not
# calibrated against lidar yet [UNVERIFIED]; 2.4 m is assumed for the ground estimate only.
LOOKAROUND_CAMERA_HEIGHT = 2.4


def camera_height(source: str, date: str | None) -> float:
    if source == "lookaround":
        return LOOKAROUND_CAMERA_HEIGHT
    try:
        y = int(str(date)[:4])
    except (TypeError, ValueError):
        return CAMERA_HEIGHT_DEFAULT
    return CAMERA_HEIGHT_BY_YEAR.get(y, CAMERA_HEIGHT_DEFAULT)


def z17_tile(lat: float, lon: float) -> tuple[int, int]:
    n = 2**17
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _date_str(d) -> str | None:
    if d is None:
        return None
    if hasattr(d, "year") and hasattr(d, "month"):
        day = getattr(d, "day", None)
        return f"{d.year:04d}-{d.month:02d}" + (
            f"-{day:02d}" if isinstance(day, int) and hasattr(d, "hour") else ""
        )
    return str(d)


@dataclass
class PanoMeta:
    source: str  # google | lookaround
    id: str
    lat: float
    lon: float
    E: float
    N: float
    elevation: float | None
    heading_rad: float  # see camera.EquirectModel for how each source maps to columns
    pitch_rad: float
    roll_rad: float
    date: str | None
    extra: dict

    @property
    def cam_height(self) -> float:
        return camera_height(self.source, self.date)

    @property
    def ground_h(self) -> float | None:
        return None if self.elevation is None else self.elevation - self.cam_height

    def to_dict(self):
        return {
            "source": self.source,
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "E": self.E,
            "N": self.N,
            "elevation": self.elevation,
            "heading_rad": self.heading_rad,
            "pitch_rad": self.pitch_rad,
            "roll_rad": self.roll_rad,
            "date": self.date,
            "cam_height": self.cam_height,
            "ground_h": self.ground_h,
            **self.extra,
        }

    @staticmethod
    def from_dict(d: dict) -> PanoMeta:
        known = {
            "source",
            "id",
            "lat",
            "lon",
            "E",
            "N",
            "elevation",
            "heading_rad",
            "pitch_rad",
            "roll_rad",
            "date",
            "cam_height",
            "ground_h",
        }
        return PanoMeta(
            d["source"],
            d["id"],
            d["lat"],
            d["lon"],
            d["E"],
            d["N"],
            d.get("elevation"),
            d["heading_rad"],
            d.get("pitch_rad", 0.0),
            d.get("roll_rad", 0.0),
            d.get("date"),
            {k: v for k, v in d.items() if k not in known},
        )


def atomic_json(path: Path, value):
    with atomic_target(path) as temporary:
        temporary.write_text(json.dumps(value, ensure_ascii=False))


class PanoCache:
    def __init__(self, root: Path | None = None, sleep: float = 0.2):
        self.root = Path(root) if root else streetview_dir()
        self.read_roots = [config.cache_dir("streetview"), config.raw_dir("streetview")]
        self.read_roots += [
            Path(p).expanduser()
            for p in os.environ.get("TURKUTEX_READ_CACHE", "").split(os.pathsep)
            if p
        ]
        self.sleep = sleep
        (self.root / "coverage").mkdir(parents=True, exist_ok=True)
        self._auth = None

    def read_path(self, relative: str | Path) -> Path:
        """Read through shared caches; all new data is written under self.root."""
        own = self.root / relative
        if own.exists():
            return own
        return next((r / relative for r in self.read_roots if (r / relative).is_file()), own)

    def read_json(self, relative: str | Path):
        for root in [self.root, *self.read_roots]:
            path = root / relative
            if path.is_file():
                try:
                    return json.loads(path.read_text())
                except json.JSONDecodeError:
                    warnings.warn(
                        f"Ignoring invalid reference cache: {path}", RuntimeWarning, stacklevel=2
                    )
        return None

    # ------------------------------ Google ------------------------------
    @synchronized
    def google_coverage(self, lat: float, lon: float) -> list[dict]:
        from .sl import streetview

        x, y = z17_tile(lat, lon)
        f = self.root / "coverage" / f"google_z17_{x}_{y}.json"
        cached = self.read_json(f.relative_to(self.root))
        if cached is not None:
            return cached
        panos = streetview.get_coverage_tile(x, y)
        res = [{"id": p.id, "lat": p.lat, "lon": p.lon, "date": _date_str(p.date)} for p in panos]
        atomic_json(f, res)
        time.sleep(self.sleep)
        return res

    @synchronized
    def photosphere_coverage(self, lat: float, lon: float) -> list[PanoMeta]:
        """Same third-party discovery RPC as tools/refs/gmaps_photospheres.py, cached by grid cell."""
        from .sl import streetview

        # 50 m grid, not one nearest result for an entire coverage tile.
        E, N = config.wgs84_to_en(lat, lon)
        gx, gy = round(E / 50), round(N / 50)
        path = self.root / "coverage" / f"photospheres_{gx}_{gy}.json"
        ids = self.read_json(path.relative_to(self.root))
        if ids is None:
            qlat, qlon = config.en_to_wgs84(gx * 50, gy * 50)
            p = streetview.find_panorama(qlat, qlon, radius=60, search_third_party=True)
            ids = [p.id] if p and streetview.is_third_party_panoid(p.id) else []
            atomic_json(path, ids)
        return [m for pid in ids if (m := self.google_meta(pid)) is not None]

    @synchronized
    def google_meta(self, pid: str) -> PanoMeta | None:
        d = self.root / "google" / pid
        f = d / "meta.json"
        cached = self.read_json(f.relative_to(self.root))
        if cached is not None:
            return PanoMeta.from_dict(cached)
        from .sl import streetview

        d.mkdir(parents=True, exist_ok=True)
        pano, depth_err = None, None
        try:
            pano = streetview.find_panorama_by_id(pid, download_depth=True)
        except Exception as e:  # noqa: BLE001 - depth decoder errors (empty bit string, index)
            depth_err = f"{type(e).__name__}: {e}"
            pano = streetview.find_panorama_by_id(pid, download_depth=False)
        time.sleep(self.sleep)
        if pano is None:
            return None
        has_depth = (
            getattr(pano, "depth", None) is not None
            and getattr(pano.depth, "data", None) is not None
        )
        if has_depth:
            with atomic_target(d / "depth.npy") as temp:
                np.save(temp, np.asarray(pano.depth.data, dtype=np.float32))
        E, N = config.wgs84_to_en(pano.lat, pano.lon)
        hist = [{"id": h.id, "date": _date_str(h.date)} for h in (pano.historical or [])]
        m = PanoMeta(
            "google",
            pano.id,
            pano.lat,
            pano.lon,
            float(E),
            float(N),
            None if pano.elevation is None else float(pano.elevation),
            float(pano.heading or 0.0),
            float(pano.pitch or 0.0),
            float(pano.roll or 0.0),
            _date_str(pano.date),
            {
                "historical": hist,
                "has_depth": has_depth,
                "depth_error": depth_err,
                "depth_orientation": "raw streetlevel; apply np.fliplr to match the image",
                "image_sizes": [[s.x, s.y] for s in (pano.image_sizes or [])],
                "address": [str(a.value) for a in (pano.address or [])] if pano.address else None,
                "source_kind": str(pano.source) if pano.source else None,
                "copyright": pano.copyright_message,
            },
        )
        atomic_json(f, m.to_dict())
        return m

    @synchronized
    def google_newest(self, pid: str) -> PanoMeta | None:
        """Newest time layer at the spot of `pid` (Verification #5)."""
        m = self.google_meta(pid)
        if m is None:
            return None
        cands = [(m.date or "", m.id)] + [
            (h["date"] or "", h["id"]) for h in m.extra.get("historical", [])
        ]
        date, nid = max(cands)
        return m if nid == m.id else (self.google_meta(nid) or m)

    def load_depth(self, pid: str) -> np.ndarray | None:
        f = self.read_path(Path("google") / pid / "depth.npy")
        return np.fliplr(np.load(f)) if f.exists() else None

    @synchronized
    def google_image(self, pid: str, zoom: int = 5) -> Path:
        f = self.root / "google" / pid / f"pano_z{zoom}.jpg"
        cached = self.read_path(f.relative_to(self.root))
        if cached.exists():
            return cached
        from .sl import streetview

        pano = streetview.find_panorama_by_id(pid, download_depth=False)
        img = streetview.get_panorama(pano, zoom=zoom)
        f.parent.mkdir(parents=True, exist_ok=True)
        with atomic_target(f) as temp:
            img.save(temp, quality=93)
        time.sleep(self.sleep)
        return f

    @synchronized
    def mapillary_coverage(self, lat, lon):
        from . import mapillary

        return mapillary.coverage(self, lat, lon)

    @synchronized
    def mapillary_image(self, meta):
        from . import mapillary

        return mapillary.image(self, meta)

    # ------------------------------ Look Around ------------------------------
    @synchronized
    def lookaround_coverage(self, lat: float, lon: float) -> list[PanoMeta]:
        from .sl import lookaround

        x, y = z17_tile(lat, lon)
        f = self.root / "coverage" / f"lookaround_z17_{x}_{y}.json"
        cached = self.read_json(f.relative_to(self.root))
        if cached is not None:
            return [PanoMeta.from_dict(d) for d in cached]
        tile = lookaround.get_coverage_tile_by_latlon(lat, lon)
        res = []
        for p in tile.panos:
            E, N = config.wgs84_to_en(p.lat, p.lon)
            cams = [
                {
                    "fov_s": c.lens_projection.fov_s,
                    "fov_h": c.lens_projection.fov_h,
                    "cx": c.lens_projection.cx,
                    "cy": c.lens_projection.cy,
                    "yaw": c.position.yaw,
                    "pitch": c.position.pitch,
                    "roll": c.position.roll,
                }
                for c in (p.camera_metadata or [])
            ]
            res.append(
                PanoMeta(
                    "lookaround",
                    str(p.id),
                    p.lat,
                    p.lon,
                    float(E),
                    float(N),
                    float(p.elevation),
                    float(p.heading),
                    float(p.pitch),
                    float(p.roll),
                    _date_str(p.date),
                    {
                        "build_id": p.build_id,
                        "coverage_type": str(p.coverage_type),
                        "cameras": cams,
                        "heading_convention": "streetlevel: 0 = north, 90 deg = west (CCW)",
                    },
                )
            )
        atomic_json(f, [m.to_dict() for m in res])
        time.sleep(self.sleep)
        return res

    @synchronized
    def lookaround_faces(self, m: PanoMeta, zoom: int = 1) -> list[Path]:
        from .sl import lookaround

        d = self.root / "lookaround" / m.id
        d.mkdir(parents=True, exist_ok=True)
        atomic_json(d / "meta.json", m.to_dict())
        out = []
        for k in range(4):  # side faces only; top/bottom are not needed for facades
            f = d / f"face{k}_z{zoom}.heic"
            cached = self.read_path(f.relative_to(self.root))
            if not cached.exists():
                if self._auth is None:
                    self._auth = lookaround.Authenticator()
                with atomic_target(f) as temp:
                    temp.write_bytes(
                        lookaround.get_panorama_face(
                            (int(m.id), int(m.extra["build_id"])), k, zoom, self._auth
                        )
                    )
                time.sleep(self.sleep)
            out.append(cached)
        return out

    @synchronized
    def lookaround_equirect(self, m: PanoMeta, zoom: int = 1) -> Path:
        """Side faces pasted into an equirect, same layout as streetlevel.lookaround.reproject
        (no torch needed: side faces are already equirect segments). Top/bottom stay black."""
        f = self.root / "lookaround" / m.id / f"equi_z{zoom}.jpg"
        cached = self.read_path(f.relative_to(self.root))
        if cached.exists():
            return cached
        from pillow_heif import register_heif_opener

        register_heif_opener()
        faces = [Image.open(p).convert("RGB") for p in self.lookaround_faces(m, zoom)]
        cams = m.extra["cameras"]
        full_w = round(faces[0].width * (1024 / 5632)) * 16
        full_h = full_w // 2
        st = Image.new("RGB", (full_w, full_h))
        for k in range(3, -1, -1):
            c = cams[k]
            phi0 = math.pi + c["yaw"] - c["fov_s"] / 2
            if phi0 < 0:
                phi0 += 2 * math.pi
            th0 = math.pi / 2 - c["fov_h"] / 2 - c["cy"]
            fw, fh = c["fov_s"] * full_h / math.pi, c["fov_h"] * full_h / math.pi
            x, y = phi0 * full_h / math.pi, th0 * full_h / math.pi
            sc = faces[k].resize((math.ceil(fw), math.ceil(fh)), Image.LANCZOS)
            st.paste(sc, (math.ceil(x), math.ceil(y)))
            if x + sc.width > full_w:
                st.paste(sc, (math.ceil(x - full_w), math.ceil(y)))
        with atomic_target(f) as temp:
            st.save(temp, quality=93)
        return f

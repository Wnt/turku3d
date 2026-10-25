"""Mapillary Graph v4 reference discovery. The token is only sent as an OAuth header.

Metadata: https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/
Perspective/fisheye images require calibrated intrinsics and a computed camera rotation.
Unsupported/missing calibration stays in metadata, never treated as a panorama.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

from turku3d import config

from .panos import PanoMeta

API = "https://graph.mapillary.com"
FIELDS = ("id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,camera_type,"
          "camera_parameters,computed_rotation,computed_altitude,exif_orientation,width,height,thumb_original_url")


def request(path: str, params: dict, token_file: Path | None = None) -> dict:
    token_file = token_file or Path.home() / ".config/turku3d/mapillary.token"
    token = token_file.read_text().strip()
    req = urllib.request.Request(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"OAuth {token}"})
    # Do not log response bodies, headers or paging URLs (they can contain access tokens).
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def coverage(cache, lat: float, lon: float) -> list[PanoMeta]:
    from .panos import z17_tile
    x, y = z17_tile(lat, lon)
    path = cache.root / "coverage" / f"mapillary_v2_z17_{x}_{y}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        def ll(tx, ty):
            return tx / 2**17 * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / 2**17))))
        west, north = ll(x, y)
        east, south = ll(x + 1, y + 1)
        params = {"bbox": f"{west},{south},{east},{north}", "fields": FIELDS, "limit": 2000}
        rows = []
        while True:
            page = request("images", params)
            rows.extend(page.get("data", []))
            after = page.get("paging", {}).get("cursors", {}).get("after")
            if not page.get("paging", {}).get("next") or not after:
                break
            params["after"] = after
        path.write_text(json.dumps(rows))
    out = []
    for row in rows:
        kind = row.get("camera_type")
        if kind not in ("spherical", "perspective", "fisheye"):
            continue
        if kind != "spherical" and (len(row.get("camera_parameters") or []) != 3
                or len(row.get("computed_rotation") or []) != 3 or row.get("exif_orientation", 1) != 1):
            continue
        if kind != "spherical" and (row["camera_parameters"][0] <= 0 or not all(
                math.isfinite(v) for v in row["camera_parameters"] + row["computed_rotation"])):
            continue
        loc = row.get("computed_geometry") or row.get("geometry")
        heading = row.get("computed_compass_angle")
        if heading is None:
            heading = row.get("compass_angle")
        if not loc or heading is None:
            continue
        lon, lat = loc["coordinates"][:2]
        E, N = config.wgs84_to_en(lat, lon)
        date = dt.datetime.fromtimestamp(row["captured_at"] / 1000, dt.timezone.utc).date().isoformat()
        out.append(PanoMeta("mapillary", row["id"], lat, lon, E, N, None,
                            math.radians(config.true_to_grid_bearing(E, N, heading)), 0, 0, date,
                            {"image_sizes": [[row.get("width", 2048), row.get("height", 1024)]],
                             "image_url": row.get("thumb_original_url"),
                             "camera_type": kind, "camera_parameters": row.get("camera_parameters"),
                             "computed_rotation": row.get("computed_rotation"),
                             "computed_altitude": row.get("computed_altitude"),
                             "pose_confidence": "computed rotation/intrinsics; approximate camera height from local ground"}))
    return out


def image(cache, meta: PanoMeta) -> Path:
    path = cache.root / "mapillary" / meta.id / "pano.jpg"
    if not path.exists():
        # Refresh expiring image URLs only on a cache miss.
        row = request(meta.id, {"fields": "thumb_original_url"})
        req = urllib.request.Request(row["thumb_original_url"])
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return path

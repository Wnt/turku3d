"""Pure camera conversion; the consumer Maps URL altitude datum needs explicit calibration.

Unlike the Maps API, the consumer URL is not a documented camera contract. Do not
silently equate N2000, ellipsoidal height and height above ground. A HeightReference
states the hypothesis used by a comparison; the JSON record must retain it.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from urllib.parse import unquote, urlsplit

from turku3d.config import en_to_wgs84, grid_bearing_of_true_north, wgs84_to_en


@dataclass(frozen=True)
class Pose:
    E: float
    N: float
    H: float
    yaw: float
    pitch: float
    fov: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("camera values must be finite")
        if not -90 <= self.pitch <= 0 or not 0 < self.fov < 180:
            raise ValueError("Google aerial comparisons require pitch -90..0 and fov 0..180")

    @classmethod
    def parse(cls, text: str) -> Pose:
        values = text.split(",")
        if len(values) != 6:
            raise ValueError("--cam requires E,N,H,yaw,pitch,fov (no roll)")
        return cls(*(float(v) for v in values))

    def cam(self) -> str:
        return ",".join(f"{v:.9f}" for v in asdict(self).values())


@dataclass(frozen=True)
class HeightReference:
    datum: str
    offset_m: float

    def __post_init__(self):
        if self.datum not in ("ellipsoid", "ground", "n2000"):
            raise ValueError("altitude datum must be ellipsoid, ground or n2000")
        if not math.isfinite(self.offset_m):
            raise ValueError("height offset must be finite")
        if self.datum == "n2000" and self.offset_m != 0:
            raise ValueError("N2000 datum requires zero offset")

    def google_altitude(self, height: float) -> float:
        # Ellipsoid h = H_N2000 + geoid undulation; AGL = H_N2000 - ground_H.
        return height + self.offset_m if self.datum == "ellipsoid" else height - self.offset_m

    def n2000(self, altitude: float) -> float:
        return altitude - self.offset_m if self.datum == "ellipsoid" else altitude + self.offset_m


@dataclass(frozen=True)
class GooglePose:
    lat: float
    lon: float
    altitude: float
    fov: float
    heading: float
    tilt: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Google pose values must be finite")
        if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
            raise ValueError("invalid latitude/longitude")
        if self.altitude <= 0 or not 0 < self.fov < 180 or not 0 <= self.tilt <= 90:
            raise ValueError("invalid aerial altitude, fov or tilt")

    def url(self) -> str:
        return (
            f"https://www.google.com/maps/@{self.lat:.9f},{self.lon:.9f},"
            f"{self.altitude:.6f}a,{self.fov:.6f}y,{self.heading % 360:.6f}h,"
            f"{self.tilt:.6f}t/data=!3m1!1e3?hl=en"
        )

    @classmethod
    def parse(cls, url: str) -> GooglePose:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ("www.google.com", "maps.google.com"):
            raise ValueError("expected an https Google Maps URL")
        number = r"(-?\d+(?:\.\d+)?)"
        match = re.search(
            "@"
            + ",".join(number + suffix for suffix in ("", "", "a", "y", "h", "t"))
            + r"/data=!3m1!1e3(?:/|$)",
            unquote(parts.path),
        )
        if not match:
            raise ValueError("expected aerial @lat,lon,alt a,fov y,heading h,tilt t/data=!3m1!1e3")
        return cls(*(float(v) for v in match.groups()))


def to_google(pose: Pose, heights: HeightReference) -> GooglePose:
    lat, lon = en_to_wgs84(pose.E, pose.N)
    return GooglePose(
        lat,
        lon,
        heights.google_altitude(pose.H),
        pose.fov,
        (pose.yaw - grid_bearing_of_true_north(pose.E, pose.N)) % 360,
        pose.pitch + 90,
    )


def from_google(pose: GooglePose, heights: HeightReference) -> Pose:
    e, n = wgs84_to_en(pose.lat, pose.lon)
    return Pose(
        e,
        n,
        heights.n2000(pose.altitude),
        (pose.heading + grid_bearing_of_true_north(e, n)) % 360,
        pose.tilt - 90,
        pose.fov,
    )

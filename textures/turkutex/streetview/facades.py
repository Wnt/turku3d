"""Facade line segments (EPSG:3877 + N2000) and their extraction from kantakartta footprints.

Facade GeoJSON (input of the reference tool), FeatureCollection of LineStrings with 2 points:
  geometry.coordinates: [[E0, N0], [E1, N1]]       EPSG:3877 metres
  properties:
    id          str, unique ([A-Za-z0-9_.-])
    height_m    float, wall height above base (eaves), required
    base_h      float, N2000 height of the facade foot (optional; else estimated from the pano
                elevation minus the campaign camera height)
    normal      "right" (default) | "left": outward side relative to p0->p1. For a CCW footprint
                ring (E right, N up) the outward normal is on the right.
    building_id, address, floors, material, street: free metadata
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Facade:
    id: str
    p0: np.ndarray  # (E, N)
    p1: np.ndarray
    height_m: float
    base_h: float | None = None
    props: dict = field(default_factory=dict)
    normal_side: str = "right"

    @property
    def centre(self) -> np.ndarray:
        return (self.p0 + self.p1) / 2

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.p1 - self.p0))

    @property
    def tangent(self) -> np.ndarray:
        d = self.p1 - self.p0
        return d / np.linalg.norm(d)

    @property
    def normal(self) -> np.ndarray:
        t = self.tangent
        n = np.array([t[1], -t[0]])  # right of p0->p1
        return n if self.normal_side == "right" else -n

    @property
    def normal_azimuth_deg(self) -> float:
        """Compass direction the facade faces (0 = N, 90 = E)."""
        n = self.normal
        return math.degrees(math.atan2(n[0], n[1])) % 360

    def corners(self, base_h: float) -> np.ndarray:
        """4x3 (E, N, H): bottom-left, bottom-right, top-right, top-left as seen from outside.

        Seen from outside (looking along -normal) the viewer's right is +tangent for a right-hand
        normal, so p0 is on the left.
        """
        a, b = (self.p0, self.p1) if self.normal_side == "right" else (self.p1, self.p0)
        h0, h1 = base_h, base_h + self.height_m
        return np.array([[*a, h0], [*b, h0], [*b, h1], [*a, h1]], dtype=float)

    def to_feature(self) -> dict:
        props = {
            "id": self.id,
            "height_m": round(self.height_m, 2),
            "normal": self.normal_side,
            **self.props,
        }
        if self.base_h is not None:
            props["base_h"] = round(self.base_h, 3)
        return {
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(float(v), 3) for v in self.p0],
                    [round(float(v), 3) for v in self.p1],
                ],
            },
        }


def load_facades(path: Path) -> list[Facade]:
    d = json.loads(Path(path).read_text())
    out = []
    for f in d["features"]:
        pr = dict(f["properties"])
        if f["geometry"]["type"] != "LineString":
            raise ValueError("facade geometry must be a LineString in EPSG:3877")
        c = f["geometry"]["coordinates"]
        if len(c) != 2:
            raise ValueError(f"facade {pr.get('id')}: expected 2 points, got {len(c)}")
        fid = pr.pop("id")
        if fid in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", fid):
            raise ValueError(f"bad facade id {fid!r}")
        facade = Facade(
            fid,
            np.array(c[0][:2], float),
            np.array(c[1][:2], float),
            float(pr.pop("height_m")),
            pr.pop("base_h", None),
            pr,
            pr.pop("normal", "right"),
        )
        if (
            not np.isfinite(np.r_[facade.p0, facade.p1, facade.height_m]).all()
            or min(facade.length, facade.height_m) <= 0
            or facade.normal_side not in ("left", "right")
        ):
            raise ValueError(f"{fid}: invalid facade geometry")
        out.append(facade)
    return out


def save_facades(facades: list[Facade], path: Path, crs: str = "EPSG:3877"):
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs}},
        "features": [f.to_feature() for f in facades],
    }
    Path(path).write_text(json.dumps(fc, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------
# Extraction from Turku kantakartta WFS (kanta:Rakennus 66000 footprints, GIS:Keskilinjat streets)
# ---------------------------------------------------------------------------------------------
NS = {
    "gml": "http://www.opengis.net/gml",
    "kanta": "http://www.kuntatietopalvelu.fi/gml/kantakartta",
    "yht": "http://www.kuntatietopalvelu.fi/gml/yhteiset",
}


def _coords(text: str) -> np.ndarray:
    return np.array([[float(v) for v in c.split(",")[:2]] for c in text.split()])


def read_footprints(gml: Path) -> list[dict]:
    """kanta:Rakennus kplkoodi 66000 polygons -> [{id, ring (CCW, closed removed), floors, address, ...}]."""
    root = ET.parse(gml).getroot()
    res = []
    for f in root.iter(f"{{{NS['kanta']}}}Rakennus"):
        if f.findtext("kanta:kplkoodi", namespaces=NS) != "66000":
            continue
        poly = f.find(".//gml:Polygon", NS)
        if poly is None:
            continue
        ring = _coords(poly.find(".//gml:outerBoundaryIs//gml:coordinates", NS).text)
        if np.allclose(ring[0], ring[-1], rtol=0, atol=1e-6):
            ring = ring[:-1]
        area2 = np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])
        if area2 < 0:
            ring = ring[::-1]
        name = f.findtext(".//yht:osoitenimi/yht:teksti", namespaces=NS)
        no = f.findtext(".//yht:osoitenumero", namespaces=NS)
        fl = f.findtext("kanta:kerrosluku", namespaces=NS)
        res.append(
            {
                "id": f.findtext("kanta:rakennustunnus", namespaces=NS) or f"b{len(res)}",
                "ring": ring,
                "floors": int(fl) if fl and fl.isdigit() else None,
                "address": f"{name} {no}" if name else None,
                "material": f.findtext("kanta:julkisivumateriaali", namespaces=NS),
                "year": (f.findtext("kanta:kottovuosi", namespaces=NS) or "")[:4] or None,
            }
        )
    return res


def read_streets(gml: Path) -> list[dict]:
    """GIS:Keskilinjat -> [{name, kind, line (Nx2)}]."""
    root = ET.parse(gml).getroot()
    res = []
    for f in root.iter():
        if not f.tag.endswith("}Keskilinjat"):
            continue
        name = next((e.text for e in f.iter() if e.tag.endswith("}Kadunnimi")), None)
        kind = next((e.text for e in f.iter() if e.tag.endswith("}Katuosalaji")), None)
        co = next((e.text for e in f.iter() if e.tag.endswith("}coordinates")), None)
        if co:
            res.append({"name": name, "kind": kind, "line": _coords(co)})
    return res


def point_in_ring(p, ring) -> bool:
    x, y = p
    c = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _ray_seg(o, d, a, b):
    """Distance t >= 0 along ray o + t d to segment ab, or None."""
    e = b - a
    den = d[0] * (-e[1]) - d[1] * (-e[0])
    if abs(den) < 1e-12:
        return None
    w = a - o
    t = (w[0] * (-e[1]) - w[1] * (-e[0])) / den
    s = (d[0] * w[1] - d[1] * w[0]) / den
    return t if t >= 0 and 0 <= s <= 1 else None


def merge_edges(
    ring: np.ndarray, max_turn_deg: float = 12.0, max_dev: float = 0.6
) -> list[tuple[int, int]]:
    """Group consecutive ring edges into straight runs; returns (start_idx, end_idx) vertex pairs.

    A run continues while each edge turns < max_turn_deg from the run's chord and every vertex stays
    within max_dev metres of it. Starts at the sharpest corner so runs do not wrap mid-facade.
    """
    n = len(ring)

    def turn(i):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        u, v = b - a, c - b
        return abs(math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u @ v)))

    start = max(range(n), key=turn)
    order = [(start + k) % n for k in range(n + 1)]
    runs, s = [], 0
    k = 1
    while k <= n:
        a = ring[order[s]]
        b = ring[order[k]]
        ch = b - a
        L = np.linalg.norm(ch)
        ok = True
        if k - s > 1 and L > 0:
            t = ch / L
            for m in range(s + 1, k):
                p = ring[order[m]] - a
                if abs(p[0] * t[1] - p[1] * t[0]) > max_dev:
                    ok = False
                    break
            e = ring[order[k]] - ring[order[k - 1]]
            if ok and np.linalg.norm(e) > 0.3:
                ang = abs(math.degrees(math.atan2(t[0] * e[1] - t[1] * e[0], t @ e)))
                ok = ang < max_turn_deg
        if ok:
            k += 1
        else:
            runs.append((order[s], order[k - 1]))
            s = k - 1
    runs.append((order[s], order[n]))
    return runs


def street_facades(
    footprints: list[dict],
    streets: list[dict],
    min_len: float = 6.0,
    max_street_dist: float = 30.0,
    storey_h: float = 3.2,
    plinth_h: float = 0.8,
    only_ids: set[str] | None = None,
) -> list[Facade]:
    """Footprint runs that face a street: a ray along the outward normal from the run centre must hit a
    street centreline within max_street_dist before it hits any other footprint."""
    out = []
    for b in footprints:
        if only_ids and b["id"] not in only_ids:
            continue
        ring = b["ring"]
        for i0, i1 in merge_edges(ring):
            p0, p1 = ring[i0], ring[i1]
            L = float(np.linalg.norm(p1 - p0))
            if L < min_len:
                continue
            f = Facade("", p0.copy(), p1.copy(), 0.0)
            o = f.centre + f.normal * 0.05
            d = f.normal
            # other buildings only: the run chord may cut through its own small jogs (max_dev)
            hit_b = min(
                (
                    t
                    for ob in footprints
                    if ob is not b
                    for k in range(len(ob["ring"]))
                    if (t := _ray_seg(o, d, ob["ring"][k - 1], ob["ring"][k])) is not None
                ),
                default=1e9,
            )
            best = None
            for s in streets:
                ln = s["line"]
                for k in range(1, len(ln)):
                    t = _ray_seg(o, d, ln[k - 1], ln[k])
                    if (
                        t is not None
                        and t < max_street_dist
                        and t < hit_b
                        and (best is None or t < best[0])
                    ):
                        best = (t, s["name"])
            if not best:
                continue
            floors = b["floors"] or 1
            fid = f"{b['id']}_{int(round(f.normal_azimuth_deg)):03d}"
            n_same = sum(1 for g in out if g.id.startswith(fid))
            fid = fid if n_same == 0 else f"{fid}_{n_same}"
            f.id = fid
            f.height_m = round(plinth_h + floors * storey_h, 2)
            f.props = {
                "building_id": b["id"],
                "address": b["address"],
                "floors": b["floors"],
                "material": b["material"],
                "year": b["year"],
                "street": best[1],
                "street_dist_m": round(best[0], 1),
                "length_m": round(L, 2),
                "height_source": f"floors*{storey_h}+{plinth_h}",
            }
            out.append(f)
    return out


WFS_URL = "https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx"


def fetch_wfs(layer: str, bbox_en: tuple[float, float, float, float], out: Path) -> Path:
    """GML for a kantakartta/GIS layer in an EPSG:3877 bbox (WFS 1.0.0 BBOX filter, as in
    tools/research/wfs_probe.py). Cached: an existing file is reused."""
    import urllib.parse
    import urllib.request

    out = Path(out)
    if out.exists() and out.stat().st_size > 0:
        return out
    x0, y0, x1, y1 = bbox_en
    flt = (
        f'<Filter><BBOX><PropertyName>Geometry</PropertyName><gml:Box xmlns:gml="http://www.opengis.net/gml">'
        f"<gml:coordinates>{x0},{y0} {x1},{y1}</gml:coordinates></gml:Box></BBOX></Filter>"
    )
    q = dict(service="WFS", version="1.0.0", request="GetFeature", typeName=layer, filter=flt)
    req = urllib.request.Request(
        WFS_URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "turku3d/0.1"}
    )
    body = urllib.request.urlopen(req, timeout=300).read()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return out


def main(argv=None) -> int:
    import argparse

    from turku3d import config

    ap = argparse.ArgumentParser(
        prog="turkutex facades",
        description="Street-facing facade lines from kantakartta footprints",
    )
    ap.add_argument("--near", nargs=2, type=float, metavar=("E", "N"), required=True)
    ap.add_argument("--radius", type=float, default=60.0, help="bbox half-size for the WFS query")
    ap.add_argument("--buildings", nargs="*", help="only these rakennustunnus ids")
    ap.add_argument("--containing", action="store_true", help="only the building containing --near")
    ap.add_argument("--min-len", type=float, default=6.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    E, N = a.near
    bb = (E - a.radius, N - a.radius, E + a.radius, N + a.radius)
    tag = f"{int(bb[0])}_{int(bb[1])}_{int(bb[2])}_{int(bb[3])}"
    wfs = config.cache_dir("wfs") / "facades"
    fp = read_footprints(fetch_wfs("kanta:Rakennus", bb, wfs / f"kanta_Rakennus_{tag}.gml"))
    st = read_streets(fetch_wfs("GIS:Keskilinjat", bb, wfs / f"GIS_Keskilinjat_{tag}.gml"))
    ids = set(a.buildings) if a.buildings else None
    if a.containing:
        ids = {b["id"] for b in fp if point_in_ring((E, N), b["ring"])}
    fs = street_facades(fp, st, min_len=a.min_len, only_ids=ids)
    save_facades(fs, a.out)
    for f in fs:
        print(
            f.id,
            f.props.get("address"),
            f.props.get("street"),
            f"len {f.length:.1f} m",
            f"faces {f.normal_azimuth_deg:.0f} deg",
            f"h {f.height_m} m",
        )
    return 0

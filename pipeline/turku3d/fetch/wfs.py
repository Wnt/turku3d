"""Turku TeklaOGCWeb WFS (GML only) -> raw/wfs/<area>/gml/<layer>/<bbox>.gml pages + <layer>.parquet.

Paging: the server returns at most WFS_FEATURE_CAP (1000) features per request and WFS 1.0.0 has no
startIndex, so a bbox whose response holds >= cap features is split into 4 quadrants, recursively.
Features that cross page borders come back in several pages; they are de-duplicated by gml:id
(CityGML) or by a hash of the feature's XML (Tekla GML2, which has no ids).

Note: the BBOX filter selects every feature that *intersects* the box, so geometries extend beyond it.

GeoParquet output, one row per geometry element (a feature with several geometries -> several rows):

* ``geometry``        EPSG:3877 (+ N2000 Z when the GML has it)
* ``geometry_local``  engine coordinates as (x, z[, y]) = (E-E0, -(N-N0)[, H]); no CRS. The z axis is
                      flipped relative to N, so polygon ring orientation is reversed relative to ``geometry``.
* ``layer``, ``feature_key`` (dedupe key), ``gml_id``, ``geom_index``, ``geom_type``, ``geom_role``
  (nearest non-GML ancestor tag, e.g. ``reunaviiva``, ``lod2MultiSurface``), ``surface_type`` (CityGML
  ``RoofSurface``/``WallSurface``/..., else empty), ``part_id`` (CityGML BuildingPart id), and every simple
  attribute of the feature as a string column (CityGML generic attributes by their ``name``).
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import config
from .common import Registry, atomic_write_bytes, atomic_write_text, http_get, kind_dir, sha256_bytes
from .sources import WFS_FEATURE_CAP, WFS_LAYERS, WFS_URL, WfsLayer

GML_NS = "http://www.opengis.net/gml"
APP_NS = "http://www.opengis.net/citygml/appearance/2.0"
MIN_PAGE_M = 5.0
GEOM_TAGS = {"Point", "LineString", "Polygon", "Triangle", "LinearRing"}
LEAF_GEOMS = {"Point", "LineString", "Polygon", "Triangle"}


class WfsUnauthorized(Exception):
    pass


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


# --------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------


def request_params(layer: WfsLayer, bbox: tuple[float, float, float, float]) -> dict:
    x0, y0, x1, y1 = bbox
    if layer.version == "1.1.0":
        return dict(service="WFS", version="1.1.0", request="GetFeature", typeName=layer.name,
                    bbox=f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}")
    flt = ('<Filter><BBOX><PropertyName>Geometry</PropertyName><gml:Box xmlns:gml="http://www.opengis.net/gml">'
           f"<gml:coordinates>{x0:.3f},{y0:.3f} {x1:.3f},{y1:.3f}</gml:coordinates></gml:Box></BBOX></Filter>")
    return dict(service="WFS", version="1.0.0", request="GetFeature", typeName=layer.name, filter=flt)


def page_key(bbox) -> str:
    return "_".join(f"{v:.3f}" for v in bbox)


def split4(bbox):
    x0, y0, x1, y1 = bbox
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [(x0, ym, xm, y1), (xm, ym, x1, y1), (x0, y0, xm, ym), (xm, y0, x1, ym)]


def feature_elements(root: ET.Element) -> list[ET.Element]:
    out = []
    for m in root:
        t = _ln(m.tag)
        if t in ("featureMember", "cityObjectMember", "featureMembers"):
            out.extend(list(m))
    return out


def count_features(body: bytes) -> int:
    return body.count(b"<gml:featureMember") + body.count(b"<core:cityObjectMember")


def check_body(body: bytes, layer: str) -> None:
    head = body[:2000]
    if b"ExceptionReport" in head or b"ServiceException" in head:
        raise RuntimeError(f"WFS exception for {layer}: {body[:500]!r}")
    if b"FeatureCollection" not in head:
        raise RuntimeError(f"WFS: not a FeatureCollection for {layer}: {body[:300]!r}")
    # A truncated body (connection dropped, no Content-Length) still starts like a FeatureCollection;
    # parse it before it is cached, otherwise the broken page is "complete" forever and every re-run fails.
    try:
        ET.fromstring(body)
    except ET.ParseError as e:
        raise RuntimeError(f"WFS: malformed/truncated GML for {layer} ({len(body)} bytes): {e}") from e


def fetch_pages(layer: WfsLayer, bbox, pages_dir: Path, reg: Registry, log=print,
                get=http_get) -> list[Path]:
    """Quadtree-paged fetch. Returns leaf page paths (each < cap features, or at MIN_PAGE_M)."""
    leaves: list[Path] = []
    stack = [tuple(bbox)]
    while stack:
        bb = stack.pop()
        p = pages_dir / f"{page_key(bb)}.gml"
        if reg.is_complete(p):
            body = p.read_bytes()
        else:
            try:
                body, _ = get(WFS_URL, request_params(layer, bb))
            except Exception as e:  # urllib HTTPError has .code
                if getattr(e, "code", None) == 401:
                    raise WfsUnauthorized(layer.name) from e
                raise
            check_body(body, layer.name)
            atomic_write_bytes(p, body)
            reg.record(p, url=WFS_URL, layer=layer.name, bbox=list(bb), features=count_features(body))
        n = count_features(body)
        if n >= WFS_FEATURE_CAP and (bb[2] - bb[0]) > MIN_PAGE_M:
            stack.extend(split4(bb))
        else:
            if n >= WFS_FEATURE_CAP:
                log(f"wfs  {layer.name}: WARNING page {page_key(bb)} still at cap ({n}) at min size")
            leaves.append(p)
    return sorted(leaves)


# --------------------------------------------------------------------------------------
# GML parsing
# --------------------------------------------------------------------------------------


def _parse_coord_text(txt: str) -> list[tuple[float, ...]]:
    """GML2 <gml:coordinates>: 'x,y[,z] x,y[,z] ...'."""
    return [tuple(float(a) for a in tok.split(",")) for tok in txt.split()]


def _parse_poslist(txt: str, dim: int) -> list[tuple[float, ...]]:
    v = [float(a) for a in txt.split()]
    return [tuple(v[i:i + dim]) for i in range(0, len(v) - dim + 1, dim)]


def _coords(el: ET.Element, default_dim: int = 3) -> list[tuple[float, ...]]:
    c = el.find(f"{{{GML_NS}}}coordinates")
    if c is not None and c.text:
        return _parse_coord_text(c.text)
    pl = el.find(f"{{{GML_NS}}}posList")
    if pl is not None and pl.text:
        dim = int(pl.get("srsDimension") or pl.get("dimension") or default_dim)
        return _parse_poslist(pl.text, dim)
    out = []
    for pos in el.findall(f"{{{GML_NS}}}pos"):
        dim = int(pos.get("srsDimension") or default_dim)
        out.extend(_parse_poslist(pos.text or "", dim) or [])
    return out


def _rings(poly: ET.Element, default_dim: int) -> tuple[list, list[list]]:
    ext, holes = [], []
    for child in poly:
        t = _ln(child.tag)
        ring = child.find(f"{{{GML_NS}}}LinearRing")
        if ring is None:
            continue
        if t in ("outerBoundaryIs", "exterior"):
            ext = _coords(ring, default_dim)
        elif t in ("innerBoundaryIs", "interior"):
            holes.append(_coords(ring, default_dim))
    return ext, holes


def _shape(el: ET.Element, default_dim: int):
    from shapely import geometry as sg

    t = _ln(el.tag)
    if t == "Point":
        c = _coords(el, default_dim)
        return sg.Point(c[0]) if c else None
    if t == "LineString":
        c = _coords(el, default_dim)
        return sg.LineString(c) if len(c) >= 2 else None
    if t in ("Polygon", "Triangle"):
        ext, holes = _rings(el, default_dim)
        if len(ext) < 3:
            return None
        return sg.Polygon(ext, [h for h in holes if len(h) >= 3])
    return None


def _attrs(feat: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in feat.iter():
        if el is feat:
            continue
        ns = _ns(el.tag)
        if ns in (GML_NS, APP_NS):
            continue
        ln = _ln(el.tag)
        if ln.endswith("Attribute") and el.get("name"):
            v = next((c.text for c in el if _ln(c.tag) == "value"), None)
            if v is not None and v.strip():
                out[el.get("name")] = v.strip()
            continue
        if ln == "value":
            continue
        if len(el) == 0 and el.text and el.text.strip():
            out[ln] = el.text.strip()
    return out


def parse_features(body: bytes, layer: str, default_dim: int | None = None):
    """Yield (feature_key, gml_id, attrs, [geom rows]) for each feature in a GML page."""
    root = ET.fromstring(body)
    if default_dim is None:
        default_dim = 3 if b"citygml" in body[:3000] else 2
    for feat in feature_elements(root):
        gid = feat.get(f"{{{GML_NS}}}id") or feat.get("fid") or ""
        key = gid or hashlib.sha1(ET.tostring(feat)).hexdigest()
        parent = {c: p for p in feat.iter() for c in p}
        rows = []
        for el in feat.iter():
            if _ns(el.tag) != GML_NS or _ln(el.tag) not in LEAF_GEOMS:
                continue
            # skip geometries nested in another leaf geometry (does not happen in practice; defensive)
            p, nested, role, surface, part = parent.get(el), False, "", "", ""
            while p is not None and p is not feat:
                pln, pns = _ln(p.tag), _ns(p.tag)
                if pns == GML_NS and pln in LEAF_GEOMS:
                    nested = True
                if pns != GML_NS:
                    if not role:
                        role = pln
                    if not surface and pln.endswith("Surface") and not pln.startswith("lod"):
                        surface = pln
                    if not part and pln.endswith("Part"):
                        part = p.get(f"{{{GML_NS}}}id") or ""
                p = parent.get(p)
            if nested:
                continue
            g = _shape(el, default_dim)
            if g is None or g.is_empty:
                continue
            rows.append({"geometry": g, "geom_type": _ln(el.tag), "geom_role": role,
                         "surface_type": surface, "part_id": part,
                         "surface_id": el.get(f"{{{GML_NS}}}id") or ""})
        yield key, gid, _attrs(feat), rows


def to_local(geoms):
    """EPSG:3877 shapely geometries -> engine (x, z[, H]) geometries (vectorised)."""
    import numpy as np
    import shapely

    geoms = np.asarray(geoms, dtype=object)
    out = np.empty(len(geoms), dtype=object)
    hz = shapely.has_z(geoms)
    for mask, inc in ((hz, True), (~hz, False)):
        if mask.any():
            def f(c):
                c = c.copy()
                c[:, 0] = c[:, 0] - config.ORIGIN_E
                c[:, 1] = -(c[:, 1] - config.ORIGIN_N)
                if inc:
                    c[:, 2] = c[:, 2] - config.ORIGIN_H
                return np.round(c, 4)  # inputs are mm; drop float subtraction noise
            out[mask] = shapely.transform(geoms[mask], f, include_z=inc)
    return out


RESERVED = {"geometry", "geometry_local", "layer", "feature_key", "gml_id", "geom_index", "geom_type",
            "geom_role", "surface_type", "part_id", "surface_id"}


def pages_to_frame(pages: list[Path], layer: str):
    import geopandas as gpd

    seen: set[str] = set()
    recs = []
    for p in pages:
        for key, gid, attrs, rows in parse_features(p.read_bytes(), layer):
            if key in seen:
                continue
            seen.add(key)
            attrs = {(f"attr_{k}" if k in RESERVED else k): v for k, v in attrs.items()}
            for gi, r in enumerate(rows):
                recs.append({"layer": layer, "feature_key": key, "gml_id": gid, "geom_index": gi, **r, **attrs})
    if not recs:
        gdf = gpd.GeoDataFrame({"layer": [], "feature_key": []}, geometry=[], crs=config.CRS_HORIZONTAL)
        gdf["geometry_local"] = gpd.GeoSeries([], crs=None)
        return gdf, 0
    gdf = gpd.GeoDataFrame(recs, geometry="geometry", crs=config.CRS_HORIZONTAL)
    for c in gdf.columns:
        if c not in ("geometry", "geom_index"):
            gdf[c] = gdf[c].astype("string")
    gdf["geometry_local"] = gpd.GeoSeries(to_local(gdf.geometry.values), crs=None)
    return gdf, len(seen)


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------


def fetch_layer(layer: WfsLayer, area: config.Area, log=print, force_parquet: bool = False) -> dict:
    root = kind_dir("wfs")
    reg = Registry(root)
    adir = root / area.name
    bbox = area.with_margin.to_en().as_tuple()
    summary_path = adir / f"{layer.slug}.summary.json"
    try:
        leaves = fetch_pages(layer, bbox, adir / "gml" / layer.slug, reg, log=log)
    except WfsUnauthorized:
        s = {"layer": layer.name, "status": "unauthorized", "expected": layer.expect_401}
        atomic_write_text(summary_path, json.dumps(s, indent=1))
        log(f"wfs  {layer.name}: 401 unauthorized" + (" (expected)" if layer.expect_401 else " (UNEXPECTED)"))
        return s
    pages_sig = sha256_bytes("\n".join(reg.get(p)["sha256"] for p in leaves).encode())
    out = adir / f"{layer.slug}.parquet"
    if not force_parquet and summary_path.exists() and out.exists():
        s = json.loads(summary_path.read_text())
        if s.get("pages_sig") == pages_sig and reg.is_complete(out):
            log(f"wfs  {layer.name}: cached, {s['features']} features, {s['rows']} rows")
            return s
    gdf, nfeat = pages_to_frame(leaves, layer.name)
    tmp = out.with_name(out.name + ".tmp")
    gdf.to_parquet(tmp, index=False)
    tmp.replace(out)
    reg.record(out, url=None, layer=layer.name, derived_from=len(leaves), features=nfeat)
    all_pages = sorted((adir / "gml" / layer.slug).glob("*.gml"))
    s = {
        "layer": layer.name, "status": "ok", "area": area.name, "bbox_en": list(bbox),
        "features": nfeat, "rows": int(len(gdf)), "leaf_pages": len(leaves), "requests": len(all_pages),
        "gml_bytes": sum(p.stat().st_size for p in all_pages), "parquet_bytes": out.stat().st_size,
        "geom_types": {k: int(v) for k, v in gdf["geom_type"].value_counts().items()} if len(gdf) else {},
        "has_z": bool(gdf.geometry.has_z.any()) if len(gdf) else False,
        "pages_sig": pages_sig,
    }
    for col in ("kplkoodi", "Katuosalaji", "Pintamateriaali", "surface_type"):
        if col in gdf.columns:
            vc = gdf.drop_duplicates("feature_key")[col].value_counts() if col != "surface_type" else gdf[col].value_counts()
            s[f"by_{col}"] = {str(k): int(v) for k, v in vc.head(25).items()}
    atomic_write_text(summary_path, json.dumps(s, indent=1, ensure_ascii=False))
    log(f"wfs  {layer.name}: {nfeat} features, {len(gdf)} rows, {len(all_pages)} requests ({len(leaves)} leaf pages)")
    return s


def fetch_area(area: config.Area, layers=None, log=print) -> list[dict]:
    names = set(layers) if layers else None
    out = []
    for L in WFS_LAYERS:
        if names and L.name not in names:
            continue
        try:
            out.append(fetch_layer(L, area, log=log))
        except Exception as e:  # keep going; report at the end
            log(f"wfs  {L.name}: ERROR {e}")
            out.append({"layer": L.name, "status": "error", "error": str(e)[:500]})
    return out
